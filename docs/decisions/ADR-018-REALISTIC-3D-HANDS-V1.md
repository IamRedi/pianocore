# ADR-018 — Realistic 3D Hands V1

- **Status:** ACCEPTED — TIER-1 + TIER-A + FINAL ENGINEERING MILESTONE OWNER-ACCEPTED / LEGAL_RELEASE_GATE_PENDING
- **Task:** PIANO-UI-007
- **Owner authorization:** `USER_REQUEST_PIANO-UI-007-REALISTIC-3D-HANDS` (2026-09-20; in-session directive; verbatim record and SHA-256 in `docs/work/evidence/PIANO-UI-007-AUTHORIZATION.txt` and `docs/work/TASKS.yaml`)
- **Builds on:** ADR-014 (§6 synchronization, §8.1 renderer candidate, §9 extension points), ADR-016 (score projection D1, geometry D4), ADR-017 (version-bound plan D1–D4)
- **Scope note:** The directive references a locked UI-007 specification (2026-09-19) and a final locked amendment A1–A3 (2026-09-20). Neither document exists in the repository or its git history (verified by full-tree search and `git log` at preflight). The directive text itself was executed as the complete operative contract; any locked-spec decision not restated in the directive could not be consulted and remains flagged for independent review.

## 1. Source facts

UI-006 publishes a version-bound, advisory-only fingering plan
(`GET /api/v1/fingering/{song_id}`) whose targets carry score identity,
pitch, channel, onset, duration, hand and finger — and explicitly no pose,
wrist, joint, IK, collision or animation values (ADR-017 D5). UI-005
established one canonical score projection, the accepted playback anchor
clock and the canonical 88-key geometry fractions derived from the ONE
UI-003 key model. The browser had no 3D surface, no renderer dependency,
and no vendored third-party runtime code.

## 2. Decision

### D1 — One-way authority chain, presentation only

The implementation preserves the locked pipeline absolutely:

```text
ScoreProjection -> FingeringPlan -> MotionEngine -> IK / Pose -> HandRenderer
```

`handmotion.js` (the MotionEngine and the analytical pose solver) is pure:
deterministic, stateless, browser-independent, Node-testable, free of
Three.js and DOM access, driven only by canonical song time. It never
parses MIDI, never selects hand or finger, never changes a fingering
decision, never grades Practice, never advances Learning, never controls
Playback or LEDs, and is never a musical timing authority. The renderer
consumes MotionState only and reads no musical data for fingering
decisions. Camera state and quality policy are presentation-only and can
never reach back into MotionState.

### D2 — MotionEngine contract

`createMotionView({ songId, sourceVersion, plan, geometry })` performs the
defensive motion-view construction: malformed or non-finite targets are
DROPPED (never thrown, never repaired, `invalid_target_dropped:<n>`);
same hand+finger whose half-open occupancies `[onset, onset + duration)`
overlap over DIFFERENT keys is a `plan_occupancy_violation` — the whole
hand is marked omitted and hidden while the other hand stays usable, with
no reassignment, no alternative fingering and no target-key modification;
same-MIDI overlaps are aliases, not violations; zero-duration attacks
press and release but create no occupancy. The path is deterministic and
non-throwing for any payload.

`evaluate(view, positionSeconds, tempoRate)` maps canonical song time to
the locked phase model — PREPARE, APPROACH, PRESS, HOLD, RELEASE, RECOVER —
per finger. Chords press together (shared onset); repeated notes
re-press deterministically (the newest attack owns the finger from its
approach start); zero-duration notes never HOLD; pause freezes the pose
(position stops); seek reconstructs immediately (stateless evaluator);
reload reconstructs identically (the view is a pure function of the
validated plan and geometry); there is no historical-frame replay.
`tempoRate` scales only the APPROACH lead (travel feasibility at the
wall-clock pace); all phase windows are rate-independent song-second
constants, so tempo changes re-anchor approach timing without touching
press/hold/release truth. MotionState locks: song/source/plan identity,
LEFT/RIGHT root/wrist state, per-finger state (MIDI target, immutable
contact point, press amount, phase), and a fixed warning vocabulary.

### D3 — Contact immutability and collision priority

The fingertip contact point is derived ONCE per attack from the canonical
geometry plus presentation constants (white/black contact band along the
key) and is immutable for the attack lifetime. Collision/IK/rendering may
never change MIDI key, hand, finger or contact band. The locked collision
priority is implemented as single bounded passes — active contact fixed;
wrist/root adjustment (one bounded shift down/toward the keys/laterally
toward unreachable targets); non-contact joint posture (deterministic
idle curl); transitioning/inactive-finger lift; secondary pose clamp
(knuckle splay); warning + visual imperfection — and unresolved cases
warn `collision_unresolved`. `solveHandPose` never selects another key or
finger to improve appearance; when reach limits are hit the pose clamps
and warns `pose_clamped`. Musical/fingering truth always wins over visual
perfection.

### D4 — Analytical IK

Per finger, an exact planar two-link law-of-cosines solve in the finger's
vertical curl plane (middle + distal phalanges kept colinear) puts the
fingertip EXACTLY on its target — `contactIntegrity` (pressed tips
exactly on canonical contacts) is asserted by the Node suite and the
browser suite. Three joints per finger are modeled; the distal joint is
driven direction-preserving so the tip never drifts off the contact. The
thumb uses its own base offsets and deterministic opposition plane. There
are no iteration loops, no physics engine, and no unbounded solving.

### D5 — Renderer dependency: vendored Three.js, pinned and provenance-recorded

Three.js r186 (`three@0.186.0`) is vendored locally under
`src/piano_core/web/static/vendor/` — NO CDN, NO npm/build-step runtime
chain. Recorded provenance (SHA-256):

| Artifact | SHA-256 |
|---|---|
| npm tarball `three-0.186.0.tgz` (dist-tag latest, published 2026-09-08) | `61eeff9d7616005c9a481c796f52287d81fbbbc0d55eaca5565322924252c1aa` |
| `vendor/three.webgpu.js` (2,284,823 bytes) | `a78400fa1d359e81fcc12b84ed0a89a7fbecb4b8373e28ae8f9d2b11f6ea13bc` |
| `vendor/three.core.js` (1,458,113 bytes) | `9edde002b066a9a05676a6127f67735b62baf399bdea529f2f7e31657da769e6` |
| `vendor/three.LICENSE.txt` (MIT, three.js authors 2010-2026) | `8b378ebe60e2fe500158cb0ac71cb5e8b7d92953c2abcc63a0eb90499653b5bc` |

License: MIT (redistribution-compatible; the full license text ships
beside the files and is allowlisted). Loading is a DYNAMIC import of the
bare specifier `three/webgpu` through an import map added to
`index.html`; the vendored build's internal `./three.core.js` import
resolves relatively inside `vendor/`. The allowlist
(`piano_core.web.UI_ASSETS`) and wheel package-data were extended for the
new modules and vendor files. A renderer/import failure degrades the
hands pane to COMPATIBILITY while the rest of the UI (player, keyboard,
score, Learning) continues unaffected — proven by the browser suite.

### D6 — Backend ladder and plain-LAN functionality

`HandRenderer.initialize()` uses Three.js `WebGPURenderer`: WebGPU when
genuinely available, with the WebGL2 fallback engaged automatically when
WebGPU is unavailable, and an explicit `forceWebGL` retry as a second
ladder rung. If both fail, `RendererUnavailableError` degrades the
feature to COMPATIBILITY (existing 2D UI). WebGPU is never mandatory:
the verified real-browser run over plain loopback HTTP rendered through
WebGL2 (headless Chromium 152, SwiftShader software — recorded honestly
as such; these results are never device/tablet/Pi/GPU-hardware
performance).

### D7 — Rig abstraction and Tier R / Tier A

`validateRigDefinition` is the canonical, bone-name-independent rig
contract: a wrist plus three joints per finger (an optional fourth/ext
joint allowed), resolved to Object3D nodes, validated FAIL-CLOSED
(`RigValidationError`); bind-pose capture, scale normalization and
left/right handling belong to the adapter that supplies resolved nodes.
An invalid realistic asset NEVER reaches the scene — the side keeps its
procedural fallback.

The procedural fallback hand (Tier R) is built from primitive geometry
and exercises the same wrist/root planner, the same IK, the same finger
chains (3 joints + tip), the same contact model, the same renderer
lifecycle, the same collision rules and the same tests as any future
asset. It is the PERMANENT runtime fallback, not a throwaway mock.
Finger pivots are placed at the solver's exact world joint points
(point-matching), so fingertips sit exactly on canonical contacts with no
forward-kinematics drift.

**Tier A — realistic rigged hand GLB — was not satisfied by the Tier-1
milestone.** The
`attachAsset(hand, definition)` path (validate fail-closed -> replace the
procedural rig for that side -> `applyPose(pose)` through the asset's
own bone adapter) is the only realistic-asset path.

The isolated Tier-A step initially evaluated a CGTrader source but rejected it
as the distributable browser asset. That result remains historical evidence
only. The Owner then supplied an unrigged Fal-generated hand. The final
candidate pipeline reads only that exact hash, constructs Piano Core's own
wrist and three-joint-per-finger armature/weights, bakes a mirror-safe right
derivative, and exports the sole production GLB. No CGTrader geometry,
textures, rig, skin weights or source bytes enter the production artifact.

The Fal GLB (`e9e6b598...6970d3`) has two skins, maximum four influences, zero
unweighted vertices and passes Khronos validation with 0 errors/warnings/
infos/hints. It uses the same explicit adapter, real-GLB loading,
replacement/disposal, recovery and permanent Tier-R fallback. Exact Fal
service model/parameters and output-rights terms were not recoverable locally;
that is recorded factually as a separate terms-review item, not represented as
a license claim. Full evidence:
`docs/work/evidence/PIANO-UI-007-TIER-A-CANDIDATE.md`.

At the executor handoff this established a candidate for independent review,
not acceptance. The later independent rereview and Owner acceptance are
recorded in §5. The permanent Tier-R fallback remains mandatory when the GLB
is absent or invalid.

### D8 — Scene, camera, quality

The scene is minimal: camera, ambient + directional lighting, the 88-key
keyboard mesh (every key X from the canonical geometry fractions; only
Y/Z dimensions are presentation constants), left hand, right hand. No
room, furniture or decorative environment. Camera modes are FULL
(deterministic whole-keyboard fit for any aspect, portrait included) and
FOLLOW (presents the active hands' mean root); switching is
presentation-only and never touches the motion view. Quality modes are
AUTO / PREMIUM / BALANCED / BATTERY / COMPATIBILITY; AUTO starts at
BALANCED and moves an exponential frame-interval average with
downgrade-fast / upgrade-slow hysteresis (thresholds 24 ms down, 15/13 ms
up over sustained windows). Quality changes pixel ratio and shadow
configuration only — canonical musical timing, hand identity, finger
identity, target key and transport synchronization are untouched, and
Core musical timing is never altered to improve graphics.

### D9 — Presentation integration and pressed-key dip

The hands pane consumes the SAME visual frame (one presentation clock,
the accepted UI-002 anchor model or the Learning WAITING target onset)
and the SAME validated replicas as the UI-005 views. Pressed keys dip
under the immutable contact band by the canonical key-dip constant — a
renderer-local visual; no key state leaves the presentation layer.
Recovery: page reload, A→B song switch, same-song source replacement,
stale plan, score/fingering mismatch, browser sleep/wake, renderer
initialization failure, asset failure and WebGL context loss all flow
through one identity gate (an old hand pose never survives a
source/plan identity change) and one recovery path (`recover()`, with a
bounded canvas-replacement retry for poisoned contexts).

### D10 — Bounded read-only test/debug surface

The only browser-exposed surface is `globalThis.__pianoHands` — frozen,
containing exactly `info()` (renderer status snapshot) and `motion()`
(frozen deep copy of the last MotionState). It can mutate nothing.
Hand preferences (enabled/camera/quality) persist locally with the same
bounded-preference semantics as the visual-mode preference.

## 3. Verification summary (Tier 1, technical)

- `node tests/weblogic/handmotion.test.mjs` — 27/27: single LH/RH notes,
  repeated notes, canonical-fixture scales, thumb-under fixture, 3/4/5-note
  chords arriving together, held chord + moving finger, bass + melody,
  converging/diverging hands, large jump, fast repetition, long sustain,
  zero-duration, pause/seek/resume, tempo re-anchor, same-source
  deterministic reconstruction, occupancy violation (fail-closed),
  same-MIDI alias tolerance, dense collision, contact immutability,
  NaN/Infinity rejection, and structural invariants. Fixtures are
  generated from the accepted canonical Python pipeline and pinned by
  `tests/test_ui007_fixture_canonicality.py`.
- `TMPDIR=/var/tmp node tests/weblogic/browser_hands.mjs` — 18/18 real
  Chromium/HTTP tests over the packaged UI (backend recorded honestly;
  SwiftShader scope declared; never device performance).
- Performance is measured separately for MotionEngine evaluate, IK solve,
  scene update and browser frame interval, with exact scope statements in
  the milestone evidence — headless/SwiftShader numbers are never
  attributed to device, tablet, Pi or GPU hardware.

## 4. Limits

- Tier-R hands remain the permanent stylized fallback; the accepted Fal-based
  Tier-A asset supplies the realistic production-hand presentation.
- The canonical fingering engine is a heuristic (ADR-017 §4); hand
  presentation faithfully renders its ergonomics, including
  pose_clamped/collision warnings where assignments stretch the
  canonical hand model — these warnings are honest presentation limits,
  never fingering corrections.
- No seek/loop/live-tempo Core transport extension is introduced;
  presentation "seek" in tests is an authoritative position jump through
  the same anchor model.
- The `three.webgpu.js` + `three.core.js` vendor payload (~3.7 MB) ships
  inside the wheel and is served with the appliance's conservative
  no-cache policy; the COMPATIBILITY path never loads it.
- Physical-device performance, tablet/Pi behavior and WebGPU-on-hardware
  are NOT_RUN / NOT_VERIFIED and are not claimed.
- The production hand is Owner-generated via fal.ai and source/transformation
  hashes are recorded. Exact service model/parameters and output-rights terms
  were not locally recoverable; legal terms review remains separate and no
  license claim is made.

## 5. Owner acceptance and release boundary

On 2026-09-25 Owner Redion Cekrezi accepted implementation/evidence candidate
`75dc2e60a0f9f744efa20217e16d9621388112ad` as both the PIANO-UI-007 Tier-A
engineering result and the final PIANO-UI-007 engineering milestone. The
targeted independent rereview verdict was **UI007-A-R1 REREVIEW PASS — TIER-A
READY FOR OWNER DECISION**; UI007-A-R1 is **CLOSED** and there are no technical
blockers.

This acceptance establishes the realistic-hands architecture and the
Fal-based candidate technically. It does not establish legal approval of Fal
commercial redistribution or application rights. That independent release
state is **LEGAL_RELEASE_GATE_PENDING**; the asset is not cleared for
commercial distribution until the applicable terms are reviewed and recorded.
The gate does not reopen UI-007 engineering. The adapter boundary remains
replaceable so an Owner-owned or independently licensed hand, including a
future premium/4K asset, can use the accepted architecture without changing
musical authority.

Physical tablet, Raspberry Pi and hardware-GPU validation remain **NOT_RUN /
NOT_VERIFIED**. No merge, deployment, Production acceptance, UI-008 or UI-009
authority follows from this decision. Formal evidence:
`docs/work/evidence/PIANO-UI-007-FINAL-ACCEPTANCE.md`.
