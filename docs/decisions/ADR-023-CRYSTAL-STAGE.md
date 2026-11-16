# ADR-023 — Crystal Stage: falling notes as light, crystal keyboard

- Status: Accepted by the Owner on the device (2026-11-16)
- Builds on: ADR-022 (Glass Studio)

## Decision

The falling-notes screen is no longer a black box. It is smoked glass, so the studio photo stays visible behind it (`.workspace-scene[data-view="falling"]`).

- **Notes** are light capsules. Each has a glass body in the hand's colour (left = ice cyan, right = lavender), a neon bloom, a white rim and a white core line, plus a little stardust above the tail. When no fingering is known, the hand is taken from the keyboard side (below C4 = left).
- **Impact.** When a note reaches its key, three things happen: an anamorphic flare, a white star on the key, and 7–12 star sparks that rise and fall back.
- **Keys.** The struck key glows from inside in the hand's colour. A key the player presses on the piano glows champagne gold. The strike line is a thin ice-cyan light that pulses on hits.
- **Keyboard.** White keys are frosted crystal with a front lip. Black keys are glossy smoked glass with a gloss stripe. Pressed keys sink 2 px.
- **Finger numbers** show only while learning. The Player shows pure light.
- **Phone Player** frames only the song's own keys, so the keys are big enough to read.

All effects are pure functions of the song position, so they freeze with stale Core truth and survive seeking. The visualizer keeps no particle state.

## Quality

The tier is read from the existing "Hands detail" setting.

| Tier | When | What is drawn |
|---|---|---|
| PREMIUM | default | bloom 16 px, 12 sparks, stardust |
| BALANCED | "Balanced", or 4 or fewer CPU cores | bloom 9 px, 7 sparks |
| BATTERY / COMPATIBILITY | chosen in the setting | no bloom, no sparks |
| OFF | `prefers-reduced-motion` | no sparks |

## Assets

There are five white alpha sprites in `static/crystal/`: `ck-spark-star-a`, `ck-spark-star-b`, `ck-spark-dust`, `ck-impact-flare` and `ck-key-glow`. ChatGPT made them, and they are used untouched. The code tints them per hand.

The concept frames and the task live in Drive at `CLAUDE_INBOX/PianoCore_CrystalStage_20261116`.

## Where

- `src/piano_core/web/static/visualizer.js`: the Crystal Stage helpers and `FallingNotesRenderer`.
- `styles.css`: the "Crystal Stage" section.
- `app.js`: the phone focus range.

## Brand (same day)

The crystal grand piano (`pianocore-crystal-transparent-v3.png`, from ChatGPT, in Drive) replaces the red tile logo and `favicon.svg`.

- `static/brand/` holds the trimmed nav mark and the PWA icons.
- The icons are 192, 512 and maskable 512 px, on a dark plum rounded tile.
- `static/brand/` also holds the favicon (64 px) and the Apple touch icon (180 px).
