# Piano Core MIDI Input — Operations Guide

> **Task:** PIANO-P004 · **Status:** verified behavior only (see status markers)  
> **Appliance contract:** `docs/operations/APPLIANCE_BASELINE.md` · Evidence: `docs/work/evidence/PIANO-P004-MIDI-INPUT.md`

---

## What exists (verified)

- A canonical MIDI **input** event model (`piano_core.midi.events`): NOTE_ON / NOTE_OFF / SUSTAIN (CC64), channels 1–16, monotonic timestamps, NOTE_ON velocity 0 normalized to NOTE_OFF.
- An input-only adapter (`piano_core.midi.input`): endpoint selection (case-insensitive name matcher; exact preference; **ambiguous match fails closed**), connection states DISCONNECTED/CONNECTING/CONNECTED, endpoint-loss detection, bounded reconnect, clean shutdown.
- A diagnostic capture CLI (`piano_core.midi.capture`) that prints canonical events as JSON lines.

## Configuration (service)

Environment variables via `/etc/piano-core/piano-core.env` (see `ops/systemd/piano-core.env.example`):

| Variable | Values | Default | Meaning |
|---|---|---|---|
| `PIANO_CORE_MIDI_INPUT_ENABLED` | `true` / `false` | `false` | enables the MIDI input adapter at startup |
| `PIANO_CORE_MIDI_INPUT_PORT_MATCHER` | non-empty string | `roland` | case-insensitive input-port name matcher |

Unknown `PIANO_CORE_*` variables fail closed at startup.

## Diagnostic capture (verified on the appliance host, 2026-09-04)

Using a Python environment that contains `piano_core`, `mido==1.3.3` and a working `rtmidi` backend (e.g. `/opt/piano-core/venv` after provisioning, or a temporary venv):

```
python -m piano_core.midi.capture --matcher roland --type sustain --run-for 300 --output /tmp/capture.jsonl
```

- `--matcher` selects the input port by case-insensitive name substring; ambiguous matches abort (never guess).
- `--type` limits evidence to one event type (`all` / `note_on` / `note_off` / `sustain`).
- Output: one JSON line per canonical event plus a final summary line.
- INPUT ONLY: the capture opens input ports; it cannot send MIDI.

Run it while playing on the Roland to produce bounded evidence (event metadata only: type, channel, note, velocity/value, pressed, monotonic timestamp).

## Production service integration (VERIFIED 2026-09-05)

The application lifecycle owns the MIDI input adapter (F2 closure, PIANO-P005):

- `PIANO_CORE_MIDI_INPUT_ENABLED=true` in `/etc/piano-core/piano-core.env` → `piano-core.service` starts the input adapter at startup; the Roland endpoint is selected and the adapter reaches CONNECTED; application READY follows (READY is not gated on MIDI connection — a missing endpoint keeps the process healthy with the adapter in DISCONNECTED/reconnect).
- `PIANO_CORE_MIDI_INPUT_ENABLED=false` (or absent) → no backend instantiation; behavior identical to the pre-MIDI service.
- `systemctl restart piano-core.service` applies configuration changes; the adapter reconnects automatically after restart and after endpoint loss.
- Production logging stays note-free: per-event detail is DEBUG only (bounded evidence windows may temporarily raise `PIANO_CORE_LOG_LEVEL=DEBUG`; restore to `INFO` afterward — both verified).

## Dependency provisioning (verified offline path)

`/opt/piano-core/venv` contains `piano-core`, `mido==1.3.3`, `python-rtmidi==1.5.8` (aarch64/py3.13 build) and `packaging`. Offline install from the controlled artifact set (see `docs/work/evidence/PIANO-P005-PRODUCTION-MIDI.md` for hashes and provenance):

```
sudo /opt/piano-core/venv/bin/pip install --no-index \
  --find-links /tmp/p005-artifacts --force-reinstall "piano-core==0.1.0.dev0"
```

The `python-rtmidi` aarch64 wheel originates from the target's own pip build cache (built from the official PyPI sdist; provenance recorded). Do NOT copy installed site-packages from the legacy venv — that path is not an accepted production mechanism.

## What is NOT implemented (deliberately)

- MIDI output / playback / note transmission (hard boundary — the input adapter and backends expose no send surface).
- HTTP/WebSocket exposure of MIDI state, LED coupling, Home Assistant, HUGO, UI.
- Canonical StateStore consumption of the events (later task; the current production sink counts and DEBUG-logs only).
- ALSA reconnect frameworks beyond simple bounded retry; journald tuning; MIDI routing/throughput configuration.

## Historical provisioning note (P004, superseded by P005)

During P004 hardware validation, `mido==1.3.3` and `python-rtmidi==1.5.8` (aarch64, cpython-3.13) were provisioned by copying installed packages from the legacy environment (`/home/pianopi/RediON/services/pluto-piano-server/.venv/lib/python3.13/site-packages/`). That copy path was a bounded validation step only and is **not accepted for production provisioning**.

Current accepted direction (P005, verified): dependencies are canonically pinned in package metadata (`mido==1.3.3`, `python-rtmidi==1.5.8`; `packaging` arrives transitively via mido) and installed offline from the controlled wheel artifact set with recorded provenance and hashes (see `docs/work/evidence/PIANO-P005-PRODUCTION-MIDI.md`). Never copy legacy site-packages into a production environment.
