# PianoCore

<img src="src/piano_core/web/static/brand/pc-logo-crystal.png" alt="PianoCore crystal grand piano" width="180">

**See the music. Feel every note.** PianoCore brings a MIDI player, a music library, guided practice, keyboard LEDs, Crystal Stage visuals and smart-home control to your digital piano.

[Website](https://www.redioncekrezi.de/app-sites/pianocore/en.html) · [Deutsch](README.md) · [Technical record](EVIDENCE.md) · [Security](SECURITY.md) · [License](LICENSE)

![PianoCore Crystal Studio](docs/assets/crystal-studio.webp)

## A complete piano studio

- **MIDI player.** Choose a piece and hear it played on your digital piano. Start, pause, stop, adjust the tempo and move to the next song.
- **MIDI library.** Browse your song list, search for a title, organize playlists and return to recent music.
- **Keyboard LEDs.** Lights above the keys follow the music and highlight practice targets.
- **Guided practice.** Wait for the right notes, work on one hand, repeat a passage, slow the tempo and follow lesson progress.
- **Crystal Stage.** Falling notes, a crystal keyboard, left/right colour guidance and finger numbers in learning mode. Choose sheet, falling-note or hybrid views.
- **Smart home.** Control the piano through its command interface and include it in scenes and automations.
- **Your screens.** Use a phone, tablet or computer. Day, Night and Auto share one studio identity, with the crystal grand piano mark and clear glass surfaces.

## Local architecture

PianoCore runs on Raspberry Pi and connects to the digital piano over USB-MIDI. Python, SQLite and mido/python-rtmidi power the local musical runtime; the browser uses the same-origin HTTP/SSE interface. The Pi owns the musical state and the displays follow it.

The web client includes an install manifest and crystal app icons. Open your appliance at its HTTPS address to use browser installation; Safari offers Share → Add to Home Screen. The native product website is under `/ui/about/` in SQ/EN/DE/IT.

## Development

```bash
git clone https://github.com/IamRedi/pianocore && cd pianocore
python3 -m venv .venv && . .venv/bin/activate
pip install -e .  # Linux build dependency: libasound2-dev
python tools/demo_local.py
```

Open `http://127.0.0.1:8788/ui/`. The local development player includes four public-domain melodies and silent MIDI adapters. The lesson catalog can be imported separately.

```bash
PYTHONPATH=src:tests python -m unittest discover -s tests -t .
npm install --no-save --no-package-lock ws@8.18.0
node --test tests/weblogic/*.test.mjs
python tools/sync_presentation_assets.py --check
```

Development checks run locally; GitHub Actions can be started manually. [EVIDENCE.md](EVIDENCE.md) holds the technical history and verification details.

Code, product graphics and studio assets: Redion Cekrezi. Three.js and Manrope retain their bundled licenses. See [LICENSE](LICENSE) and [SECURITY.md](SECURITY.md).
