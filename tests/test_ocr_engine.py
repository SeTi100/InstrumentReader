import importlib.util

import cv2
import numpy as np
import pytest

from instrument_reader.core import ocr_engine
from instrument_reader.core.ocr_engine import (
    OCRResult, TesseractEngine, parse_tesseract_config, resolve_tesseract_cmd,
)
from instrument_reader.core.roi import ROIConfig
from instrument_reader.gui.ocr_worker import OCRWorker

HAS_TESSERACT = resolve_tesseract_cmd() is not None
HAS_TESSEROCR = importlib.util.find_spec("tesserocr") is not None


def test_parse_tesseract_config():
    lang, psm, variables = parse_tesseract_config("--psm 10 -l lets -c tessedit_char_whitelist=0123456789-")
    assert (lang, psm, variables) == ("lets", 10, {"tessedit_char_whitelist": "0123456789-"})
    assert parse_tesseract_config("--psm 7") == ("eng", 7, {})


def test_resolve_tesseract_cmd_order(monkeypatch):
    monkeypatch.setattr(ocr_engine.shutil, "which", lambda name: "/usr/bin/tesseract")
    monkeypatch.delenv(ocr_engine.ENV_TESSERACT_CMD, raising=False)
    assert resolve_tesseract_cmd() == "/usr/bin/tesseract"
    monkeypatch.setenv(ocr_engine.ENV_TESSERACT_CMD, r"D:\Tools\tesseract.exe")
    assert resolve_tesseract_cmd() == r"D:\Tools\tesseract.exe"
    assert resolve_tesseract_cmd("/opt/tess") == "/opt/tess"


def test_missing_tesseract_returns_failed_results(monkeypatch):
    monkeypatch.setattr(ocr_engine, "resolve_tesseract_cmd", lambda cmd=None: None)
    engine = TesseractEngine(backend="cli")
    results = engine.recognize_many([np.zeros((10, 10), np.uint8)] * 2)
    assert [r.success for r in results] == [False, False]
    assert all(r.raw_text == "" for r in results)


class BatchRecordingEngine:
    """Liefert je Konfiguration vorgegebene Texte pro Bildbreite und merkt sich die Batches."""

    def __init__(self, answers):
        self.answers = answers  # config -> {width: text}
        self.batches = []

    def recognize_many(self, images, config=None):
        self.batches.append((config, [img.shape[1] for img in images]))
        out = []
        for img in images:
            text = self.answers.get(config, {}).get(img.shape[1], "")
            out.append(OCRResult(raw_text=text, parsed_value=None, confidence=0.9, success=True))
        return out


def test_slot_fallback_only_reruns_missing_slots():
    worker = OCRWorker()
    container = ROIConfig(name="Waage", is_container=True, decimal_position=2,
                          coordinates=[0, 0, 300, 100], sub_roi_ids=["S1", "S2", "S3"])
    s1 = ROIConfig(name="S1", coordinates=[10, 10, 41, 70])
    s2 = ROIConfig(name="S2", coordinates=[60, 10, 42, 70])
    s3 = ROIConfig(name="S3", coordinates=[110, 10, 43, 70])
    first = "--psm 10 -l lets -c tessedit_char_whitelist=0123456789-"
    second = "--psm 10 -c tessedit_char_whitelist=0123456789-"
    worker.engine = BatchRecordingEngine({
        first: {41: "1", 43: "3"},
        second: {42: "2"},
    })

    readings = worker.process_frame(np.zeros((200, 300, 3), np.uint8), [container, s1, s2, s3])

    assert worker.engine.batches == [(first, [41, 42, 43]), (second, [42])]
    composite = next(r for r in readings if r["roi_name"] == "Waage")
    assert composite["parsed_value"] == 12.3


def _digit_image(ch: str) -> np.ndarray:
    img = np.full((90, 60), 255, np.uint8)
    cv2.putText(img, ch, (8, 75), cv2.FONT_HERSHEY_SIMPLEX, 2.6, 0, 6)
    return img


@pytest.mark.skipif(not HAS_TESSERACT, reason="tesseract nicht installiert")
def test_cli_batch_matches_single_calls():
    engine = TesseractEngine(backend="cli")
    cfg = "--psm 10 -c tessedit_char_whitelist=0123456789"
    images = [_digit_image(ch) for ch in "1543"]
    batch = [r.raw_text for r in engine.recognize_many(images, cfg)]
    single = [engine.recognize(img, cfg).raw_text for img in images]
    assert batch == single == ["1", "5", "4", "3"]


@pytest.mark.skipif(not (HAS_TESSERACT and HAS_TESSEROCR), reason="tesserocr nicht installiert")
def test_tesserocr_matches_cli():
    cfg = "--psm 10 -c tessedit_char_whitelist=0123456789"
    images = [_digit_image(ch) for ch in "1543"]
    cli = [r.raw_text for r in TesseractEngine(backend="cli").recognize_many(images, cfg)]
    fast = [r.raw_text for r in TesseractEngine(backend="tesserocr").recognize_many(images, cfg)]
    assert fast == cli
