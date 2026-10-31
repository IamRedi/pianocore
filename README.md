# Piano Core

[![CI](https://github.com/IamRedi/pianocore/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/IamRedi/pianocore/actions/workflows/ci.yml)

**Ein Raspberry Pi macht aus einem Digitalpiano ein lernendes Instrument.** Piano Core läuft lokal neben dem Piano, spricht mit ihm über USB-MIDI, spielt Stücke darauf ab, hört beim Üben zu und zeigt auf einem Tablet, welche Taste mit welchem Finger als Nächstes kommt. Keine Cloud, kein Konto, kein Internet nötig.

[English version](README.en.md) · [Projektstand](docs/PROJEKTSTAND.md) · [Prüfprotokoll](EVIDENCE.md) · [Sicherheit](SECURITY.md)

![Musik: Now Playing mit Bibliothek, dunkles Design](docs/assets/music-dark.png)

## Auf einen Blick

| | |
|---|---|
| **Was** | Gerätesoftware für ein Digitalpiano: Player, Bibliothek, Lernmodus mit Fingersatz, Live-Klaviatur, optionale LED-Leiste über den Tasten |
| **Hardware** | Raspberry Pi 4, Roland-Digitalpiano über USB-MIDI (in beide Richtungen), optional ESP32 mit WS2812-LED-Leiste (196 LEDs für 88 Tasten) |
| **Bedienung** | Web-Oberfläche im Heimnetz, gebaut für Tablet, läuft auch auf Handy und Desktop |
| **Technik** | Python 3.11+ (Standardbibliothek plus `mido`/`python-rtmidi`), SQLite, HTTP-API mit Server-Sent Events, Oberfläche in reinem JavaScript ohne Build-Schritt, Three.js für die 3D-Hände |
| **Tests** | 1.240 Python-Tests und 535 Prüfungen der Oberflächenlogik, alle grün ([Prüfprotokoll](EVIDENCE.md)) |
| **Status** | Läuft auf dem eigenen Raspberry Pi des Autors am eigenen Piano. Kein kommerzielles Produkt. |

## Was es kann

- **Musik.** Antippen und das Stück läuft auf dem echten Piano, wie bei einem Musik-Player: Suche, Playlists und Zuletzt gespielt in einer Seitenleiste, Weiter/Zurück und automatisch das nächste Stück. Ansicht als Cover, fallende Noten oder 3D-Hände.
- **Lernen.** 1.557 Lektionen in 80 Kursen aus dem offenen Pianio-Lehrplan (CC0). Modi: Warten auf die richtige Taste, Mitspielen im Takt, rechte oder linke Hand allein, Abschnitte in Schleife, langsames Üben. Der Fortschritt wird pro Lektion gespeichert.
- **Fingersatz und Hände.** Für jedes Stück wird ein Fingersatz berechnet (R1–R5, L1–L5) und als fallende Noten oder mit animierten 3D-Händen gezeigt.
- **Live-Klaviatur.** Jede gedrückte Taste und das Pedal erscheinen sofort auf dem Bildschirm, getrennt nach „selbst gespielt“ und „vom Player“.
- **LED-Leiste.** Wenn ein ESP32 mit LED-Streifen angeschlossen ist, leuchten die nächsten Tasten direkt über der Klaviatur.
- **Dunkles und helles Design.** Unter Einstellungen › Darstellung: Dunkel, Hell oder Automatisch. Notenbild, Klaviatur und Hände bleiben immer dunkel, damit sie gut lesbar sind.
- **Ein Gerät, viele Bildschirme.** Der Pi hält den einzigen gültigen Zustand. Tablet, Handy oder später Home Assistant sind nur Clients der API.

| Musik, helles Design | Fallende Noten | 3D-Hände |
|---|---|---|
| ![Musik hell](docs/assets/music-light.png) | ![Fallende Noten](docs/assets/notes.png) | ![3D-Hände](docs/assets/hands.png) |

| Lern-Akademie | Live-Klaviatur | Handy dunkel | Handy hell |
|---|---|---|---|
| ![Akademie](docs/assets/academy.png) | ![Klaviatur](docs/assets/piano.png) | ![Handy dunkel](docs/assets/phone-dark.png) | ![Handy hell](docs/assets/phone-light.png) |

Die Bilder stammen aus einer lokalen Demo (stummes Platzhalter-MIDI statt echtem Piano, nur gemeinfreie Titel, Beispiel-Lernkatalog), nicht vom Gerät.

## Ausprobieren ohne Piano

```bash
git clone https://github.com/IamRedi/pianocore && cd pianocore
python3 -m venv .venv && . .venv/bin/activate
pip install -e .            # unter Linux braucht python-rtmidi das Paket libasound2-dev

# Player + Bibliothek mit vier gemeinfreien Melodien
python tools/demo_local.py

# zusätzlich der Lernkatalog aus dem offenen Pianio-Lehrplan
git clone --depth 1 https://github.com/ismaelponce/pianio /tmp/pianio
python tools/demo_local.py --pianio /tmp/pianio
```

Dann `http://127.0.0.1:8788/ui/` öffnen. Die Demo startet die echte Anwendung; nur Piano-Ein- und -Ausgang sind stumme Platzhalter. Alles liegt in einem temporären Ordner und verschwindet beim Beenden.

## Tests

```bash
PYTHONPATH=src:tests python -m unittest discover -s tests -t .
npm install --no-save ws@8      # nur für release_harness.test.mjs
for f in tests/weblogic/*.test.mjs; do node "$f" || exit 1; done
```

Die Tests laufen ohne Hardware: MIDI-Geräte, LED-Controller und Uhr werden durch kontrollierte Test-Doubles ersetzt. Als root ausgeführt schlagen einige Tests fehl, weil sie Dateirechte prüfen. In GitHub Actions laufen beide Suiten und ein Secret-Scan bei jedem Push.

## Aufbau

```
src/piano_core/
  app.py            Zusammensetzung aller Teilsysteme, Start und geordnetes Herunterfahren
  midi/             USB-MIDI Ein- und Ausgang, Wiederverbindung, Sicherheits-Stopp
  playback/         Zeitachse und Abspielen von MIDI auf dem Piano
  library/          Einlesen und Indexieren der MIDI-Bibliothek (nur lesend)
  learn/            Bau des Lernkatalogs aus offenen Quellen
  packages/         Übungspakete: Import, Prüfung, Fortschritt
  practice/         Übungsmodi und Bewertung
  fingering/        Fingersatz-Berechnung
  led/              Protokoll zur LED-Leiste (ESP32)
  api/              HTTP-API und Server-Sent Events
  web/static/       Oberfläche (HTML, CSS, JavaScript, Three.js)
ops/                systemd-Units und Deploy-Helfer für den Raspberry Pi
docs/               Architekturentscheidungen (ADR), Betriebsdokumentation, Baseline-Spezifikation
tools/              lokale Demo, Werkzeug zum Bau der 3D-Hand
```

Wichtige Entscheidungen stehen in [`docs/decisions/`](docs/decisions/), der Betrieb auf dem Pi in [`docs/operations/`](docs/operations/).

## Herkunft

Piano Core ist ein privates Projekt von Redion Cekrezi, entwickelt zusammen mit KI-Coding-Agenten unter einem eigenen Arbeitsstandard: jede Aufgabe mit Abnahmekriterien, unabhängiger Prüfung und schriftlichem Nachweis, bevor etwas auf das Gerät geht. Dieses öffentliche Repository ist ein bereinigter Schnappschuss des privaten Entwicklungsrepositorys (Stand 14. Oktober 2026), ohne Verlauf, interne Nachweisordner und Daten aus dem Heimnetz. Was genau entfernt wurde, steht im [Prüfprotokoll](EVIDENCE.md).

## Lizenz

Quelltext einsehbar, aber nicht frei verwendbar: siehe [LICENSE](LICENSE). Drittkomponenten (Three.js, MIT) behalten ihre eigene Lizenz.
