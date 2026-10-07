# Init
import os

# Opening a webcam via Media Foundation (OpenCV's default backend on Windows) can take
# several seconds with hardware transforms enabled; disabling them makes (re)connects fast.
os.environ.setdefault("OPENCV_VIDEOIO_MSMF_ENABLE_HW_TRANSFORMS", "0")
