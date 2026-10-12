# Changelog

Alle nennenswerten Änderungen an InstrumentReader. Neueste Einträge oben.
Gruppiert nach Datum, solange die Versionsnummer bei 0.1.0 steht.

## 2026-10-12

### Kamera
- **Live-Kameras laufen in einem eigenen Hilfsprozess.** Stürzt der Kameratreiber
  oder der Windows-Kameradienst (Frame Server) ab, stirbt nur der Hilfsprozess. Die
  App startet einen neuen, Messung, Aufnahme und Oberfläche laufen weiter. Vorher
  konnte ein solcher Absturz die ganze App mitreißen (`0xc0000374`). Mit
  `INSTRUMENT_READER_CAMERA_HELPER=0` wird die Kamera wie früher im App-Prozess gelesen.
- **Ersatzweg bei hängender Neuverbindung:** Scheitern 3 Verbindungsversuche
  hintereinander, versucht die App dieselbe Kamera (per Name) über den anderen
  Windows-Treiberweg, also DirectShow statt Media Foundation oder umgekehrt. Die
  Statuszeile zeigt dann „Kamera verbunden (Ersatzweg: …)“.
- Behoben: Bilder in Kameragröße (z. B. 640×480) ließen den Hilfsprozess bei
  jedem Bild abstürzen, die Verbindung sprang zwischen „unterbrochen“ und „verbunden“.

### Aufnahme
- **Aufnahmen sind jetzt MKV statt MP4.** Eine MKV bleibt nach Absturz oder
  Stromausfall lesbar, eine MP4 war dann komplett verloren.
- **Zeitstempel pro Bild:** Neben jedem Video liegt `<name>.timestamps.csv` mit
  der Aufnahmezeit jedes Bildes (etwa 19 MB für 8 h). Beim Abspielen nutzt die App
  diese Zeiten, Lücken durch Kameraausfälle behalten ihre echte Länge. Vorher lag
  nach einer Lücke alles zu früh. Ältere Aufnahmen ohne diese Datei laufen wie bisher.
- Bilder, die mit anderer Größe ankommen, werden angepasst statt stillschweigend
  weggelassen.

### OCR
- Schriftart pro ROI wählbar: „7-Segment / LCD“ (Modell `lets`) oder „Normale
  Schrift“ (Modell `eng`). `lets` hatte normale Ziffern verlesen.
- TSV-Ausgabe von Tesseract funktioniert auch mit `tessdata`-Ordnern ohne `configs/`.

## 2026-10-07

### Kamera
- Live-Kamera läuft in einer überwachten Sitzung: Hängt oder verschwindet sie,
  verbindet die App automatisch neu und gibt das Gerät für andere Programme frei.
- Kamerawahl als Dropdown mit Gerätenamen, auch für reine DirectShow-Kameras wie
  die OBS Virtual Camera (Index ab 700, z. B. `701`). Jeder Index lässt sich auch eintippen.
- Die App folgt der gewählten Kamera per Name, auch wenn Windows die Geräte neu
  nummeriert, und verbindet sich nie mit einem Platzhalter-Gerät.

### Wiedergabe und Zeit
- Kein künstliches Bremsen mehr: Live-Kameras laufen mit ihrer echten Bildrate,
  Videos im Takt der Videozeit.
- Wiedergabegeschwindigkeit wählbar (0,5× bis 10× und „Max“). Bei „Max“ wartet
  die Kamera auf die OCR, es wird kein Messintervall übersprungen.
- Bei Videos wird die Videozeit statt der Rechnerzeit geloggt, Raten aus der
  Offline-Auswertung stimmen bei jeder Geschwindigkeit.

### OCR
- Deutlich schneller: alle Ziffern-Slots in einem Tesseract-Aufruf, optional
  `tesserocr` im Prozess (Extra `fast`).
- Tesseract-Pfad konfigurierbar (`INSTRUMENT_READER_TESSERACT`), Debug-Ausgaben
  über `logging` statt Logdatei pro Aufruf.
- `scripts/benchmark_ocr.py` zum Messen.

## 2026-10-05
- Runs-Dashboard, CSV-Export über mehrere Runs (Rohdaten und Pivot),
  frei definierbare Run-Parameter, LED-Anzeige der aktuellen Stufe.

## 2026-10-01
- Rechenkanäle mit Formeln (per Drag & Drop), Rotameter-Schwimmer-Diagnose.
- Stufenerkennung für Waagen (ScaleStepDetector), Videoaufnahme, Wiedergabe mit
  Vor-/Zurückspulen (Zurück nur nach Bestätigung).

## 2026-09-30
- Erste Version.
