"""Enumerates the cameras attached to this machine without opening them.

Opening a device just to probe it can disturb an application that is already using it
(e.g. OBS), so names come from the OS: Media Foundation and DirectShow on Windows (via
cv2_enumerate_cameras), sysfs on Linux, Qt Multimedia as fallback. The index of each
entry is the source OpenCVCamera takes; indices of 100 and up carry OpenCV's backend
offset (701 = DirectShow device 1).
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


def _from_windows() -> list[CameraDevice]:
    """Media Foundation cameras by plain index, plus DirectShow-only ones (virtual
    cameras such as the OBS Virtual Camera) by OpenCV's encoded index, e.g. 701."""
    import cv2
    from cv2_enumerate_cameras import enumerate_cameras

    msmf = enumerate_cameras(cv2.CAP_MSMF)
    devices = [CameraDevice(c.index, c.name or f"Kamera {c.index}") for c in msmf]
    known = {c.name for c in msmf}
    devices += [
        CameraDevice(cv2.CAP_DSHOW + c.index, c.name or f"DirectShow-Kamera {c.index}")
        for c in enumerate_cameras(cv2.CAP_DSHOW)
        if c.name not in known
    ]
    return devices


def list_cameras() -> list[CameraDevice] | None:
    """Returns the cameras currently attached ([] if none), or None if they cannot be listed."""
    sources = [_from_qt]
    if sys.platform.startswith("linux"):
        sources.insert(0, _from_v4l2_sysfs)
    elif sys.platform.startswith("win"):
        sources.insert(0, _from_windows)
    result = None
    for source in sources:
        try:
            devices = source()
        except Exception:
            continue
        if devices:
            return devices
        result = []
    return result


def label_name(text: str) -> str | None:
    """The camera name from a dropdown label like "1: Logitech C920", else None."""
    _, sep, name = text.partition(":")
    if sep and _LABEL_INDEX_RE.match(text) and name.strip():
        return name.strip()
    return None
