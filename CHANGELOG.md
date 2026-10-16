# Changelog

Alle nennenswerten Änderungen an InstrumentReader. Neueste Einträge oben.
Gruppiert nach Datum, solange die Versionsnummer bei 0.1.0 steht.

## 2026-10-17

### Ablesung → Excel
- **Knopf „Ablesung“ (auch Taste F9)**: speichert den aktuellen Wert des gewählten Waagen-ROIs mit
  der Uhrzeit des Klicks. Klicks wechseln zwischen Start (Masse_0/Zeit_0) und Ende (Masse_1/Zeit_1),
  ein Messschritt ergibt eine Zeile `Masse_0 | Masse_1 | Zeit_0 | Zeit_1` wie Spalten D:G im Labjournal.
  „Neue Zeile“ beginnt die nächste Zeile, auch wenn Masse_1 fehlt.
- Pro Tag `ablesungen/Ablesungen_JJJJ-MM-TT.csv` (nur anhängend, Grundlage nach Neustart) und
  `Ablesungen_JJJJ-MM-TT.xlsx` (Blätter „Ablesungen“ und „Protokoll“). Ist die xlsx in Excel geöffnet,
  bleibt der Klick in der CSV und die xlsx wird beim nächsten Klick nachgetragen.
- Kein gültiger Waagenwert: die Zeit wird trotzdem gespeichert, der Wert rot markiert.
- Neue Abhängigkeit `openpyxl`.

## 2026-10-15

### Runs und Experimente
- **Knopf „Run beenden“**: beendet die Speicherung in den laufenden Run; das Experiment bleibt offen
  für einen neuen Run. Kamera und Videoaufnahme laufen weiter. Mit Rückfrage.
- **Knopf „Experiment beenden“**: beendet den laufenden Run und schließt das Experiment.
- Unter den Knöpfen steht immer, ob gerade Messwerte gespeichert werden.
- Runs haben jetzt einen Status (`running` / `completed` / `aborted`), einen Ende-Grund und die
  Zeit des ersten und letzten Messwerts (bei Videos die Videozeit). Jeder Start, Stopp,
  Fortsetzen und Abbruch steht in der neuen Tabelle `run_events`.
- **Beendete Runs fortsetzen** im Runs Dashboard, mit Rückfrage; die Lücke bleibt in
  `run_events` sichtbar. Ein geschlossenes Experiment wird dafür wieder geöffnet.
  Der vorher laufende Run wird dabei sauber beendet (vorher blieb er offen).
- **Nach einem Absturz** markiert die App beim Start noch laufende Runs als abgebrochen
  (Ende = letzter Messwert) und sagt das an.
- Behoben: Startzeiten waren UTC, Endzeiten Ortszeit (Runs wirkten 2 h falsch lang). Alle Zeiten
  sind jetzt Ortszeit; bestehende Datenbanken werden beim Start einmalig umgestellt (nach dem Start-Backup).
## 2026-10-12

### Datensicherheit
- **Messdaten liegen außerhalb des Programmordners**, in `%USERPROFILE%\InstrumentReader-Daten\`
  (`INSTRUMENT_READER_DATA_DIR`). Auslöser: Beim Wechsel auf einen alten `main`-Stand
  hat Git die aus Git genommene Datenbank im Programmordner überschrieben, der folgende
  `git pull` hat sie gelöscht. Die App startet nicht, wenn der Datenordner in einem Git-Ordner liegt.
- Eine alte `instrument_reader.db` im Startordner wird beim ersten Start **kopiert**, nie verschoben.
- **Nur anhängbares Journal** (`journal/JJJJ-MM-TT.jsonl`) aller Schreibvorgänge, vergangene
  Tage schreibgeschützt; `scripts/rebuild_db.py` baut daraus die Datenbank neu auf.
- **Automatische Backups** beim Start, nach jedem Run, alle 30 min und beim Schließen, ausgedünnt
  nach Tag und Woche; optional zusätzlich in einen zweiten Ordner (`INSTRUMENT_READER_BACKUP_MIRROR`).
- Fehlt die Datenbank beim Start oder enthält sie weniger als beim letzten Mal, fragt die App
  nach und bietet das neueste Backup an.
- Aufnahmen liegen ebenfalls im Datenordner. Der Fenstertitel zeigt, welcher Datenordner benutzt wird.
- Tests benutzen immer einen eigenen temporären Datenordner.

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
