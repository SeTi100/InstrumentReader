from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox, 
    QDoubleSpinBox, QDialogButtonBox, QSpinBox, QCheckBox,
    QListWidget, QListWidgetItem, QLabel, QGroupBox, QMessageBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QHBoxLayout,
    QWidget
)
from PySide6.QtCore import Qt
from typing import Optional, List
from instrument_reader.core.roi import ROIConfig, DisplayType, ROIShape

class ROIConfigDialog(QDialog):
    def __init__(self, roi: ROIConfig, all_rois: Optional[List[ROIConfig]] = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configure ROI")
        self.roi = roi
        self.all_rois = all_rois or []
        layout = QVBoxLayout(self)
        
        form = QFormLayout()
        self.name_edit = QLineEdit(roi.name)
        
        self.type_combo = QComboBox()
        for t in DisplayType:
            self.type_combo.addItem(t.value, t)
        if roi.shape == ROIShape.RULER:
            self.type_combo.setCurrentText(DisplayType.ANALOG.value)
            self.type_combo.setEnabled(False)
            self.type_combo.setToolTip("Ruler ROIs always use analog scale reading.")
        else:
            self.type_combo.setCurrentText(roi.display_type.value)
        
        self.unit_edit = QLineEdit(roi.unit)
        
        self.min_edit = QDoubleSpinBox()
        self.min_edit.setRange(-99999, 99999)
        if roi.value_min is not None:
            self.min_edit.setValue(roi.value_min)
            
        self.max_edit = QDoubleSpinBox()
        self.max_edit.setRange(-99999, 99999)
        if roi.value_max is not None:
            self.max_edit.setValue(roi.value_max)
            
        self.delta_edit = QDoubleSpinBox()
        self.delta_edit.setRange(0, 99999)
        if roi.max_delta_per_sec is not None:
            self.delta_edit.setValue(roi.max_delta_per_sec)

        self.decimal_edit = QSpinBox()
        self.decimal_edit.setRange(0, 10)
        if roi.decimal_places is not None:
            self.decimal_edit.setValue(roi.decimal_places)
        else:
            self.decimal_edit.setValue(0)

        form.addRow("Name:", self.name_edit)
        form.addRow("Display Type:", self.type_combo)
        form.addRow("Unit:", self.unit_edit)
        form.addRow("Min Value:", self.min_edit)
        form.addRow("Max Value:", self.max_edit)
        form.addRow("Max Delta/s:", self.delta_edit)
        form.addRow("Decimal Places:", self.decimal_edit)

        # Composite / Container ROI Settings
        self.is_container_cb = QCheckBox("Container ROI (Ziffern / Sub-ROIs zusammenfassen)")
        self.is_container_cb.setChecked(getattr(roi, "is_container", False))

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Left to Right (ltr)", "Right to Left (rtl)"])
        if getattr(roi, "sort_direction", "ltr") == "rtl":
            self.sort_combo.setCurrentIndex(1)
        else:
            self.sort_combo.setCurrentIndex(0)

        self.decimal_pos_spin = QSpinBox()
        self.decimal_pos_spin.setRange(0, 10)
        self.decimal_pos_spin.setToolTip("Kommaposition: Nach der wievielten Ziffer (0 = Deaktiviert)")
        if getattr(roi, "decimal_position", None) is not None:
            self.decimal_pos_spin.setValue(roi.decimal_position)
        else:
            self.decimal_pos_spin.setValue(0)

        self.allow_blank_cb = QCheckBox("Führende Leerstellen erlauben (z.B. '  15.4')")
        self.allow_blank_cb.setChecked(getattr(roi, "allow_leading_blank", True))

        self.allow_neg_cb = QCheckBox("Negative Zahlen erlauben (Minuszeichen in Slot 1)")
        self.allow_neg_cb.setChecked(getattr(roi, "allow_negative", True))

        # Analog / Rotameter Calibration Mark Settings
        self.cal_mark_cb = QCheckBox("Als Skalen-Kalibriermarke verwenden")
        self.analog_val_spin = QDoubleSpinBox()
        self.analog_val_spin.setRange(-99999, 99999)
        self.analog_val_spin.setDecimals(2)
        if getattr(roi, "analog_value", None) is not None:
            self.cal_mark_cb.setChecked(True)
            self.analog_val_spin.setValue(roi.analog_value)
        else:
            self.cal_mark_cb.setChecked(False)
            self.analog_val_spin.setValue(0.0)

        if roi.shape != ROIShape.RULER:
            form.addRow(self.is_container_cb)
            form.addRow("Sort Direction:", self.sort_combo)
            form.addRow("Decimal Position (after digit N):", self.decimal_pos_spin)
            form.addRow(self.allow_blank_cb)
            form.addRow(self.allow_neg_cb)
            form.addRow(self.cal_mark_cb)
            form.addRow("Calibration Value:", self.analog_val_spin)
        
        layout.addLayout(form)

        # Ruler-specific settings
        self.ruler_group = None
        if roi.shape == ROIShape.RULER:
            self.ruler_group = QGroupBox("Ruler / Messachse Einstellungen")
            ruler_layout = QVBoxLayout(self.ruler_group)
            
            strip_layout = QHBoxLayout()
            strip_layout.addWidget(QLabel("Messstreifen-Breite (px):"))
            self.strip_width_spin = QDoubleSpinBox()
            self.strip_width_spin.setRange(5.0, 500.0)
            self.strip_width_spin.setValue(getattr(roi, "strip_width", 30.0))
            strip_layout.addWidget(self.strip_width_spin)
            ruler_layout.addLayout(strip_layout)

            edge_layout = QHBoxLayout()
            edge_layout.addWidget(QLabel("Erkannte Kante (Reading Edge):"))
            self.ruler_edge_combo = QComboBox()
            self.ruler_edge_combo.addItem("Oberkante (Top)", "top")
            self.ruler_edge_combo.addItem("Unterkante (Bottom)", "bottom")
            self.ruler_edge_combo.addItem("Mitte / Kugeläquator (Center)", "center")
            curr_edge = getattr(roi, "ruler_edge", "top")
            c_idx = self.ruler_edge_combo.findData(curr_edge)
            if c_idx >= 0:
                self.ruler_edge_combo.setCurrentIndex(c_idx)
            edge_layout.addWidget(self.ruler_edge_combo)
            ruler_layout.addLayout(edge_layout)

            # Scale mark suppression toggle (2D blob filter)
            self.suppress_scale_cb = QCheckBox("Skalenstriche unterdrücken (2D-Objektfilter)")
            self.suppress_scale_cb.setToolTip(
                "Ignoriert gedruckte Skalenstriche auf dem Glas und fokussiert auf den echten Schwimmerkörper."
            )
            self.suppress_scale_cb.setChecked(getattr(roi, "suppress_scale_marks", True))
            ruler_layout.addWidget(self.suppress_scale_cb)

            self.filter_opts_widget = QWidget()
            filter_opts_layout = QHBoxLayout(self.filter_opts_widget)
            filter_opts_layout.setContentsMargins(0, 0, 0, 0)

            filter_opts_layout.addWidget(QLabel("Kern-Breite:"))
            self.core_width_spin = QSpinBox()
            self.core_width_spin.setRange(10, 100)
            self.core_width_spin.setSingleStep(5)
            self.core_width_spin.setSuffix(" %")
            self.core_width_spin.setValue(int(getattr(roi, "core_width_pct", 0.6) * 100))
            filter_opts_layout.addWidget(self.core_width_spin)

            filter_opts_layout.addWidget(QLabel("Mindesthöhe:"))
            self.min_height_spin = QSpinBox()
            self.min_height_spin.setRange(2, 200)
            self.min_height_spin.setSuffix(" px")
            self.min_height_spin.setValue(getattr(roi, "min_float_height", 8))
            filter_opts_layout.addWidget(self.min_height_spin)

            self.suppress_scale_cb.toggled.connect(self.filter_opts_widget.setVisible)
            self.filter_opts_widget.setVisible(self.suppress_scale_cb.isChecked())
            ruler_layout.addWidget(self.filter_opts_widget)
            
            ruler_layout.addWidget(QLabel("Kalibriermarken (entlang der Messachse):"))
            self.marks_table = QTableWidget(0, 2)
            self.marks_table.setHorizontalHeaderLabels(["Position (0.0=Start .. 1.0=Ende)", "Skalenwert"])
            self.marks_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
            
            marks = getattr(roi, "calibration_marks", [])
            self.marks_table.setRowCount(len(marks))
            for i, m in enumerate(marks):
                pos = float(m.get("pos", 0.0))
                val = float(m.get("value", 0.0))
                self.marks_table.setItem(i, 0, QTableWidgetItem(f"{pos:.4f}"))
                self.marks_table.setItem(i, 1, QTableWidgetItem(f"{val:.2f}"))
                
            ruler_layout.addWidget(self.marks_table)
            
            btn_row = QHBoxLayout()
            self.add_mark_btn = QPushButton("Marke hinzufügen")
            self.del_mark_btn = QPushButton("Marke löschen")
            self.add_mark_btn.clicked.connect(self._add_mark)
            self.del_mark_btn.clicked.connect(self._del_mark)
            btn_row.addWidget(self.add_mark_btn)
            btn_row.addWidget(self.del_mark_btn)
            ruler_layout.addLayout(btn_row)
            
            layout.addWidget(self.ruler_group)

        # Optional Sub-ROI selection list (only if not a ruler)
        other_rois = [r for r in self.all_rois if r.name != roi.name]
        if other_rois and roi.shape != ROIShape.RULER:
            sub_group = QGroupBox("Zugeordnete Sub-ROIs (Leer = Automatische Erkennung nach Position)")
            sub_layout = QVBoxLayout(sub_group)
            self.sub_roi_list = QListWidget()
            existing_sub_ids = set(getattr(roi, "sub_roi_ids", []))
            for other in other_rois:
                item = QListWidgetItem(other.name)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                is_checked = other.name in existing_sub_ids
                item.setCheckState(Qt.Checked if is_checked else Qt.Unchecked)
                self.sub_roi_list.addItem(item)
            sub_layout.addWidget(self.sub_roi_list)
            layout.addWidget(sub_group)
        else:
            self.sub_roi_list = None
        
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def _add_mark(self):
        row = self.marks_table.rowCount()
        self.marks_table.insertRow(row)
        self.marks_table.setItem(row, 0, QTableWidgetItem("0.5000"))
        self.marks_table.setItem(row, 1, QTableWidgetItem("0.00"))

    def _del_mark(self):
        row = self.marks_table.currentRow()
        if row >= 0:
            if self.marks_table.rowCount() <= 2:
                QMessageBox.warning(self, "Warnung", "Ein Lineal benötigt mindestens 2 Kalibriermarken.")
                return
            self.marks_table.removeRow(row)

    def accept(self):
        new_name = self.name_edit.text().strip()
        if not new_name:
            QMessageBox.warning(self, "Invalid Name", "ROI name cannot be empty.")
            return

        other_names = {
            r.name.lower() for r in self.all_rois
            if getattr(r, "id", None) != getattr(self.roi, "id", None) and r is not self.roi
        }
        if new_name.lower() in other_names:
            QMessageBox.warning(
                self, "Duplicate Name",
                f"An ROI named '{new_name}' already exists. Please choose a unique name."
            )
            return

        if self.roi.shape == ROIShape.RULER and self.marks_table:
            distinct_positions = set()
            for row in range(self.marks_table.rowCount()):
                p_item = self.marks_table.item(row, 0)
                v_item = self.marks_table.item(row, 1)
                if p_item and v_item:
                    try:
                        p = float(p_item.text())
                        float(v_item.text())
                        distinct_positions.add(round(p, 5))
                    except ValueError:
                        pass
            if len(distinct_positions) < 2:
                QMessageBox.warning(
                    self, "Invalid Calibration Marks",
                    "Ruler requires at least 2 distinct calibration positions (e.g. 0.0 and 1.0)."
                )
                return

        super().accept()
        
    def get_data(self):
        self.roi.name = self.name_edit.text().strip()
        self.roi.display_type = self.type_combo.currentData()
        self.roi.unit = self.unit_edit.text()
        self.roi.value_min = self.min_edit.value()
        self.roi.value_max = self.max_edit.value()
        self.roi.max_delta_per_sec = self.delta_edit.value()
        self.roi.decimal_places = self.decimal_edit.value()
        
        if self.roi.shape == ROIShape.RULER:
            self.roi.strip_width = self.strip_width_spin.value()
            self.roi.ruler_edge = self.ruler_edge_combo.currentData()
            self.roi.suppress_scale_marks = self.suppress_scale_cb.isChecked()
            self.roi.core_width_pct = self.core_width_spin.value() / 100.0
            self.roi.min_float_height = self.min_height_spin.value()
            marks = []
            for row in range(self.marks_table.rowCount()):
                p_item = self.marks_table.item(row, 0)
                v_item = self.marks_table.item(row, 1)
                if p_item and v_item:
                    try:
                        p = float(p_item.text())
                        v = float(v_item.text())
                        marks.append({"pos": p, "value": v})
                    except ValueError:
                        pass
            self.roi.calibration_marks = sorted(marks, key=lambda m: m["pos"])
        else:
            self.roi.is_container = self.is_container_cb.isChecked()
            self.roi.sort_direction = "rtl" if self.sort_combo.currentText().startswith("Right") else "ltr"
            self.roi.decimal_position = self.decimal_pos_spin.value() if self.decimal_pos_spin.value() > 0 else None
            self.roi.allow_leading_blank = self.allow_blank_cb.isChecked()
            self.roi.allow_negative = self.allow_neg_cb.isChecked()

            if self.sub_roi_list is not None:
                selected_sub_ids = []
                for i in range(self.sub_roi_list.count()):
                    item = self.sub_roi_list.item(i)
                    if item.checkState() == Qt.Checked:
                        selected_sub_ids.append(item.text())
                self.roi.sub_roi_ids = selected_sub_ids

            if self.cal_mark_cb.isChecked():
                self.roi.analog_value = self.analog_val_spin.value()
            else:
                self.roi.analog_value = None
            
        return self.roi

