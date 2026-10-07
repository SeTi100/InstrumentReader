from abc import ABC, abstractmethod
from dataclasses import dataclass
import logging
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Umgebungsvariablen zur Konfiguration
ENV_TESSERACT_CMD = "INSTRUMENT_READER_TESSERACT"       # Pfad zu tesseract(.exe)
ENV_TESSDATA_DIR = "INSTRUMENT_READER_TESSDATA"         # Ordner mit *.traineddata
ENV_OCR_BACKEND = "INSTRUMENT_READER_OCR_BACKEND"       # auto | tesserocr | cli

WINDOWS_DEFAULT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


@dataclass
class OCRResult:
    """Ergebnis einer OCR-Erkennung."""
    raw_text: str
    parsed_value: float | None
    confidence: float
    success: bool


class OCREngine(ABC):
    @abstractmethod
    def recognize(self, image: np.ndarray, config: str | None = None) -> OCRResult: ...

    def recognize_many(self, images: list[np.ndarray], config: str | None = None) -> list[OCRResult]:
        """Erkennt mehrere Bilder mit derselben Konfiguration (Reihenfolge bleibt erhalten)."""
        return [self.recognize(img, config=config) for img in images]


def resolve_tesseract_cmd(cmd: str | None = None) -> str | None:
    """Findet die Tesseract-Programmdatei.

    Reihenfolge: explizites Argument, Umgebungsvariable
    INSTRUMENT_READER_TESSERACT, ``tesseract`` im PATH, Windows-Standardpfad.
    """
    for candidate in (cmd, os.environ.get(ENV_TESSERACT_CMD)):
        if candidate:
            return candidate
    found = shutil.which("tesseract")
    if found:
        return found
    if os.path.isfile(WINDOWS_DEFAULT_CMD):
        return WINDOWS_DEFAULT_CMD
    return None


def resolve_tessdata_dir(tessdata_dir: str | None, tesseract_cmd: str | None) -> str | None:
    """Ordner mit den Sprachmodellen; None überlässt die Suche Tesseract selbst."""
    for candidate in (tessdata_dir, os.environ.get(ENV_TESSDATA_DIR), os.environ.get("TESSDATA_PREFIX")):
        if candidate:
            return candidate
    if tesseract_cmd:
        exe = shutil.which(tesseract_cmd) or tesseract_cmd
        sibling = os.path.join(os.path.dirname(os.path.abspath(exe)), "tessdata")
        if os.path.isdir(sibling):
            return sibling
        # Linux-Pakete legen tessdata woanders ab: Tesseract selbst fragen
        try:
            out = subprocess.run([tesseract_cmd, "--list-langs"], capture_output=True,
                                 text=True, timeout=10).stdout
            match = re.search(r'"(.+?)"', out)
            if match and os.path.isdir(match.group(1)):
                return match.group(1)
        except (OSError, subprocess.SubprocessError):
            pass
    return None


def parse_tesseract_config(config: str) -> tuple[str, int | None, dict[str, str]]:
    """Zerlegt einen Tesseract-CLI-Configstring in (Sprache, PSM, Variablen)."""
    lang, psm, variables = "eng", None, {}
    tokens = shlex.split(config, posix=True)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "-l" and i + 1 < len(tokens):
            lang = tokens[i + 1]
            i += 1
        elif tok == "--psm" and i + 1 < len(tokens):
            psm = int(tokens[i + 1])
            i += 1
        elif tok == "-c" and i + 1 < len(tokens):
            key, _, value = tokens[i + 1].partition("=")
            variables[key] = value
            i += 1
        i += 1
    return lang, psm, variables


def _build_result(words: list[str], confs: list[float]) -> OCRResult:
    text = " ".join(words)
    text_clean = text.replace(" ", "").replace(".", "").replace(",", "")
    nums = re.findall(r'-?\d+', text_clean)
    parsed = float(nums[0]) if nums else None
    conf = sum(confs) / len(confs) / 100.0 if confs else 0.0
    logger.debug("OCR raw=%r nums=%s conf=%.2f", text, nums, conf)
    return OCRResult(raw_text=text, parsed_value=parsed, confidence=conf, success=True)


class TesseractEngine(OCREngine):
    """Tesseract-OCR.

    Backends:
    - ``tesserocr``: Tesseract direkt im Prozess, Modelle bleiben geladen
      (schnellste Variante, optionales Paket ``tesserocr``).
    - ``cli``: ruft ``tesseract`` als Programm auf. ``recognize_many`` übergibt
      alle Bilder in einem einzigen Aufruf (Bildliste), sodass Prozessstart und
      Modell-Laden nur einmal pro Durchlauf anfallen.

    ``auto`` (Standard) nimmt ``tesserocr``, wenn installiert, sonst ``cli``.
    """

    def __init__(self, config: str = "--psm 7 -l lets -c tessedit_char_whitelist=0123456789.-",
                 tesseract_cmd: str | None = None, tessdata_dir: str | None = None,
                 backend: str | None = None):
        self._config = config
        self.tesseract_cmd = resolve_tesseract_cmd(tesseract_cmd)
        self.tessdata_dir = resolve_tessdata_dir(tessdata_dir, self.tesseract_cmd)
        self.backend = self._select_backend(backend or os.environ.get(ENV_OCR_BACKEND, "auto"))
        self._apis: dict[str, object] = {}
        self._warned: set[tuple[str, str]] = set()
        logger.info("OCR backend: %s (tesseract=%s, tessdata=%s)",
                    self.backend, self.tesseract_cmd, self.tessdata_dir)

    @staticmethod
    def _select_backend(requested: str) -> str:
        if requested not in ("auto", "tesserocr", "cli"):
            raise ValueError(f"Unbekanntes OCR-Backend: {requested!r}")
        if requested == "cli":
            return requested
        try:
            import tesserocr  # noqa: F401
            return "tesserocr"
        except ImportError:
            if requested == "tesserocr":
                raise
            return "cli"

    def recognize(self, image: np.ndarray, config: str | None = None) -> OCRResult:
        return self.recognize_many([image], config)[0]

    def recognize_many(self, images: list[np.ndarray], config: str | None = None) -> list[OCRResult]:
        if not images:
            return []
        cfg = config or self._config
        try:
            if self.backend == "tesserocr":
                return [self._recognize_tesserocr(img, cfg) for img in images]
            return self._recognize_cli(images, cfg)
        except Exception as e:
            if (cfg, str(e)) not in self._warned:  # nur einmal pro Fehler, nicht pro Frame
                self._warned.add((cfg, str(e)))
                logger.warning("OCR-Fehler (%s): %s", cfg, e)
            return [OCRResult(raw_text="", parsed_value=None, confidence=0.0, success=False) for _ in images]

    def _recognize_cli(self, images: list[np.ndarray], cfg: str) -> list[OCRResult]:
        if not self.tesseract_cmd:
            raise RuntimeError(f"Tesseract nicht gefunden (PATH oder {ENV_TESSERACT_CMD} setzen)")
        with tempfile.TemporaryDirectory(prefix="ir_ocr_") as tmp:
            paths = []
            for i, img in enumerate(images):
                path = os.path.join(tmp, f"{i}.png")
                if img.ndim == 3 and img.shape[2] == 3:
                    img = img[:, :, ::-1]  # wie bisher (pytesseract/PIL): Kanäle als RGB lesen
                if not cv2.imwrite(path, img):
                    raise RuntimeError(f"Konnte OCR-Bild nicht schreiben: {path}")
                paths.append(path)
            if len(paths) == 1:
                source = paths[0]
            else:
                # Tesseract liest eine Textdatei mit Bildpfaden als mehrseitiges Dokument
                source = os.path.join(tmp, "images.txt")
                with open(source, "w", encoding="utf-8") as f:
                    f.write("\n".join(paths) + "\n")

            cmd = [self.tesseract_cmd, source, "stdout"]
            if self.tessdata_dir:
                cmd += ["--tessdata-dir", self.tessdata_dir]
            cmd += shlex.split(cfg, posix=not sys.platform.startswith("win")) + ["tsv"]
            proc = subprocess.run(
                cmd, capture_output=True, timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip() or f"tesseract exit {proc.returncode}")

        words: list[list[str]] = [[] for _ in images]
        confs: list[list[float]] = [[] for _ in images]
        lines = proc.stdout.decode("utf-8", "replace").splitlines()
        for line in lines[1:]:  # erste Zeile = Spaltenköpfe
            cols = line.split("\t")
            if len(cols) < 12:
                continue
            page = int(cols[1]) - 1
            if not 0 <= page < len(images):
                continue
            if cols[11].strip():
                words[page].append(cols[11])
            conf = float(cols[10])
            if conf != -1:
                confs[page].append(conf)
        return [_build_result(w, c) for w, c in zip(words, confs)]

    def _recognize_tesserocr(self, image: np.ndarray, cfg: str) -> OCRResult:
        api = self._get_api(cfg)
        img = np.ascontiguousarray(image, dtype=np.uint8)
        h, w = img.shape[:2]
        bpp = 1 if img.ndim == 2 else img.shape[2]
        api.SetImageBytes(img.tobytes(), w, h, bpp, w * bpp)
        words = api.GetUTF8Text().split()
        confs = [float(c) for c in api.AllWordConfidences()]
        return _build_result(words, confs)

    def _get_api(self, cfg: str):
        """Eine geladene Tesseract-Instanz pro Configstring (Modell bleibt im Speicher)."""
        if cfg in self._apis:
            api = self._apis[cfg]
            if api is None:
                raise RuntimeError("Tesseract konnte für diese Konfiguration nicht starten")
            return api
        try:
            import tesserocr
            lang, psm, variables = parse_tesseract_config(cfg)
            kwargs = {"lang": lang}
            if self.tessdata_dir:
                kwargs["path"] = self.tessdata_dir
            if psm is not None:
                kwargs["psm"] = psm
            api = tesserocr.PyTessBaseAPI(**kwargs)
            for key, value in variables.items():
                api.SetVariable(key, value)
        except Exception:
            self._apis[cfg] = None  # nicht bei jedem Frame erneut versuchen
            raise
        self._apis[cfg] = api
        return api
