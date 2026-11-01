import copy
import html
import uuid
from typing import Dict, List, Optional, Any

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QHBoxLayout, QListWidget, QLabel,
    QSpinBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QLineEdit, QScrollArea, QDialog, QDialogButtonBox,
    QComboBox, QRadioButton, QButtonGroup, QMessageBox, QFrame, QCheckBox, QGridLayout
)
from PySide6.QtCore import Qt, QTimer, Signal, QMimeData, QByteArray, QRectF
from PySide6.QtGui import QDrag, QFont, QPainter, QBrush, QColor, QPen

from instrument_reader.core.camera_devices import (
    label_name, list_cameras, parse_source, windows_listing_incomplete,
)
from instrument_reader.core.excel_link import SLOT_KEYS, AblesungConfig, ValueColumn
from instrument_reader.core.calculation import (
    CalculationChannel,
    CalculationResult,
    CalculationPreset,
    get_standard_presets,
    CalculationEngine,
)


def _norm_col(name: str) -> str:
    return str(name or "").strip().lower()


class DraggableTableWidget(QTableWidget):
    """QTableWidget that allows dragging the ROI name from column 0."""

    def __init__(self, rows: int, cols: int, parent=None):
        super().__init__(rows, cols, parent)
        self.setDragEnabled(True)

    def startDrag(self, supportedActions):
        row = self.currentRow()
        if row >= 0:
            item = self.item(row, 0)
            if item and item.text():
                roi_name = item.text()
                mime = QMimeData()
                mime.setText(roi_name)
                mime.setData("application/x-instrument-reader-roi", QByteArray(roi_name.encode("utf-8")))
                drag = QDrag(self)
                drag.setMimeData(mime)
                drag.exec(Qt.CopyAction)


class DraggableListWidget(QListWidget):
    """QListWidget that allows dragging the selected ROI name."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if item and item.text():
            roi_name = item.text()
            mime = QMimeData()
            mime.setText(roi_name)
            mime.setData("application/x-instrument-reader-roi", QByteArray(roi_name.encode("utf-8")))
            drag = QDrag(self)
            drag.setMimeData(mime)
            drag.exec(Qt.CopyAction)


class DroppableLineEdit(QLineEdit):
    """QLineEdit that accepts dragged ROI names."""
    roi_dropped = Signal(str)

    def __init__(self, placeholder="Drop ROI here...", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setAcceptDrops(True)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-instrument-reader-roi") or event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        text = ""
        if event.mimeData().hasFormat("application/x-instrument-reader-roi"):
            text = bytes(event.mimeData().data("application/x-instrument-reader-roi")).decode("utf-8")
        elif event.mimeData().hasText():
            text = event.mimeData().text().strip()

        if text:
            self.setText(text)
            self.roi_dropped.emit(text)
            event.acceptProposedAction()


class FormulaLineEdit(QLineEdit):
    """Formula line edit that inserts ROI names at cursor when dropped."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        font = QFont("Consolas, Courier New, monospace", 10)
        self.setFont(font)

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat("application/x-instrument-reader-roi") or event.mimeData().hasText():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        event.acceptProposedAction()

    def dropEvent(self, event):
        text = ""
        if event.mimeData().hasFormat("application/x-instrument-reader-roi"):
            text = bytes(event.mimeData().data("application/x-instrument-reader-roi")).decode("utf-8")
        elif event.mimeData().hasText():
            text = event.mimeData().text().strip()

        if text:
            # Wrap in {} if it contains spaces or operators
            token = f"{{{text}}}" if any(c in text for c in " -+*/().,") else text
            self.insert(token)
            event.acceptProposedAction()


class CalculationChannelDialog(QDialog):
    """
    Dialog for creating and editing a CalculationChannel.
    Provides preset templates, drag-and-drop parameter slots, free formula editing,
    and a live test button.
    """

    def __init__(
        self,
        channel: Optional[CalculationChannel] = None,
        available_rois: Optional[List[str]] = None,
        current_readings: Optional[Dict[str, float]] = None,
        calc_engine: Optional[CalculationEngine] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Berechnungs-Kanal konfigurieren" if channel else "Neuer Berechnungs-Kanal")
        self.resize(600, 680)

        self.original_channel = channel
        self.available_rois = list(available_rois or [])
        self.current_readings = dict(current_readings or {})
        self.calc_engine = calc_engine or CalculationEngine()
        self.presets = get_standard_presets()

        self.var_slot_widgets: Dict[str, Dict[str, Any]] = {}
        self.const_slot_widgets: Dict[str, QDoubleSpinBox] = {}

        self.setup_ui()
        self.populate_data()

    def setup_ui(self):
        main_layout = QVBoxLayout(self)

        # 1. Preset Selector
        preset_group = QGroupBox("Verfahrenstechnische Vorlagen / Presets")
        preset_layout = QVBoxLayout(preset_group)

        self.preset_combo = QComboBox()
        self.preset_combo.addItem("— Benutzerdefiniert (Freie Formel) —", None)
        for p in self.presets:
            self.preset_combo.addItem(f"{p.name} [{p.category}]", p)
        self.preset_combo.currentIndexChanged.connect(self.on_preset_changed)
        preset_layout.addWidget(self.preset_combo)

        self.preset_desc_label = QLabel()
        self.preset_desc_label.setWordWrap(True)
        self.preset_desc_label.setStyleSheet("color: #555; font-style: italic; font-size: 11px;")
        preset_layout.addWidget(self.preset_desc_label)
        main_layout.addWidget(preset_group)

        # 2. Channel Metadata
        meta_group = QGroupBox("Kanal-Informationen")
        meta_layout = QHBoxLayout(meta_group)

        name_col = QVBoxLayout()
        name_col.addWidget(QLabel("Kanal-Name:"))
        self.name_edit = QLineEdit("Neuer Kanal")
        name_col.addWidget(self.name_edit)
        meta_layout.addLayout(name_col)

        unit_col = QVBoxLayout()
        unit_col.addWidget(QLabel("Einheit:"))
        self.unit_edit = QLineEdit("g/s")
        unit_col.addWidget(self.unit_edit)
        meta_layout.addLayout(unit_col)

        dec_col = QVBoxLayout()
        dec_col.addWidget(QLabel("Dezimalstellen:"))
        self.decimal_spin = QSpinBox()
        self.decimal_spin.setRange(0, 6)
        self.decimal_spin.setValue(2)
        dec_col.addWidget(self.decimal_spin)
        meta_layout.addLayout(dec_col)

        win_col = QVBoxLayout()
        win_col.addWidget(QLabel("Zeitfenster (s):"))
        self.window_spin = QDoubleSpinBox()
        self.window_spin.setRange(1.0, 300.0)
        self.window_spin.setValue(10.0)
        self.window_spin.setSingleStep(1.0)
        win_col.addWidget(self.window_spin)
        meta_layout.addLayout(win_col)

        main_layout.addWidget(meta_group)

        # 3. Parameter Slots (Variables & Constants)
        self.params_group = QGroupBox("Eingangs-Variablen & Konstanten (Drag & Drop von ROIs möglich)")
        self.params_layout = QVBoxLayout(self.params_group)

        self.params_scroll = QScrollArea()
        self.params_scroll.setWidgetResizable(True)
        self.params_container = QWidget()
        self.params_container_layout = QVBoxLayout(self.params_container)
        self.params_container_layout.setContentsMargins(6, 6, 6, 6)
        self.params_scroll.setWidget(self.params_container)
        self.params_layout.addWidget(self.params_scroll)

        main_layout.addWidget(self.params_group)

        # 4. Formula Editor
        formula_group = QGroupBox("Mathematische Beziehung / Formel")
        formula_layout = QVBoxLayout(formula_group)

        f_note = QLabel(
            "Tipp: ROIs können direkt in das Formelfeld gezogen werden. "
            "Erlaubt sind: +, -, *, /, ^, sqrt, exp, log, ln, abs, rate(roi, win), slope(roi, win)."
        )
        f_note.setWordWrap(True)
        f_note.setStyleSheet("color: #666; font-size: 11px;")
        formula_layout.addWidget(f_note)

        self.formula_edit = FormulaLineEdit()
        self.formula_edit.setPlaceholderText("z.B. rate(m, window_s) * 3600")
        formula_layout.addWidget(self.formula_edit)

        # Test calculation bar
        test_bar = QHBoxLayout()
        self.test_btn = QPushButton("▶ Test-Berechnung mit Live-Werten")
        self.test_btn.clicked.connect(self.run_test_calculation)
        test_bar.addWidget(self.test_btn)

        self.test_result_label = QLabel("Bereit zum Testen")
        self.test_result_label.setStyleSheet("font-weight: bold; color: #333;")
        test_bar.addWidget(self.test_result_label)
        test_bar.addStretch()
        formula_layout.addLayout(test_bar)

        main_layout.addWidget(formula_group)

        # 5. Dialog Buttons
        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self.validate_and_accept)
        self.button_box.rejected.connect(self.reject)
        main_layout.addWidget(self.button_box)

    def populate_data(self):
        if self.original_channel:
            self._is_populating = True
            try:
                # Check if matches any preset formula
                matched_preset = None
                for p in self.presets:
                    if p.formula.strip() == self.original_channel.formula.strip():
                        matched_preset = p
                        break
                if matched_preset:
                    idx = self.presets.index(matched_preset) + 1
                    self.preset_combo.setCurrentIndex(idx)
                    self.build_preset_slots(
                        matched_preset,
                        saved_variables=self.original_channel.variables,
                        saved_constants=self.original_channel.constants,
                    )
                else:
                    self.preset_combo.setCurrentIndex(0)
                    self.build_custom_slots(
                        self.original_channel.variables, self.original_channel.constants
                    )

                # Explicitly restore original channel properties so preset defaults do not overwrite them
                self.name_edit.setText(self.original_channel.name)
                self.unit_edit.setText(self.original_channel.unit)
                self.decimal_spin.setValue(self.original_channel.decimal_places)
                self.window_spin.setValue(self.original_channel.window_seconds)
                self.formula_edit.setText(self.original_channel.formula)
            finally:
                self._is_populating = False
        else:
            # Default to first preset (Massenstrom g/s)
            self.preset_combo.setCurrentIndex(1)

    def on_preset_changed(self, index: int):
        if getattr(self, "_is_populating", False):
            return

        preset: Optional[CalculationPreset] = self.preset_combo.currentData()
        if preset is None:
            self.preset_desc_label.setText("Eigene benutzerdefinierte Formel und Variablen eingeben.")
            self.build_custom_slots({}, {})
            return

        self.preset_desc_label.setText(preset.description)
        self.name_edit.setText(preset.name)
        self.unit_edit.setText(preset.unit)
        self.decimal_spin.setValue(preset.decimal_places)
        self.window_spin.setValue(preset.window_seconds)
        self.formula_edit.setText(preset.formula)

        self.build_preset_slots(preset)

    def clear_param_slots(self):
        while self.params_container_layout.count():
            item = self.params_container_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
            elif item.layout():
                # Clear nested layout
                while item.layout().count():
                    nested_item = item.layout().takeAt(0)
                    if nested_item.widget():
                        nested_item.widget().deleteLater()
        self.var_slot_widgets.clear()
        self.const_slot_widgets.clear()

    def build_preset_slots(
        self,
        preset: CalculationPreset,
        saved_variables: Optional[Dict[str, str]] = None,
        saved_constants: Optional[Dict[str, float]] = None,
    ):
        self.clear_param_slots()

        # Variables slots
        if preset.default_variables:
            var_hdr = QLabel("<b>Variablen (Messwerte aus ROIs oder Fixwerte):</b>")
            self.params_container_layout.addWidget(var_hdr)

            for var_key, default_target in preset.default_variables.items():
                desc = preset.variable_descriptions.get(var_key, var_key)
                row_widget = QFrame()
                row_widget.setFrameShape(QFrame.StyledPanel)
                row_layout = QVBoxLayout(row_widget)
                row_layout.setContentsMargins(6, 4, 6, 4)

                lbl_text = f"<b>{var_key}</b>: {desc}"
                row_layout.addWidget(QLabel(lbl_text))

                ctrl_layout = QHBoxLayout()
                mode_group = QButtonGroup(row_widget)
                rb_roi = QRadioButton("Live ROI")
                rb_fix = QRadioButton("Fixwert")
                mode_group.addButton(rb_roi)
                mode_group.addButton(rb_fix)

                # Droppable ROI Line Edit
                roi_edit = DroppableLineEdit("ROI Name hier ablegen oder tippen...")
                val_to_set = (
                    saved_variables.get(var_key, default_target)
                    if saved_variables
                    else default_target
                )
                roi_edit.setText(val_to_set)

                # Fixed Constant SpinBox
                fix_spin = QDoubleSpinBox()
                fix_spin.setRange(-999999.0, 999999.0)
                fix_spin.setDecimals(3)
                if saved_constants and var_key in saved_constants:
                    fix_spin.setValue(saved_constants[var_key])
                    rb_fix.setChecked(True)
                    roi_edit.setEnabled(False)
                    fix_spin.setEnabled(True)
                else:
                    rb_roi.setChecked(True)
                    roi_edit.setEnabled(True)
                    fix_spin.setEnabled(False)

                def make_toggle_handler(r_edit, f_spin):
                    def handler():
                        is_roi = rb_roi.isChecked()
                        r_edit.setEnabled(is_roi)
                        f_spin.setEnabled(not is_roi)
                    return handler

                toggle_fn = make_toggle_handler(roi_edit, fix_spin)
                rb_roi.toggled.connect(toggle_fn)

                ctrl_layout.addWidget(rb_roi)
                ctrl_layout.addWidget(roi_edit, stretch=2)
                ctrl_layout.addWidget(rb_fix)
                ctrl_layout.addWidget(fix_spin, stretch=1)
                row_layout.addLayout(ctrl_layout)

                self.params_container_layout.addWidget(row_widget)
                self.var_slot_widgets[var_key] = {
                    "rb_roi": rb_roi,
                    "rb_fix": rb_fix,
                    "roi_edit": roi_edit,
                    "fix_spin": fix_spin,
                }

        # Constants slots
        if preset.default_constants:
            const_hdr = QLabel("<b>Konstanten & Betriebsparameter:</b>")
            self.params_container_layout.addWidget(const_hdr)

            for const_key, default_val in preset.default_constants.items():
                desc = preset.constant_descriptions.get(const_key, const_key)
                row_layout = QHBoxLayout()
                lbl = QLabel(f"<b>{const_key}</b> ({desc}):")
                row_layout.addWidget(lbl, stretch=2)

                spin = QDoubleSpinBox()
                spin.setRange(-999999.0, 999999.0)
                spin.setDecimals(4)
                val = (
                    saved_constants.get(const_key, default_val)
                    if saved_constants and const_key in saved_constants
                    else default_val
                )
                spin.setValue(val)
                row_layout.addWidget(spin, stretch=1)

                self.params_container_layout.addLayout(row_layout)
                self.const_slot_widgets[const_key] = spin

        self.params_container_layout.addStretch()

    def build_custom_slots(self, variables: Dict[str, str], constants: Dict[str, float]):
        self.clear_param_slots()

        info_lbl = QLabel(
            "Definiere hier Variablen und Konstanten oder verwende direkte ROI-Namen "
            "wie <code>{Waage}</code> direkt in der Formel."
        )
        info_lbl.setWordWrap(True)
        self.params_container_layout.addWidget(info_lbl)

        if variables:
            self.params_container_layout.addWidget(QLabel("<b>Zugeordnete ROIs:</b>"))
            for var_key, roi_target in variables.items():
                row = QHBoxLayout()
                row.addWidget(QLabel(f"<b>{var_key}</b>:"))
                edit = DroppableLineEdit()
                edit.setText(roi_target)
                row.addWidget(edit)
                self.params_container_layout.addLayout(row)
                self.var_slot_widgets[var_key] = {"roi_edit": edit}

        if constants:
            self.params_container_layout.addWidget(QLabel("<b>Konstanten:</b>"))
            for const_key, const_val in constants.items():
                row = QHBoxLayout()
                row.addWidget(QLabel(f"<b>{const_key}</b>:"))
                spin = QDoubleSpinBox()
                spin.setRange(-999999.0, 999999.0)
                spin.setValue(const_val)
                row.addWidget(spin)
                self.params_container_layout.addLayout(row)
                self.const_slot_widgets[const_key] = spin

        self.params_container_layout.addStretch()

    def get_channel_data(self) -> CalculationChannel:
        ch_id = self.original_channel.id if self.original_channel else str(uuid.uuid4())
        name = self.name_edit.text().strip() or "Kanal"
        unit = self.unit_edit.text().strip()
        formula = self.formula_edit.text().strip()
        dec = self.decimal_spin.value()
        win = self.window_spin.value()

        vars_dict: Dict[str, str] = {}
        consts_dict: Dict[str, float] = {}

        # Harvest variable slots
        for var_key, slot in self.var_slot_widgets.items():
            if "rb_roi" in slot and slot["rb_roi"].isChecked():
                roi_name = slot["roi_edit"].text().strip()
                if roi_name:
                    vars_dict[var_key] = roi_name
            elif "rb_fix" in slot and slot["rb_fix"].isChecked():
                consts_dict[var_key] = slot["fix_spin"].value()
            elif "roi_edit" in slot:
                roi_name = slot["roi_edit"].text().strip()
                if roi_name:
                    vars_dict[var_key] = roi_name

        # Harvest constant slots
        for const_key, spin in self.const_slot_widgets.items():
            consts_dict[const_key] = spin.value()

        return CalculationChannel(
            id=ch_id,
            name=name,
            unit=unit,
            formula=formula,
            variables=vars_dict,
            constants=consts_dict,
            window_seconds=win,
            decimal_places=dec,
            description=self.preset_desc_label.text(),
        )

    def run_test_calculation(self):
        ch = self.get_channel_data()
        res = self.calc_engine.evaluate_channel(ch, self.current_readings)

        if res.is_valid and res.value is not None:
            self.test_result_label.setStyleSheet("font-weight: bold; color: green;")
            self.test_result_label.setText(f"✓ Ergebnis: {res.formatted_value} {res.unit}")
        else:
            err = res.error_message or "Fehler bei Berechnung"
            self.test_result_label.setStyleSheet("font-weight: bold; color: red;")
            self.test_result_label.setText(f"⚠ {err}")

    def validate_and_accept(self):
        name = self.name_edit.text().strip()
        formula = self.formula_edit.text().strip()

        if not name:
            QMessageBox.warning(self, "Ungültiger Name", "Bitte geben Sie einen Kanalnamen ein.")
            return

        # Check collision with existing ROIs
        if any(name.lower() == roi.lower() for roi in self.available_rois):
            QMessageBox.warning(
                self,
                "Ungültiger Name",
                f"Ein Messwert/ROI mit dem Namen '{name}' existiert bereits. Bitte wählen Sie einen eindeutigen Kanalnamen.",
            )
            return

        # Check collision with other calculated channels
        for ch_id, ch in self.calc_engine.channels.items():
            if self.original_channel and self.original_channel.id == ch_id:
                continue
            if ch.name.lower() == name.lower():
                QMessageBox.warning(
                    self,
                    "Ungültiger Name",
                    f"Ein Berechnungs-Kanal mit dem Namen '{name}' existiert bereits. Bitte wählen Sie einen eindeutigen Kanalnamen.",
                )
                return

        if not formula:
            QMessageBox.warning(self, "Ungültige Formel", "Bitte geben Sie eine mathematische Formel ein.")
            return

        # Validate formula syntax using AST parser
        import ast
        from instrument_reader.core.calculation import preprocess_formula
        proc, _ = preprocess_formula(formula)
        try:
            ast.parse(proc, mode="eval")
        except SyntaxError as e:
            QMessageBox.warning(
                self,
                "Syntaxfehler in Formel",
                f"Die Formel enthält einen Syntaxfehler:\n{e.msg or str(e)}",
            )
            return

        self.accept()


class LEDIndicator(QWidget):
    """
    Clean circular 12x12px LED indicator widget.
    Colors:
      - Steady: #2e7d32 (Green)
      - Transition: #ef6c00 (Orange)
      - Inactive: #9e9e9e (Grey)
    Minimalist: No text next to the LED circle; stage and status
    are exclusively displayed via tooltip (setToolTip).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(16, 16)
        self._color = "#2e7d32"
        self._stage = 1
        self._status = "Stationär"
        self._is_transition = False
        self._target_stage = None
        self._inactive = False
        self._text = "Stufe 1 (Stationär)"
        self.setToolTip(self._text)

    def set_status(
        self,
        stage: int,
        status: str = "Stationär",
        is_transition: bool = False,
        target_stage: Optional[int] = None,
        inactive: bool = False,
    ):
        self._stage = stage
        self._status = status
        self._is_transition = is_transition
        self._target_stage = target_stage
        self._inactive = inactive

        if inactive:
            self._color = "#9e9e9e"
            self._text = f"Inaktiv ({status})"
        elif is_transition:
            target = target_stage or (stage + 1)
            self._color = "#ef6c00"
            self._text = f"Stufenwechsel {stage} → {target} ({status})"
        else:
            self._color = "#2e7d32"
            self._text = f"Stufe {stage} ({status})"

        self.setToolTip(self._text)
        self.update()

    def text(self) -> str:
        """Compatibility accessor returning tooltip status text."""
        return self._text

    def styleSheet(self) -> str:
        """Compatibility accessor returning background color."""
        return f"background-color: {self._color};"

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.Antialiasing)
            rect = QRectF(2, 2, 12, 12)
            color = QColor(self._color)
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(color.darker(130), 1))
            painter.drawEllipse(rect)
        finally:
            painter.end()


class _CameraCombo(QComboBox):
    """Editable dropdown that re-scans the cameras each time it is opened."""

    about_to_show = Signal()

    def showPopup(self):
        self.about_to_show.emit()
        super().showPopup()


class ControlPanel(QWidget):
    calc_channel_added = Signal(object)
    calc_channel_updated = Signal(object)
    calc_channel_deleted = Signal(str)
    stage_reset_requested = Signal()
    ablesung_config_changed = Signal(object)  # AblesungConfig
    # Emitted after every camera list refresh: list[CameraDevice], or None if unknown.
    cameras_changed = Signal(object)

    # (label, speed factor); 0 = unpaced ("as fast as possible")
    PLAYBACK_SPEEDS = [("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0), ("4x", 4.0), ("10x", 10.0), ("Max", 0.0)]

    def __init__(self):
        super().__init__()
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)

        # Scroll area for control panel
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        layout = QVBoxLayout(container)

        # Camera Controls
        cam_group = QGroupBox("Camera")
        cam_layout = QVBoxLayout(cam_group)

        # Editable dropdown: pick a detected camera or type an index, video file or stream URL.
        # camera_source is the dropdown's text field, so text()/setText() keep working.
        self.camera_combo = _CameraCombo()
        self.camera_combo.setEditable(True)
        self.camera_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.camera_combo.setToolTip("Kamera auswählen oder Index / Videodatei / Stream-URL eintragen")
        self.camera_source = self.camera_combo.lineEdit()
        self.camera_refresh_btn = QPushButton("↻")
        self.camera_refresh_btn.setFixedWidth(28)
        self.camera_refresh_btn.setToolTip("Kameraliste aktualisieren")
        self.camera_refresh_btn.clicked.connect(self.refresh_cameras)
        source_layout = QHBoxLayout()
        source_layout.addWidget(QLabel("Source:"))
        source_layout.addWidget(self.camera_combo, 1)
        source_layout.addWidget(self.camera_refresh_btn)
        cam_layout.addLayout(source_layout)

        self.camera_status = QLabel("")
        self.camera_status.setWordWrap(True)
        self.camera_status.setVisible(False)
        cam_layout.addWidget(self.camera_status)

        self.camera_hint = QLabel(
            "DirectShow-Kameras (z. B. virtuelle Kameras) können nicht aufgelistet werden: "
            "Paket „cv2-enumerate-cameras“ fehlt (uv sync / pip install -e .). "
            "Index eintippen funktioniert trotzdem."
        )
        self.camera_hint.setWordWrap(True)
        self.camera_hint.setStyleSheet("color: #b26a00;")
        self.camera_hint.setVisible(windows_listing_incomplete())
        cam_layout.addWidget(self.camera_hint)

        self._cameras_initialized = False
        self.refresh_cameras()
        self.camera_combo.about_to_show.connect(self.refresh_cameras)
        # Virtual cameras (DirectShow) send no hot-plug notification, so re-scan regularly.
        self._camera_scan_timer = QTimer(self)
        self._camera_scan_timer.setInterval(3000)
        self._camera_scan_timer.timeout.connect(self.refresh_cameras)
        self._camera_scan_timer.start()
        self._media_devices = None
        try:
            from PySide6.QtMultimedia import QMediaDevices
            self._media_devices = QMediaDevices(self)
            self._media_devices.videoInputsChanged.connect(self.refresh_cameras)
        except Exception:
            pass

        # Neutral grey button style
        self.NEUTRAL_BTN_STYLE = (
            "QPushButton {"
            "  background-color: #e0e0e0; color: #202020;"
            "  border: 1px solid #ababab; border-radius: 3px;"
            "  padding: 4px 8px; font-size: 11px;"
            "}"
            "QPushButton:hover { background-color: #d4d4d4; }"
            "QPushButton:pressed { background-color: #c8c8c8; }"
            "QPushButton:disabled { background-color: #f0f0f0; color: #9e9e9e; border-color: #dcdcdc; }"
        )

        # Row 1: Playback Start / Stop
        self.start_btn = QPushButton("Start / Play")
        self.stop_btn = QPushButton("Stop / Pause")
        self.start_btn.setStyleSheet(self.NEUTRAL_BTN_STYLE)
        self.stop_btn.setStyleSheet(self.NEUTRAL_BTN_STYLE)
        play_layout = QHBoxLayout()
        play_layout.addWidget(self.start_btn)
        play_layout.addWidget(self.stop_btn)
        cam_layout.addLayout(play_layout)

        # Row 2: Seek Backward / Forward
        self.back_btn = QPushButton("Backwards (-5s)")
        self.fwd_btn = QPushButton("Forward (+5s)")
        self.back_btn.setStyleSheet(self.NEUTRAL_BTN_STYLE)
        self.fwd_btn.setStyleSheet(self.NEUTRAL_BTN_STYLE)
        seek_layout = QHBoxLayout()
        seek_layout.addWidget(self.back_btn)
        seek_layout.addWidget(self.fwd_btn)
        cam_layout.addLayout(seek_layout)

        # Row 3: Unlock Backwards Checkbox
        self.unlock_back_cb = QCheckBox("Unlock Backwards")
        self.unlock_back_cb.clicked.connect(self._on_unlock_back_clicked)
        cam_layout.addWidget(self.unlock_back_cb)

        # Row 4: Video Recorder
        rec_layout = QHBoxLayout()
        self.record_btn = QPushButton("Record")
        self.record_btn.setStyleSheet(self.NEUTRAL_BTN_STYLE)
        rec_layout.addWidget(self.record_btn)
        cam_layout.addLayout(rec_layout)

        layout.addWidget(cam_group)

        self.camera_source.textChanged.connect(lambda _: self.update_playback_state())

        # Drawing Mode
        draw_group = QGroupBox("ROI Drawing Mode")
        draw_layout = QHBoxLayout(draw_group)
        self.rect_btn = QPushButton("Rectangle")
        self.poly_btn = QPushButton("Polygon")
        self.ruler_btn = QPushButton("Ruler (Rotameter)")
        self.rect_btn.setCheckable(True)
        self.poly_btn.setCheckable(True)
        self.ruler_btn.setCheckable(True)
        self.rect_btn.setChecked(True)
        draw_layout.addWidget(self.rect_btn)
        draw_layout.addWidget(self.poly_btn)
        draw_layout.addWidget(self.ruler_btn)
        layout.addWidget(draw_group)

        # Settings
        settings_group = QGroupBox("Settings")
        settings_layout = QHBoxLayout(settings_group)
        settings_layout.addWidget(QLabel("Logging Interval (ms):"))
        self.interval_spin = QSpinBox()
        self.interval_spin.setRange(100, 10000)
        self.interval_spin.setValue(1000)
        settings_layout.addWidget(self.interval_spin)
        settings_layout.addWidget(QLabel("Video Speed:"))
        self.speed_combo = QComboBox()
        for label, speed in self.PLAYBACK_SPEEDS:
            self.speed_combo.addItem(label, speed)
        self.speed_combo.setCurrentIndex(1)
        self.speed_combo.setToolTip(
            "Playback speed for video files. 'Max' analyses the video as fast as possible; "
            "readings are timestamped with video time, so rates stay correct at any speed."
        )
        settings_layout.addWidget(self.speed_combo)
        layout.addWidget(settings_group)

        # Experiment Info
        exp_group = QGroupBox("Current Run")
        exp_layout = QVBoxLayout(exp_group)

        info_row = QHBoxLayout()
        self.exp_label = QLabel("Experiment: None | Run: None")
        info_row.addWidget(self.exp_label, stretch=1)

        self.stage_led = LEDIndicator()
        self.stage_badge = self.stage_led
        info_row.addWidget(self.stage_led, alignment=Qt.AlignCenter)

        self.reset_stage_btn = QPushButton("Reset Stage")
        self.reset_stage_btn.clicked.connect(self._on_reset_stage_clicked)
        info_row.addWidget(self.reset_stage_btn)
        exp_layout.addLayout(info_row)

        btn_row = QHBoxLayout()
        self.new_exp_btn = QPushButton("New Experiment")
        self.new_run_btn = QPushButton("New Run")
        self.dashboard_btn = QPushButton("Runs Dashboard")
        btn_row.addWidget(self.new_exp_btn)
        btn_row.addWidget(self.new_run_btn)
        btn_row.addWidget(self.dashboard_btn)
        exp_layout.addLayout(btn_row)

        # Separate row and wording from the camera's "Stop / Pause": these end data logging.
        end_row = QHBoxLayout()
        self.end_run_btn = QPushButton("■ Run beenden")
        self.end_exp_btn = QPushButton("Experiment beenden")
        self.end_run_btn.setToolTip("Speichert ab jetzt keine Messwerte mehr in diesen Run. Kamera und Videoaufnahme laufen weiter.")
        self.end_exp_btn.setToolTip("Beendet den laufenden Run und schließt das Experiment.")
        end_row.addWidget(self.end_run_btn)
        end_row.addWidget(self.end_exp_btn)
        end_row.addStretch()
        exp_layout.addLayout(end_row)
        self.logging_hint = QLabel()
        self.logging_hint.setWordWrap(True)
        exp_layout.addWidget(self.logging_hint)
        self.set_run_state(experiment_open=False, run_active=False)

        layout.addWidget(exp_group)

        # Manual readings: T0/T1 write scale value + time into the row selected in Excel
        abl_group = QGroupBox("Ablesung → Labjournal (Excel)")
        abl_layout = QVBoxLayout(abl_group)

        self.excel_target_label = QLabel()
        self.excel_target_label.setWordWrap(True)
        self.excel_target_label.setTextFormat(Qt.RichText)
        self.excel_target_label.setMinimumHeight(54)
        self.excel_target_label.setStyleSheet("padding: 4px; border: 1px solid #999; border-radius: 3px;")
        abl_layout.addWidget(self.excel_target_label)

        # What a key press writes: per key any number of "live reading -> column" rows plus
        # the time column. Freely editable, saved on every change.
        sheet_row = QHBoxLayout()
        sheet_row.addWidget(QLabel("Blatt:"))
        self.excel_sheet_edit = QLineEdit()
        self.excel_sheet_edit.setPlaceholderText("aktives Blatt")
        self.excel_sheet_edit.setToolTip("Nur auf dieses Excel-Blatt schreiben. Leer = das Blatt, "
                                         "auf dem in Excel gerade der Cursor steht.")
        self.excel_sheet_edit.editingFinished.connect(self._on_ablesung_edited)
        sheet_row.addWidget(self.excel_sheet_edit, 1)
        self.excel_defaults_btn = QPushButton("Standard")
        self.excel_defaults_btn.setToolTip("Setzt Blatt und Spalten auf die Standardwerte zurück "
                                           "(T0: Waage → Masse_0, Zeit_0; T1: Waage → Masse_1, Zeit_1).")
        self.excel_defaults_btn.clicked.connect(self._reset_ablesung_config)
        sheet_row.addWidget(self.excel_defaults_btn)
        abl_layout.addLayout(sheet_row)

        self._roi_names: List[str] = []
        self._abl_config = AblesungConfig()
        self._abl_rows: Dict[str, list] = {}  # slot -> [(combo, column edit, value label)]
        self._abl_grids: Dict[str, QGridLayout] = {}
        self.excel_time_edits: Dict[str, QLineEdit] = {}
        for slot, key in SLOT_KEYS.items():
            box = QFrame()
            box.setFrameShape(QFrame.StyledPanel)
            box_layout = QVBoxLayout(box)
            box_layout.setContentsMargins(6, 4, 6, 4)
            head = QHBoxLayout()
            head.addWidget(QLabel(f"<b>{slot} [{key}]</b>"))
            head.addStretch()
            head.addWidget(QLabel("Zeit →"))
            time_edit = QLineEdit()
            time_edit.setPlaceholderText("keine Zeit")
            time_edit.setToolTip("Spalte für die Uhrzeit des Tastendrucks. Leer = keine Zeit schreiben.")
            time_edit.setMaximumWidth(130)
            time_edit.editingFinished.connect(self._on_ablesung_edited)
            head.addWidget(time_edit)
            self.excel_time_edits[slot] = time_edit
            box_layout.addLayout(head)
            grid = QGridLayout()
            grid.setContentsMargins(0, 0, 0, 0)
            grid.setColumnStretch(0, 3)
            grid.setColumnStretch(2, 2)
            box_layout.addLayout(grid)
            self._abl_grids[slot] = grid
            add_btn = QPushButton("+ Wert")
            add_btn.setToolTip(f"Noch einen Messwert mit {key} schreiben.")
            add_btn.clicked.connect(lambda _=False, s=slot: self._add_ablesung_row(s))
            add_row = QHBoxLayout()
            add_row.addWidget(add_btn)
            add_row.addStretch()
            box_layout.addLayout(add_row)
            abl_layout.addWidget(box)

        t_row = QHBoxLayout()
        self.t0_btn = QPushButton()
        self.t1_btn = QPushButton()
        for b in (self.t0_btn, self.t1_btn):
            b.setMinimumHeight(40)
            b.setStyleSheet("font-weight: bold;")
            b.setToolTip("Schreibt die gewählten Live-Readings und die Uhrzeit in die Zeile, in der in Excel "
                         "der Cursor steht. Belegte Zellen werden nie überschrieben.")
            t_row.addWidget(b)
        abl_layout.addLayout(t_row)

        self.ablesung_status = QLabel()
        self.ablesung_status.setWordWrap(True)
        abl_layout.addWidget(self.ablesung_status)
        self.ablesung_history = QListWidget()
        self.ablesung_history.setMaximumHeight(90)
        abl_layout.addWidget(self.ablesung_history)
        layout.addWidget(abl_group)
        self.set_ablesung_config(AblesungConfig())

        # Readings Table (Draggable)
        layout.addWidget(QLabel("<b>Live Readings (Drag ROIs to calculations):</b>"))
        self.readings_table = DraggableTableWidget(0, 5)
        self.readings_table.setHorizontalHeaderLabels(["ROI Name", "Value", "Unit", "Conf", "Status"])
        self.readings_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.readings_table.setMinimumHeight(150)
        layout.addWidget(self.readings_table)

        # Calculated Channels Section
        calc_group = QGroupBox("Berechnete Kanäle (Calculated Channels)")
        calc_layout = QVBoxLayout(calc_group)

        self.calc_table = QTableWidget(0, 5)
        self.calc_table.setHorizontalHeaderLabels(["Name", "Wert", "Einheit", "Formel", "Status"])
        self.calc_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.calc_table.setMinimumHeight(140)
        calc_layout.addWidget(self.calc_table)

        calc_btn_layout = QHBoxLayout()
        self.add_calc_btn = QPushButton("+ Neue Berechnung")
        self.edit_calc_btn = QPushButton("Bearbeiten")
        self.delete_calc_btn = QPushButton("Löschen")
        calc_btn_layout.addWidget(self.add_calc_btn)
        calc_btn_layout.addWidget(self.edit_calc_btn)
        calc_btn_layout.addWidget(self.delete_calc_btn)
        calc_layout.addLayout(calc_btn_layout)
        layout.addWidget(calc_group)

        # ROI List (Draggable)
        layout.addWidget(QLabel("ROIs:"))
        self.roi_list = DraggableListWidget()
        self.roi_list.setMinimumHeight(100)
        layout.addWidget(self.roi_list)

        self.delete_roi_btn = QPushButton("Delete Selected ROI")
        layout.addWidget(self.delete_roi_btn)

        scroll.setWidget(container)
        outer_layout.addWidget(scroll)

        self.calculated_channels: List[CalculationChannel] = []
        self.update_playback_state()

    def add_roi(self, roi):
        self.roi_list.addItem(roi.name)

    def update_readings(self, readings):
        self.readings_table.setRowCount(len(readings))
        for i, r in enumerate(readings):
            self.readings_table.setItem(i, 0, QTableWidgetItem(r["roi_name"]))
            val_str = f"{r['parsed_value']}" if r["parsed_value"] is not None else "-"
            self.readings_table.setItem(i, 1, QTableWidgetItem(val_str))
            self.readings_table.setItem(i, 2, QTableWidgetItem(r.get("unit", "")))
            conf = r.get("confidence")
            conf_str = f"{conf:.2f}" if conf is not None else "-"
            self.readings_table.setItem(i, 3, QTableWidgetItem(conf_str))

            status = "✓" if r["is_valid"] else f"⚠ {r.get('reason', '')}"
            if r.get("used_fallback"):
                status += " (OCCLUDED)"
            self.readings_table.setItem(i, 4, QTableWidgetItem(status))

    def update_calculated_readings(self, results: List[CalculationResult]):
        self.calc_table.setRowCount(len(results))
        for i, res in enumerate(results):
            item_name = QTableWidgetItem(res.name)
            item_name.setData(Qt.UserRole, res.channel_id)
            self.calc_table.setItem(i, 0, item_name)
            self.calc_table.setItem(i, 1, QTableWidgetItem(res.formatted_value))
            self.calc_table.setItem(i, 2, QTableWidgetItem(res.unit))
            self.calc_table.setItem(i, 3, QTableWidgetItem(res.formula))

            status = "✓" if res.is_valid else f"⚠ {res.error_message or 'Fehler'}"
            self.calc_table.setItem(i, 4, QTableWidgetItem(status))

    def set_calculated_channels(self, channels: List[CalculationChannel]):
        self.calculated_channels = list(channels)
        self.calc_table.setRowCount(len(channels))
        for i, ch in enumerate(channels):
            item_name = QTableWidgetItem(ch.name)
            item_name.setData(Qt.UserRole, ch.id)
            self.calc_table.setItem(i, 0, item_name)
            self.calc_table.setItem(i, 1, QTableWidgetItem("-"))
            self.calc_table.setItem(i, 2, QTableWidgetItem(ch.unit))
            self.calc_table.setItem(i, 3, QTableWidgetItem(ch.formula))
            self.calc_table.setItem(i, 4, QTableWidgetItem("Initialisiert"))

    def remove_roi_reading(self, roi_name: str, roi_id: Optional[str] = None):
        """Removes rows matching roi_name from live readings table."""
        rows_to_remove = []
        for row in range(self.readings_table.rowCount()):
            item = self.readings_table.item(row, 0)
            if item and item.text() == roi_name:
                rows_to_remove.append(row)
        for row in reversed(rows_to_remove):
            self.readings_table.removeRow(row)

    def update_roi_name(self, old_name: str, new_name: str):
        """Updates display name in live readings table when an ROI is renamed."""
        for row in range(self.readings_table.rowCount()):
            item = self.readings_table.item(row, 0)
            if item and item.text() == old_name:
                item.setText(new_name)

    # -- Ablesung (T0/T1 -> Excel) ----------------------------------------------------------

    def resolve_roi(self, roi: str) -> str:
        """The live reading a row stands for: its own choice, else (empty) the scale, i.e.
        the first reading named like 'Waage', else the first one."""
        if roi:
            return roi
        return next((n for n in self._roi_names if "waage" in n.lower()),
                    self._roi_names[0] if self._roi_names else "")

    def set_scale_roi_names(self, names: List[str]) -> None:
        """Offers the current live readings in every row's dropdown."""
        if names == self._roi_names:
            return
        self._roi_names = list(names)
        for slot, rows in self._abl_rows.items():
            for i, (combo, _edit, _lbl) in enumerate(rows):
                self._fill_roi_combo(combo, self._abl_config.slots[slot].values[i].roi)

    def _fill_roi_combo(self, combo: QComboBox, roi: str) -> None:
        shown = self.resolve_roi(roi)
        items = list(self._roi_names)
        if shown and shown not in items:
            items.append(shown)  # saved reading that has no ROI (yet): keep showing it
        combo.blockSignals(True)
        combo.clear()
        combo.addItems(items)
        combo.setCurrentText(shown)
        combo.blockSignals(False)

    def ablesung_config(self) -> AblesungConfig:
        return copy.deepcopy(self._abl_config)

    def set_ablesung_config(self, config: AblesungConfig) -> None:
        """Shows the configuration (without emitting a change)."""
        self._abl_config = copy.deepcopy(config)
        self.excel_sheet_edit.setText(config.sheet)
        for slot in SLOT_KEYS:
            self.excel_time_edits[slot].setText(config.slots[slot].time)
            self._rebuild_ablesung_rows(slot)
        self._label_ablesung_buttons()

    def _rebuild_ablesung_rows(self, slot: str) -> None:
        grid = self._abl_grids[slot]
        while grid.count():
            w = grid.takeAt(0).widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        rows = []
        for i, vc in enumerate(self._abl_config.slots[slot].values):
            combo = QComboBox()
            combo.setToolTip("Dieser Live-Reading-Wert wird geschrieben.")
            self._fill_roi_combo(combo, vc.roi)
            combo.activated.connect(lambda _i, s=slot, c=combo: self._on_roi_picked(s, c))
            edit = QLineEdit(vc.column)
            edit.setPlaceholderText("Spalte")
            edit.setToolTip("Spaltenüberschrift in Excel (z. B. Masse_0) oder Spaltenbuchstabe (z. B. D). "
                            "Gibt es eine Überschrift mit genau diesem Namen, gilt die Überschrift.")
            edit.editingFinished.connect(self._on_ablesung_edited)
            value_lbl = QLabel("–")
            value_lbl.setMinimumWidth(60)
            value_lbl.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            del_btn = QPushButton("×")
            del_btn.setFixedWidth(28)
            del_btn.setToolTip("Diesen Wert nicht mehr schreiben.")
            del_btn.clicked.connect(lambda _=False, s=slot, c=combo: self._remove_ablesung_row(s, c))
            grid.addWidget(combo, i, 0)
            grid.addWidget(QLabel("→"), i, 1)
            grid.addWidget(edit, i, 2)
            grid.addWidget(value_lbl, i, 3)
            grid.addWidget(del_btn, i, 4)
            rows.append((combo, edit, value_lbl))
        self._abl_rows[slot] = rows

    def _row_index(self, slot: str, combo: QComboBox) -> int:
        return next(i for i, (c, _e, _l) in enumerate(self._abl_rows[slot]) if c is combo)

    def _on_roi_picked(self, slot: str, combo: QComboBox) -> None:
        self._abl_config.slots[slot].values[self._row_index(slot, combo)].roi = combo.currentText()
        self._emit_ablesung_config()

    def _add_ablesung_row(self, slot: str) -> None:
        self._read_ablesung_fields()
        used = {self.resolve_roi(v.roi) for v in self._abl_config.slots[slot].values}
        roi = next((n for n in self._roi_names if n not in used), "")
        self._abl_config.slots[slot].values.append(ValueColumn(roi=roi, column=""))
        self._rebuild_ablesung_rows(slot)
        self._abl_rows[slot][-1][1].setFocus()
        self._emit_ablesung_config()

    def _remove_ablesung_row(self, slot: str, combo: QComboBox) -> None:
        self._read_ablesung_fields()
        del self._abl_config.slots[slot].values[self._row_index(slot, combo)]
        self._rebuild_ablesung_rows(slot)
        self._emit_ablesung_config()

    def _read_ablesung_fields(self) -> None:
        cfg = self._abl_config
        cfg.sheet = self.excel_sheet_edit.text().strip()
        for slot, rows in self._abl_rows.items():
            cfg.slots[slot].time = self.excel_time_edits[slot].text().strip()
            for vc, (_combo, edit, _lbl) in zip(cfg.slots[slot].values, rows):
                vc.column = edit.text().strip()

    def _on_ablesung_edited(self) -> None:
        before = copy.deepcopy(self._abl_config)
        self._read_ablesung_fields()
        if self._abl_config != before:
            self._emit_ablesung_config()

    def _emit_ablesung_config(self) -> None:
        self._label_ablesung_buttons()
        self.ablesung_config_changed.emit(self.ablesung_config())

    def _reset_ablesung_config(self) -> None:
        self.set_ablesung_config(AblesungConfig())
        self._emit_ablesung_config()

    def _label_ablesung_buttons(self) -> None:
        for slot, btn in (("T0", self.t0_btn), ("T1", self.t1_btn)):
            cols = [c for c in self._abl_config.columns(slot) if c]
            text = ", ".join(cols) if cols else "nichts"
            if len(text) > 28:
                text = f"{len(cols)} Spalten"
            btn.setText(f"{slot} → {text}   [{SLOT_KEYS[slot]}]")

    def update_ablesung_values(self, values: Dict[str, tuple]) -> None:
        """Shows each row's live value; ``values`` is reading name -> (value, valid)."""
        for slot, rows in self._abl_rows.items():
            for vc, (_combo, _edit, lbl) in zip(self._abl_config.slots[slot].values, rows):
                value, valid = values.get(self.resolve_roi(vc.roi), (None, False))
                lbl.setText("–" if value is None else f"{value:g}")
                lbl.setStyleSheet("font-weight: bold;" + ("" if valid else " color: #c62828;"))

    def show_excel_target(self, target, error: str = "", config: Optional[AblesungConfig] = None) -> None:
        """Shows where T0/T1 would write right now (or why they can't)."""
        if error:
            self.excel_target_label.setText(f"<b>Ziel:</b> <span style='color:#c62828'>{html.escape(error)}</span>")
            return
        config = config or self._abl_config
        time_cols = {_norm_col(sc.time) for sc in config.slots.values() if sc.time.strip()}

        def cell(col):
            v = target.values.get(col)
            if v is None or (isinstance(v, str) and not v.strip()):
                value = "<span style='color:#2e7d32'>leer</span>"
            else:
                if _norm_col(col) in time_cols and isinstance(v, float) and 0 <= v < 1:
                    s = round(v * 86400)
                    v = f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"
                elif isinstance(v, float):
                    v = f"{v:g}"
                value = f"<b>{html.escape(str(v))}</b>"
            return f"{html.escape(col)} {value}"

        ctx = f" ({html.escape(target.context)})" if target.context else ""
        parts = [f"{slot}: " + (", ".join(cell(c) for c in config.columns(slot)) or "–") for slot in config.slots]
        self.excel_target_label.setText(
            f"<b>Ziel:</b> {html.escape(target.workbook)} › {html.escape(target.sheet)} › <b>Zeile {target.row}</b>{ctx}<br>"
            + " &nbsp;|&nbsp; ".join(parts))

    def add_ablesung_history(self, text: str) -> None:
        self.ablesung_history.insertItem(0, text)

    def set_ablesung_status(self, text: str, ok: bool = True) -> None:
        self.ablesung_status.setText(text)
        self.ablesung_status.setStyleSheet("" if ok else "color: #c62828; font-weight: bold;")

    def _on_reset_stage_clicked(self):
        self.set_stage_status(1, "Stationär", is_transition=False)
        self.stage_reset_requested.emit()

    def set_run_state(self, experiment_open: bool, run_active: bool) -> None:
        """Enables the run buttons for the current state and says whether values are saved."""
        self.end_run_btn.setEnabled(run_active)
        self.end_exp_btn.setEnabled(experiment_open)
        if run_active:
            self.logging_hint.setText("● Messwerte werden gespeichert")
            self.logging_hint.setStyleSheet("color: #2e7d32; font-weight: bold;")
        else:
            self.logging_hint.setText("Kein aktiver Run – Messwerte werden NICHT gespeichert")
            self.logging_hint.setStyleSheet("color: #c62828; font-weight: bold;")

    def set_stage_status(
        self,
        stage: int,
        status: str = "Stationär",
        is_transition: bool = False,
        target_stage: Optional[int] = None,
        inactive: bool = False,
    ):
        self.stage_led.set_status(
            stage=stage,
            status=status,
            is_transition=is_transition,
            target_stage=target_stage,
            inactive=inactive,
        )

    def selected_source(self) -> int | str:
        """The selected source as OpenCV expects it: a camera index or a path/URL."""
        return parse_source(self.camera_source.text())

    def is_video_source(self) -> bool:
        src = self.selected_source()
        return isinstance(src, str) and len(src) > 0 and not src.startswith("/dev/video")

    def refresh_cameras(self):
        """Re-reads the attached cameras into the dropdown, keeping the selected camera.

        The selection follows the camera's name, so it stays correct when the OS
        renumbers devices after a camera is unplugged and plugged back in.
        """
        current = self.camera_source.text().strip()
        current_src = parse_source(current)
        current_name = label_name(current)
        found = list_cameras()
        devices = found or []

        labels = [dev.label for dev in devices]
        existing = [self.camera_combo.itemText(i) for i in range(self.camera_combo.count())]
        first = not self._cameras_initialized
        self._cameras_initialized = True
        if labels != existing or first:
            self.camera_combo.blockSignals(True)
            self.camera_combo.clear()
            for dev in devices:
                self.camera_combo.addItem(dev.label, dev.index)
            self.camera_combo.blockSignals(False)

            match = -1
            if current_name is not None:
                match = next((i for i, dev in enumerate(devices) if dev.name == current_name), -1)
            if match < 0 and current_name is None:
                match = next((i for i, dev in enumerate(devices) if dev.index == current_src), -1)
            if match >= 0:
                self.camera_combo.setCurrentIndex(match)
            elif current or not first:
                # Never overwrite what the user typed (or cleared) themselves.
                self.camera_combo.setEditText(current)
            elif devices:
                self.camera_combo.setCurrentIndex(0)
            else:
                self.camera_combo.setEditText("0")

        self.cameras_changed.emit(found)

    def set_camera_status(self, state: str, message: str):
        colors = {"connected": "#2e7d32", "connecting": "#b26a00", "reconnecting": "#c62828"}
        self.camera_status.setText(message)
        self.camera_status.setStyleSheet(f"color: {colors.get(state, '#606060')};")
        self.camera_status.setVisible(bool(message))

    def update_playback_state(self):
        is_video = self.is_video_source()
        if is_video:
            self.fwd_btn.setEnabled(True)
            self.unlock_back_cb.setEnabled(True)
            self.back_btn.setEnabled(self.unlock_back_cb.isChecked())
        else:
            self.fwd_btn.setEnabled(False)
            self.lock_backwards()
            self.unlock_back_cb.setEnabled(False)

    def _on_unlock_back_clicked(self):
        if not self.is_video_source():
            self.unlock_back_cb.setChecked(False)
            self.back_btn.setEnabled(False)
            return

        if self.unlock_back_cb.isChecked():
            reply = QMessageBox.warning(
                self,
                "Sicherheitswarnung / Safety Warning",
                "Warnung: Rückwärtsspulen während aktiver Messungen kann zu doppelten Datensätzen "
                "oder nicht-monotonen Zeitstempeln führen.\n\n"
                "Warning: Backward seeking during active analysis may cause duplicate data or non-monotonic timestamps.\n\n"
                "Möchten Sie Rückwärtsspulen wirklich freischalten / Do you want to unlock backwards seeking?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.back_btn.setEnabled(True)
            else:
                self.unlock_back_cb.setChecked(False)
                self.back_btn.setEnabled(False)
        else:
            self.back_btn.setEnabled(False)

    def unlock_backwards(self, prompt: bool = True) -> bool:
        """Unlocks backwards seek button. If prompt is True, asks user for confirmation."""
        if not self.is_video_source():
            return False
        if prompt:
            reply = QMessageBox.warning(
                self,
                "Sicherheitswarnung / Safety Warning",
                "Warnung: Rückwärtsspulen während aktiver Messungen kann zu doppelten Datensätzen "
                "oder nicht-monotonen Zeitstempeln führen.\n\n"
                "Warning: Backward seeking during active analysis may cause duplicate data or non-monotonic timestamps.\n\n"
                "Möchten Sie Rückwärtsspulen wirklich freischalten / Do you want to unlock backwards seeking?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                self.unlock_back_cb.setChecked(False)
                self.back_btn.setEnabled(False)
                return False
        self.unlock_back_cb.setChecked(True)
        self.back_btn.setEnabled(True)
        return True

    def lock_backwards(self):
        self.unlock_back_cb.setChecked(False)
        self.back_btn.setEnabled(False)

    def playback_speed(self) -> float:
        speed = self.speed_combo.currentData()
        return float(speed) if speed is not None else 1.0

    def set_recording(self, recording: bool):
        if recording:
            self.record_btn.setText("Stop Recording")
            self.record_btn.setStyleSheet(
                "QPushButton {"
                "  background-color: #ffebee; color: #c62828; font-weight: bold;"
                "  border: 1px solid #ef5350; border-radius: 3px;"
                "  padding: 4px 8px; font-size: 11px;"
                "}"
                "QPushButton:hover { background-color: #ffcdd2; }"
            )
        else:
            self.record_btn.setText("Record")
            self.record_btn.setStyleSheet(self.NEUTRAL_BTN_STYLE)


