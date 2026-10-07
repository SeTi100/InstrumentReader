"""Enumerates the cameras attached to this machine without opening them.

Opening a device just to probe it can disturb an application that is already using it
(e.g. OBS), so names come from the OS: sysfs on Linux, Qt Multimedia elsewhere. The
index of each entry is the index OpenCV uses for ``cv2.VideoCapture(index)``.
"""
import re
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CameraDevice:
    index: int
    name: str

    @property
    def label(self) -> str:
        return f"{self.index}: {self.name}"


_LABEL_INDEX_RE = re.compile(r"^\s*(\d+)\s*(?::.*)?$")


def parse_source(text: str) -> int | str:
    """Turns the text of the source field into an OpenCV source.

    ``"1"`` and ``"1: Logitech C920"`` both give camera index 1; anything else
    (a video file, ``/dev/video2``, a stream URL) is returned unchanged.
    """
    text = text.strip()
    m = _LABEL_INDEX_RE.match(text)
    return int(m.group(1)) if m else text


def _from_v4l2_sysfs() -> list[CameraDevice]:
    root = Path("/sys/class/video4linux")
    devices = []
    for node in root.glob("video*"):
        m = re.fullmatch(r"video(\d+)", node.name)
        if not m:
            continue
        # Each UVC camera exposes a capture node (index 0) plus metadata nodes.
        try:
            if (node / "index").read_text().strip() != "0":
                continue
        except OSError:
            pass
        try:
            name = (node / "name").read_text().strip()
        except OSError:
            name = node.name
        devices.append(CameraDevice(int(m.group(1)), name or node.name))
    return sorted(devices, key=lambda d: d.index)


def _from_qt() -> list[CameraDevice]:
    from PySide6.QtMultimedia import QMediaDevices

    return [
        CameraDevice(i, dev.description() or f"Kamera {i}")
        for i, dev in enumerate(QMediaDevices.videoInputs())
    ]


def list_cameras() -> list[CameraDevice]:
    """Returns the cameras currently attached, or an empty list if none can be found."""
    sources = [_from_qt]
    if sys.platform.startswith("linux"):
        sources.insert(0, _from_v4l2_sysfs)
    for source in sources:
        try:
            devices = source()
        except Exception:
            continue
        if devices:
            return devices
    return []
