import json
import uuid
from typing import Optional, Dict, Any
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QSplitter, QMenuBar, QMenu, QFileDialog, QMessageBox, QInputDialog
)
from PySide6.QtCore import Qt, QTimer
from instrument_reader.core.camera import OpenCVCamera
from instrument_reader.core.recorder import VideoRecorder
from instrument_reader.core.roi import ROIShape, ROIConfig, DisplayType
from instrument_reader.core.preprocessing import PreprocessingConfig
from instrument_reader.core.calculation import CalculationEngine, CalculationChannel
from instrument_reader.gui.video_widget import VideoWidget
from instrument_reader.gui.control_panel import ControlPanel, CalculationChannelDialog
from instrument_reader.gui.ocr_worker import OCRWorker
from instrument_reader.gui.db_writer import DatabaseWriter
from instrument_reader.gui.experiment_dialog import ExperimentDialog, NewRunDialog
from instrument_reader.gui.export_dialog import ExportDialog
from instrument_reader.gui.runs_dashboard import RunsDashboardDialog
from instrument_reader.gui.roi_config_dialog import ROIConfigDialog
from instrument_reader.gui.preprocessing_dialog import PreprocessingDialog
from instrument_reader.gui.camera_thread import CameraThread
from instrument_reader.core.camera_devices import label_name, parse_source

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Instrument Reader - Phase 1.1")
        self.setMinimumSize(1200, 800)
        
        self.db_writer = DatabaseWriter()
        self.ocr_worker = OCRWorker()
        self.calc_engine = CalculationEngine()
        self.recorder = VideoRecorder(output_dir="recordings")
        self.db_writer.set_phase(f"STAGE_{self.calc_engine.step_detector.stage}")
        self._roi_counter = 1
        self.current_experiment_id = None
        self.current_experiment_name = ""
        self.last_run_parameters = {}
        
        self.setup_ui()
        
        self.camera = None
        self.camera_thread = None
        # Name of the live camera in use, so a reconnect finds it again even if the
        # OS renumbers devices (and never silently switches to another camera).
        self._camera_name = None
        self._device_watch = QTimer(self)
        self._device_watch.setInterval(500)
        self._device_watch.timeout.connect(self.control_panel.refresh_cameras)
        
        self.setup_connections()
        self.setup_menus()
        
    def setup_ui(self):
        central = QWidget()
        main_layout = QVBoxLayout(central)
        
        top_splitter = QSplitter(Qt.Horizontal)
        self.video_widget = VideoWidget()
        self.control_panel = ControlPanel()
        
        top_splitter.addWidget(self.video_widget)
        top_splitter.addWidget(self.control_panel)
        top_splitter.setSizes([800, 400])
        
        main_layout.addWidget(top_splitter)
        self.setCentralWidget(central)
        
    def setup_connections(self):
        def start_camera():
            src = self.control_panel.selected_source()

            if self.camera_thread and self.camera_thread.running:
                if self.camera and getattr(self.camera, "_source", None) == src:
                    if self.camera_thread.paused:
                        if getattr(self.camera, "is_eof", lambda: False)():
                            self.calc_engine.history.clear()
                            self.calc_engine.reset_scale_detector(hard=False)
                            self.control_panel.set_stage_status(1, "Stationär", is_transition=False)
                            self.db_writer.set_phase("STAGE_1")
                            self.camera_thread.seek_to_frame(0)
                        self.camera_thread.resume()
                    return
                else:
                    self.camera_thread.stop()
                    self.camera_thread = None
                    self.camera = None

            if self.camera:
                self.camera.release()
                self.camera = None

            self.camera = OpenCVCamera(src)
            self.camera_thread = CameraThread(self.camera)
            self.camera_thread.frame_ready.connect(self.video_widget.update_frame)
            # Direct connection: OCRWorker.update_frame is mutex-protected and must see
            # every frame (with its timestamp) even when the GUI thread is busy.
            self.camera_thread.frame_captured.connect(self.ocr_worker.update_frame, Qt.DirectConnection)
            self.camera_thread.backpressure = self.ocr_worker.wait_while_pending
            self.camera_thread.set_playback_speed(self.control_panel.playback_speed())
            self.camera_thread.frame_ready.connect(self.recorder.write_frame)
            self.camera_thread.status_changed.connect(self.on_camera_status)
            self._camera_name = None
            if isinstance(src, int):
                combo = self.control_panel.camera_combo
                labels = [self.control_panel.camera_source.text()]
                labels += [combo.itemText(i) for i in range(combo.count()) if parse_source(combo.itemText(i)) == src]
                self._camera_name = next((n for n in map(label_name, labels) if n), None)
                self._device_watch.start()
            self.camera_thread.start()
            self.ocr_worker.start()

        def stop_camera():
            if self.camera_thread and self.camera_thread.running:
                if self.camera and getattr(self.camera, "is_video_file", False):
                    if not self.camera_thread.paused:
                        self.camera_thread.pause()
                        return
                self._device_watch.stop()
                self.camera_thread.stop()
                self.camera_thread = None
                self.camera = None
                self.control_panel.set_camera_status("stopped", "")
            self.ocr_worker.stop()
            if self.recorder.is_recording:
                self.recorder.stop_recording()
                self.control_panel.set_recording(False)

        self.control_panel.cameras_changed.connect(self.on_cameras_changed)
        self.control_panel.start_btn.clicked.connect(start_camera)
        self.control_panel.stop_btn.clicked.connect(stop_camera)
        self.control_panel.fwd_btn.clicked.connect(lambda: self.seek_video(5.0))
        self.control_panel.back_btn.clicked.connect(lambda: self.seek_video(-5.0))
        self.control_panel.record_btn.clicked.connect(self.toggle_recording)
        
        self.video_widget.roi_created.connect(self.on_roi_created)
        
        self.control_panel.rect_btn.clicked.connect(lambda: self.set_draw_mode(ROIShape.RECTANGLE))
        self.control_panel.poly_btn.clicked.connect(lambda: self.set_draw_mode(ROIShape.POLYGON))
        self.control_panel.ruler_btn.clicked.connect(lambda: self.set_draw_mode(ROIShape.RULER))
        
        self.control_panel.interval_spin.valueChanged.connect(self.ocr_worker.set_interval)
        self.control_panel.speed_combo.currentIndexChanged.connect(self._on_playback_speed_changed)
        self.control_panel.new_exp_btn.clicked.connect(self.create_experiment)
        self.control_panel.new_run_btn.clicked.connect(self.create_new_run)
        self.control_panel.dashboard_btn.clicked.connect(self.open_runs_dashboard)
        self.control_panel.delete_roi_btn.clicked.connect(self.delete_roi)
        
        self.ocr_worker.readings_ready.connect(self.on_readings_ready)

        self.control_panel.add_calc_btn.clicked.connect(self.add_calculated_channel)
        self.control_panel.edit_calc_btn.clicked.connect(self.edit_calculated_channel)
        self.control_panel.delete_calc_btn.clicked.connect(self.delete_calculated_channel)
        self.control_panel.calc_table.itemDoubleClicked.connect(lambda item: self.edit_calculated_channel())

        self.control_panel.roi_list.itemDoubleClicked.connect(self.on_roi_double_clicked)
        self.control_panel.roi_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.control_panel.roi_list.customContextMenuRequested.connect(self.on_roi_context_menu)
        self.control_panel.stage_reset_requested.connect(self.on_reset_stage)

    def on_reset_stage(self):
        self.calc_engine.reset_scale_detector(hard=False)
        self.control_panel.set_stage_status(1, "Stationär", is_transition=False)
        self.db_writer.set_phase("STAGE_1")

    def _on_playback_speed_changed(self, _index=None):
        if self.camera_thread:
            self.camera_thread.set_playback_speed(self.control_panel.playback_speed())

    def seek_video(self, seconds: float):
        # Clear calculation engine history buffer and reset scale step detector
        self.calc_engine.history.clear()
        self.calc_engine.reset_scale_detector(hard=False)
        self.control_panel.set_stage_status(1, "Stationär", is_transition=False)
        self.db_writer.set_phase("STAGE_1")

        if self.camera_thread and self.camera_thread.running:
            self.camera_thread.seek(seconds)
        else:
            src_str = self.control_panel.camera_source.text().strip()
            if src_str and self.control_panel.is_video_source():
                if self.camera is None or not getattr(self.camera, "is_opened", False):
                    if self.camera:
                        self.camera.release()
                    self.camera = OpenCVCamera(src_str)
                    if not self.camera.open():
                        return
                self.camera.seek(seconds)
                ret, frame = self.camera.read()
                if ret and frame is not None:
                    self.video_widget.update_frame(frame)

    def on_cameras_changed(self, devices):
        """Keeps the live camera thread pointed at the selected camera's current index."""
        thread = self.camera_thread
        if thread is None or not thread.running or self._camera_name is None or devices is None:
            return
        matches = [d.index for d in devices if d.name == self._camera_name]
        if not matches:
            thread.set_device(None)
        else:
            current = getattr(self.camera, "source", None)
            thread.set_device(current if current in matches else matches[0])

    def on_camera_status(self, state: str, message: str):
        self.control_panel.set_camera_status(state, message)
        if state in ("connecting", "reconnecting"):
            # Without a live picture the OCR must not keep logging the last frame as current.
            self.ocr_worker.update_frame(None)

    def toggle_recording(self):
        if not self.recorder.is_recording:
            fps = self.camera.fps if self.camera else 30.0
            self.recorder.start_recording(fps=fps)
            self.control_panel.set_recording(True)
        else:
            self.recorder.stop_recording()
            self.control_panel.set_recording(False)

    def setup_menus(self):
        menu = self.menuBar()
        file_menu = menu.addMenu("File")
        
        new_exp = file_menu.addAction("New Experiment")
        new_exp.triggered.connect(self.create_experiment)
        
        export_csv = file_menu.addAction("Export CSV")
        export_csv.triggered.connect(self.export_csv)
        
        file_menu.addSeparator()
        
        save_roi = file_menu.addAction("Save ROI Preset")
        save_roi.triggered.connect(self.save_roi_preset)
        
        load_roi = file_menu.addAction("Load ROI Preset")
        load_roi.triggered.connect(self.load_roi_preset)
        
        file_menu.addSeparator()
        quit_act = file_menu.addAction("Quit")
        quit_act.triggered.connect(self.close)

        db_menu = menu.addMenu("Database")
        dashboard_act = db_menu.addAction("Runs Dashboard...")
        dashboard_act.triggered.connect(self.open_runs_dashboard)
        
    def set_draw_mode(self, mode):
        self.control_panel.rect_btn.setChecked(mode == ROIShape.RECTANGLE)
        self.control_panel.poly_btn.setChecked(mode == ROIShape.POLYGON)
        self.control_panel.ruler_btn.setChecked(mode == ROIShape.RULER)
        self.video_widget.set_drawing_mode(mode)
        
    def on_roi_created(self, roi):
        existing_names = {r.name.lower() for r in self.video_widget.rois if r is not roi and r.name}
        
        while f"roi_{self._roi_counter}".lower() in existing_names:
            self._roi_counter += 1
        default_name = roi.name if (roi.name and roi.name.lower() not in existing_names) else f"ROI_{self._roi_counter}"
        
        while True:
            name, ok = QInputDialog.getText(self, "ROI Name", "Enter unique ROI name:", text=default_name)
            if not ok:
                # User cancelled creation
                if roi in self.video_widget.rois:
                    self.video_widget.rois.remove(roi)
                self.video_widget._update_display()
                return
                
            name = name.strip()
            if not name:
                QMessageBox.warning(self, "Invalid Name", "ROI name cannot be empty. Please enter a valid name.")
                continue
                
            if name.lower() in existing_names:
                QMessageBox.warning(
                    self, "Duplicate Name",
                    f"An ROI named '{name}' already exists. Please choose a unique name."
                )
                continue
                
            roi.name = name
            if roi.name.startswith("ROI_"):
                try:
                    num = int(roi.name[4:])
                    if num >= self._roi_counter:
                        self._roi_counter = num + 1
                except ValueError:
                    self._roi_counter += 1
            else:
                self._roi_counter += 1
            self.video_widget._roi_counter = max(self._roi_counter, self.video_widget._roi_counter)
            break

        self.control_panel.add_roi(roi)
        self.ocr_worker.update_rois(self.video_widget.rois)
        self.video_widget._update_display()

    def on_roi_double_clicked(self, item):
        row = self.control_panel.roi_list.row(item)
        roi = self.video_widget.rois[row]
        old_name = roi.name
        dlg = ROIConfigDialog(roi, all_rois=self.video_widget.rois, parent=self)
        if dlg.exec():
            updated_roi = dlg.get_data()
            self.video_widget.rois[row] = updated_roi
            item.setText(updated_roi.name)

            if updated_roi.name != old_name:
                # Update references in container ROIs
                for r in self.video_widget.rois:
                    sub_ids = getattr(r, "sub_roi_ids", [])
                    for idx, sid in enumerate(sub_ids):
                        if sid == old_name:
                            sub_ids[idx] = updated_roi.name

                # Update live readings caches and table
                for r in self.video_widget.latest_readings:
                    if r.get("roi_id") == getattr(updated_roi, "id", None) or r.get("roi_name") == old_name:
                        r["roi_name"] = updated_roi.name
                self.control_panel.update_roi_name(old_name, updated_roi.name)

                # Keep monotonic counter ahead
                if updated_roi.name.startswith("ROI_"):
                    try:
                        num = int(updated_roi.name[4:])
                        if num >= self._roi_counter:
                            self._roi_counter = num + 1
                            self.video_widget._roi_counter = self._roi_counter
                    except ValueError:
                        pass

                # Synchronize CalculationEngine history and channel variable references
                if old_name in self.calc_engine.history:
                    self.calc_engine.history[updated_roi.name] = self.calc_engine.history.pop(old_name)

                if self.calc_engine.scale_roi_name == old_name:
                    self.calc_engine.scale_roi_name = updated_roi.name

                for ch in self.calc_engine.channels.values():
                    for var_k, target_roi in list(ch.variables.items()):
                        if target_roi == old_name:
                            ch.variables[var_k] = updated_roi.name
                    if f"{{{old_name}}}" in ch.formula:
                        ch.formula = ch.formula.replace(f"{{{old_name}}}", f"{{{updated_roi.name}}}")

            self.ocr_worker.update_rois(self.video_widget.rois)
            self.video_widget._update_display()
            
    def on_roi_context_menu(self, pos):
        item = self.control_panel.roi_list.itemAt(pos)
        if not item: return
        row = self.control_panel.roi_list.row(item)
        roi = self.video_widget.rois[row]
        
        menu = QMenu()
        config_pre = menu.addAction("Configure Preprocessing")
        action = menu.exec(self.control_panel.roi_list.viewport().mapToGlobal(pos))
        
        if action == config_pre:
            if self.video_widget.current_frame is not None:
                frame = self.video_widget.current_frame
                # Get crop
                if roi.shape == ROIShape.RECTANGLE:
                    x, y, w, h = roi.coordinates
                    h_img, w_img = frame.shape[:2]
                    x1, y1 = max(0, x), max(0, y)
                    x2, y2 = min(w_img, x+w), min(h_img, y+h)
                    crop = frame[y1:y2, x1:x2]
                elif roi.shape == ROIShape.RULER:
                    from instrument_reader.core.analog_reader import RotameterRulerReader
                    endpoints = RotameterRulerReader.get_endpoints_from_roi(roi)
                    if not endpoints:
                        return
                    p1, p2 = endpoints
                    sw = getattr(roi, "strip_width", 30.0)
                    marks = getattr(roi, "calibration_marks", [])
                    is_p2_top = RotameterRulerReader.is_p2_top(p1, p2, marks)
                    p_top, p_bottom = (p2, p1) if is_p2_top else (p1, p2)
                    crop = RotameterRulerReader.extract_rectified_strip(frame, p_top, p_bottom, sw)
                else:
                    import cv2, numpy as np
                    pts = np.array(roi.coordinates, np.int32)
                    x, y, w, h = cv2.boundingRect(pts)
                    h_img, w_img = frame.shape[:2]
                    x1, y1 = max(0, x), max(0, y)
                    x2, y2 = min(w_img, x+w), min(h_img, y+h)
                    crop = frame[y1:y2, x1:x2]
                
                if crop is None or crop.size == 0:
                    return

                config = PreprocessingConfig.from_dict(roi.preprocessing_params)
                config.ruler_edge = getattr(roi, "ruler_edge", "top")
                config.suppress_scale_marks = getattr(roi, "suppress_scale_marks", True)
                config.core_width_pct = getattr(roi, "core_width_pct", 0.6)
                config.min_float_height = getattr(roi, "min_float_height", 8)
                dlg = PreprocessingDialog(crop, config, self)
                if dlg.exec():
                    roi.preprocessing_params = dlg.get_config().__dict__
                    if hasattr(dlg.get_config(), "ruler_edge"):
                        roi.ruler_edge = dlg.get_config().ruler_edge
                    if hasattr(dlg.get_config(), "suppress_scale_marks"):
                        roi.suppress_scale_marks = dlg.get_config().suppress_scale_marks
                    if hasattr(dlg.get_config(), "core_width_pct"):
                        roi.core_width_pct = dlg.get_config().core_width_pct
                    if hasattr(dlg.get_config(), "min_float_height"):
                        roi.min_float_height = dlg.get_config().min_float_height
                    self.ocr_worker.update_rois(self.video_widget.rois)
                    
    def delete_roi(self):
        row = self.control_panel.roi_list.currentRow()
        if row >= 0:
            deleted_roi = self.video_widget.rois[row]
            del_id = getattr(deleted_roi, "id", None)
            del_name = deleted_roi.name

            self.control_panel.roi_list.takeItem(row)
            self.video_widget.rois.pop(row)

            # Clean up references in other container ROIs
            for r in self.video_widget.rois:
                sub_ids = getattr(r, "sub_roi_ids", [])
                if del_name in sub_ids:
                    sub_ids.remove(del_name)
                if del_id in sub_ids:
                    sub_ids.remove(del_id)

            # Clean up readings in video widget and control panel immediately
            self.video_widget.latest_readings = [
                r for r in self.video_widget.latest_readings
                if r.get("roi_id") != del_id and r.get("roi_name") != del_name
            ]
            self.control_panel.remove_roi_reading(del_name, del_id)
            self.calc_engine.history.pop(del_name, None)
            if self.calc_engine.scale_roi_name == del_name:
                self.calc_engine.scale_roi_name = None
                self.calc_engine.reset_scale_detector(hard=True)
                self.control_panel.set_stage_status(1, "Stationär", is_transition=False)
                self.db_writer.set_phase("STAGE_1")

            self.ocr_worker.update_rois(self.video_widget.rois)
            self.video_widget._update_display()
            
    def create_experiment(self):
        dlg = ExperimentDialog(self)
        if dlg.exec():
            data = dlg.get_data()
            exp_id = self.db_writer.create_experiment(data["exp_name"], data["voc_type"], data["exp_desc"])
            params = data.get("parameters")
            if not params and data.get("target_temp") is not None:
                params = {"Temperatur": f"{data['target_temp']} °C"}
            elif not params:
                params = {}
            self.last_run_parameters = dict(params)

            run_id = self.db_writer.create_run(
                exp_id,
                target_temp=data.get("target_temp"),
                notes=data.get("run_notes", ""),
                parameters=params,
            )
            self.current_experiment_id = exp_id
            self.current_experiment_name = data["exp_name"]
            self.control_panel.exp_label.setText(f"Experiment: {data['exp_name']} (ID: {exp_id}) | Run: {run_id}")
            self.calc_engine.reset_scale_detector(hard=True)
            self.control_panel.set_stage_status(1, "Stationär", is_transition=False)
            self.db_writer.set_phase("STAGE_1")

    def create_new_run(self):
        if not self.current_experiment_id:
            QMessageBox.information(
                self,
                "No Active Experiment",
                "Please create an experiment first before starting a new run."
            )
            return

        dlg = NewRunDialog(
            exp_name=self.current_experiment_name,
            exp_id=self.current_experiment_id,
            initial_parameters=self.last_run_parameters,
            parent=self,
        )
        if dlg.exec():
            data = dlg.get_data()
            params = data.get("parameters")
            if not params and data.get("target_temp") is not None:
                params = {"Temperatur": f"{data['target_temp']} °C"}
            elif not params:
                params = {}
            self.last_run_parameters = dict(params)

            run_id = self.db_writer.create_run(
                self.current_experiment_id,
                target_temp=data.get("target_temp"),
                notes=data.get("run_notes", ""),
                parameters=params,
            )
            self.control_panel.exp_label.setText(
                f"Experiment: {self.current_experiment_name} (ID: {self.current_experiment_id}) | Run: {run_id}"
            )
            self.calc_engine.reset_scale_detector(hard=True)
            self.control_panel.set_stage_status(1, "Stationär", is_transition=False)
            self.db_writer.set_phase("STAGE_1")

    def open_runs_dashboard(self):
        dlg = RunsDashboardDialog(
            self.db_writer.db,
            current_run_id=self.db_writer.current_run_id,
            parent=self,
        )
        dlg.active_run_changed.connect(self.set_active_run)
        dlg.active_run_cleared.connect(self.clear_active_run)
        dlg.exec()

    def set_active_run(self, run_id: int, exp_id: int, exp_name: str, parameters: Optional[dict] = None):
        self.db_writer.set_active_run(run_id)
        self.current_experiment_id = exp_id
        self.current_experiment_name = exp_name
        if parameters is not None:
            self.last_run_parameters = dict(parameters)
        else:
            try:
                tree = self.db_writer.db.get_experiments_tree()
                for exp in tree:
                    for r in exp.get("runs", []):
                        if r["id"] == run_id:
                            self.last_run_parameters = dict(r.get("parameters", {}))
                            break
            except Exception:
                pass
        self.control_panel.exp_label.setText(f"Experiment: {exp_name} (ID: {exp_id}) | Run: {run_id}")
        self.calc_engine.reset_scale_detector(hard=True)
        self.control_panel.set_stage_status(1, "Stationär", is_transition=False)
        self.db_writer.set_phase("STAGE_1")

    def clear_active_run(self):
        self.db_writer.current_run_id = None
        self.current_experiment_id = None
        self.current_experiment_name = ""
        self.last_run_parameters = {}
        self.control_panel.exp_label.setText("Experiment: None | Run: None")
        self.control_panel.set_stage_status(1, "Inaktiv", inactive=True)
            
    def on_readings_ready(self, readings, timestamp=None):
        self.control_panel.update_readings(readings)
        self.video_widget.update_readings(readings)

        # Check if readings have timestamp or use parameter
        ts = timestamp
        if ts is None and readings and "timestamp" in readings[0] and isinstance(readings[0]["timestamp"], (int, float)):
            ts = float(readings[0]["timestamp"])

        # Feed valid readings into calculation history buffer
        self.calc_engine.update_readings(readings, timestamp=ts)

        # Build map of current values from valid readings
        current_vals = {}
        for r in readings:
            if r.get("roi_name") and r.get("parsed_value") is not None and r.get("is_valid", True):
                current_vals[r["roi_name"]] = r["parsed_value"]

        # Evaluate all calculated channels
        calc_results = self.calc_engine.calculate_all(current_vals, current_time=ts)
        self.control_panel.update_calculated_readings(calc_results)

        # Update stage badge and db_writer phase status from step detector
        detector = self.calc_engine.step_detector
        if detector.has_readings:
            if detector.is_transition:
                phase = "STAGE_TRANSITION"
                self.control_panel.set_stage_status(
                    detector.stage,
                    detector.status_text,
                    is_transition=True,
                    target_stage=detector.transient_target_stage,
                )
            else:
                phase = f"STAGE_{detector.stage}"
                self.control_panel.set_stage_status(
                    detector.stage,
                    detector.status_text,
                    is_transition=False,
                )
            self.db_writer.set_phase(phase)

        # Prepare combined readings for database logging
        all_readings = list(readings)
        for res in calc_results:
            all_readings.append({
                "roi_id": res.channel_id,
                "roi_name": res.name,
                "raw_text": res.formula,
                "parsed_value": res.value,
                "unit": res.unit,
                "confidence": 1.0,
                "is_valid": res.is_valid,
                "reason": res.error_message or "",
                "used_fallback": False,
                "is_child": False,
                "is_calculated": 1,
            })

        self.db_writer.insert_readings(all_readings, timestamp=ts)

    def _get_current_evaluation_values(self):
        current_vals = {
            r["roi_name"]: r["parsed_value"]
            for r in self.video_widget.latest_readings
            if r.get("parsed_value") is not None and r.get("is_valid", True)
        }
        for res in self.calc_engine.latest_results.values():
            if res.is_valid and res.value is not None:
                current_vals[res.name] = res.value

        detector = self.calc_engine.step_detector
        if detector.has_readings:
            current_vals["Waage_korrigiert"] = detector.corrected_mass
            current_vals["VOC_verdampft"] = detector.evaporated_mass
            current_vals["Stufe"] = float(detector.stage)
            current_vals["Stufen_Status"] = detector.status_text

        return current_vals

    def add_calculated_channel(self):
        rois = [r.name for r in self.video_widget.rois if r.name]
        for vr in ("Waage_korrigiert", "VOC_verdampft", "Stufe"):
            if vr not in rois:
                rois.append(vr)
        current_vals = self._get_current_evaluation_values()
        dlg = CalculationChannelDialog(
            channel=None,
            available_rois=rois,
            current_readings=current_vals,
            calc_engine=self.calc_engine,
            parent=self,
        )
        if dlg.exec():
            ch = dlg.get_channel_data()
            self.calc_engine.add_channel(ch)
            self.control_panel.set_calculated_channels(list(self.calc_engine.channels.values()))
            calc_results = self.calc_engine.calculate_all(current_vals)
            self.control_panel.update_calculated_readings(calc_results)

    def edit_calculated_channel(self):
        row = self.control_panel.calc_table.currentRow()
        if row < 0:
            return

        item = self.control_panel.calc_table.item(row, 0)
        ch_id = item.data(Qt.UserRole) if item else None
        ch = self.calc_engine.get_channel(ch_id) if ch_id else None
        if ch is None:
            channels = list(self.calc_engine.channels.values())
            if row < len(channels):
                ch = channels[row]

        if ch:
            rois = [r.name for r in self.video_widget.rois if r.name]
            for vr in ("Waage_korrigiert", "VOC_verdampft", "Stufe"):
                if vr not in rois:
                    rois.append(vr)
            current_vals = self._get_current_evaluation_values()
            dlg = CalculationChannelDialog(
                channel=ch,
                available_rois=rois,
                current_readings=current_vals,
                calc_engine=self.calc_engine,
                parent=self,
            )
            if dlg.exec():
                updated_ch = dlg.get_channel_data()
                self.calc_engine.add_channel(updated_ch)
                self.control_panel.set_calculated_channels(list(self.calc_engine.channels.values()))
                calc_results = self.calc_engine.calculate_all(current_vals)
                self.control_panel.update_calculated_readings(calc_results)

    def delete_calculated_channel(self):
        row = self.control_panel.calc_table.currentRow()
        if row < 0:
            return

        item = self.control_panel.calc_table.item(row, 0)
        ch_id = item.data(Qt.UserRole) if item else None
        ch = self.calc_engine.get_channel(ch_id) if ch_id else None
        if ch is None:
            channels = list(self.calc_engine.channels.values())
            if row < len(channels):
                ch = channels[row]

        if ch:
            self.calc_engine.remove_channel(ch.id)
            self.control_panel.set_calculated_channels(list(self.calc_engine.channels.values()))
            current_vals = self._get_current_evaluation_values()
            calc_results = self.calc_engine.calculate_all(current_vals)
            self.control_panel.update_calculated_readings(calc_results)

    def export_csv(self):
        dlg = ExportDialog(self, db=self.db_writer.db, current_run_id=self.db_writer.current_run_id)
        if dlg.exec():
            data = dlg.get_data()
            run_id = data[0]
            path = data[1]
            channels = data[2] if len(data) > 2 else None
            try:
                self.db_writer.db.export_csv(path, run_id, channels)
                QMessageBox.information(self, "Success", f"Exported to {path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
                
    def save_roi_preset(self):
        if not self.video_widget.rois and not self.calc_engine.channels:
            return
        
        name, ok = QInputDialog.getText(self, "Save Preset", "Enter preset name:")
        if not ok or not name:
            return
            
        rois_dict = []
        for r in self.video_widget.rois:
            rois_dict.append({
                "id": getattr(r, "id", str(uuid.uuid4())),
                "name": r.name,
                "shape": r.shape.value,
                "display_type": r.display_type.value,
                "coordinates": r.coordinates,
                "unit": r.unit,
                "value_min": r.value_min,
                "value_max": r.value_max,
                "max_delta_per_sec": r.max_delta_per_sec,
                "preprocessing_params": r.preprocessing_params,
                "decimal_places": r.decimal_places,
                "is_container": getattr(r, "is_container", False),
                "sub_roi_ids": getattr(r, "sub_roi_ids", []),
                "sort_direction": getattr(r, "sort_direction", "ltr"),
                "decimal_position": getattr(r, "decimal_position", None),
                "allow_leading_blank": getattr(r, "allow_leading_blank", True),
                "allow_negative": getattr(r, "allow_negative", True),
                "analog_calibration": getattr(r, "analog_calibration", []),
                "analog_value": getattr(r, "analog_value", None),
                "strip_width": getattr(r, "strip_width", 30.0),
                "calibration_marks": getattr(r, "calibration_marks", []),
                "ruler_edge": getattr(r, "ruler_edge", "top"),
                "suppress_scale_marks": getattr(r, "suppress_scale_marks", True),
                "core_width_pct": getattr(r, "core_width_pct", 0.6),
                "min_float_height": getattr(r, "min_float_height", 8)
            })

        calc_dict = [ch.to_dict() for ch in self.calc_engine.channels.values()]

        preset_data = {
            "rois": rois_dict,
            "calculated_channels": calc_dict,
        }
        jstr = json.dumps(preset_data)
        self.db_writer.save_roi_preset(name, jstr)
        QMessageBox.information(self, "Success", "Preset saved.")
        
    def load_roi_preset(self):
        presets = self.db_writer.load_roi_presets()
        if not presets:
            QMessageBox.information(self, "Info", "No presets found.")
            return
            
        preset_names = [p[1] for p in presets]
        name, ok = QInputDialog.getItem(self, "Load Preset", "Select preset:", preset_names, 0, False)
        if not ok or not name:
            return
            
        selected_preset = next(p for p in presets if p[1] == name)
        jstr = selected_preset[2]
        
        parsed_data = json.loads(jstr)
        if isinstance(parsed_data, dict) and "rois" in parsed_data:
            rois_dict = parsed_data["rois"]
            calc_dict = parsed_data.get("calculated_channels", [])
        else:
            rois_dict = parsed_data
            calc_dict = []

        self.video_widget.rois.clear()
        self.control_panel.roi_list.clear()
        
        seen_names = set()
        for d in rois_dict:
            roi_name = d["name"]
            base_name = roi_name
            k = 1
            while roi_name.lower() in seen_names:
                roi_name = f"{base_name}_{k}"
                k += 1
            seen_names.add(roi_name.lower())

            roi = ROIConfig(
                id=d.get("id", str(uuid.uuid4())),
                name=roi_name,
                shape=ROIShape(d["shape"]),
                display_type=DisplayType(d.get("display_type", "digital")),
                coordinates=d["coordinates"],
                unit=d.get("unit", ""),
                value_min=d.get("value_min"),
                value_max=d.get("value_max"),
                max_delta_per_sec=d.get("max_delta_per_sec"),
                preprocessing_params=d.get("preprocessing_params", {}),
                decimal_places=d.get("decimal_places"),
                is_container=d.get("is_container", False),
                sub_roi_ids=d.get("sub_roi_ids", []),
                sort_direction=d.get("sort_direction", "ltr"),
                decimal_position=d.get("decimal_position"),
                allow_leading_blank=d.get("allow_leading_blank", True),
                allow_negative=d.get("allow_negative", True),
                analog_calibration=d.get("analog_calibration", []),
                analog_value=d.get("analog_value"),
                strip_width=d.get("strip_width", 30.0),
                calibration_marks=d.get("calibration_marks", []),
                ruler_edge=d.get("ruler_edge", "top"),
                suppress_scale_marks=d.get("suppress_scale_marks", True),
                core_width_pct=d.get("core_width_pct", 0.6),
                min_float_height=d.get("min_float_height", 8)
            )
            self.video_widget.rois.append(roi)
            self.control_panel.add_roi(roi)

        for r in self.video_widget.rois:
            if r.name.startswith("ROI_"):
                try:
                    num = int(r.name[4:])
                    if num >= self._roi_counter:
                        self._roi_counter = num + 1
                except ValueError:
                    pass
        self.video_widget._roi_counter = self._roi_counter
            
        self.ocr_worker.update_rois(self.video_widget.rois)
        self.video_widget._update_display()

        # Re-populate calculated channels
        self.calc_engine.clear_channels()
        for cd in calc_dict:
            ch = CalculationChannel.from_dict(cd)
            self.calc_engine.add_channel(ch)
        self.control_panel.set_calculated_channels(list(self.calc_engine.channels.values()))

        
    def closeEvent(self, event):
        if self.recorder.is_recording:
            self.recorder.stop_recording()
        if self.camera_thread:
            self.camera_thread.stop()
            self.camera_thread = None
            self.camera = None
        self.ocr_worker.stop()
        self.db_writer.end_run()
        event.accept()

