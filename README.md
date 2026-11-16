# PianoCore

<img src="src/piano_core/web/static/brand/pc-logo-crystal.png" alt="PianoCore crystal grand piano" width="180">

**Dein Klavier. Eine Welt zum Spielen.** Ein lokales Klavierstudio auf deinem Raspberry Pi: USB-MIDI-Wiedergabe, durchsuchbare Bibliothek, geführtes Üben und Crystal-Stage-Notenansicht.

[Website](https://www.redioncekrezi.de/app-sites/pianocore/de.html) · [English](README.en.md) · [Prüfprotokoll](EVIDENCE.md) · [Sicherheit](SECURITY.md) · [Lizenz](LICENSE)

![Aktuelle Crystal-Studio-App](docs/assets/crystal-studio.webp)

Die aktuelle Oberfläche verbindet Studiofotos mit klarem Glas, dem Kristallflügel-Logo und Cyan/Lavendel-Handführung. Tag und Nacht zeigen dasselbe Studio; Auto folgt der Gerätedarstellung. Der Screenshot zeigt die echte App lokal mit synthetischer Demonstrationsmusik, ohne physisches Piano.

## Verbinden und installieren

Verbinde dein USB-MIDI-Piano mit einem Raspberry Pi, auf dem PianoCore läuft. Öffne die Pi-Adresse am Handy, Tablet oder Computer im selben vertrauenswürdigen Netzwerk. Die Website präsentiert Musik, geführtes Üben und Crystal Stage mit stillen Illustrationen aus dem eigenen App-Renderer.

Zur Installation öffnest du die **App unter der HTTPS-Adresse des Pi** und wählst die Browser-Funktion App installieren, sobald verfügbar. Auf iPhone/iPad: Safari → Teilen → Zum Home-Bildschirm. Der installierte Client benötigt weiterhin den erreichbaren Pi. Die Präsentationswebsite installiert keine Klavier-Engine.

Das Manifest enthält eine stabile Identität, Scope sowie 192/512-px- und maskierbare Kristall-Icons. Nur ein echtes Browser-Installationsereignis aktiviert den App-Button. Kein Service Worker, kein Offline-API-Cache und keine automatische Installation. HTTPS-Einrichtung und Gerätebereitstellung sind separate Freigabeschritte; eine physische Abnahme wird nicht behauptet.

## Umgesetzt

- MIDI-Suche, Playlists, zuletzt gespielte Musik und Wiedergabesteuerung.
- Geführtes Üben mit Warten auf die richtigen Noten, Fortschritt und Abschnittswiederholung.
- Notenblatt, fallende Noten, Hybridansicht und Crystal-Stage-Tastatur auf der Core-Zeitachse.
- Live-Tasten-/Pedalanzeige und optionale ESP32-LED-Führung.
- Eine lokale musikalische Instanz mit Handy-, Tablet- und Desktop-Clients.

3D-Hände bleiben experimentell. Der Lernkatalog wird separat importiert und ist nicht enthalten. Dies ist ein MVP, kein kommerziell abgenommenes Gerät.

## Local development

```bash
git clone https://github.com/IamRedi/pianocore && cd pianocore
python3 -m venv .venv && . .venv/bin/activate
pip install -e .  # Linux: python-rtmidi requires libasound2-dev
python tools/demo_local.py
```

Open `http://127.0.0.1:8788/ui/`. The disposable local demo uses the real Application with silent synthetic MIDI boundaries and four public-domain melodies. It does not connect to your appliance. The native presentation is at `/ui/about/en.html` (also SQ/DE/IT).

```bash
PYTHONPATH=src:tests python -m unittest discover -s tests -t .
npm install --no-save --no-package-lock ws@8.18.0
node --test tests/weblogic/*.test.mjs
```

Routine verification runs locally. GitHub Actions is manual only; no cloud run was requested for this update. Historical checks and current limits are recorded in [EVIDENCE.md](EVIDENCE.md).

## Architecture and rights

Python 3.11+, SQLite, mido/python-rtmidi, same-origin HTTP/SSE and browser-native HTML/CSS/JavaScript. The Pi owns musical truth; views do not advance practice independently. The unauthenticated control API is intended for trusted networks only; never expose it to the public internet. See [SECURITY.md](SECURITY.md).

This public source mirror excludes private task records, device logs, personal paths and the appliance address. Crystal identity and studio assets belong to Redion Cekrezi; Three.js and Manrope retain their bundled licenses. Experimental 3D-hand commercial licensing remains unverified. See [LICENSE](LICENSE) and [EVIDENCE.md](EVIDENCE.md).
