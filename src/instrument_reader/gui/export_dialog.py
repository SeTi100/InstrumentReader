import os
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QSpinBox, QLineEdit, QPushButton,
    QDialogButtonBox, QFileDialog, QGroupBox, QCheckBox, QScrollArea,
    QWidget, QHBoxLayout, QLabel
)


class ExportDialog(QDialog):
    def __init__(self, parent=None, db=None, current_run_id=None):
        super().__init__(parent)
        self.setWindowTitle("Export CSV")
        self.resize(450, 520)
        self.db = db
        self.channel_checkboxes: dict[str, QCheckBox] = {}

        layout = QVBoxLayout(self)

        form = QFormLayout()
        self.run_id = QSpinBox()
        self.run_id.setRange(1, 999999)
        if current_run_id:
            self.run_id.setValue(current_run_id)

        self.file_path = QLineEdit("export.csv")
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse)

        path_layout = QHBoxLayout()
        path_layout.addWidget(self.file_path)
        path_layout.addWidget(browse_btn)

        form.addRow("Run ID:", self.run_id)
        form.addRow("Export Path:", path_layout)
        layout.addLayout(form)

        # Channels selection section
        self.channel_group = QGroupBox("Channels to Export")
        chan_layout = QVBoxLayout(self.channel_group)

        self.chan_scroll = QScrollArea()
        self.chan_scroll.setWidgetResizable(True)
        self.chan_container = QWidget()
        self.chan_list_layout = QVBoxLayout(self.chan_container)
        self.chan_list_layout.setContentsMargins(4, 4, 4, 4)
        self.chan_scroll.setWidget(self.chan_container)
        chan_layout.addWidget(self.chan_scroll)

        # Buttons to quickly select/deselect
        btn_layout = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.select_none_btn = QPushButton("Deselect All")
        self.default_btn = QPushButton("Default (Raw Only)")
        btn_layout.addWidget(self.select_all_btn)
        btn_layout.addWidget(self.select_none_btn)
        btn_layout.addWidget(self.default_btn)
        chan_layout.addLayout(btn_layout)

        self.select_all_btn.clicked.connect(self.select_all)
        self.select_none_btn.clicked.connect(self.deselect_all)
        self.default_btn.clicked.connect(self.select_default)

        layout.addWidget(self.channel_group)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.run_id.valueChanged.connect(self.reload_channels)
        self.reload_channels()

    def browse(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV", "", "CSV Files (*.csv)")
        if path:
            self.file_path.setText(path)

    def reload_channels(self):
        """Clears and reloads available channels for current run_id from database."""
        # Clear existing
        while self.chan_list_layout.count():
            item = self.chan_list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.deleteLater()
        self.channel_checkboxes.clear()

        if not self.db:
            lbl = QLabel("No database connection available.")
            lbl.setStyleSheet("color: gray; font-style: italic;")
            self.chan_list_layout.addWidget(lbl)
            return

        run_id = self.run_id.value()
        channels = self.db.get_run_channels(run_id)

        if not channels:
            lbl = QLabel(f"No logged readings found for Run {run_id}.")
            lbl.setStyleSheet("color: gray; font-style: italic;")
            self.chan_list_layout.addWidget(lbl)
            return

        for roi_name, is_calc in channels:
            cb_label = f"{roi_name} (Calculated)" if is_calc else f"{roi_name} (Raw ROI)"
            cb = QCheckBox(cb_label)
            # Raw channels default to checked; calculated channels default to unchecked (opt-out)
            cb.setChecked(not is_calc)
            cb.setProperty("is_calc", is_calc)
            self.chan_list_layout.addWidget(cb)
            self.channel_checkboxes[roi_name] = cb

        self.chan_list_layout.addStretch()

    def select_all(self):
        for cb in self.channel_checkboxes.values():
            cb.setChecked(True)

    def deselect_all(self):
        for cb in self.channel_checkboxes.values():
            cb.setChecked(False)

    def select_default(self):
        for cb in self.channel_checkboxes.values():
            is_calc = cb.property("is_calc")
            cb.setChecked(not is_calc)

    def get_data(self):
        selected_channels = None
        if self.channel_checkboxes:
            selected_channels = [name for name, cb in self.channel_checkboxes.items() if cb.isChecked()]
        return self.run_id.value(), self.file_path.text(), selected_channels
