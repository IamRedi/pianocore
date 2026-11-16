# Current presentation revision · 16 November 2026

## Owner presentation revision · 16 November 2026

The owner rejected operational setup/release prose on the product website and requested a premium musical presentation. Replaced the four visitor pages, removed address/connection/install sections, added actual Crystal Stage renderer illustrations and the requested teal/red-glass evening plus warm cream/gold daylight material pair. Portfolio card summaries and visible case-study status now describe the product. Private client manifest/install behavior remains unchanged.

Validation for this revision: 43/43 Python asset/serving/manifest tests pass; all 26 Node test files / 35 runner results pass; exact renderer, pure mapping and five-sprite sync passes. Portfolio lint/typecheck/tests/build/GitHub sync pass. Lint reports two pre-existing unused-parameter warnings in the unchanged copied renderer; no errors. The product-page guard verifies four locales / 20 used local assets, no setup/release/form copy, no device/audio API client and real production-server HTTP/CSP/no-cookie behavior. Narrow 320px SQ/IT, 390×844 phone, desktop and 844×390 landscape checked; a decorative-line overflow at 320px was corrected and rechecked. SQ/EN desktop/phone captures retained. Language/theme menus, live Auto state, silent animation pause/resume, practice illustration, natural footer and no JS errors verified.

No new provider, AI generation, dependency, appliance access or musical behavior change. Current native/public/portfolio presentation files remain byte-identical. New proof screenshots are retained in the authorized chat outputs; earlier captures and tests below are historical. Publication remains through the existing PRs, with a fresh exact-commit Preview; no Production claim.

---

## Earlier candidate evidence — presentation copy superseded

# Current update · 16 November 2026

Product source synchronized from the current private Crystal Studio/logo revision `429bba8` plus the bounded Pi-connected presentation/install change. Private work records and live device configuration are excluded. Current local verification is recorded below; the October evidence remains historical.

- New crystal logo, all app icon sizes and studio/Crystal Stage assets.
- Four localized native/public presentation pages, explicit Pi-address navigation, device-following appearance and native installation capability.
- No service worker, API cache, new listener, authentication change or appliance deployment.
- Real app capture uses synthetic local MIDI; no physical or audible piano acceptance is claimed.
- The observed owner-device entry uses HTTP. End-to-end installation on that Pi is unverified pending a secure origin and approved release.

## Local verification · 16 November 2026

- Linux/WSL Python 3.12 with mido 1.3.3 and python-rtmidi 1.5.8: `PYTHONPATH=src:tests python3 -m unittest discover -s tests -t .` — 1,246 tests, OK, 7 skipped. Physical MIDI/LED checks are not claimed. A first run exposed Windows-only dependencies and incomplete temporary test fixtures; corrected the disposable Linux environment, not product code, before the successful complete run.
- Node 22.19.0: `node --test tests/weblogic/*.test.mjs` — all 26 test files pass, including 7 explicit native-install lifecycle checks. Release harness dependency ws 8.18.0 installed locally. This is event-model verification, not an OS installation claim.
- Exact product source mirrors: all 153 non-cache files in the private/public `src/` trees byte-identical.
- Public source gitleaks 8.30.1 directory scan: no leaks. Official release archive SHA-256 checked against its published checksum. Existing public sanitization preserved; no private work records copied.
- Portfolio: lint, TypeScript, content/native/public site tests, production build and GitHub synchronization PASS. Existing 14 SSR language checks and 98 public host/CSP/isolation checks PASS. Added PianoCore check: four locales, 14 local assets, real production-server HTTP 200, no cookies, scripts/fonts self, connect-src none.
- Rendered SQ/EN website and portfolio on desktop and 390×844 phone. DE/IT website headings and language transitions checked. Day/Night/Auto, no phone overflow, crystal logo, current app captures and explicit HTTP navigation checked. JavaScript URL and credential-bearing URL rejected. Explicit local website → real demo app navigation passed.
- Real Application demo uses four synthetic public-domain melodies and silent MIDI. Native appearance was checked against the current reachable Pi read-only. No Core controls were issued to the Pi.
- No service worker or API cache. Installation on the owner's Pi, secure entry, physical/audible acceptance, independent review and deployment are NOT_VERIFIED/PENDING_RECHECK.
- Browser verification used temporary IAB tabs and one-shot page screenshots, without native desktop capture. Task tabs closed and temporary viewport reset; no recording or persistent capture session created.

The four About HTML files, site CSS/JS and assets are canonical in `src/piano_core/web/static/about/`; the portfolio mirror is `public/app-sites/pianocore/`. Change both in the same workstream and compare hashes. The public website is informational; only the Pi-origin `/ui/` client is installable. Do not add private device addresses, discovery probes or cached API responses to the public site.

---

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

Routineprüfungen laufen lokal. GitHub Actions ist inzwischen ausschließlich manuell auslösbar; für dieses Update wurde kein Cloud-Lauf gestartet.

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

Final install checks: legacy implicit root app identity preserved while start/scope use /ui/. Focused manifest/icon/serving suite: 43/43 PASS, including two new identity/icon tests. All 76 packaged UI assets match the built wheel byte-for-byte; wheel SHA-256 a2a68ad41690231e0cb5ca2984b9a6432a567ac95c6b52c0f0d788f0c0074ca9. Native Settings → About → app return verified, no JS warnings/errors and no address form on the native origin.

Latest upstream phone-landscape CSS a728313 was incorporated before delivery. A real 844x390 app render has a visible side rail, all transport controls, body height 390 and no horizontal overflow. After the merge, manifest and static-contract tests pass 24/24; final wheel repackaging matches all 76 allowlisted assets.

Final revised presentation: 88 allowlisted assets match the wheel; SHA-256 f3b5019b088f5d0cf9d28f2f2cc9a4134087a13a66d9aeb9d54237cc6deeedaa. Native/public source and portfolio About mirrors verified identical.
