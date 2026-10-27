# Piano Core StateStore — Operations Guide

> **Task:** PIANO-P006 · **Status:** implemented, in-memory only (see scope) — **production-verified on the real Pi/Roland (PIANO-P008, 2026-09-11/12)**
>
> **Module:** `src/piano_core/state.py` · Evidence: `docs/work/evidence/PIANO-P006-STATESTORE.md`, `docs/work/evidence/PIANO-P008-PRODUCTION-STATESTORE.md`

---

## What exists (canonical runtime state)

`piano_core.state.StateStore` is the **single source of truth** for current Piano Core runtime state. The MIDI input adapter reduces every canonical input event into the store; future API / LED / HUGO / UI layers read immutable `PianoState` snapshots from it and never parse raw MIDI themselves.

## Canonical state fields (`PianoState`, frozen)

| Field | Type | Meaning |
|---|---|---|
| `pressed_notes` | `frozenset[int]` | keys **physically held** per NOTE_ON/NOTE_OFF input (note numbers 0–127). Sustain does **not** retain released notes. |
| `sustain_value` | `int` 0–127 | raw sustain (CC64) pedal value |
| `sustain_pressed` | `bool` | `value >= 64` (canonical threshold, derived upstream) |
| `last_input` | `LastInput \| None` | observational metadata of the last accepted event: canonical event type (`note_on`/`note_off`/`sustain`), channel (1–16), note/value where applicable, monotonic timestamp |
| `midi_connection` | `MidiConnectionState` | `DISCONNECTED` / `CONNECTING` / `CONNECTED` |
| `revision` | `int` | monotonic material-state version (see below) |

All constructors are validated and fail closed; a `PianoState` cannot hold out-of-range or mistyped data. There is deliberately **no playback/output state**.

## Reducer semantics

`StateStore.apply_event(event)` accepts only canonical `MidiInputEvent` records (anything else raises `TypeError`):

- **NOTE_ON** → add the note to `pressed_notes`. A NOTE_ON for an already-held note changes nothing material (same set).
- **NOTE_OFF** → discard the note. NOTE_OFF for an absent note is safe (set unchanged).
- **SUSTAIN** → update `sustain_value` and `sustain_pressed` (pressed derives from the value at the canonical constructor).

Velocity-0 normalization happens **upstream** (canonical constructors and the adapter normalize NOTE_ON velocity 0 to NOTE_OFF before any store sees it). The store never adds its own normalization.

## Revision policy (explicit)

Material state = `pressed_notes` + `sustain_value` + `sustain_pressed` + `midi_connection`.

- Every accepted event produces a new snapshot, and `last_input` is updated on **every** accepted event.
- `revision` increments by **exactly 1 iff material state changed**. This is **Option B**: `last_input` is observational metadata excluded from materiality, so a snapshot may differ from its predecessor (new `last_input`) while sharing its revision.
- No-ops that do NOT bump the revision: repeated NOTE_ON for a held note; NOTE_OFF for an absent note; SUSTAIN with the same value; `set_midi_connection` with the current value.
- A transition to `DISCONNECTED` atomically clears `pressed_notes` and resets
  sustain to `0`/released. The connection plus reset is one material update
  (one revision increment), preventing a missing NOTE_OFF from reappearing as
  a stuck key after reconnect. Repeating `DISCONNECTED` after state is already
  clear remains a no-op.
- Initial revision is `0`; restart resets it (in-memory only).

Consumers that need to react to *any* input activity should compare full snapshots or watch `last_input`; consumers tracking *musical state* compare `revision`.

## Thread-safety and snapshot contract

- One internal `threading.Lock` guards the current state. Writers: the MIDI reader thread (`apply_event`) and connection observers (`set_midi_connection`). Readers: any thread (`snapshot()`).
- `snapshot()` returns the current frozen `PianoState` atomically and coherently. Snapshots are immutable (`frozen` dataclass + `frozenset`), so callers can never mutate store internals, and a held snapshot never changes under you.
- No async framework, no event bus, no locks exposed to callers.

## Application integration

`Application` owns the `StateStore` (composition root, `piano_core.app`):

```
MidiInputAdapter --event_callback--> StateStore.apply_event
                 --on_connection_state--> Application._on_midi_connection_state
                                       --> StateStore.set_midi_connection
```

Connection transitions are observed via a guarded adapter notification (`on_connection_state`), mapped from adapter `ConnectionState` to canonical `MidiConnectionState` in the composition root. Observer failures are logged and swallowed — they can never kill the reader thread or reach `Application.request_stop` (P001 race stays unreachable).

## Logging

- Per-note / per-event detail: **DEBUG only** (`piano_core.state`). Production INFO stays note-free.
- MIDI connection transitions: one INFO line each (`midi connection state=…`), actual changes only.
- Whole snapshots are never logged.

## Restart / reset semantics

State is **in-memory only**; nothing persists to disk or database. On process restart: `pressed_notes` empty, sustain `0`/not pressed, `last_input` `None`, connection `DISCONNECTED`, `revision` `0`.

Input disconnect uses the same safe empty key/pedal semantics without resetting
the monotonic in-process revision or erasing observational `last_input`.

## Authority boundary

`StateStore` is authoritative for current runtime state. Raw MIDI messages are authoritative **only inside the MIDI adapter**. LED, API, Home Assistant, HUGO, and UI must consume canonical `PianoState` snapshots / store contracts — never re-derive state from raw MIDI independently.

## Production verification (VERIFIED on the real appliance, PIANO-P008, 2026-09-11/12)

The full path **Roland → MIDI input adapter → canonical MidiInputEvent → StateStore → immutable PianoState** was verified under the real `piano-core.service` on the Raspberry Pi, using a bounded `PIANO_CORE_LOG_LEVEL=DEBUG` evidence window (restored exactly afterward; production INFO confirmed quiet):

- Committed-source deployment: wheel built from exactly HEAD `081169a…` (SHA-256 `926fde66…`), installed `--no-index --no-deps`; only `piano-core.service` restarted; legacy untouched.
- Connection transitions `DISCONNECTED → CONNECTING → CONNECTED` reach the store (journal INFO lines; post-restore CONNECTED journal-verified).
- Real keys: NOTE_ON adds to `pressed_notes`, NOTE_OFF removes (single and two-key dynamic cases verified); revision increments by exactly 1 per material change.
- Real pedal: `sustain_value`/`sustain_pressed` update on press and release; a note released while the pedal is held does **not** remain in `pressed_notes`.
- Hardware sub-check not proven: the duplicate-held-note no-op (re-strike of a held key) was NOT_PROVEN due to an Owner interaction error — not a product failure; Option B remains fully covered by deterministic local tests.
- Output separation maintained: MIDI output stayed at its disabled safe default throughout (executable guards); no output was sent for evidence.
- Evidence authority: Owner-executed block confirmation (`docs/work/evidence/PIANO-P008-PRODUCTION-STATESTORE.md` §4–§7).

## What is NOT implemented (deliberately)

- MIDI output / playback state or transmission (hard boundary).
- Sustained/sounding-note semantics (sustain does not retain keys).
- Per-source held-note bookkeeping (frozen spec §5 — later task, when a second input source exists).
- Persistence, cloud sync, or a generic event bus. PIANO-UI-003 now exposes a
  read-only copy through the bounded live-note snapshot/SSE contract; it does
  not change StateStore authority.
