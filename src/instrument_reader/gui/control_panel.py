from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QPushButton, QHBoxLayout, QListWidget, QLabel, 
    QSpinBox, QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox, QLineEdit
)
from PySide6.QtCore import Qt

class ControlPanel(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        
        # Camera Controls
        cam_group = QGroupBox("Camera")
        cam_layout = QVBoxLayout(cam_group)
        
        self.camera_source = QLineEdit("0")
        source_layout = QHBoxLayout()
        source_layout.addWidget(QLabel("Source:"))
        source_layout.addWidget(self.camera_source)
        cam_layout.addLayout(source_layout)
        self.start_btn = QPushButton("Start Camera")
        self.stop_btn = QPushButton("Stop Camera")
        btn_layout = QHBoxLayout()
        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)
        cam_layout.addLayout(btn_layout)
        layout.addWidget(cam_group)
        
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
        self.exp_label = QLabel("Experiment: None | Run: None")
        exp_layout.addWidget(self.exp_label)
        self.new_exp_btn = QPushButton("New Experiment/Run")
        exp_layout.addWidget(self.new_exp_btn)
        layout.addWidget(exp_group)
        
        # Readings Table
        layout.addWidget(QLabel("Live Readings:"))
        self.readings_table = QTableWidget(0, 5)
        self.readings_table.setHorizontalHeaderLabels(["ROI Name", "Value", "Unit", "Conf", "Status"])
        self.readings_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.readings_table)
        
        # ROI List
        layout.addWidget(QLabel("ROIs:"))
        self.roi_list = QListWidget()
        layout.addWidget(self.roi_list)
        
        self.delete_roi_btn = QPushButton("Delete Selected ROI")
        layout.addWidget(self.delete_roi_btn)
        
    def add_roi(self, roi):
        self.roi_list.addItem(roi.name)
        
    def update_readings(self, readings):
        self.readings_table.setRowCount(len(readings))
        for i, r in enumerate(readings):
            self.readings_table.setItem(i, 0, QTableWidgetItem(r["roi_name"]))
            val_str = f"{r['parsed_value']}" if r["parsed_value"] is not None else "-"
            self.readings_table.setItem(i, 1, QTableWidgetItem(val_str))
            self.readings_table.setItem(i, 2, QTableWidgetItem(r.get("unit", "")))
            self.readings_table.setItem(i, 3, QTableWidgetItem(f"{r['confidence']:.2f}"))
            
            status = "✓" if r["is_valid"] else f"⚠ {r.get('reason', '')}"
            if r.get("used_fallback"):
                status += " (OCCLUDED)"
            self.readings_table.setItem(i, 4, QTableWidgetItem(status))

    def remove_roi_reading(self, roi_name: str, roi_id: str = None):
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
