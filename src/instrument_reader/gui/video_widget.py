import cv2
import numpy as np
from PySide6.QtWidgets import QLabel, QMenu, QInputDialog, QMessageBox
from PySide6.QtCore import Qt, Signal, QRect, QPoint
from PySide6.QtGui import QImage, QPixmap, QPainter, QPen, QColor, QPolygon
from instrument_reader.core.roi import ROIConfig, ROIShape, DisplayType

class VideoWidget(QLabel):
    roi_created = Signal(ROIConfig)
    
    def __init__(self):
        super().__init__()
        self.setText("Video Feed")
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background-color: black;")
        self.setMinimumSize(640, 480)
        self.current_frame = None
        self.rois = []
        self.latest_readings = []
        self._roi_counter = 1
        
        self.drawing_mode = ROIShape.RECTANGLE
        self.drawing = False
        self.start_point = None
        self.end_point = None
        self.polygon_points = []
        
        # Scaling ratios
        self.scale_x = 1.0
        self.scale_y = 1.0

    def _get_next_default_name(self) -> str:
        existing = {r.name.lower() for r in self.rois if r.name}
        while f"roi_{self._roi_counter}".lower() in existing:
            self._roi_counter += 1
        name = f"ROI_{self._roi_counter}"
        self._roi_counter += 1
        return name
        
    def set_drawing_mode(self, mode: ROIShape):
        self.drawing_mode = mode
        self.drawing = False
        self.start_point = None
        self.end_point = None
        self.polygon_points = []
        self._update_display()
        
    def update_frame(self, frame):
        self.current_frame = frame.copy()
        self._update_display()
        
    def _map_to_frame(self, x, y):
        return int(x / self.scale_x), int(y / self.scale_y)
        
    def _map_to_widget(self, x, y):
        return int(x * self.scale_x), int(y * self.scale_y)
        
    def update_readings(self, readings):
        self.latest_readings = readings
        self._update_display()

    def _update_display(self):
        if self.current_frame is None:
            return
            
        frame_rgb = cv2.cvtColor(self.current_frame, cv2.COLOR_BGR2RGB)
        h, w, ch = frame_rgb.shape
        bytes_per_line = ch * w
        q_img = QImage(frame_rgb.data, w, h, bytes_per_line, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(q_img)
        
        scaled_pixmap = pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        
        # Calculate actual rendered rect within QLabel
        x_offset = (self.width() - scaled_pixmap.width()) // 2
        y_offset = (self.height() - scaled_pixmap.height()) // 2
        
        self.scale_x = scaled_pixmap.width() / w
        self.scale_y = scaled_pixmap.height() / h
        self.x_offset = x_offset
        self.y_offset = y_offset
        
        painter = QPainter(scaled_pixmap)
        for roi in self.rois:
            roi_id = getattr(roi, "id", None)
            r = None
            if roi_id:
                r = next((item for item in self.latest_readings if item.get("roi_id") == roi_id), None)
            if r is None:
                r = next((item for item in self.latest_readings if item.get("roi_name") == roi.name), None)
            is_container = getattr(roi, "is_container", False)
            is_child = r.get("is_child", False) if r else (getattr(roi, "analog_value", None) is not None)
            
            if is_container:
                pen = QPen(QColor(0, 190, 255), 3)  # Bright cyan/blue for container
                val_str = ""
                if r and r.get("parsed_value") is not None:
                    val_str = f" : {r['parsed_value']} {roi.unit}"
                elif r and not r.get("is_valid", False):
                    val_str = f" [{r.get('reason', '...')}]"
                display_text = f"{roi.name}{val_str}"
            elif is_child:
                pen = QPen(QColor(255, 165, 0), 2, Qt.DashLine)  # Amber dashed for sub-ROIs
                slot_idx = r.get("slot_index", "") if r else ""
                slot_val = r.get("raw_text", "") if r else ""
                if slot_val.startswith("Cal:"):
                    display_text = f"[{slot_idx}] {slot_val}"
                elif slot_val:
                    display_text = f"[{slot_idx}] '{slot_val}'"
                elif getattr(roi, "analog_value", None) is not None:
                    display_text = f"Cal: {roi.analog_value}"
                else:
                    display_text = f"[{slot_idx}] {roi.name}" if slot_idx else roi.name
            else:
                pen = QPen(QColor(0, 255, 0), 2)  # Standard green
                val_str = ""
                if r and r.get("parsed_value") is not None:
                    val_str = f" : {r['parsed_value']} {r.get('unit', '')}"
                display_text = roi.name + val_str
            
            painter.setPen(pen)
            if roi.shape == ROIShape.RECTANGLE and len(roi.coordinates) == 4:
                rx, ry, rw, rh = roi.coordinates
                wx, wy = self._map_to_widget(rx, ry)
                ww = int(rw * self.scale_x)
                wh = int(rh * self.scale_y)
                painter.drawRect(wx, wy, ww, wh)
                painter.drawText(wx, max(14, wy - 5), display_text)

                # Draw detected rotameter float line across container
                if is_container and roi.display_type == DisplayType.ANALOG and r and r.get("y_float") is not None:
                    float_wy = self._map_to_widget(0, ry + r["y_float"])[1]
                    if wy <= float_wy <= wy + wh:
                        float_pen = QPen(QColor(255, 50, 50), 2, Qt.SolidLine)
                        painter.setPen(float_pen)
                        painter.drawLine(wx, float_wy, wx + ww, float_wy)
                        painter.setPen(pen)
            elif roi.shape == ROIShape.POLYGON and len(roi.coordinates) > 0:
                pts = [QPoint(*self._map_to_widget(px, py)) for px, py in roi.coordinates]
                painter.drawPolygon(QPolygon(pts))
                painter.drawText(pts[0].x(), max(14, pts[0].y() - 5), display_text)
            elif roi.shape == ROIShape.RULER:
                from instrument_reader.core.analog_reader import RotameterRulerReader
                endpoints = RotameterRulerReader.get_endpoints_from_roi(roi)
                if endpoints:
                    (fx1, fy1), (fx2, fy2) = endpoints
                    wx1, wy1 = self._map_to_widget(fx1, fy1)
                    wx2, wy2 = self._map_to_widget(fx2, fy2)
                    dx = wx2 - wx1
                    dy = wy2 - wy1
                    len_w = float(np.hypot(dx, dy))
                    if len_w >= 1.0:
                        ux = dx / len_w
                        uy = dy / len_w
                        nx = -uy
                        ny = ux
                        sw = getattr(roi, "strip_width", 30.0)
                        hw_w = (sw * self.scale_x) / 2.0
                        
                        # Draw strip boundary
                        c1 = QPoint(int(wx1 - hw_w * nx), int(wy1 - hw_w * ny))
                        c2 = QPoint(int(wx1 + hw_w * nx), int(wy1 + hw_w * ny))
                        c3 = QPoint(int(wx2 + hw_w * nx), int(wy2 + hw_w * ny))
                        c4 = QPoint(int(wx2 - hw_w * nx), int(wy2 - hw_w * ny))
                        strip_pen = QPen(QColor(0, 200, 255, 120), 1, Qt.DashLine)
                        painter.setPen(strip_pen)
                        painter.drawPolygon(QPolygon([c1, c2, c3, c4]))
                        
                        # Draw axis line
                        axis_pen = QPen(QColor(0, 220, 255), 2, Qt.SolidLine)
                        painter.setPen(axis_pen)
                        painter.drawLine(wx1, wy1, wx2, wy2)
                        
                        # Draw end caps
                        painter.drawLine(c1, c2)
                        painter.drawLine(c4, c3)
                        
                        # Draw calibration tick marks
                        tick_pen = QPen(QColor(255, 230, 0), 2, Qt.SolidLine)
                        text_pen = QPen(QColor(255, 255, 255), 1)
                        for mark in getattr(roi, "calibration_marks", []):
                            m_pos = float(mark.get("pos", 0.0))
                            m_val = mark.get("value", 0.0)
                            mx = wx1 + m_pos * dx
                            my = wy1 + m_pos * dy
                            t1 = QPoint(int(mx - (hw_w * 0.7) * nx), int(my - (hw_w * 0.7) * ny))
                            t2 = QPoint(int(mx + (hw_w * 0.7) * nx), int(my + (hw_w * 0.7) * ny))
                            painter.setPen(tick_pen)
                            painter.drawLine(t1, t2)
                            painter.setPen(text_pen)
                            lbl_x = int(mx + (hw_w + 5) * nx)
                            lbl_y = int(my + (hw_w + 5) * ny)
                            painter.drawText(lbl_x, lbl_y, f"{m_val:g}")
                            
                        # Draw float reading if detected
                        if r and r.get("edge_pts"):
                            (ex1, ey1), (ex2, ey2) = r["edge_pts"]
                            w_ex1, w_ey1 = self._map_to_widget(ex1, ey1)
                            w_ex2, w_ey2 = self._map_to_widget(ex2, ey2)
                            float_pen = QPen(QColor(255, 40, 40), 3, Qt.SolidLine)
                            painter.setPen(float_pen)
                            painter.drawLine(w_ex1, w_ey1, w_ex2, w_ey2)
                            if r.get("parsed_value") is not None:
                                edge_lbl = f" ({getattr(roi, 'ruler_edge', 'top').capitalize()})"
                                painter.setPen(QPen(QColor(255, 80, 80), 2))
                                painter.drawText(w_ex2 + 5, w_ey2, f"◀ {r['parsed_value']} {roi.unit}{edge_lbl}")
                        elif r and r.get("rel_pos") is not None:
                            rel_s = float(r["rel_pos"])
                            mx = wx1 + rel_s * dx
                            my = wy1 + rel_s * dy
                            e1 = QPoint(int(mx - hw_w * nx), int(my - hw_w * ny))
                            e2 = QPoint(int(mx + hw_w * nx), int(my + hw_w * ny))
                            float_pen = QPen(QColor(255, 40, 40), 3, Qt.SolidLine)
                            painter.setPen(float_pen)
                            painter.drawLine(e1, e2)
                            if r.get("parsed_value") is not None:
                                edge_lbl = f" ({getattr(roi, 'ruler_edge', 'top').capitalize()})"
                                painter.setPen(QPen(QColor(255, 80, 80), 2))
                                painter.drawText(e2.x() + 5, e2.y(), f"◀ {r['parsed_value']} {roi.unit}{edge_lbl}")

                        # ROI Name and reading text
                        val_str = ""
                        if r and r.get("parsed_value") is not None:
                            val_str = f" : {r['parsed_value']} {roi.unit}"
                        elif r and not r.get("is_valid", False):
                            val_str = f" [{r.get('reason', '...')}]"
                        display_text = f"{roi.name}{val_str}"
                        painter.setPen(QPen(QColor(0, 220, 255), 2))
                        painter.drawText(wx1, max(14, wy1 - 10), display_text)
                
        if self.drawing:
            if self.drawing_mode == ROIShape.RECTANGLE and self.start_point and self.end_point:
                rect = QRect(self.start_point, self.end_point)
                painter.drawRect(rect)
            elif self.drawing_mode == ROIShape.POLYGON:
                if len(self.polygon_points) > 0:
                    for i in range(len(self.polygon_points) - 1):
                        painter.drawLine(self.polygon_points[i], self.polygon_points[i+1])
                    if self.end_point:
                        painter.drawLine(self.polygon_points[-1], self.end_point)
            elif self.drawing_mode == ROIShape.RULER and self.start_point and self.end_point:
                p1 = self.start_point
                p2 = self.end_point
                dx = p2.x() - p1.x()
                dy = p2.y() - p1.y()
                len_p = float(np.hypot(dx, dy))
                if len_p > 2:
                    ux = dx / len_p
                    uy = dy / len_p
                    nx = -uy
                    ny = ux
                    hw = 15.0 * self.scale_x
                    c1 = QPoint(int(p1.x() - hw * nx), int(p1.y() - hw * ny))
                    c2 = QPoint(int(p1.x() + hw * nx), int(p1.y() + hw * ny))
                    c3 = QPoint(int(p2.x() + hw * nx), int(p2.y() + hw * ny))
                    c4 = QPoint(int(p2.x() - hw * nx), int(p2.y() - hw * ny))
                    
                    preview_pen = QPen(QColor(0, 255, 255, 180), 1, Qt.DashLine)
                    painter.setPen(preview_pen)
                    painter.drawPolygon(QPolygon([c1, c2, c3, c4]))
                    
                    axis_pen = QPen(QColor(0, 255, 255), 2, Qt.SolidLine)
                    painter.setPen(axis_pen)
                    painter.drawLine(p1, p2)
                        
        painter.end()
        
        final_pixmap = QPixmap(self.size())
        final_pixmap.fill(Qt.black)
        painter = QPainter(final_pixmap)
        painter.drawPixmap(x_offset, y_offset, scaled_pixmap)
        painter.end()
        
        self.setPixmap(final_pixmap)
        
    def _get_event_pos_on_pixmap(self, event):
        pos = event.position().toPoint()
        px = pos.x() - self.x_offset
        py = pos.y() - self.y_offset
        if px < 0: px = 0
        if py < 0: py = 0
        if px > self.width() - 2*self.x_offset: px = self.width() - 2*self.x_offset
        if py > self.height() - 2*self.y_offset: py = self.height() - 2*self.y_offset
        return QPoint(px, py)
        
    def mousePressEvent(self, event):
        if self.current_frame is None: return
        pos = self._get_event_pos_on_pixmap(event)
        
        if event.button() == Qt.LeftButton:
            if self.drawing_mode == ROIShape.RECTANGLE:
                self.drawing = True
                self.start_point = pos
                self.end_point = pos
            elif self.drawing_mode == ROIShape.POLYGON:
                self.drawing = True
                self.polygon_points.append(pos)
                self.end_point = pos
            elif self.drawing_mode == ROIShape.RULER:
                if not self.drawing:
                    self.drawing = True
                    self.start_point = pos
                    self.end_point = pos
                    self._update_display()
                else:
                    self.end_point = pos
                    self._finish_ruler()
                
    def mouseMoveEvent(self, event):
        if self.drawing:
            self.end_point = self._get_event_pos_on_pixmap(event)
            self._update_display()
            
    def mouseDoubleClickEvent(self, event):
        if self.drawing_mode == ROIShape.POLYGON and self.drawing and len(self.polygon_points) > 2:
            self._finish_polygon()
            
    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.drawing = False
            self.start_point = None
            self.end_point = None
            self.polygon_points = []
            self._update_display()
            event.accept()
        else:
            super().keyPressEvent(event)

    def mouseReleaseEvent(self, event):
        if not self.drawing: return
        pos = self._get_event_pos_on_pixmap(event)
        
        if event.button() == Qt.RightButton:
            if self.drawing_mode == ROIShape.POLYGON:
                self._finish_polygon()
            else:
                self.drawing = False
                self.start_point = None
                self.end_point = None
                self._update_display()
            return
            
        if self.drawing_mode == ROIShape.RECTANGLE and event.button() == Qt.LeftButton:
            self.drawing = False
            self.end_point = pos
            rect = QRect(self.start_point, self.end_point).normalized()
            if rect.width() > 5 and rect.height() > 5:
                fx, fy = self._map_to_frame(rect.x(), rect.y())
                fw = int(rect.width() / self.scale_x)
                fh = int(rect.height() / self.scale_y)
                roi = ROIConfig(
                    name=self._get_next_default_name(),
                    shape=ROIShape.RECTANGLE,
                    coordinates=[fx, fy, fw, fh]
                )
                self.rois.append(roi)
                self._update_display()
                self.roi_created.emit(roi)
            else:
                self._update_display()

        elif self.drawing_mode == ROIShape.RULER and event.button() == Qt.LeftButton:
            # Check if this was a drag release of at least 20 px
            if self.start_point and self.end_point:
                dx = self.end_point.x() - self.start_point.x()
                dy = self.end_point.y() - self.start_point.y()
                if (dx * dx + dy * dy) >= 400:
                    self._finish_ruler()
            
    def _finish_polygon(self):
        self.drawing = False
        if len(self.polygon_points) > 2:
            frame_pts = [self._map_to_frame(p.x(), p.y()) for p in self.polygon_points]
            roi = ROIConfig(
                name=self._get_next_default_name(),
                shape=ROIShape.POLYGON,
                coordinates=frame_pts
            )
            self.rois.append(roi)
            self._update_display()
            self.roi_created.emit(roi)
        else:
            self.polygon_points = []
            self._update_display()
        self.polygon_points = []

    def _finish_ruler(self):
        self.drawing = False
        if not self.start_point or not self.end_point:
            self.start_point = None
            self.end_point = None
            self._update_display()
            return
            
        fx1, fy1 = self._map_to_frame(self.start_point.x(), self.start_point.y())
        fx2, fy2 = self._map_to_frame(self.end_point.x(), self.end_point.y())
        
        dist = float(np.hypot(fx2 - fx1, fy2 - fy1))
        if dist < 10.0:
            self.start_point = None
            self.end_point = None
            self._update_display()
            return
            
        v1, ok1 = QInputDialog.getDouble(
            self, "Ruler Calibration", "Enter scale value at Start point (P1):",
            100.0, -999999.0, 999999.0, 2
        )
        if not ok1:
            self.start_point = None
            self.end_point = None
            self._update_display()
            return
            
        v2, ok2 = QInputDialog.getDouble(
            self, "Ruler Calibration", "Enter scale value at End point (P2):",
            800.0, -999999.0, 999999.0, 2
        )
        if not ok2:
            self.start_point = None
            self.end_point = None
            self._update_display()
            return

        if abs(float(v1) - float(v2)) < 1e-6:
            QMessageBox.warning(self, "Invalid Scale", "Start and End calibration values cannot be identical.")
            self.start_point = None
            self.end_point = None
            self._update_display()
            return
            
        unit, ok_u = QInputDialog.getText(
            self, "Ruler Unit", "Enter measurement unit (e.g. Nl/h):",
            text="Nl/h"
        )
        unit_str = unit.strip() if ok_u and unit.strip() else "Nl/h"
        
        roi = ROIConfig(
            name=self._get_next_default_name(),
            shape=ROIShape.RULER,
            display_type=DisplayType.ANALOG,
            coordinates=[[fx1, fy1], [fx2, fy2]],
            strip_width=30.0,
            calibration_marks=[
                {"pos": 0.0, "value": float(v1)},
                {"pos": 1.0, "value": float(v2)}
            ],
            unit=unit_str
        )
        self.start_point = None
        self.end_point = None
        self.rois.append(roi)
        self._update_display()
        self.roi_created.emit(roi)

