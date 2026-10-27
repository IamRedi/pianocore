# Sicherheit / Security

## Einsatzmodell

Piano Core ist ein Gerät für das eigene Heimnetz. Es gibt kein Konto, keine Cloud-Verbindung und keine Telemetrie. Die Anwendung öffnet nur zwei Ports:

| Port | Zweck | Standard |
|---|---|---|
| 8788 | HTTP-API und Web-Oberfläche | `127.0.0.1`; auf dem Gerät bewusst `0.0.0.0` (siehe [docs/operations/API.md](docs/operations/API.md)) |
| 8787 | WebSocket-Endpunkt, mit dem sich der ESP32 der LED-Leiste verbindet | nur wenn LEDs eingeschaltet sind |

## Bekannte Grenzen

- **Keine Anmeldung.** Wer im selben Netz ist, kann den Zustand lesen und das Abspielen starten oder stoppen. Die API darf deshalb nie ins Internet weitergeleitet werden (keine Portfreigabe, kein Tunnel). Anmeldung, TLS und eine Schnittstellen-Allowlist sind als spätere Arbeit festgehalten, nicht umgesetzt.
- **Kein globaler MIDI-Notaus über die API.** „Stop“ beendet nur die eigene Wiedergabe; ein allgemeines „All Notes Off“ bleibt eine Bedienhandlung am Gerät.
- **Gerätekennung.** Die Kennung (`PC-…`) wird einmal vom Betreiber erzeugt. Der Endpunkt gibt nie MAC-Adresse, Seriennummer, IP, Hostname, Pfade oder WLAN-Daten heraus.

## Was im Code abgesichert ist

- Die Oberfläche wird nur über eine feste Liste von Dateien ausgeliefert; Pfad-Traversal, auch prozent-kodiert, endet in einem 404.
- Die MIDI-Bibliothek muss ein schreibgeschützter Ordner sein; die Anwendung startet die Bibliothek sonst nicht.
- Übungspakete werden vor dem Import geprüft (Größen, Notenbereich, Ereignisanzahl, keine Pfade außerhalb des Pakets in Tar-Archiven) und atomar aktiviert.
- Der systemd-Dienst läuft als eigener Benutzer `piano-core` mit `NoNewPrivileges`. Der Deploy-Helfer ist das einzige Programm, das per `sudo` laufen darf, und prüft die SHA-256-Prüfsummen des Pakets.
- Secret-Scan (gitleaks) in GitHub Actions bei jedem Push.

## Schwachstelle melden

Bitte nicht als öffentliches Issue. Kontakt über das GitHub-Profil [@IamRedi](https://github.com/IamRedi) oder über „Report a vulnerability“ im Security-Tab dieses Repositorys, falls aktiviert.

---

**English summary.** Piano Core is a home-network appliance with no account, cloud link or telemetry. The HTTP API and UI have **no authentication** by design for now: anyone on the LAN can read state and start or stop playback, so never expose port 8788 to the internet. The UI is served from a fixed allowlist, the MIDI library must be read-only, practice packages are validated before an atomic import, and the service runs as a dedicated unprivileged user. Report vulnerabilities privately via the GitHub profile above, not as a public issue.
