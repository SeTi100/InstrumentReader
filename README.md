# InstrumentReader

## Tesseract einrichten

Die App braucht ein installiertes [Tesseract](https://github.com/tesseract-ocr/tesseract)
(Windows: z. B. der UB-Mannheim-Installer) und für Sieben-Segment-Anzeigen das
Sprachmodell `lets.traineddata` im `tessdata`-Ordner.

Tesseract wird in dieser Reihenfolge gesucht:

1. Umgebungsvariable `INSTRUMENT_READER_TESSERACT` (voller Pfad zu `tesseract`/`tesseract.exe`)
2. `tesseract` im `PATH`
3. `C:\Program Files\Tesseract-OCR\tesseract.exe`

Der `tessdata`-Ordner kann mit `INSTRUMENT_READER_TESSDATA` (oder `TESSDATA_PREFIX`) gesetzt werden.

### Schnellere OCR mit tesserocr (optional)

Mit dem Paket `tesserocr` läuft Tesseract direkt im Programm, die Modelle bleiben
geladen und ein Frame ist über 15-mal schneller als mit dem `tesseract`-Programm:

```
uv sync --extra fast        # Linux / macOS
conda install -c conda-forge tesserocr   # Windows (PyPI hat dort keine fertigen Pakete)
```

Ist `tesserocr` installiert, wird es automatisch benutzt. Erzwingen lässt sich das
Backend mit `INSTRUMENT_READER_OCR_BACKEND=tesserocr` bzw. `=cli`.

Zeitmessung: `python scripts/benchmark_ocr.py`
