# Init
import os

# Opening a webcam via Media Foundation (OpenCV's default backend on Windows) can take
# several seconds with hardware transforms enabled; disabling them makes (re)connects fast.
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")
# OpenCV prints a warning for every failed grab of a camera that another program holds
# (dozens per second during reconnects). The app reports camera problems in the GUI and in
# kamera.log instead; set OPENCV_LOG_LEVEL=INFO to see OpenCV's own diagnostics again.
# Set here, before cv2 is imported, so camera helper processes inherit it.
os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")
