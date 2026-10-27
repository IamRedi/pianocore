# Piano Core MIDI Output — Operations Guide

> **Task:** PIANO-P007 · **Status:** canonical output foundation (minimal primitives; NO playback)  
> **Module:** `src/piano_core/midi/output.py` + `output_events.py` · Evidence: `docs/work/evidence/PIANO-P007-MIDI-OUTPUT.md`

---

## What exists (canonical output foundation)

A typed canonical output command path:

```
Piano Core internal caller
    → typed OutputCommand (validated, fail closed)
    → MidiOutputAdapter (ownership tracking, serial execution)
    → MidoOutputBackend (raw mido serialization; only mido construction site)
    → mido / python-rtmidi → Roland MIDI OUT
```

Proven primitives: NOTE_ON, NOTE_OFF, SUSTAIN (CC64), STOP_AUTOMATED_NOTES, PANIC. **No** MIDI-file playback, timeline, scheduler, MIDI library, or recording exists.

## Canonical command model

| Command | Payload (validated) | Effect |
|---|---|---|
| `note_on(ch, note, velocity)` | channel 1–16, note 0–127, velocity **1–127** (0 forbidden) | send NOTE_ON; `(channel, note)` becomes **owned** |
| `note_off(ch, note, release_velocity=0)` | release velocity 0–127 | send NOTE_OFF; ownership discarded (unowned NOTE_OFF is a safe no-op on ownership) |
| `sustain(ch, value)` | value 0–127 | send CC64; channel becomes sustain-owned while value ≥ 64 |
| `stop_automated_notes()` | none (fail closed if payload given) | releases **only** Piano Core-owned notes + owned sustain |
| `panic()` | none | global emergency silence (see below) |

Duplicate NOTE_ON on an owned note: re-sends (retrigger); ownership stays a single entry. Channels are canonical 1-based; the backend converts to mido's 0-based internally. Commands require an open port (`OutputNotOpenError` otherwise).

## Endpoint selection

- Stable **port-name** matching only — identical fail-closed semantics to the input path: exact preferred name wins, otherwise the single case-insensitive matcher match; **zero matches → safe failure**, **multiple matches → fail closed**. Never an arbitrary choice (the legacy first-port fallback is deliberately not reproduced).
- No hard-coded ALSA addresses anywhere; runtime client/port numbers are derived evidence only.
- The adapter inspects **output**-capable ports (`mido.get_output_names()`); the Roland exposes separate input/output ports on the same `0582:01b1` USB identity.

## Output ownership

- The adapter tracks `(channel, note)` pairs Piano Core started and has not released, plus channels where it set sustain pressed. This is **output ownership**, internal to the adapter.
- It is a separate concept from the P006 StateStore's `pressed_notes` (physical human input). Output commands never mutate `pressed_notes`; human playing never creates output ownership. If future canonical playback state is needed, it will be added then — `PianoState` is untouched by P007.

## STOP vs PANIC — different operations, never substituted

- **STOP (`stop_automated_notes`)**: releases all and **only** Piano Core-owned notes (NOTE_OFF velocity 0 each) and releases sustain on channels where the adapter itself set it pressed. No global sweep, no effect on human-played sound. No-op when nothing is owned.
- **PANIC (`panic`)**: global emergency MIDI silence — for **all 16 channels**: sustain off (CC64=0), all-notes-off (CC123), all-sound-off (CC120). This is the deployed legacy heritage control strategy (frozen spec §11; the legacy 128-note-off sweep is intentionally omitted as redundant with CC123/120). It silences the instrument regardless of who started what.
- PANIC is never called automatically by the Application and is never silently substituted for STOP.

## Safe startup / shutdown

- Output is **disabled by default** (`PIANO_CORE_MIDI_OUTPUT_ENABLED=false`). When disabled, no output backend is constructed.
- Startup with output enabled only opens the port — **no note is ever emitted at startup**, and nothing ties input events to output.
- Normal shutdown releases only Piano Core-owned notes/sustain (STOP) before closing the port; **never automatic PANIC** (legacy graceful-shutdown panic is documented heritage, not canonical behavior). Cleanup failures are logged and cannot break shutdown.
- `open()` and `close()` are idempotent.

## Input / output separation (hard rule)

There is **no MIDI thru, echo, loopback, mirroring, or automatic replay**: a `MidiInputEvent` can never become an output command anywhere in Piano Core. Enforced by tests (input events reach only the StateStore; the output port records zero sends while human keys are played). The input-boundary test suite was rescoped by P007 to protect the input surface while containing the sanctioned output surface to `output.py`, `output_events.py`, and the output backend class in `backends.py`.

## Production configuration

| Variable | Values | Default | Meaning |
|---|---|---|---|
| `PIANO_CORE_MIDI_OUTPUT_ENABLED` | `true` / `false` | **`false`** (safe) | opens the output adapter at startup |
| `PIANO_CORE_MIDI_OUTPUT_PORT_MATCHER` | non-empty string | `roland` | case-insensitive output-port name matcher |

Unknown `PIANO_CORE_*` variables fail closed at startup. No volatile ALSA address is configurable by design.

## Bounded diagnostic procedure (Owner-assisted)

1. Confirm the Owner is not actively playing; legacy service healthy.
2. With the output-capable build deployed, enable output and restart only `piano-core.service` (input stays enabled; legacy untouched; no reboot).
3. Send ONE moderate note (middle register, velocity ~40–60), hold ~300–500 ms, send the matching NOTE_OFF, verify no hanging note.
4. Send a SECOND distinct note once; verify STOP releases it.
5. Only in a confirmed no-human-playing moment: PANIC once; verify silence and clean ownership.
6. Restore `PIANO_CORE_MIDI_OUTPUT_ENABLED=false` unless explicitly required otherwise.

## What is NOT implemented (deliberately)

- MIDI-file playback/interpretation, timeline/scheduler, playback threads or queues, asyncio engines.
- MIDI library, recording, program/instrument selection, CC7 volume command surface (the backend can send any CC, but no canonical volume command exists yet).
- Any automatic input→output routing, raw-midi public API, LED/API/HA/HUGO/UI.
