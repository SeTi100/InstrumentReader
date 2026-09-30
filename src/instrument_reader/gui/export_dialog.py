from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QSpinBox, QLineEdit, QPushButton, QDialogButtonBox, QFileDialog
import os

class ExportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Export CSV")
        layout = QVBoxLayout(self)
        
        form = QFormLayout()
        self.run_id = QSpinBox()
        self.run_id.setRange(1, 999999)
        self.file_path = QLineEdit("export.csv")
        
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self.browse)
        
        path_layout = QVBoxLayout()
        path_layout.addWidget(self.file_path)
        path_layout.addWidget(browse_btn)
        
        form.addRow("Run ID:", self.run_id)
        form.addRow("Export Path:", path_layout)
        
        layout.addLayout(form)
        
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        
    def browse(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save CSV", "", "CSV Files (*.csv)")
        if path:
            self.file_path.setText(path)
            
    def get_data(self):
        return self.run_id.value(), self.file_path.text()
