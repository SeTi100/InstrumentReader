import json
import uuid
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QSplitter, QMenuBar, QMenu, QFileDialog, QMessageBox, QInputDialog
)
from PySide6.QtCore import Qt, QThread, Signal
from instrument_reader.core.camera import OpenCVCamera
from instrument_reader.core.roi import ROIShape, ROIConfig, DisplayType
from instrument_reader.core.preprocessing import PreprocessingConfig
from instrument_reader.gui.video_widget import VideoWidget
from instrument_reader.gui.control_panel import ControlPanel
from instrument_reader.gui.ocr_worker import OCRWorker
from instrument_reader.gui.db_writer import DatabaseWriter
from instrument_reader.gui.experiment_dialog import ExperimentDialog
from instrument_reader.gui.export_dialog import ExportDialog
from instrument_reader.gui.roi_config_dialog import ROIConfigDialog
from instrument_reader.gui.preprocessing_dialog import PreprocessingDialog

class CameraThread(QThread):
    frame_ready = Signal(object)
    
    def __init__(self, camera):
        super().__init__()
        self.camera = camera
        self.running = False
        
    def run(self):
        self.running = True
        self.camera.open()
        while self.running:
            ret, frame = self.camera.read()
            if ret and frame is not None:
                self.frame_ready.emit(frame)
            self.msleep(int(1000 / self.camera.fps))
            
    def stop(self):
        self.running = False
        self.wait()
        self.camera.release()

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Instrument Reader - Phase 1.1")
        self.setMinimumSize(1200, 800)
        
        self.db_writer = DatabaseWriter()
        self.ocr_worker = OCRWorker()
        self._roi_counter = 1
        
        self.setup_ui()
        
        self.camera = None
        self.camera_thread = None
        
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
            src_str = self.control_panel.camera_source.text()
            src = int(src_str) if src_str.isdigit() else src_str
            self.camera = OpenCVCamera(src)
            self.camera_thread = CameraThread(self.camera)
            self.camera_thread.frame_ready.connect(self.video_widget.update_frame)
            self.camera_thread.frame_ready.connect(self.ocr_worker.update_frame)
            self.camera_thread.start()
            self.ocr_worker.start()

        def stop_camera():
            if self.camera_thread:
                self.camera_thread.stop()
            self.ocr_worker.stop()

        self.control_panel.start_btn.clicked.connect(start_camera)
        self.control_panel.stop_btn.clicked.connect(stop_camera)
        
        self.video_widget.roi_created.connect(self.on_roi_created)
        
        self.control_panel.rect_btn.clicked.connect(lambda: self.set_draw_mode(ROIShape.RECTANGLE))
        self.control_panel.poly_btn.clicked.connect(lambda: self.set_draw_mode(ROIShape.POLYGON))
        self.control_panel.ruler_btn.clicked.connect(lambda: self.set_draw_mode(ROIShape.RULER))
        
        self.control_panel.interval_spin.valueChanged.connect(self.ocr_worker.set_interval)
        self.control_panel.new_exp_btn.clicked.connect(self.create_experiment)
        self.control_panel.delete_roi_btn.clicked.connect(self.delete_roi)
        
        self.ocr_worker.readings_ready.connect(self.control_panel.update_readings)
        self.ocr_worker.readings_ready.connect(self.db_writer.insert_readings)
        self.ocr_worker.readings_ready.connect(self.video_widget.update_readings)

        self.control_panel.roi_list.itemDoubleClicked.connect(self.on_roi_double_clicked)
        self.control_panel.roi_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.control_panel.roi_list.customContextMenuRequested.connect(self.on_roi_context_menu)
        
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

            self.ocr_worker.update_rois(self.video_widget.rois)
            self.video_widget._update_display()
            
    def create_experiment(self):
        dlg = ExperimentDialog(self)
        if dlg.exec():
            data = dlg.get_data()
            exp_id = self.db_writer.create_experiment(data["exp_name"], data["voc_type"], data["exp_desc"])
            run_id = self.db_writer.create_run(exp_id, data["target_temp"], data["run_notes"])
            self.control_panel.exp_label.setText(f"Experiment: {exp_id} | Run: {run_id}")
            
    def export_csv(self):
        dlg = ExportDialog(self)
        if dlg.exec():
            run_id, path = dlg.get_data()
            try:
                self.db_writer.db.export_csv(path, run_id)
                QMessageBox.information(self, "Success", f"Exported to {path}")
            except Exception as e:
                QMessageBox.critical(self, "Error", str(e))
                
    def save_roi_preset(self):
        if not self.video_widget.rois:
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
        jstr = json.dumps(rois_dict)
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
        
        rois_dict = json.loads(jstr)
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

        
    def closeEvent(self, event):
        if self.camera_thread:
            self.camera_thread.stop()
        self.ocr_worker.stop()
        self.db_writer.end_run()
        event.accept()

