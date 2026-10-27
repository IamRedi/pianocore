# Projektstand

**Stand: 27. Oktober 2026** (Code-Stand des privaten Repositorys: 14. Oktober 2026). Belege zu jeder Zahl: [EVIDENCE.md](../EVIDENCE.md).

## Kurz

Piano Core ist fertig genug, um täglich am eigenen Piano benutzt zu werden, und noch nicht fertig genug, um es anderen zu verkaufen. Die Technik ist auf dem Gerät installiert und durch Tests abgesichert; die Abnahme am Instrument durch den Besitzer steht aus.

## Teilsysteme

| Bereich | Stand | Bemerkung |
|---|---|---|
| USB-MIDI Eingang (Tasten, Pedal) | fertig | Wiederverbindung nach Abziehen, Lesefehler werden isoliert |
| USB-MIDI Ausgang (Piano spielt selbst) | fertig | Sicherheits-Stopp beim Schließen, keine hängenden Töne |
| Player und Bibliothek | fertig | Bibliothek nur lesend, Index in SQLite |
| Lernkatalog (Pianio + gemeinfreie Stücke) | fertig | reproduzierbarer Bau, Herkunft und Lizenz pro Lektion gespeichert |
| Übungsmodi und Bewertung | fertig | Warten, Mitspielen im Takt, Hand allein, Schleife, langsam |
| Fingersatz | fertig | berechnet, nicht aus der Quelle übernommen; im UI als R1–R5 / L1–L5 |
| 3D-Hände | fertig | realistische Hand (Ausgangsmodell mit fal.ai erzeugt), prozedurale Hand als automatischer Ersatz |
| LED-Leiste (ESP32, WS2812) | umgesetzt | Protokoll und Integration getestet; Hardware-Abnahme im privaten Repository dokumentiert |
| Web-Oberfläche (Tablet) | umgesetzt | Abnahme am echten Tablet steht aus |
| HTTP-API + Server-Sent Events | fertig | keine Anmeldung, nur fürs Heimnetz (siehe [SECURITY.md](../SECURITY.md)) |
| Betrieb auf dem Raspberry Pi | läuft | systemd, Deploy-Helfer mit Rücksprung auf die vorherige Version |

## Offen

- Abnahme durch den Besitzer am Instrument: selbst spielen, hören, Bedienung am Tablet.
- Anmeldung und TLS für die API, bevor das Gerät jemals außerhalb eines vertrauenswürdigen Heimnetzes läuft.
- Prüfung der fal.ai-Bedingungen, bevor die realistische 3D-Hand kommerziell oder in Werbung genutzt wird.
- Einbindung in Home Assistant und den Sprachassistenten des Autors (vorgesehen, nicht begonnen).

## Vorgehen

Jede Aufgabe hatte eine schriftliche Freigabe, Abnahmekriterien, eine Prüfung durch einen zweiten, unabhängigen Agenten und einen Nachweis, bevor etwas auf das Gerät ging. Fehlgeschlagene Freigaben wurden zurückgerollt und dokumentiert, nicht umetikettiert. Diese internen Unterlagen sind nicht Teil des öffentlichen Repositorys.
