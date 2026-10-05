import uuid
from typing import Dict, List, Optional, Any

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QHBoxLayout, QListWidget, QLabel,
    QSpinBox, QDoubleSpinBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QGroupBox, QLineEdit, QScrollArea, QDialog, QDialogButtonBox,
    QComboBox, QRadioButton, QButtonGroup, QMessageBox, QFrame, QCheckBox
)
from PySide6.QtCore import Qt, Signal, QMimeData, QByteArray, QRectF
from PySide6.QtGui import QDrag, QFont, QPainter, QBrush, QColor, QPen

from instrument_reader.core.calculation import (
    CalculationChannel,
    CalculationResult,
    CalculationPreset,
    get_standard_presets,
    CalculationEngine,
)


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


class ControlPanel(QWidget):
    calc_channel_added = Signal(object)
    calc_channel_updated = Signal(object)
    calc_channel_deleted = Signal(str)
    stage_reset_requested = Signal()

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

        self.camera_source = QLineEdit("0")
        source_layout = QHBoxLayout()
        source_layout.addWidget(QLabel("Source:"))
        source_layout.addWidget(self.camera_source)
        cam_layout.addLayout(source_layout)

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

        layout.addWidget(exp_group)

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

    def _on_reset_stage_clicked(self):
        self.set_stage_status(1, "Stationär", is_transition=False)
        self.stage_reset_requested.emit()

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

    def is_video_source(self) -> bool:
        s = self.camera_source.text().strip()
        return not s.isdigit() and len(s) > 0 and not s.startswith("/dev/video")

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


