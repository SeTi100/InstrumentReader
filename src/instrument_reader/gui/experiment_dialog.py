from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QLineEdit, QTextEdit,
    QTableWidget, QTableWidgetItem, QHeaderView, QPushButton,
    QDialogButtonBox, QLabel, QComboBox, QWidget
)
from PySide6.QtCore import Qt
from typing import Optional, Dict, Any
import re


def extract_temperature(params: Optional[Dict[str, Any]]) -> Optional[float]:
    """Extract numeric temperature from parameters dict, supporting both dot and comma decimals."""
    if not isinstance(params, dict):
        return None
    for k, v in params.items():
        k_clean = str(k).strip().lower()
        if k_clean in ("temperatur", "temperature", "target temp", "target_temp", "temp", "target temperature"):
            if isinstance(v, (int, float)):
                return float(v)
            if isinstance(v, str):
                v_clean = v.replace(",", ".")
                m = re.search(r"[-+]?(?:\d+\.?\d*|\.\d+)", v_clean)
                if m:
                    try:
                        return float(m.group(0))
                    except ValueError:
                        pass
    return None


class ParametersTableWidget(QWidget):
    """Dynamic key-value table for experiment/run parameters."""

    SUGGESTIONS = [
        "Konzentration",
        "Temperatur",
        "Volumenstrom",
        "Druck",
        "GHSV",
        "Katalysator",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Parameter", "Wert"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

        btn_layout = QHBoxLayout()
        self.add_btn = QPushButton("+ Add Parameter")
        self.remove_btn = QPushButton("- Remove")
        self.add_btn.clicked.connect(lambda: self.add_parameter())
        self.remove_btn.clicked.connect(self.remove_parameter)
        btn_layout.addWidget(self.add_btn)
        btn_layout.addWidget(self.remove_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

    def add_parameter(self, name: Any = "", value: Any = ""):
        row = self.table.rowCount()
        self.table.insertRow(row)

        combo = QComboBox()
        combo.setEditable(True)
        combo.addItems(self.SUGGESTIONS)
        name_str = "" if (isinstance(name, bool) or name is None) else str(name)
        combo.setEditText(name_str)
        self.table.setCellWidget(row, 0, combo)

        val_str = "" if (isinstance(value, bool) or value is None) else str(value)
        val_item = QTableWidgetItem(val_str)
        self.table.setItem(row, 1, val_item)
        self.table.setCurrentCell(row, 0)

    add_row = add_parameter

    def remove_parameter(self):
        current_row = self.table.currentRow()
        if current_row >= 0:
            self.table.removeRow(current_row)
        elif self.table.rowCount() > 0:
            self.table.removeRow(self.table.rowCount() - 1)

    remove_selected_row = remove_parameter

    def get_parameters(self) -> Dict[str, str]:
        params = {}
        for r in range(self.table.rowCount()):
            k = ""
            w0 = self.table.cellWidget(r, 0)
            if isinstance(w0, QComboBox):
                k = w0.currentText().strip()
            elif isinstance(w0, QLineEdit):
                k = w0.text().strip()
            else:
                item0 = self.table.item(r, 0)
                if item0:
                    k = item0.text().strip()

            v = ""
            w1 = self.table.cellWidget(r, 1)
            if isinstance(w1, QLineEdit):
                v = w1.text().strip()
            elif isinstance(w1, QComboBox):
                v = w1.currentText().strip()
            else:
                item1 = self.table.item(r, 1)
                if item1:
                    v = item1.text().strip()

            if k:
                params[k] = v
        return params

    def set_parameters(self, params: Optional[Dict[str, Any]]):
        self.table.setRowCount(0)
        if not params:
            return
        for k, v in params.items():
            self.add_parameter(k, v if v is not None else "")


class ExperimentDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create Experiment & Run")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        
        form = QFormLayout()
        self.exp_name = QLineEdit()
        self.voc_type = QLineEdit()
        self.exp_desc = QTextEdit()
        self.exp_desc.setMaximumHeight(60)
        
        self.params_widget = ParametersTableWidget()
        self.params_widget.set_parameters({"Temperatur": "20 °C"})
        
        self.run_notes = QTextEdit()
        self.run_notes.setMaximumHeight(60)
        
        form.addRow("Experiment Name:", self.exp_name)
        form.addRow("VOC Type:", self.voc_type)
        form.addRow("Experiment Desc:", self.exp_desc)
        form.addRow("Run 1 Parameters:", self.params_widget)
        form.addRow("Run Notes:", self.run_notes)
        
        layout.addLayout(form)
        
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        
    def get_data(self):
        params = self.params_widget.get_parameters()
        target_temp = extract_temperature(params)
        if target_temp is None:
            target_temp = 20.0

        return {
            "exp_name": self.exp_name.text(),
            "voc_type": self.voc_type.text(),
            "exp_desc": self.exp_desc.toPlainText(),
            "parameters": params,
            "target_temp": target_temp,
            "run_notes": self.run_notes.toPlainText()
        }


class NewRunDialog(QDialog):
    """Dialog to create a new run under the active experiment."""

    def __init__(
        self,
        exp_name: Any = "",
        exp_id: Optional[int] = None,
        initial_parameters: Optional[Dict[str, Any]] = None,
        parent=None,
        **kwargs
    ):
        if isinstance(exp_name, QWidget) and parent is None:
            parent = exp_name
            exp_name = ""
        elif isinstance(initial_parameters, QWidget) and parent is None:
            parent = initial_parameters
            initial_parameters = kwargs.get("initial_parameters", None)

        super().__init__(parent)
        self.setWindowTitle("New Run")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)

        form = QFormLayout()
        if exp_name or exp_id is not None:
            exp_info = f"{exp_name} (ID: {exp_id})" if exp_name and exp_id else str(exp_name or exp_id)
            exp_info_lbl = QLabel(exp_info)
            exp_info_lbl.setStyleSheet("font-weight: bold;")
            form.addRow("Experiment:", exp_info_lbl)

        self.params_widget = ParametersTableWidget()
        if initial_parameters is not None:
            self.params_widget.set_parameters(initial_parameters)
        else:
            self.params_widget.set_parameters({"Temperatur": "20 °C"})

        self.run_notes = QTextEdit()
        self.run_notes.setMaximumHeight(80)
        self.run_notes.setPlaceholderText("Notes, gas concentration, parameters...")

        form.addRow("Parameters:", self.params_widget)
        form.addRow("Run Notes:", self.run_notes)
        layout.addLayout(form)

        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

    def get_data(self):
        params = self.params_widget.get_parameters()
        target_temp = extract_temperature(params)

        return {
            "parameters": params,
            "target_temp": target_temp,
            "run_notes": self.run_notes.toPlainText().strip()
        }
