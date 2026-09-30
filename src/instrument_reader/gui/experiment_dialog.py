from PySide6.QtWidgets import QDialog, QVBoxLayout, QFormLayout, QLineEdit, QTextEdit, QDoubleSpinBox, QDialogButtonBox
from PySide6.QtCore import Qt

class ExperimentDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Experiment & Run")
        layout = QVBoxLayout(self)
        
        form = QFormLayout()
        self.exp_name = QLineEdit()
        self.voc_type = QLineEdit()
        self.exp_desc = QTextEdit()
        self.exp_desc.setMaximumHeight(60)
        
        self.target_temp = QDoubleSpinBox()
        self.target_temp.setRange(-273.15, 1000)
        self.target_temp.setValue(20.0)
        self.run_notes = QTextEdit()
        self.run_notes.setMaximumHeight(60)
        
        form.addRow("Experiment Name:", self.exp_name)
        form.addRow("VOC Type:", self.voc_type)
        form.addRow("Experiment Desc:", self.exp_desc)
        form.addRow("Target Temp:", self.target_temp)
        form.addRow("Run Notes:", self.run_notes)
        
        layout.addLayout(form)
        
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        
    def get_data(self):
        return {
            "exp_name": self.exp_name.text(),
            "voc_type": self.voc_type.text(),
            "exp_desc": self.exp_desc.toPlainText(),
            "target_temp": self.target_temp.value(),
            "run_notes": self.run_notes.toPlainText()
        }
