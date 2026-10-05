from typing import Optional, Dict, Any, List, Tuple
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QFormLayout, QSlider, QCheckBox, 
    QComboBox, QDialogButtonBox, QLabel, QDoubleSpinBox, QSpinBox, 
    QPushButton, QListWidget, QGraphicsView, QGraphicsScene, 
    QGraphicsPixmapItem, QGraphicsRectItem, QGraphicsLineItem, 
    QGraphicsSimpleTextItem, QGraphicsPathItem, QSplitter, 
    QGroupBox, QScrollArea, QWidget, QRadioButton, QButtonGroup
)
from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QImage, QPixmap, QPen, QColor, QPainter, QMouseEvent, QPainterPath, QFont
import cv2
import numpy as np
from instrument_reader.core.preprocessing import PreprocessingPipeline, PreprocessingConfig
from instrument_reader.core.analog_reader import RotameterReader, RotameterRulerReader

class ZoomableGraphicsView(QGraphicsView):
    mask_created = Signal(int, int, int, int)  # x, y, w, h in scene coordinates

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setRenderHint(QPainter.Antialiasing, False)
        self.setRenderHint(QPainter.SmoothPixmapTransform, False)
        self.mode = "nav"  # "nav" or "draw_mask"
        self.drawing = False
        self.start_scene_pt = None
        self.temp_rect_item = None
        self.setDragMode(QGraphicsView.ScrollHandDrag)

    def set_mode(self, mode: str):
        self.mode = mode
        if mode == "nav":
            self.setDragMode(QGraphicsView.ScrollHandDrag)
        else:
            self.setDragMode(QGraphicsView.NoDrag)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        factor = 1.25 if delta > 0 else 0.8
        current_scale = self.transform().m11()
        if (factor > 1.0 and current_scale < 80.0) or (factor < 1.0 and current_scale > 0.02):
            self.scale(factor, factor)

    def mousePressEvent(self, event: QMouseEvent):
        if self.mode == "draw_mask" and event.button() == Qt.LeftButton:
            self.drawing = True
            self.start_scene_pt = self.mapToScene(event.pos())
            if self.temp_rect_item:
                self.scene().removeItem(self.temp_rect_item)
                self.temp_rect_item = None
            self.temp_rect_item = QGraphicsRectItem()
            pen = QPen(QColor(255, 50, 50), 1, Qt.DashLine)
            pen.setCosmetic(True)
            self.temp_rect_item.setPen(pen)
            self.scene().addItem(self.temp_rect_item)
            self.temp_rect_item.setRect(QRectF(self.start_scene_pt, self.start_scene_pt))
            return
        elif event.button() == Qt.MiddleButton:
            self.setDragMode(QGraphicsView.ScrollHandDrag)
            fake = QMouseEvent(event.type(), event.position(), Qt.LeftButton, Qt.LeftButton, event.modifiers())
            super().mousePressEvent(fake)
            return

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self.drawing and self.start_scene_pt and self.temp_rect_item:
            current_scene_pt = self.mapToScene(event.pos())
            rect = QRectF(self.start_scene_pt, current_scene_pt).normalized()
            self.temp_rect_item.setRect(rect)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if self.drawing and event.button() == Qt.LeftButton:
            self.drawing = False
            if self.start_scene_pt:
                end_scene_pt = self.mapToScene(event.pos())
                rect = QRectF(self.start_scene_pt, end_scene_pt).normalized()
                if self.temp_rect_item:
                    self.scene().removeItem(self.temp_rect_item)
                    self.temp_rect_item = None
                if rect.width() >= 2 and rect.height() >= 2:
                    self.mask_created.emit(int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height()))
            return
        elif event.button() == Qt.MiddleButton:
            if self.mode != "nav":
                self.setDragMode(QGraphicsView.NoDrag)
            super().mouseReleaseEvent(event)
            return

class ProfilePlotWidget(QWidget):
    """
    Mini-plot displaying 1D intensity profile I(y) and vertical gradient dI/dy
    aligned with the tube height y.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(150)
        self.setMaximumHeight(220)
        self.debug_info: Optional[Dict[str, Any]] = None

    def set_data(self, debug_info: Optional[Dict[str, Any]]):
        self.debug_info = debug_info
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()

        # Dark background
        painter.fillRect(0, 0, w, h, QColor(25, 27, 32))
        painter.setPen(QPen(QColor(60, 65, 75), 1))
        painter.drawRect(0, 0, w - 1, h - 1)

        if not self.debug_info or self.debug_info.get("profile") is None:
            painter.setPen(QColor(140, 145, 155))
            painter.drawText(self.rect(), Qt.AlignCenter, "Keine Profildaten vorhanden")
            return

        profile = self.debug_info.get("profile")
        grad = self.debug_info.get("grad_full")
        if grad is None and self.debug_info.get("gradient") is not None:
            grad = self.debug_info.get("gradient")
        
        y_float = self.debug_info.get("y_float")
        candidates = self.debug_info.get("candidates_1d", [])
        
        img_len = len(profile)
        if img_len < 2:
            return

        # Margins for axes / labels
        left_m = 35
        right_m = 15
        top_m = 24
        bottom_m = 12
        plot_w = max(10, w - left_m - right_m)
        plot_h = max(10, h - top_m - bottom_m)

        # Draw Title & Legend
        font = painter.font()
        font.setPointSize(8)
        painter.setFont(font)

        painter.setPen(QColor(80, 190, 255))
        painter.drawText(left_m, 16, "— I(y)")

        painter.setPen(QColor(255, 160, 40))
        painter.drawText(left_m + 65, 16, "— dI/dy")

        if y_float is not None:
            painter.setPen(QColor(255, 60, 60))
            painter.drawText(left_m + 130, 16, "— Float y")

        # Zero center line for gradient
        cx = left_m + plot_w / 2.0
        painter.setPen(QPen(QColor(50, 55, 65), 1, Qt.DashLine))
        painter.drawLine(int(cx), top_m, int(cx), top_m + plot_h)

        # Plot paths
        path_prof = QPainterPath()
        path_grad = QPainterPath()

        max_g = float(np.max(np.abs(grad))) if grad is not None and len(grad) > 0 else 1.0
        if max_g < 1e-3:
            max_g = 1.0

        for i in range(img_len):
            py = top_m + (i / float(img_len - 1)) * plot_h
            # Intensity x
            px = left_m + (float(profile[i]) / 255.0) * plot_w
            if i == 0:
                path_prof.moveTo(px, py)
            else:
                path_prof.lineTo(px, py)

            # Gradient x (centered)
            if grad is not None and i < len(grad):
                gx = cx + (float(grad[i]) / max_g) * (plot_w / 2.0) * 0.95
                if i == 0:
                    path_grad.moveTo(gx, py)
                else:
                    path_grad.lineTo(gx, py)

        # Draw intensity curve
        pen_prof = QPen(QColor(70, 170, 240, 200), 1.5)
        painter.setPen(pen_prof)
        painter.drawPath(path_prof)

        # Draw gradient curve
        pen_grad = QPen(QColor(255, 150, 30, 220), 1.5)
        painter.setPen(pen_grad)
        painter.drawPath(path_grad)

        # Draw candidate lines
        for cand in candidates:
            cy = cand.get("y")
            if cy is not None and 0 <= cy < img_len:
                p_y = top_m + (cy / float(img_len - 1)) * plot_h
                is_sel = cand.get("selected", False)
                if not is_sel:
                    painter.setPen(QPen(QColor(255, 160, 0, 160), 1, Qt.DashLine))
                    painter.drawLine(left_m, int(p_y), left_m + plot_w, int(p_y))

        # Draw selected float line
        if y_float is not None and 0 <= y_float < img_len:
            p_y = top_m + (y_float / float(img_len - 1)) * plot_h
            painter.setPen(QPen(QColor(255, 40, 40), 2, Qt.SolidLine))
            painter.drawLine(left_m - 4, int(p_y), left_m + plot_w + 4, int(p_y))
            painter.setPen(QColor(255, 80, 80))
            painter.drawText(2, int(p_y) + 4, f"{int(round(y_float))}")


class PreprocessingDialog(QDialog):
    def __init__(self, crop: np.ndarray, config: PreprocessingConfig, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preprocessing Settings - Pixel Zoom & Masking")
        self.resize(1150, 750)
        self.crop = crop
        self.config = config
        # Ensure new fields exist on config
        if not hasattr(self.config, "upscale_factor"):
            self.config.upscale_factor = 1
        if not hasattr(self.config, "morphology_shape"):
            self.config.morphology_shape = "rect"
        if not hasattr(self.config, "masks"):
            self.config.masks = []
        if not hasattr(self.config, "ruler_edge"):
            self.config.ruler_edge = "top"
        if not hasattr(self.config, "suppress_scale_marks"):
            self.config.suppress_scale_marks = True
        if not hasattr(self.config, "core_width_pct"):
            self.config.core_width_pct = 0.6
        if not hasattr(self.config, "min_float_height"):
            self.config.min_float_height = 8
            
        self.pipeline = PreprocessingPipeline()
        self.rotameter_reader = RotameterReader()
        self.mask_outline_items = []
        self.float_line_item = None
        self.float_box_item = None
        self.debug_overlay_items = []
        self.first_fit_done = False

        # Main Layout
        main_layout = QVBoxLayout(self)
        splitter = QSplitter(Qt.Horizontal)

        # Left: Interactive View & Tools
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)

        # Toolbar above view
        toolbar = QHBoxLayout()
        self.mode_group = QButtonGroup(self)
        self.nav_radio = QRadioButton("Pan / Navigate")
        self.nav_radio.setChecked(True)
        self.draw_radio = QRadioButton("Draw Mask")
        self.mode_group.addButton(self.nav_radio)
        self.mode_group.addButton(self.draw_radio)
        self.nav_radio.toggled.connect(self._on_mode_toggled)

        toolbar.addWidget(QLabel("Mode:"))
        toolbar.addWidget(self.nav_radio)
        toolbar.addWidget(self.draw_radio)
        toolbar.addSpacing(15)

        toolbar.addWidget(QLabel("Mask Color:"))
        self.mask_color_combo = QComboBox()
        self.mask_color_combo.addItems(["Black (0 - Background)", "White (255 - Background)"])
        toolbar.addWidget(self.mask_color_combo)
        toolbar.addSpacing(15)

        reset_zoom_btn = QPushButton("Fit View")
        reset_zoom_btn.clicked.connect(self.fit_view)
        toolbar.addWidget(reset_zoom_btn)
        toolbar.addStretch()

        left_layout.addLayout(toolbar)

        # Graphics Scene & View
        self.scene = QGraphicsScene(self)
        self.pixmap_item = QGraphicsPixmapItem()
        self.scene.addItem(self.pixmap_item)

        self.view = ZoomableGraphicsView()
        self.view.setScene(self.scene)
        self.view.mask_created.connect(self.on_mask_drawn)
        left_layout.addWidget(self.view)

        # Hint below view
        hint_label = QLabel("Tip: Mouse wheel to zoom (down to pixel level). Click & drag to pan (or draw mask).")
        hint_label.setStyleSheet("color: gray; font-size: 11px;")
        left_layout.addWidget(hint_label)

        splitter.addWidget(left_widget)

        # Right: Settings & Mask Management
        right_scroll = QScrollArea()
        right_scroll.setWidgetResizable(True)
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        # Group 1: General Preprocessing
        pre_group = QGroupBox("Image Filters & Morphology")
        form = QFormLayout(pre_group)

        self.upscale_combo = QComboBox()
        self.upscale_combo.addItems(["1x (Original)", "2x (Upscaled)", "4x (High-Res)"])
        upscale_map = {1: 0, 2: 1, 4: 2}
        self.upscale_combo.setCurrentIndex(upscale_map.get(getattr(config, "upscale_factor", 1), 0))
        self.upscale_combo.currentIndexChanged.connect(self.update_preview)

        self.gray_cb = QCheckBox("Grayscale")
        self.gray_cb.setChecked(config.grayscale)
        self.gray_cb.toggled.connect(self.update_preview)

        self.invert_cb = QCheckBox("Invert")
        self.invert_cb.setChecked(config.invert)
        self.invert_cb.toggled.connect(self.update_preview)

        self.thresh_combo = QComboBox()
        self.thresh_combo.addItems(["otsu", "adaptive", "fixed"])
        self.thresh_combo.setCurrentText(config.threshold_method)
        self.thresh_combo.currentTextChanged.connect(self.update_preview)

        self.thresh_slider = QSlider(Qt.Horizontal)
        self.thresh_slider.setRange(0, 255)
        self.thresh_slider.setValue(config.threshold_value)
        self.thresh_slider.valueChanged.connect(self.update_preview)

        self.blur_slider = QSlider(Qt.Horizontal)
        self.blur_slider.setRange(0, 21)
        self.blur_slider.setSingleStep(2)
        self.blur_slider.setValue(config.blur_kernel)
        self.blur_slider.valueChanged.connect(self.update_preview)

        self.alpha_slider = QDoubleSpinBox()
        self.alpha_slider.setRange(0.1, 5.0)
        self.alpha_slider.setSingleStep(0.1)
        self.alpha_slider.setValue(config.contrast_alpha)
        self.alpha_slider.valueChanged.connect(self.update_preview)

        self.beta_slider = QSlider(Qt.Horizontal)
        self.beta_slider.setRange(-100, 100)
        self.beta_slider.setValue(config.contrast_beta)
        self.beta_slider.valueChanged.connect(self.update_preview)

        self.morph_combo = QComboBox()
        self.morph_combo.addItems(["none", "dilate", "erode", "open", "close"])
        self.morph_combo.setCurrentText(config.morphology_op)
        self.morph_combo.currentTextChanged.connect(self.update_preview)

        self.morph_shape_combo = QComboBox()
        self.morph_shape_combo.addItems(["rect", "cross", "vertical", "horizontal"])
        self.morph_shape_combo.setCurrentText(getattr(config, "morphology_shape", "rect"))
        self.morph_shape_combo.currentTextChanged.connect(self.update_preview)

        k_layout = QHBoxLayout()
        self.morph_k_slider = QSlider(Qt.Horizontal)
        self.morph_k_slider.setRange(1, 21)
        self.morph_k_slider.setValue(config.morphology_kernel)
        self.morph_k_label = QLabel(str(config.morphology_kernel))
        self.morph_k_slider.valueChanged.connect(lambda v: self.morph_k_label.setText(str(v)))
        self.morph_k_slider.valueChanged.connect(self.update_preview)
        k_layout.addWidget(self.morph_k_slider)
        k_layout.addWidget(self.morph_k_label)

        self.rot_slider = QDoubleSpinBox()
        self.rot_slider.setRange(-180.0, 180.0)
        self.rot_slider.setValue(config.rotation_angle)
        self.rot_slider.valueChanged.connect(self.update_preview)

        form.addRow("Upscale Factor:", self.upscale_combo)
        form.addRow(self.gray_cb)
        form.addRow(self.invert_cb)
        form.addRow("Threshold Method:", self.thresh_combo)
        form.addRow("Threshold Value:", self.thresh_slider)
        form.addRow("Blur Kernel:", self.blur_slider)
        form.addRow("Contrast Alpha:", self.alpha_slider)
        form.addRow("Contrast Beta:", self.beta_slider)
        form.addRow("Morphology Op:", self.morph_combo)
        form.addRow("Morphology Shape:", self.morph_shape_combo)
        form.addRow("Morphology Kernel:", k_layout)
        form.addRow("Rotation Angle:", self.rot_slider)

        right_layout.addWidget(pre_group)

        # Group 2: Rotameter / Analog Inspection
        rotameter_group = QGroupBox("Rotameter Float Inspection")
        rot_layout = QVBoxLayout(rotameter_group)
        self.show_float_cb = QCheckBox("Show Detected Float Edge (Red Line)")
        self.show_float_cb.toggled.connect(self.update_preview)
        rot_layout.addWidget(self.show_float_cb)

        self.suppress_scale_cb = QCheckBox("Skalenstriche unterdrücken (2D-Objektfilter)")
        self.suppress_scale_cb.setToolTip(
            "Ignoriert gedruckte Skalenstriche auf dem Glas und fokussiert auf den echten Schwimmerkörper."
        )
        self.suppress_scale_cb.setChecked(getattr(self.config, "suppress_scale_marks", True))
        self.suppress_scale_cb.toggled.connect(self.update_preview)
        rot_layout.addWidget(self.suppress_scale_cb)

        edge_sub_layout = QHBoxLayout()
        edge_sub_layout.addWidget(QLabel("Reading Edge:"))
        self.edge_mode_combo = QComboBox()
        self.edge_mode_combo.addItem("Oberkante (Top)", "top")
        self.edge_mode_combo.addItem("Unterkante (Bottom)", "bottom")
        self.edge_mode_combo.addItem("Mitte (Center)", "center")
        curr_edge = getattr(self.config, "ruler_edge", "top")
        c_idx = self.edge_mode_combo.findData(curr_edge)
        if c_idx >= 0:
            self.edge_mode_combo.setCurrentIndex(c_idx)
        self.edge_mode_combo.currentIndexChanged.connect(self.update_preview)
        edge_sub_layout.addWidget(self.edge_mode_combo)
        rot_layout.addLayout(edge_sub_layout)

        filter_params_layout = QHBoxLayout()
        filter_params_layout.addWidget(QLabel("Kern-Breite:"))
        self.core_width_spin = QSpinBox()
        self.core_width_spin.setRange(10, 100)
        self.core_width_spin.setSingleStep(5)
        self.core_width_spin.setSuffix(" %")
        self.core_width_spin.setValue(int(getattr(self.config, "core_width_pct", 0.6) * 100))
        self.core_width_spin.valueChanged.connect(self.update_preview)
        filter_params_layout.addWidget(self.core_width_spin)

        filter_params_layout.addWidget(QLabel("Min-Höhe:"))
        self.min_height_spin = QSpinBox()
        self.min_height_spin.setRange(2, 200)
        self.min_height_spin.setSuffix(" px")
        self.min_height_spin.setValue(getattr(self.config, "min_float_height", 8))
        self.min_height_spin.valueChanged.connect(self.update_preview)
        filter_params_layout.addWidget(self.min_height_spin)
        rot_layout.addLayout(filter_params_layout)

        self.show_debug_cb = QCheckBox("Debug-Visualisierung anzeigen")
        self.show_debug_cb.setToolTip(
            "Zeigt detaillierte visuelle Diagnose-Informationen: gefundene Peaks, Gradientenwerte, Blobs und Schnittgrenzen."
        )
        self.show_debug_cb.setChecked(False)
        self.show_debug_cb.toggled.connect(self._on_debug_toggled)
        rot_layout.addWidget(self.show_debug_cb)

        self.profile_plot = ProfilePlotWidget()
        self.profile_plot.setVisible(False)
        rot_layout.addWidget(self.profile_plot)

        self.float_status_label = QLabel("Float Position: -")
        self.float_status_label.setWordWrap(True)
        rot_layout.addWidget(self.float_status_label)
        right_layout.addWidget(rotameter_group)

        # Group 3: Manual Masks
        mask_group = QGroupBox("Manual Masks (Occlusion)")
        mask_layout = QVBoxLayout(mask_group)
        self.mask_list = QListWidget()
        self.mask_list.currentRowChanged.connect(self._on_mask_selection_changed)
        mask_layout.addWidget(self.mask_list)

        mask_btn_layout = QHBoxLayout()
        self.del_mask_btn = QPushButton("Delete Selected Mask")
        self.del_mask_btn.clicked.connect(self.delete_selected_mask)
        self.clear_masks_btn = QPushButton("Clear All Masks")
        self.clear_masks_btn.clicked.connect(self.clear_all_masks)
        mask_btn_layout.addWidget(self.del_mask_btn)
        mask_btn_layout.addWidget(self.clear_masks_btn)
        mask_layout.addLayout(mask_btn_layout)

        right_layout.addWidget(mask_group)
        right_layout.addStretch()

        right_scroll.setWidget(right_widget)
        splitter.addWidget(right_scroll)
        splitter.setSizes([750, 400])

        main_layout.addWidget(splitter)

        # Dialog Buttons
        self.buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        main_layout.addWidget(self.buttons)

        self._refresh_mask_list()
        self.update_preview()

    def _on_debug_toggled(self, checked: bool):
        self.update_preview()
        if checked:
            self.fit_view()

    def _on_mode_toggled(self):
        if self.draw_radio.isChecked():
            self.view.set_mode("draw_mask")
        else:
            self.view.set_mode("nav")

    def fit_view(self):
        is_debug = hasattr(self, "show_debug_cb") and self.show_debug_cb.isChecked()
        if is_debug and not self.scene.sceneRect().isEmpty():
            self.view.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)
        elif self.pixmap_item and not self.pixmap_item.pixmap().isNull():
            self.view.fitInView(self.pixmap_item, Qt.KeepAspectRatio)

    def _on_mask_selection_changed(self, row: int):
        selected_row = self.mask_list.currentRow()
        for idx, itm in enumerate(self.mask_outline_items):
            if idx == selected_row:
                pen = QPen(QColor(0, 230, 255), 2, Qt.SolidLine)
            else:
                pen = QPen(QColor(255, 140, 0), 1, Qt.DashLine)
            pen.setCosmetic(True)
            itm.setPen(pen)

    def on_mask_drawn(self, sx: int, sy: int, sw: int, sh: int):
        scale = max(1, self.config.upscale_factor)
        if self.crop is None or self.crop.size == 0:
            return
        crop_h, crop_w = self.crop.shape[:2]
        
        # Convert scene (upscaled) coordinates back to unscaled crop coordinates safely
        x1 = max(0, int(round(sx / scale)))
        y1 = max(0, int(round(sy / scale)))
        x2 = min(crop_w, int(round((sx + sw) / scale)))
        y2 = min(crop_h, int(round((sy + sh) / scale)))
        cw = max(0, x2 - x1)
        ch = max(0, y2 - y1)
        cx = x1
        cy = y1

        if cw > 0 and ch > 0:
            color = 0 if self.mask_color_combo.currentIndex() == 0 else 255
            self.config.masks.append({
                "type": "rect",
                "coords": [cx, cy, cw, ch],
                "color": color
            })
            self._refresh_mask_list()
            self.update_preview()

    def delete_selected_mask(self):
        row = self.mask_list.currentRow()
        if 0 <= row < len(self.config.masks):
            self.config.masks.pop(row)
            self._refresh_mask_list()
            self.update_preview()

    def clear_all_masks(self):
        if self.config.masks:
            self.config.masks.clear()
            self._refresh_mask_list()
            self.update_preview()

    def _refresh_mask_list(self):
        self.mask_list.clear()
        for idx, m in enumerate(self.config.masks):
            coords = m.get("coords", [0, 0, 0, 0])
            col_name = "Black (0)" if m.get("color", 0) == 0 else "White (255)"
            self.mask_list.addItem(f"#{idx+1}: {col_name} - [{coords[0]}, {coords[1]}, {coords[2]}x{coords[3]}]")

    def update_preview(self):
        idx = self.upscale_combo.currentIndex()
        scale_val = [1, 2, 4][idx]
        self.config.upscale_factor = scale_val
        self.config.grayscale = self.gray_cb.isChecked()
        self.config.invert = self.invert_cb.isChecked()
        self.config.threshold_method = self.thresh_combo.currentText()
        self.config.threshold_value = self.thresh_slider.value()
        self.config.blur_kernel = self.blur_slider.value()
        self.config.contrast_alpha = self.alpha_slider.value()
        self.config.contrast_beta = self.beta_slider.value()
        self.config.morphology_op = self.morph_combo.currentText()
        self.config.morphology_shape = self.morph_shape_combo.currentText()
        self.config.morphology_kernel = self.morph_k_slider.value()
        self.config.rotation_angle = self.rot_slider.value()

        if self.crop is None or self.crop.size == 0:
            return

        processed = self.pipeline.process(self.crop, self.config)
        if processed is None or processed.size == 0:
            return

        if len(processed.shape) == 2:
            display_img = cv2.cvtColor(processed, cv2.COLOR_GRAY2RGB)
        else:
            display_img = cv2.cvtColor(processed, cv2.COLOR_BGR2RGB)

        h, w, ch = display_img.shape
        bytes_per_line = ch * w
        q_img = QImage(display_img.data, w, h, bytes_per_line, QImage.Format_RGB888)
        pixmap = QPixmap.fromImage(q_img)
        self.pixmap_item.setPixmap(pixmap)
        self.scene.setSceneRect(0, 0, w, h)

        if not self.first_fit_done and w > 0:
            self.fit_view()
            self.first_fit_done = True

        # Render mask outlines in scene
        for itm in self.mask_outline_items:
            self.scene.removeItem(itm)
        self.mask_outline_items.clear()

        scale = self.config.upscale_factor
        selected_row = self.mask_list.currentRow()
        for idx, m in enumerate(self.config.masks):
            coords = m.get("coords", [])
            if len(coords) == 4:
                mx, my, mw, mh = coords
                sx = mx * scale
                sy = my * scale
                sw = mw * scale
                sh = mh * scale
                rect_item = QGraphicsRectItem(sx, sy, sw, sh)
                if idx == selected_row:
                    pen = QPen(QColor(0, 230, 255), 2, Qt.SolidLine)
                else:
                    pen = QPen(QColor(255, 140, 0), 1, Qt.DashLine)
                pen.setCosmetic(True)
                rect_item.setPen(pen)
                self.scene.addItem(rect_item)
                self.mask_outline_items.append(rect_item)

        # Rotameter Float Line, Float Body Box & Debug Overlays
        if self.float_line_item:
            self.scene.removeItem(self.float_line_item)
            self.float_line_item = None
        if self.float_box_item:
            self.scene.removeItem(self.float_box_item)
            self.float_box_item = None
        if hasattr(self, "debug_overlay_items"):
            for item in self.debug_overlay_items:
                try:
                    self.scene.removeItem(item)
                except Exception:
                    pass
            self.debug_overlay_items.clear()
        else:
            self.debug_overlay_items = []

        is_float_active = self.show_float_cb.isChecked()
        is_debug_active = hasattr(self, "show_debug_cb") and self.show_debug_cb.isChecked()

        if hasattr(self, "profile_plot"):
            self.profile_plot.setVisible(is_debug_active)

        if is_float_active or is_debug_active:
            edge_mode = self.edge_mode_combo.currentData() if hasattr(self, "edge_mode_combo") else "top"
            suppress = self.suppress_scale_cb.isChecked() if hasattr(self, "suppress_scale_cb") else True
            core_pct = (self.core_width_spin.value() / 100.0) if hasattr(self, "core_width_spin") else 0.6
            min_h = self.min_height_spin.value() if hasattr(self, "min_height_spin") else 8

            self.config.ruler_edge = edge_mode
            self.config.suppress_scale_marks = suppress
            self.config.core_width_pct = core_pct
            self.config.min_float_height = min_h

            if is_debug_active:
                debug_info = self.rotameter_reader.detect_float_y(
                    processed,
                    edge_mode=edge_mode,
                    suppress_scale_marks=suppress,
                    core_width_pct=core_pct,
                    min_float_height=int(min_h * scale),
                    return_debug=True
                )
                y_float_scaled = debug_info.get("y_float")
                blob_box = debug_info.get("box")
                debug_mode = debug_info.get("mode", "1d")

                if hasattr(self, "profile_plot"):
                    self.profile_plot.set_data(debug_info)
            else:
                y_float_scaled, blob_box = self.rotameter_reader.detect_float_y(
                    processed,
                    edge_mode=edge_mode,
                    suppress_scale_marks=suppress,
                    core_width_pct=core_pct,
                    min_float_height=int(min_h * scale),
                    return_box=True
                )
                debug_info = None
                debug_mode = "2d" if suppress else "1d"

            if y_float_scaled is not None:
                y_float_unscaled = y_float_scaled / scale
                extra_scaled = f" (scaled: {y_float_scaled:.1f})" if scale > 1 else ""

                # Draw main red float line
                line_item = QGraphicsLineItem(0, y_float_scaled, w, y_float_scaled)
                pen = QPen(QColor(255, 0, 0), 2, Qt.SolidLine)
                pen.setCosmetic(True)
                line_item.setPen(pen)
                self.scene.addItem(line_item)
                self.float_line_item = line_item

                # If 2D blob was detected, draw translucent green bounding box around float body
                if blob_box is not None and (suppress or debug_mode == "2d"):
                    bx, by, bw, bh = blob_box
                    box_item = QGraphicsRectItem(bx, by, bw, bh)
                    box_pen = QPen(QColor(0, 255, 120, 220), 1, Qt.DashLine)
                    box_pen.setCosmetic(True)
                    box_item.setPen(box_pen)
                    box_item.setBrush(QColor(0, 255, 120, 35))
                    self.scene.addItem(box_item)
                    self.float_box_item = box_item

            # Detailed debug rendering in scene
            if is_debug_active and debug_info:
                reason = debug_info.get("reason", "")
                mode_str = "2D-Objektfilter" if debug_mode == "2d" else "1D-Gradient"

                if debug_mode == "1d":
                    # 1D Overlays:
                    # 1. Vertical margin lines
                    mx = int(w * self.rotameter_reader.margin_x_pct)
                    if mx > 0 and w - 2 * mx >= 3:
                        for mx_pos in (mx, w - mx):
                            m_line = QGraphicsLineItem(mx_pos, 0, mx_pos, h)
                            m_pen = QPen(QColor(0, 190, 255, 160), 1, Qt.DashLine)
                            m_pen.setCosmetic(True)
                            m_line.setPen(m_pen)
                            self.scene.addItem(m_line)
                            self.debug_overlay_items.append(m_line)

                    # 2. Horizontal candidate lines
                    candidates = debug_info.get("candidates_1d", [])
                    plot_x = w + 15
                    plot_w = 70
                    line_end_x = plot_x + plot_w
                    label_x = line_end_x + 8

                    for cand in candidates:
                        cy = cand.get("y")
                        g_val = cand.get("grad", 0.0)
                        is_sel = cand.get("selected", False)
                        if cy is not None and not is_sel:
                            c_line = QGraphicsLineItem(0, cy, line_end_x, cy)
                            c_pen = QPen(QColor(255, 140, 0, 190), 1, Qt.DashLine)
                            c_pen.setCosmetic(True)
                            c_line.setPen(c_pen)
                            self.scene.addItem(c_line)
                            self.debug_overlay_items.append(c_line)

                            txt = QGraphicsSimpleTextItem(f"g={g_val:+.1f}")
                            txt.setBrush(QColor(255, 160, 20))
                            txt.setFont(QFont("sans-serif", 7))
                            txt.setPos(label_x, cy - 6)
                            self.scene.addItem(txt)
                            self.debug_overlay_items.append(txt)
                        elif cy is not None and is_sel:
                            # Selected candidate peak: bold red line across tube and curve track
                            c_line = QGraphicsLineItem(0, cy, line_end_x, cy)
                            c_pen = QPen(QColor(255, 40, 40), 2, Qt.SolidLine)
                            c_pen.setCosmetic(True)
                            c_line.setPen(c_pen)
                            self.scene.addItem(c_line)
                            self.debug_overlay_items.append(c_line)

                            txt = QGraphicsSimpleTextItem(f"Float y={cy:.0f} (g={g_val:+.1f})")
                            txt.setBrush(QColor(255, 50, 50))
                            txt.setFont(QFont("sans-serif", 8, QFont.Bold))
                            txt.setPos(label_x, cy - 7)
                            self.scene.addItem(txt)
                            self.debug_overlay_items.append(txt)

                    # Status text
                    if y_float_scaled is not None:
                        self.float_status_label.setText(
                            f"Float Position: y = {y_float_unscaled:.1f} px{extra_scaled} [{mode_str}]\n"
                            f"1D-Peaks: {len(candidates)} gefunden | Kante: {edge_mode}\n"
                            f"Details: {reason}"
                        )
                    else:
                        self.float_status_label.setText(
                            f"Float Position: Not detected [{mode_str}]\n"
                            f"Details: {reason}"
                        )

                else:
                    # 2D Overlays:
                    # 1. Vertical core strip lines
                    core_bounds = debug_info.get("core_bounds")
                    if core_bounds:
                        x_start, x_end = core_bounds
                        for cx_pos in (x_start, x_end):
                            c_line = QGraphicsLineItem(cx_pos, 0, cx_pos, h)
                            c_pen = QPen(QColor(0, 220, 255, 190), 1, Qt.DashLine)
                            c_pen.setCosmetic(True)
                            c_line.setPen(c_pen)
                            self.scene.addItem(c_line)
                            self.debug_overlay_items.append(c_line)

                    # 2. Rejected blobs (faint yellow dotted boxes)
                    blobs = debug_info.get("blobs", [])
                    cand_2d = [b for b in blobs if b.get("is_float")]
                    supp_count = len(blobs) - len(cand_2d)

                    for b in blobs:
                        if not b.get("selected"):
                            bx, by, bw, bh = b["box"]
                            rej_item = QGraphicsRectItem(bx, by, bw, bh)
                            rej_pen = QPen(QColor(255, 215, 0, 160), 1, Qt.DotLine)
                            rej_pen.setCosmetic(True)
                            rej_item.setPen(rej_pen)
                            rej_item.setBrush(QColor(255, 215, 0, 25))
                            self.scene.addItem(rej_item)
                            self.debug_overlay_items.append(rej_item)

                    # Status text
                    if y_float_scaled is not None:
                        self.float_status_label.setText(
                            f"Float Position: y = {y_float_unscaled:.1f} px{extra_scaled} [{mode_str}]\n"
                            f"Blobs: {len(blobs)} ({supp_count} Striche unterdrückt, {len(cand_2d)} Schwimmer)\n"
                            f"Details: {reason}"
                        )
                    else:
                        self.float_status_label.setText(
                            f"Float Position: Not detected [{mode_str}]\n"
                            f"Details: {reason}"
                        )

                # 3. Synchronized Profile & Gradient track beside strip in scene
                profile = debug_info.get("profile")
                grad = debug_info.get("grad_full")
                if grad is None and debug_info.get("gradient") is not None:
                    grad = debug_info.get("gradient")

                if profile is not None and len(profile) == h:
                    plot_x = w + 15
                    plot_w = 70

                    # Background
                    bg_rect = QGraphicsRectItem(plot_x, 0, plot_w, h)
                    bg_pen = QPen(QColor(50, 55, 65, 100), 1)
                    bg_pen.setCosmetic(True)
                    bg_rect.setPen(bg_pen)
                    bg_rect.setBrush(QColor(20, 22, 28, 140))
                    self.scene.addItem(bg_rect)
                    self.debug_overlay_items.append(bg_rect)

                    # Center line for gradient zero
                    zero_line = QGraphicsLineItem(plot_x + plot_w / 2.0, 0, plot_x + plot_w / 2.0, h)
                    zero_pen = QPen(QColor(80, 85, 95, 120), 1, Qt.DotLine)
                    zero_pen.setCosmetic(True)
                    zero_line.setPen(zero_pen)
                    self.scene.addItem(zero_line)
                    self.debug_overlay_items.append(zero_line)

                    # Headers
                    t_prof = QGraphicsSimpleTextItem("I(y)")
                    t_prof.setBrush(QColor(70, 180, 255))
                    t_prof.setFont(QFont("sans-serif", 7))
                    t_prof.setPos(plot_x + 5, -13)
                    self.scene.addItem(t_prof)
                    self.debug_overlay_items.append(t_prof)

                    t_grad = QGraphicsSimpleTextItem("dI/dy")
                    t_grad.setBrush(QColor(255, 160, 30))
                    t_grad.setFont(QFont("sans-serif", 7))
                    t_grad.setPos(plot_x + 35, -13)
                    self.scene.addItem(t_grad)
                    self.debug_overlay_items.append(t_grad)

                    # Paths
                    path_i = QPainterPath()
                    path_g = QPainterPath()
                    cx = plot_x + plot_w / 2.0
                    max_g = float(np.max(np.abs(grad))) if grad is not None and len(grad) > 0 else 1.0
                    if max_g < 1e-3:
                        max_g = 1.0

                    for yi in range(h):
                        ix = plot_x + (float(profile[yi]) / 255.0) * plot_w
                        if yi == 0:
                            path_i.moveTo(ix, yi)
                        else:
                            path_i.lineTo(ix, yi)

                        if grad is not None and yi < len(grad):
                            gx = cx + (float(grad[yi]) / max_g) * (plot_w / 2.0) * 0.9
                            if yi == 0:
                                path_g.moveTo(gx, yi)
                            else:
                                path_g.lineTo(gx, yi)

                    item_i = QGraphicsPathItem(path_i)
                    pen_i = QPen(QColor(70, 180, 255, 200), 1)
                    pen_i.setCosmetic(True)
                    item_i.setPen(pen_i)
                    self.scene.addItem(item_i)
                    self.debug_overlay_items.append(item_i)

                    item_g = QGraphicsPathItem(path_g)
                    pen_g = QPen(QColor(255, 160, 30, 220), 1)
                    pen_g.setCosmetic(True)
                    item_g.setPen(pen_g)
                    self.scene.addItem(item_g)
                    self.debug_overlay_items.append(item_g)

                # Expand scene bounds so all debug overlays, curves, and labels fit comfortably
                items_rect = self.scene.itemsBoundingRect()
                self.scene.setSceneRect(items_rect.adjusted(-10, -20, 20, 10))

            elif not is_debug_active:
                self.scene.setSceneRect(0, 0, w, h)
                if y_float_scaled is not None:
                    filter_status = "2D-Filter: ON" if suppress else "1D-Gradient: ON"
                    self.float_status_label.setText(
                        f"Float Position: y = {y_float_unscaled:.1f} px{extra_scaled} [{filter_status}]"
                    )
                else:
                    self.float_status_label.setText("Float Position: Not detected")
        else:
            self.scene.setSceneRect(0, 0, w, h)
            self.float_status_label.setText("Float Position: -")

    def get_config(self) -> PreprocessingConfig:
        return self.config

