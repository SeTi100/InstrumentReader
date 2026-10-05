import csv
from datetime import datetime
from typing import List, Optional, Dict, Any, Set

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QSplitter, QTreeWidget, QTreeWidgetItem,
    QLabel, QPushButton, QTableWidget, QTableWidgetItem, QHeaderView, QGroupBox,
    QFormLayout, QMessageBox, QFileDialog, QRadioButton, QButtonGroup, QCheckBox,
    QLineEdit, QScrollArea, QWidget
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QBrush
from instrument_reader.core.database import Database


class MultiRunExportDialog(QDialog):
    """
    Dialog for configuring and executing multi-run CSV export.
    Allows format selection (Raw vs Pivot) and channel filtering.
    """

    def __init__(self, db: Database, run_ids: List[int], parent=None):
        super().__init__(parent)
        self.db = db
        self.run_ids = list(run_ids)
        self.setWindowTitle("Export Selected Runs to CSV")
        self.resize(520, 520)

        layout = QVBoxLayout(self)

        # Run summary header
        info_lbl = QLabel(f"<b>Exporting {len(self.run_ids)} selected run(s)</b> (IDs: {', '.join(map(str, self.run_ids))})")
        layout.addWidget(info_lbl)

        # Format choice group
        format_group = QGroupBox("CSV Format")
        fmt_layout = QVBoxLayout(format_group)
        self.btn_group = QButtonGroup(self)

        self.pivot_radio = QRadioButton("Pivot Format (Wide format: Timestamp, Run ID, Phase + Channels as columns)")
        self.pivot_radio.setChecked(True)
        self.raw_radio = QRadioButton("Raw Format (Long format: All readings with metadata)")

        self.btn_group.addButton(self.pivot_radio)
        self.btn_group.addButton(self.raw_radio)
        fmt_layout.addWidget(self.pivot_radio)
        fmt_layout.addWidget(self.raw_radio)
        layout.addWidget(format_group)

        # Channels selection group
        chan_group = QGroupBox("Channels to Include")
        chan_layout = QVBoxLayout(chan_group)

        chan_btn_layout = QHBoxLayout()
        self.sel_all_chan_btn = QPushButton("Select All")
        self.desel_all_chan_btn = QPushButton("Deselect All")
        chan_btn_layout.addWidget(self.sel_all_chan_btn)
        chan_btn_layout.addWidget(self.desel_all_chan_btn)
        chan_btn_layout.addStretch()
        chan_layout.addLayout(chan_btn_layout)

        # Scroll area with channel checkboxes
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        chan_container = QWidget()
        self.chan_list_layout = QVBoxLayout(chan_container)
        self.channel_checkboxes: Dict[str, QCheckBox] = {}

        channels = self.db.get_multi_run_channels(self.run_ids)
        for roi_name, is_calc in channels:
            label = f"{roi_name} (Calculated)" if is_calc else roi_name
            cb = QCheckBox(label)
            cb.setChecked(True)
            self.channel_checkboxes[roi_name] = cb
            self.chan_list_layout.addWidget(cb)

        if not channels:
            self.chan_list_layout.addWidget(QLabel("No readings/channels found in selected runs."))

        self.chan_list_layout.addStretch()
        scroll.setWidget(chan_container)
        chan_layout.addWidget(scroll)
        layout.addWidget(chan_group)

        self.sel_all_chan_btn.clicked.connect(self._select_all_channels)
        self.desel_all_chan_btn.clicked.connect(self._deselect_all_channels)

        # Output path
        path_group = QGroupBox("Destination File")
        path_layout = QHBoxLayout(path_group)
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText("Select CSV destination...")
        default_name = f"runs_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        self.path_edit.setText(default_name)
        self.browse_btn = QPushButton("Browse...")
        self.browse_btn.clicked.connect(self._browse_path)
        path_layout.addWidget(self.path_edit)
        path_layout.addWidget(self.browse_btn)
        layout.addWidget(path_group)

        # Dialog buttons
        btn_box = QHBoxLayout()
        self.export_btn = QPushButton("Export")
        self.export_btn.setStyleSheet("font-weight: bold;")
        self.export_btn.clicked.connect(self._do_export)
        self.cancel_btn = QPushButton("Cancel")
        self.cancel_btn.clicked.connect(self.reject)
        btn_box.addStretch()
        btn_box.addWidget(self.export_btn)
        btn_box.addWidget(self.cancel_btn)
        layout.addLayout(btn_box)

    def _select_all_channels(self):
        for cb in self.channel_checkboxes.values():
            cb.setChecked(True)

    def _deselect_all_channels(self):
        for cb in self.channel_checkboxes.values():
            cb.setChecked(False)

    def _browse_path(self):
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save CSV Export",
            self.path_edit.text(),
            "CSV Files (*.csv);;All Files (*)"
        )
        if path:
            self.path_edit.setText(path)

    def _do_export(self):
        file_path = self.path_edit.text().strip()
        if not file_path:
            QMessageBox.warning(self, "Invalid Path", "Please specify a destination file path.")
            return

        fmt = "pivot" if self.pivot_radio.isChecked() else "raw"
        selected_channels = [
            name for name, cb in self.channel_checkboxes.items() if cb.isChecked()
        ]

        try:
            self.db.export_multi_run_csv(
                file_path=file_path,
                run_ids=self.run_ids,
                channels=selected_channels,
                format=fmt,
            )
            QMessageBox.information(
                self,
                "Export Complete",
                f"Successfully exported {len(self.run_ids)} run(s) to:\n{file_path}"
            )
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Export Failed", f"Failed to export CSV:\n{str(e)}")


class RunsDashboardDialog(QDialog):
    """
    Professional Database Dashboard dialog for browsing experiments and runs,
    inspecting readings, toggling active run, deleting runs, and multi-run CSV export.
    """
    active_run_changed = Signal(int, int, str)  # run_id, exp_id, exp_name
    active_run_cleared = Signal()

    def __init__(self, db: Database, current_run_id: Optional[int] = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.current_run_id = current_run_id
        self._updating_checks = False

        self.setWindowTitle("Database - Runs Dashboard")
        self.resize(1100, 680)

        main_layout = QVBoxLayout(self)

        splitter = QSplitter(Qt.Horizontal)

        # Left pane: Experiments and runs tree
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        left_layout.addWidget(QLabel("<b>Experiments & Runs:</b>"))

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels([
            "ID", "Name / Notes", "VOC Type", "Parameters", "Started At", "Duration / End", "Readings"
        ])
        self.tree.header().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(1, QHeaderView.Stretch)
        self.tree.itemChanged.connect(self._on_tree_item_changed)
        self.tree.currentItemChanged.connect(self._on_tree_selection_changed)
        left_layout.addWidget(self.tree)

        # Selection buttons row
        sel_row = QHBoxLayout()
        self.select_all_btn = QPushButton("Select All")
        self.deselect_all_btn = QPushButton("Deselect All")
        self.select_all_btn.clicked.connect(self.select_all)
        self.deselect_all_btn.clicked.connect(self.deselect_all)
        sel_row.addWidget(self.select_all_btn)
        sel_row.addWidget(self.deselect_all_btn)
        sel_row.addStretch()
        left_layout.addLayout(sel_row)

        # Action buttons row
        act_row = QHBoxLayout()
        self.set_active_btn = QPushButton("Set as Active Run")
        self.set_active_btn.clicked.connect(self.set_as_active)

        self.delete_btn = QPushButton("Delete Selected")
        self.delete_btn.clicked.connect(self.delete_selected)

        self.export_btn = QPushButton("Export Selected Runs...")
        self.export_btn.clicked.connect(self.export_selected_runs)

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self.refresh_tree)

        act_row.addWidget(self.set_active_btn)
        act_row.addWidget(self.delete_btn)
        act_row.addWidget(self.export_btn)
        act_row.addWidget(self.refresh_btn)
        left_layout.addLayout(act_row)

        splitter.addWidget(left_widget)

        # Right pane: Run details and data preview
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(0, 0, 0, 0)

        # Run Details Group
        details_group = QGroupBox("Selected Run Details")
        details_form = QFormLayout(details_group)

        self.detail_exp_lbl = QLabel("-")
        self.detail_run_lbl = QLabel("-")
        self.detail_temp_lbl = QLabel("-")
        self.detail_time_lbl = QLabel("-")
        self.detail_count_lbl = QLabel("-")
        self.detail_notes_lbl = QLabel("-")
        self.detail_notes_lbl.setWordWrap(True)

        self.params_table = QTableWidget(0, 2)
        self.params_table.setHorizontalHeaderLabels(["Parameter", "Wert"])
        self.params_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.params_table.setMaximumHeight(110)
        self.params_table.setEditTriggers(QTableWidget.NoEditTriggers)

        details_form.addRow("Experiment:", self.detail_exp_lbl)
        details_form.addRow("Run ID:", self.detail_run_lbl)
        details_form.addRow("Parameters:", self.params_table)
        details_form.addRow("Target Temp:", self.detail_temp_lbl)
        details_form.addRow("Timestamps:", self.detail_time_lbl)
        details_form.addRow("Reading Count:", self.detail_count_lbl)
        details_form.addRow("Notes:", self.detail_notes_lbl)

        right_layout.addWidget(details_group)

        # Data Preview Group
        preview_group = QGroupBox("Readings Preview (Latest 100)")
        preview_layout = QVBoxLayout(preview_group)

        self.preview_table = QTableWidget(0, 5)
        self.preview_table.setHorizontalHeaderLabels([
            "Timestamp", "Phase", "ROI / Channel", "Value", "Unit"
        ])
        self.preview_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        preview_layout.addWidget(self.preview_table)
        right_layout.addWidget(preview_group)

        splitter.addWidget(right_widget)
        splitter.setSizes([650, 450])
        main_layout.addWidget(splitter)

        # Bottom Close button
        bottom_bar = QHBoxLayout()
        bottom_bar.addStretch()
        self.close_btn = QPushButton("Close")
        self.close_btn.clicked.connect(self.accept)
        bottom_bar.addWidget(self.close_btn)
        main_layout.addLayout(bottom_bar)

        self.refresh_tree()

    def _format_duration_or_end(self, run: Dict[str, Any]) -> str:
        started_at = run.get("started_at")
        ended_at = run.get("ended_at")
        first_reading = run.get("first_reading")
        last_reading = run.get("last_reading")

        if ended_at:
            dur_str = ""
            if started_at:
                try:
                    s_dt = datetime.fromisoformat(str(started_at))
                    e_dt = datetime.fromisoformat(str(ended_at))
                    secs = int((e_dt - s_dt).total_seconds())
                    if secs >= 0:
                        hours, rem = divmod(secs, 3600)
                        mins, s = divmod(rem, 60)
                        dur_str = f"{hours}h {mins}m {s}s" if hours > 0 else f"{mins}m {s}s" if mins > 0 else f"{s}s"
                except Exception:
                    pass
            return f"{dur_str} ({ended_at})" if dur_str else str(ended_at)

        if first_reading and last_reading and first_reading != last_reading:
            try:
                s_dt = datetime.fromisoformat(str(first_reading))
                e_dt = datetime.fromisoformat(str(last_reading))
                secs = int((e_dt - s_dt).total_seconds())
                if secs >= 0:
                    mins, s = divmod(secs, 60)
                    return f"{mins}m {s}s (active)"
            except Exception:
                pass

        return "-"

    def _format_parameters_summary(self, params: Dict[str, Any], target_temp: Optional[float] = None) -> str:
        if params:
            return " | ".join(f"{k}: {v}" for k, v in params.items())
        if target_temp is not None:
            return f"Temp: {target_temp:.1f} °C"
        return ""

    def refresh_tree(self):
        """Reloads experiments and runs from database and repopulates the tree."""
        self._updating_checks = True
        self.tree.clear()
        experiments = self.db.get_experiments_tree()
        selected_item = None

        for exp in experiments:
            exp_item = QTreeWidgetItem(self.tree)
            exp_item.setText(0, f"Exp {exp['id']}")
            exp_item.setText(1, exp.get("name") or "")
            exp_item.setText(2, exp.get("voc_type") or "")
            exp_item.setText(4, exp.get("created_at") or "")

            total_readings = sum(r.get("reading_count", 0) for r in exp.get("runs", []))
            exp_item.setText(6, str(total_readings))

            exp_item.setFlags(exp_item.flags() | Qt.ItemIsUserCheckable)
            exp_item.setCheckState(0, Qt.Unchecked)
            exp_item.setData(0, Qt.UserRole, {
                "type": "experiment",
                "id": exp["id"],
                "name": exp.get("name", ""),
                "data": exp,
            })

            for run in exp.get("runs", []):
                run_item = QTreeWidgetItem(exp_item)
                run_id = run["id"]
                is_active = (run_id == self.current_run_id)
                prefix = f"Run {run_id} [ACTIVE]" if is_active else f"Run {run_id}"

                run_item.setText(0, prefix)
                run_item.setText(1, run.get("notes") or "")
                params = run.get("parameters", {})
                param_summary = self._format_parameters_summary(params, run.get("target_temperature"))
                run_item.setText(3, param_summary)
                run_item.setToolTip(3, param_summary)
                run_item.setText(4, run.get("started_at") or "")
                run_item.setText(5, self._format_duration_or_end(run))
                run_item.setText(6, str(run.get("reading_count", 0)))

                if is_active:
                    run_item.setForeground(0, QBrush(QColor("#2e7d32")))
                    font = run_item.font(0)
                    font.setBold(True)
                    run_item.setFont(0, font)
                    selected_item = run_item

                run_item.setFlags(run_item.flags() | Qt.ItemIsUserCheckable)
                run_item.setCheckState(0, Qt.Unchecked)
                run_item.setData(0, Qt.UserRole, {
                    "type": "run",
                    "id": run_id,
                    "exp_id": exp["id"],
                    "exp_name": exp.get("name", ""),
                    "data": run,
                })

        self.tree.expandAll()
        self._updating_checks = False

        if not self.tree.currentItem():
            if selected_item:
                self.tree.setCurrentItem(selected_item)
            elif self.tree.topLevelItemCount() > 0:
                top = self.tree.topLevelItem(0)
                if top.childCount() > 0:
                    self.tree.setCurrentItem(top.child(0))
                else:
                    self.tree.setCurrentItem(top)

    def _on_tree_item_changed(self, item: QTreeWidgetItem, column: int):
        if column != 0 or self._updating_checks:
            return

        self._updating_checks = True
        try:
            data = item.data(0, Qt.UserRole)
            if not data:
                return

            # If an experiment item was checked/unchecked, propagate to children
            if data.get("type") == "experiment":
                state = item.checkState(0)
                for i in range(item.childCount()):
                    item.child(i).setCheckState(0, state)
            elif data.get("type") == "run":
                # Update parent check state: show PartiallyChecked if any child is checked
                parent = item.parent()
                if parent:
                    checked_count = sum(
                        1 for i in range(parent.childCount()) if parent.child(i).checkState(0) == Qt.Checked
                    )
                    if checked_count == 0:
                        parent.setCheckState(0, Qt.Unchecked)
                    else:
                        parent.setCheckState(0, Qt.PartiallyChecked)
        finally:
            self._updating_checks = False

    def _on_tree_selection_changed(self, current: Optional[QTreeWidgetItem], previous: Optional[QTreeWidgetItem]):
        if not current:
            self._clear_details()
            return

        data = current.data(0, Qt.UserRole)
        if not data:
            self._clear_details()
            return

        if data.get("type") == "run":
            run_data = data["data"]
            exp_name = data.get("exp_name", "")
            exp_id = data.get("exp_id", "")
            self.detail_exp_lbl.setText(f"{exp_name} (ID: {exp_id})")
            self.detail_run_lbl.setText(str(data["id"]))
            temp = run_data.get("target_temperature")
            self.detail_temp_lbl.setText(f"{temp:.1f} °C" if temp is not None else "-")
            start = run_data.get("started_at") or "-"
            end = run_data.get("ended_at") or "-"
            self.detail_time_lbl.setText(f"Start: {start} | End: {end}")
            self.detail_count_lbl.setText(str(run_data.get("reading_count", 0)))
            self.detail_notes_lbl.setText(run_data.get("notes") or "-")

            self._populate_params_detail(run_data)
            self._load_readings_preview(data["id"])
        elif data.get("type") == "experiment":
            exp_data = data["data"]
            self.detail_exp_lbl.setText(f"{exp_data.get('name')} (ID: {data['id']})")
            self.detail_run_lbl.setText("All Runs")
            self.detail_temp_lbl.setText("-")
            self.detail_time_lbl.setText(f"Created: {exp_data.get('created_at') or '-'}")
            total_readings = sum(r.get("reading_count", 0) for r in exp_data.get("runs", []))
            self.detail_count_lbl.setText(str(total_readings))
            self.detail_notes_lbl.setText(exp_data.get("description") or "-")
            self.params_table.setRowCount(0)
            self.preview_table.setRowCount(0)

    def _clear_details(self):
        self.detail_exp_lbl.setText("-")
        self.detail_run_lbl.setText("-")
        self.detail_temp_lbl.setText("-")
        self.detail_time_lbl.setText("-")
        self.detail_count_lbl.setText("-")
        self.detail_notes_lbl.setText("-")
        self.params_table.setRowCount(0)
        self.preview_table.setRowCount(0)

    def _populate_params_detail(self, run_data: Dict[str, Any]):
        params = run_data.get("parameters", {})
        self.params_table.setRowCount(0)
        if params:
            self.params_table.setRowCount(len(params))
            for row_idx, (k, v) in enumerate(params.items()):
                self.params_table.setItem(row_idx, 0, QTableWidgetItem(str(k)))
                self.params_table.setItem(row_idx, 1, QTableWidgetItem(str(v)))
        elif run_data.get("target_temperature") is not None:
            self.params_table.setRowCount(1)
            self.params_table.setItem(0, 0, QTableWidgetItem("Temperatur"))
            self.params_table.setItem(0, 1, QTableWidgetItem(f"{run_data['target_temperature']:.1f} °C"))

    def _load_readings_preview(self, run_id: int):
        readings = self.db.get_run_readings_preview(run_id, limit=100)
        self.preview_table.setRowCount(len(readings))
        for row, r in enumerate(readings):
            self.preview_table.setItem(row, 0, QTableWidgetItem(str(r.get("timestamp") or "")))
            self.preview_table.setItem(row, 1, QTableWidgetItem(str(r.get("phase_status") or "")))
            self.preview_table.setItem(row, 2, QTableWidgetItem(str(r.get("roi_name") or "")))
            val = r.get("parsed_value")
            if isinstance(val, float):
                val_str = f"{val:.4f}"
            elif val is not None:
                val_str = str(val)
            else:
                val_str = ""
            self.preview_table.setItem(row, 3, QTableWidgetItem(val_str))
            self.preview_table.setItem(row, 4, QTableWidgetItem(str(r.get("unit") or "")))


    def select_all(self):
        self._updating_checks = True
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            exp_item = root.child(i)
            exp_item.setCheckState(0, Qt.Checked)
            for j in range(exp_item.childCount()):
                exp_item.child(j).setCheckState(0, Qt.Checked)
        self._updating_checks = False

    def deselect_all(self):
        self._updating_checks = True
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            exp_item = root.child(i)
            exp_item.setCheckState(0, Qt.Unchecked)
            for j in range(exp_item.childCount()):
                exp_item.child(j).setCheckState(0, Qt.Unchecked)
        self._updating_checks = False

    def get_selected_run_ids(self) -> List[int]:
        selected: Set[int] = set()
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            exp_item = root.child(i)
            for j in range(exp_item.childCount()):
                run_item = exp_item.child(j)
                if run_item.checkState(0) == Qt.Checked:
                    run_data = run_item.data(0, Qt.UserRole)
                    if run_data and "id" in run_data:
                        selected.add(run_data["id"])

        # Fallback to selected tree item
        if not selected:
            curr = self.tree.currentItem()
            if curr:
                data = curr.data(0, Qt.UserRole)
                if data and data.get("type") == "run":
                    selected.add(data["id"])
                elif data and data.get("type") == "experiment":
                    for j in range(curr.childCount()):
                        r_data = curr.child(j).data(0, Qt.UserRole)
                        if r_data and "id" in r_data:
                            selected.add(r_data["id"])

        return sorted(list(selected))

    def set_as_active(self):
        curr = self.tree.currentItem()
        if not curr:
            QMessageBox.information(self, "No Selection", "Please select a run in the tree first.")
            return

        data = curr.data(0, Qt.UserRole)
        if not data or data.get("type") != "run":
            QMessageBox.information(self, "Invalid Selection", "Please select a specific run to set as active.")
            return

        run_id = data["id"]
        exp_id = data["exp_id"]
        exp_name = data["exp_name"]
        self.current_run_id = run_id
        self.active_run_changed.emit(run_id, exp_id, exp_name)
        self.refresh_tree()
        QMessageBox.information(
            self,
            "Active Run Updated",
            f"Run {run_id} under experiment '{exp_name}' (ID: {exp_id}) is now the active recording run."
        )

    def delete_selected(self):
        root = self.tree.invisibleRootItem()
        exps_to_delete: List[int] = []
        runs_to_delete: List[int] = []

        for i in range(root.childCount()):
            exp_item = root.child(i)
            exp_data = exp_item.data(0, Qt.UserRole)
            if exp_item.checkState(0) == Qt.Checked:
                if exp_data and "id" in exp_data:
                    exps_to_delete.append(exp_data["id"])
            else:
                for j in range(exp_item.childCount()):
                    run_item = exp_item.child(j)
                    if run_item.checkState(0) == Qt.Checked:
                        run_data = run_item.data(0, Qt.UserRole)
                        if run_data and "id" in run_data:
                            runs_to_delete.append(run_data["id"])

        # Fallback to current item if nothing checked
        if not exps_to_delete and not runs_to_delete:
            curr = self.tree.currentItem()
            if curr:
                data = curr.data(0, Qt.UserRole)
                if data and data.get("type") == "experiment":
                    exps_to_delete.append(data["id"])
                elif data and data.get("type") == "run":
                    runs_to_delete.append(data["id"])

        if not exps_to_delete and not runs_to_delete:
            QMessageBox.information(
                self,
                "No Selection",
                "Please select at least one experiment or run to delete."
            )
            return

        msg = (
            f"Are you sure you want to delete {len(exps_to_delete)} experiment(s) "
            f"and {len(runs_to_delete)} run(s)?\n"
            f"All associated measurements will be permanently deleted."
        )
        reply = QMessageBox.question(
            self,
            "Confirm Deletion",
            msg,
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )

        # Collect all run IDs being deleted (both explicitly and via deleted experiments)
        all_deleted_run_ids = set(runs_to_delete)
        for i in range(root.childCount()):
            exp_item = root.child(i)
            exp_data = exp_item.data(0, Qt.UserRole)
            if exp_data and exp_data.get("id") in exps_to_delete:
                for j in range(exp_item.childCount()):
                    run_data = exp_item.child(j).data(0, Qt.UserRole)
                    if run_data and "id" in run_data:
                        all_deleted_run_ids.add(run_data["id"])

        active_deleted = (self.current_run_id is not None and self.current_run_id in all_deleted_run_ids)

        if reply == QMessageBox.Yes:
            for exp_id in exps_to_delete:
                self.db.delete_experiment(exp_id)
            for run_id in runs_to_delete:
                self.db.delete_run(run_id)

            if active_deleted:
                self.current_run_id = None
                self.active_run_cleared.emit()

            self.refresh_tree()
            self._clear_details()

    def export_selected_runs(self):
        run_ids = self.get_selected_run_ids()
        if not run_ids:
            QMessageBox.information(
                self,
                "No Runs Selected",
                "Please select at least one run to export."
            )
            return

        dlg = MultiRunExportDialog(self.db, run_ids, parent=self)
        dlg.exec()
