# Prüfprotokoll / Verification record

**Stand: 27. Oktober 2026.** Dieses Protokoll trennt, was für diese öffentliche Fassung gemessen wurde, von dem, was nur aus den privaten Entwicklungsunterlagen stammt. Zahlen ohne Quelle gibt es hier nicht.

## Gemessen für diese Fassung

Alle Läufe auf einem Linux-Container (x86_64), als normaler Benutzer, am 27.10.2026, gegen genau den Inhalt dieses Repositorys.

| Prüfung | Ergebnis |
|---|---|
| Python-Tests, `PYTHONPATH=src:tests python -m unittest discover -s tests -t .`, Python 3.13 | **1.240 Tests**, 1 übersprungen, 0 Fehler |
| dieselben Tests mit Python 3.11 (unterste unterstützte Version) | **1.240 Tests**, 1 übersprungen, 0 Fehler |
| Oberflächenlogik, `node tests/weblogic/*.test.mjs`, Node 22 | 23 Dateien, **535 Prüfungen** bestanden, 0 fehlgeschlagen |
| Secret-Scan, gitleaks 8 über alle Dateien | keine Funde |
| Lokale Demo `tools/demo_local.py --pianio …` mit Pianio-Commit `df5526d` | Anwendung startet „READY“; Lernkatalog 1.557 Lektionen in 80 Kursen gebaut und importiert, 0 ausgeschlossen; Bibliothek 4 Stücke |
| Browser-Durchgang der Demo (Chromium über Playwright, 1366 × 1024; Player zusätzlich im dunklen Farbschema) | Start, Bibliothek, Player mit laufender Wiedergabe, Lern-Akademie, fallende Noten mit Fingersatz, Live-Klaviatur, Einstellungen: alle Seiten laden ohne JavaScript-Fehler |

Die README-Bilder stammen aus diesem Durchgang. Dabei gefunden und behoben: Im Player waren Liedtitel und Seitentitel im hellen Farbschema dunkel auf dunklem Hintergrund und kaum lesbar (`styles.css`, eine Regel).

In GitHub Actions laufen bei jedem Push dieselben Python-Tests (3.11 und 3.13), die Oberflächenlogik und gitleaks.

## Aus den privaten Entwicklungsunterlagen (hier nicht nachgeprüft)

Das private Repository führt zu jeder Aufgabe Übergabe, Nachweise und eine unabhängige Prüfung. Laut diesen Unterlagen gilt für den Stand vom 14.10.2026:

- Die Version ist auf dem Raspberry Pi 4 (Debian 13, Python 3.13) installiert. Auf dem Gerät liefen dieselben Python-Tests, dazu Browser-Tests in Chromium und ein Live-Durchgang gegen das echte Gerät: Wiedergabe auf dem Roland-Piano, sauberes Stoppen, Server-Sent Events, Lastprobe mit 998 HTTP-Anfragen ohne Fehler.
- Der Lernkatalog auf dem Gerät umfasst 1.612 Lektionen in 83 Kursen (Pianio plus weitere gemeinfreie Stücke, unter anderem aus dem Mutopia-Projekt); die Player-Bibliothek enthält rund 1.000 MIDI-Stücke.
- **Nicht abgenommen:** die Abnahme durch den Besitzer am echten Instrument (selbst spielen, hören, Tablet-Bedienung) steht aus.

## Was nicht geprüft ist

- **Echte Hardware in dieser Fassung.** Hier lief nichts gegen ein Piano, einen Raspberry Pi oder eine LED-Leiste. MIDI-Geräte und LED-Controller sind in Tests und Demo Platzhalter.
- **Browser-Testsuiten** (`tests/weblogic/browser_*.mjs`) wurden für diese Fassung nicht ausgeführt; sie sind auf die Entwicklungsumgebung des Autors zugeschnitten.
- **Leistung auf Tablet und Pi** (Bildrate der 3D-Hände, WebGPU auf echter Hardware).
- **Sicherheit über das Heimnetz hinaus.** Die API hat keine Anmeldung, siehe [SECURITY.md](SECURITY.md).

## Was gegenüber dem privaten Repository entfernt oder geändert wurde

- Kompletter Git-Verlauf; dieses Repository beginnt mit einem einzigen Commit.
- `docs/work/` (Aufgabenliste, Status, Übergaben, rund 2.000 Nachweisdateien mit Logs und Gerätedaten), interne Agenten-Anleitung `AGENTS.md`, der interne Arbeitsstandard, Freigabe-Runbooks für das konkrete Gerät, Audit-Notizen mit lokalen Pfaden.
- Heimnetz-Adresse, Benutzername und Home-Pfade in Doku, Skripten und Tests durch Beispielwerte ersetzt (`192.168.1.50`, `pianopi`, `/home/dev`).
- Ein Test, der nur prüfte, dass zwei interne Nachweisdateien übereinstimmen, entfällt mit diesen Dateien.
- Neu: `tools/demo_local.py`, README auf Deutsch und Englisch, diese Datei, `docs/PROJEKTSTAND.md`, `SECURITY.md`, `LICENSE`, CI-Workflow.

## Daten und Rechte

- Keine persönlichen Daten, keine Zugangsdaten. Die Demo-Melodien (Beethoven, Petzold, Volkslied) sind gemeinfrei und im Code Note für Note ausgeschrieben.
- Der Pianio-Lehrplan ist nicht im Repository; die Demo lädt ihn auf Wunsch aus dem öffentlichen Repository (Inhalte CC0-1.0, Code MIT).
- Realistische 3D-Hand (`src/piano_core/web/static/assets/piano-hand-tier-a.glb`, SHA-256 `e9e6b598…6970d3`): das Ausgangsmodell hat der Autor mit fal.ai erzeugt; Skelett, Gewichtung und rechte Hand baut `tools/ui007_build_hand_asset.py`. Die Veröffentlichung hier hat der Autor freigegeben; eine Prüfung der fal.ai-Bedingungen für kommerzielle Nutzung oder Werbung steht aus. Fehlt die Datei, nutzt die Oberfläche automatisch die prozedurale Hand.
- Three.js liegt unverändert in `src/piano_core/web/static/vendor/` mit seiner MIT-Lizenz.
- Code, Texte und Bilder: Redion Cekrezi. Nutzung nur im Rahmen der [LICENSE](LICENSE).
