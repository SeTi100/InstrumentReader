# Mehrere Quellen gleichzeitig – Recherche und Plan

Stand: 2026-10-12. Grundlage: aktueller `main` (nach PR #9) und ein Test auf dem
Messlaptop (HP ENVY x360, Windows 11).

## Ziel

- Mehrere Quellen gleichzeitig offen: Kameras (z. B. PC-LM1E und die eingebaute
  HP-Kamera), Videodateien und **Anwendungsfenster** (z. B. PicoLog).
- Jede Quelle hat ihre eigene Ansicht mit eigenen ROIs, alle werden gleichzeitig ausgewertet.
- Ansichten standardmäßig nebeneinander (die Kamera ist breit), frei anordenbar, auch
  Bild-in-Bild (eine Quelle klein über einer anderen).

## Was heute an „einer Quelle“ hängt

| Stelle | Heute |
|---|---|
| `MainWindow` | genau ein `self.camera`, `self.camera_thread`, `self.video_widget`, `self.recorder`, `self.ocr_worker` (rund 100 Verweise) |
| `ROIConfig` (`core/roi.py`) | keine Zuordnung zu einer Quelle |
| `OCRWorker` | bekommt Bilder einer Quelle und alle ROIs |
| `ControlPanel` | ein Quellenfeld, ein Start/Stop, ein Record |
| Datenbank `readings` | Schlüssel ist `roi_name`, keine Quelle |
| `CalculationEngine` | arbeitet nur mit Kanalnamen, **unabhängig von der Quelle** |

Die Rechenkanäle funktionieren damit quellenübergreifend ohne Änderung, solange die
Kanalnamen eindeutig bleiben (z. B. Formel aus Waage auf Kamera 1 und Temperatur aus PicoLog).

## Anwendungsfenster als Quelle

Getestet mit dem laufenden PicoLog (`PicoLog.exe`, Fenster „PicoLog - Erfassung - Diagramm“):

- `PrintWindow(hwnd, PW_RENDERFULLCONTENT)` über `ctypes`, **ohne neue Abhängigkeit**:
  Bild 1000×600, korrekt (Diagramm und alle 8 Kanalwerte gut lesbar), **18 ms pro Bild**.
- Die Werte sind normale Ziffern, also ROI-Schriftart „Normale Schrift“ (Modell `eng`).
- PicoLog ist eine Web-/Electron-Oberfläche (Fenstertitel „[object Object]“ im Bild).
  Genau für solche Fenster braucht es `PW_RENDERFULLCONTENT`, sonst kommt Schwarz.

| Methode | Verdecktes Fenster | Minimiert | Abhängigkeit | Bemerkung |
|---|---|---|---|---|
| `PrintWindow` + `PW_RENDERFULLCONTENT` | laut WebRTC ja, **noch zu prüfen** | nein | keine (ctypes) | getestet: funktioniert mit PicoLog |
| Windows Graphics Capture (`windows-capture` 2.0.1, MIT) | ja (laut Doku/Berichten) | widersprüchliche Angaben | Paket, Wheel für Windows | Event-basiert, evtl. gelber Rahmen |
| Bildschirmbereich (`mss`, BitBlt) | nein | nein | klein | nimmt mit, was darüber liegt |

**Empfehlung:** `PrintWindow` zuerst; Windows Graphics Capture nur, falls verdeckte Fenster
nicht gehen. Fenster per **Prozessname** (`PicoLog.exe`) finden, nicht per Titel, weil sich
der Titel mit der Ansicht ändert. Minimiertes oder geschlossenes Fenster = Quelle „nicht
erreichbar“, wie eine abgesteckte Kamera (gleiche Statusanzeige, automatisch weiter, wenn
es wieder da ist). Größenänderungen des Fensters verschieben die ROIs, deshalb Größe merken
und bei Abweichung warnen.

**Alternative für PicoLog:** Der TC-08 lässt sich mit dem PicoSDK direkt auslesen (exakte
Werte statt OCR). Das geht aber nur, wenn PicoLog das Gerät nicht gleichzeitig benutzt.

## Ansichten anordnen

| Ansatz | Nebeneinander | Frei / Bild-in-Bild | Aufwand |
|---|---|---|---|
| **`QMdiArea`** (Unterfenster im Hauptfenster) | eigene „Kacheln“-Funktion | ja, Fenster können sich überlappen und beliebig klein sein | gering |
| `QDockWidget` | ja, mit Splittern | nur als eigenes schwebendes Fenster | gering, Layout lässt sich speichern |
| eigenes Layout (Splitter + Overlay) | ja | ja | höher |

**Empfehlung:** `QMdiArea`. Standard „nebeneinander“, frei verschiebbar, Bild-in-Bild durch
Überlappen. Anordnung pro Sitzung speichern.

## Architektur-Vorschlag

- Neue Klasse `SourceSession`: Quelle (Kamera, Video, Fenster), eigener `CameraThread`,
  eigenes `VideoWidget`, eigener `OCRWorker`, eigener `VideoRecorder`, eigene ROI-Liste.
- `ROIConfig.source_id` neu; alte ROI-Presets gehören automatisch zur ersten Quelle.
- Neue Quellenart `WindowCapture(CameraSource)` mit derselben Schnittstelle wie `OpenCVCamera`.
  Läuft im App-Prozess in einem Thread (keine Treiber beteiligt).
- Kameras laufen weiter je in einem eigenen Hilfsprozess, auch die HP-Kamera. Ein Absturz der
  einen trifft die anderen damit nicht.
- Datenbank: `readings` bekommt eine Spalte `source`, nur zur Nachvollziehbarkeit. Kanalnamen
  bleiben eindeutig, über alle Quellen hinweg (wird beim Anlegen geprüft).
- Aufnahme pro Quelle: eigene MKV mit Zeitstempeln. Ein Fenster nur mit wenigen Bildern pro
  Sekunde aufnehmen.
- Bedienfeld: eine Quellenliste (hinzufügen, entfernen, Start/Stop, Record pro Quelle oder
  für alle). ROI-Werkzeuge wirken auf die aktive Ansicht.

## Phasen

1. **Umbau ohne neue Funktion:** alles, was heute an einer Quelle hängt, in `SourceSession`
   verschieben; App verhält sich wie vorher (Tests bleiben grün).
2. **Mehrere Kameras/Videos** gleichzeitig, `QMdiArea`, ROIs pro Quelle, Spalte `source`.
3. **Anwendungsfenster als Quelle** (`PrintWindow`), Fensterauswahl nach Prozess.
4. Aufnahme und Anordnung pro Quelle speichern, Presets mit Quellen.
5. Später: mehrere Aufnahmen synchron abspielen (über die Zeitstempel-Dateien).

## Offene Fragen und Risiken

- Verdecktes PicoLog-Fenster mit `PrintWindow`: noch nicht getestet.
- Zwei USB-Kameras gleichzeitig: Bandbreite und CPU für OCR prüfen.
- Die HP-Kamera hat heute den Windows-Kameradienst abstürzen lassen. Dank Hilfsprozess
  übersteht die App das jetzt, die anderen Kameras verlieren aber kurz die Verbindung.
- Mehrere Videos synchron offline auswerten ist deutlich aufwendiger als live; deshalb Phase 5.
