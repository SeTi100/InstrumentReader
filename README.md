# InstrumentReader

Liest Laborgeräte (Waagen, Rotameter, 7-Segment-Anzeigen) per Kamera ab, erkennt
die Werte mit OCR und loggt sie live in eine SQLite-Datenbank. Aufnahmen lassen
sich später offline mit derselben Zeitachse auswerten.

## Installation und Start

Voraussetzungen: Python ≥ 3.11, [uv](https://docs.astral.sh/uv/) und Tesseract
(siehe unten).

```
uv run instrument-reader
```

`uv` legt beim ersten Start die Umgebung in `.venv` an. Tests laufen mit:

```
uv run --extra dev pytest
```

## Bedienung im Überblick

- **Quelle:** Kamera im Dropdown wählen (mit Gerätenamen), einen Index eintippen
  oder einen Videopfad eingeben. Reine DirectShow-Kameras wie die OBS Virtual
  Camera stehen unter Indizes ab 700 (`701` = DirectShow-Gerät 1).
- **ROIs:** Bereiche als Rechteck, Polygon oder Lineal (Rotameter) einzeichnen.
  Pro ROI lässt sich die Schriftart für die OCR wählen (siehe unten). ROI-Sätze
  lassen sich unter *File → Save/Load ROI Preset* speichern und laden.
- **Rechenkanäle:** Formeln aus den gemessenen Kanälen bilden, z. B. Raten.
- **Experimente und Runs:** *New Experiment* / *New Run* gliedern die Messung.
  Die Stufenerkennung für Waagen zeigt die aktuelle Stufe an (*Reset Stage* setzt
  sie zurück). Das *Runs Dashboard* vergleicht Runs und exportiert sie als CSV.
- **Wiedergabe:** Bei Videos Geschwindigkeit 0,5× bis 10× oder „Max“ (so schnell
  wie möglich, ohne Messintervalle zu überspringen). Zurückspulen muss erst
  freigeschaltet werden.

Die Messwerte landen in `instrument_reader.db` im Startordner.

## Kamera

Live-Kameras werden in einem eigenen Hilfsprozess gelesen. Stürzt der
Kameratreiber oder der Windows-Kameradienst ab, endet nur dieser Prozess. Die App
startet ihn neu, Messung und Aufnahme laufen weiter.

Fällt die Kamera aus, verbindet die App automatisch neu. Sie folgt der Kamera per
Name, auch wenn Windows die Geräte neu nummeriert. Klappt die Verbindung 3-mal
nicht, versucht sie dieselbe Kamera über den anderen Windows-Treiberweg
(DirectShow bzw. Media Foundation). Die Statuszeile zeigt das an.

`INSTRUMENT_READER_CAMERA_HELPER=0` liest die Kamera ohne Hilfsprozess direkt in
der App (nur zur Fehlersuche).

Auf Windows sollte der Grafiktreiber aktuell sein: Der Windows-Kameradienst nutzt
ihn, und ein alter AMD-Treiber hat ihn wiederholt abstürzen lassen. Während einer
Messung die eingebaute Laptop-Kamera nicht ein- oder ausschalten, das kann den
Dienst für alle Kameras abstürzen lassen.

## Aufnahme

*Record* speichert das Kamerabild in `recordings/recording_<Datum>_<Uhrzeit>.mkv`.
MKV bleibt auch nach einem Absturz oder Stromausfall lesbar.

Daneben liegt `<name>.timestamps.csv` mit der Aufnahmezeit jedes Bildes (etwa
19 MB für 8 h bei 30 fps). Beim Abspielen nutzt die App diese Zeiten, damit
Lücken (z. B. durch einen Kameraausfall) ihre echte Länge behalten. Beim Kopieren
oder Verschieben einer Aufnahme die CSV mitnehmen und im selben Ordner lassen.
Ohne sie rechnet die App die Zeit aus Dateiname und Bildnummer.

## Tesseract einrichten

Die App braucht ein installiertes [Tesseract](https://github.com/tesseract-ocr/tesseract)
(Windows: z. B. der UB-Mannheim-Installer) und für Sieben-Segment-Anzeigen das
Sprachmodell `lets.traineddata` im `tessdata`-Ordner.

Pro ROI lässt sich im ROI-Dialog unter „Schriftart (OCR)" wählen, welches Modell
benutzt wird: „7-Segment / LCD" nimmt `lets`, „Normale Schrift" (z. B. Waagen mit
normalen Ziffern) nimmt `eng`. `lets` verliest normale Ziffern, etwa eine 4 als 0 oder 1.

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

## Umgebungsvariablen

| Variable | Wirkung |
|---|---|
| `INSTRUMENT_READER_TESSERACT` | Pfad zu `tesseract`/`tesseract.exe` |
| `INSTRUMENT_READER_TESSDATA` | Ordner mit `*.traineddata` |
| `INSTRUMENT_READER_OCR_BACKEND` | `auto` (Standard), `tesserocr` oder `cli` |
| `INSTRUMENT_READER_CAMERA_HELPER` | `0`: Kamera ohne Hilfsprozess lesen |

Änderungen stehen in [CHANGELOG.md](CHANGELOG.md).
