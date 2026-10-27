# ADR-014: Piano Core Premium UI V2 — Architecture & Premium Foundation

> **Task:** PIANO-UI-001 · **Owner:** Redion Cekrezi · **Date:** 2026-09-15
> **Status:** ACCEPTED — canonical Premium UI V2 architecture baseline.
> **Authorization:** USER_REQUEST_PIANO-UI-001 (documentation/governance only).
> **Inspected revision:** `a91afb26fcbcd5ded211fd75dc2b3679a2901ef8`, clean `main`.
> **Review / acceptance:** Independent review `PASS`; Owner Redion Cekrezi accepted
> 2026-09-15. Binding evidence: `docs/work/evidence/PIANO-UI-001-ACCEPTANCE.md`.
> **Supersedes:** nothing. Existing accepted behavior and frozen evidence remain intact.

This is the single Premium UI architecture source. [PLAN](../work/PLAN.md)
owns roadmap sequencing; [TASKS](../work/TASKS.yaml) owns authorization;
[STATUS](../work/STATUS.md) owns current lifecycle. “Proposed” below means a
future contract requiring its own authorized implementation and verification.
No UI-002 feature is implemented or authorized by this document.

## 1. Current-state findings and constraints

### 1.1 Reuse the established system

| Existing fact at the inspected revision | Reuse boundary / evidence |
|---|---|
| Python >=3.11, synchronous/threaded runtime; `mido==1.3.3`, `python-rtmidi==1.5.8`; static assets packaged in one wheel | [pyproject.toml](../../pyproject.toml), [Application](../../src/piano_core/app.py); retain deployment and composition model |
| Physical input is reduced into immutable `PianoState`; `pressed_notes` means physical keys, not sounding voices; pedal does not retain released keys | [StateStore](../../src/piano_core/state.py), [operations](../operations/STATESTORE.md); UI reads this truth |
| `revision` changes only for material physical-state changes; `last_input` can change at the same revision; restart resets revision | Same StateStore contract; revision is neither a universal event counter nor a process identity |
| P009 `load_timeline`/`build_timeline` produces ordered song-time events with note on/off, channel, velocity and CC64 commands | [timeline.py](../../src/piano_core/playback/timeline.py); tempo map is applied during conversion, not retained as a public map; ticks, track IDs and time signatures are not retained in `TimelineEvent` |
| PlaybackEngine schedules canonical output on a monotonic clock; tempo multiplier 0.25..4.0 is fixed per session; pause releases owned output and resume does not re-attack held spans | [engine.py](../../src/piano_core/playback/engine.py), [manager.py](../../src/piano_core/playback/manager.py), [API ADR](ADR-CONTROL-API-V1.md); reuse scheduler and ownership |
| Practice projects the P009 timeline: exact-onset chords, MIDI 21..108, channel 10 excluded; physical release/re-arm rules; silent, self-paced progression | [Practice ADR](ADR-PRACTICE-V1.md), [plan.py](../../src/piano_core/practice/plan.py), [manager.py](../../src/piano_core/practice/manager.py); no second MIDI parser or input device |
| Playback and Practice share the appliance activity lock, including CLI/API exclusion | [advisory.py](../../src/piano_core/playback/advisory.py); no simultaneous playback/practice merely because UI can draw both |
| LibraryService resolves opaque `song_id` to a validated read-only source; SQLite catalog enrichment is separate from playback interpretation | [service.py](../../src/piano_core/library/service.py), [metadata.py](../../src/piano_core/library/metadata.py); song ID is path-derived, not a source-content revision |
| HTTP `/api/v1`, bounded handlers, deterministic errors; native ES-module UI with visibility-aware 1 s polling, timeouts and stale-search tokens | [handlers.py](../../src/piano_core/api/handlers.py), [serialization.py](../../src/piano_core/api/serialization.py), [app.js](../../src/piano_core/web/static/app.js), [Web UI ADR](ADR-WEB-UI-V1.md); no React, bundler or public WebSocket stream |
| LED policy and hardware mapping remain Core-owned; Device Identity is persistent, explicitly initialized and read-only at runtime | [LED ADR](ADR-LED-KEYBOARD-V1.md), [Identity ADR](ADR-DEVICE-IDENTITY-V1.md); no renderer may access internal device transport or initialize identity |
| P001–P015, hotfixes and PROD-001 have recorded acceptance/commit history | Existing [ledger](../work/TASKS.yaml) and [PROD-001 evidence](../work/evidence/PIANO-PROD-001-DEVICE-IDENTITY.md); these are inspected historical evidence, not fresh hardware tests |

### 1.2 Resolve assumptions without rewriting history

The frozen [v1 specification](../baselines/PIANO_CORE_V1_FROZEN_SPEC.md)
describes a union `activeNotes` StateStore, semantic HTTP+WS, and a different
identity shape. The accepted implementation exposes physical-only
`pressed_notes`, separate playback/Practice snapshots, HTTP polling and
`/api/v1/device` UUID identity. Its API lacks seek, loop A/B, live tempo changes,
track selection, hand selection and a normalized score endpoint. A public
low-latency note stream and globally coherent session snapshot do not exist.
The internal LED WebSocket transport is not that public gateway.

Preserve these repository facts under the Owner's explicit instruction to
preserve the validated system. Premium UI will compose distinct sources into
a read-only view; it will not merge playback into physical StateStore or
reinterpret identity to imitate the frozen examples. This ADR does not declare
those older discrepancies resolved or amend the frozen acceptance gates.
Any implementation that requires changing an accepted contract must first
identify its exact ADR/baseline amendment and obtain Owner approval.

The accepted [A001 overlay](../baselines/PIANO_CORE_V1_AMENDMENT_A001.md)
prefers Pi-direct LEDs subject to its hardware gate; existing P014 ESP32
behavior remains unchanged. Older PLAN phase labels and STATUS narratives
are historical, not current implementation authority. No `PIANO-UI-*` task
or ADR-014 existed at preflight; P016–P026 identifiers are not reused.

## 2. Component boundaries and data flow

All domain boxes below live logically inside the authoritative Core boundary;
they need not be new processes, threads or services. New orchestration is a
thin composition of existing managers, never a parallel playback engine.

```text
Physical Roland -> existing MIDI input -> StateStore (physical truth)
                                           | post-reduction observations
LibraryService -> canonical timeline -------+-> Session / Learning systems
                         |                 |            |
                         +-> PlaybackEngine -> canonical MIDI output -> Roland
                                           |            |
                              Core-owned LED policy <----+
                                           |            |
                            authoritative state / events / clock anchors
                                                        |
                                           Realtime UI Gateway
                                                        |
                              Premium UI replica + one presentation clock
                                  |          |         |          |
                               keyboard   falling    sheet     3D hands
                                           notes               ^
                              Fingering plan -> motion/IK adapter

UI user intent -> gateway command validation -> existing Core owner
Capability/quality policy -> renderers only (no musical-state write path)
```

The timeline is file-derived; physical input is a separate live canonical
stream. Both meet at Core session/learning boundaries; a live key is not
silently appended to or used to rewrite the selected song. Rendering data
flows out; semantic commands flow back through one governed entry path.

| Layer | Responsibility and ownership boundary |
|---|---|
| 1. Piano Core | Existing lifecycle, physical input, MIDI output ownership, arbitration, library, LED, identity and safety/reliability |
| 2. Canonical Musical Timeline | One immutable normalized score, built at the existing parser boundary; supplies playback, learning and all visual projections |
| 3. Transport / Musical Clock | Existing scheduler owns song-time position and transitions; published anchors permit read-only client interpolation |
| 4. Session State | Core composition owns selected song, active activity and accepted settings; delegates execution to existing managers |
| 5. Learning Engine | Core Practice remains authority for target, completion and progress; strategies can evolve behind that boundary |
| 6. Fingering Engine | Produces versioned advisory hand/finger assignments; Core session selects the applicable plan; no authority over physical input or output |
| 7. Realtime UI Gateway | Serializes snapshots/events, validates commands, binds identities/versions and manages backpressure/recovery; owns no musical truth |
| 8. Presentation Layer | Existing ES modules initially; controls, accessibility, navigation and local preferences; stores a replaceable server-state replica |
| 9. Visualization Layer | Independent keyboard, falling-note and sheet consumers; one frame context, no raw MIDI parsing or independent progression |
| 10. 3D Hand Renderer | Consumes fingering/motion targets and canonical position; rig and IK determine pose only, never which note is musically correct |
| 11. Capability / Performance | Client measures available graphics and sustained rendering cost; selects quality and reports capability; never changes musical semantics |

## 3. Authority matrix

Each authoritative field has one writer boundary. Aggregation does not transfer
ownership. “Proposed” rows are absent until a separately authorized milestone.

| State or decision | Sole authority | Consumer / constraint |
|---|---|---|
| Physical keys, pedal, input connection, accepted input activity | Existing MIDI adapter + StateStore reduction boundary | Practice/LED/UI observe; automated notes never enter `pressed_notes` |
| Automated MIDI notes/sustain and owned cleanup | Existing output adapter | Playback invokes it; UI cannot infer ownership from score geometry |
| Play/pause/resume/stop and actual playback position | Existing PlaybackManager / PlaybackEngine boundary | Session delegates; STOP stays owned-only, PANIC remains operator-only |
| Tempo at play time | Existing PlaybackEngine, validated by manager | UI sends intent; current range 0.25..4.0 |
| Seek, live tempo, loop A/B and loop iteration | Proposed Core transport extension | Must define discontinuity, output cleanup and pedal/note reconstruction before enablement |
| Selected active song, tracks/hands, learning mode and current section | Proposed Core session composition | Client selection before submit is only a local draft; filtering never alters physical input |
| Canonical score and metadata revision | Proposed extension of existing timeline boundary | Library provides source; every projection references the same normalized revision |
| Learning target, completion, incorrect attempts, session progress | Existing Practice / future governed learning strategy | Client/AI/IK cannot score a physical event or advance a lesson |
| Hand/finger plan | Fingering engine for suggestions; Core session for adopted plan revision | Unknown remains unknown; visual pose cannot reverse-infer accepted fingering |
| LED mode, target priority, transport recovery and physical mapping | Existing Core LED subsystem | Visual keyboard layout is not a replacement LED map |
| Persistent device identity | Existing DeviceIdentity loader / explicit operator CLI | Gateway reads descriptor; UUID is not authorization or a session ID |
| Runtime epoch, stream order and synchronization watermark | Proposed Core gateway envelope | Independent of physical material revision; no persistence of live notes |
| UI replica freshness, pending command display | Client gateway adapter | Derived transport status only; never declares server command success |
| Zoom, camera, panel selection, theme, visual quality | UI local preferences | May persist locally; cannot restore a playing session on reconnect |
| IK joints, mesh, shadow, sheet layout and frame interpolation | Respective renderer/visual adapter | Disposable derived data; cannot issue musical commands |
| AI teaching advice | Future advisor | Version-bound suggestions only; Core validates any explicitly accepted action |

## 4. Canonical Musical Timeline contract (proposed extension)

Extend the current pure timeline boundary rather than placing another parser
in the browser. A parser adapter normalizes one source revision into an
immutable score. The existing output-command timeline and Practice plan become
projections of that same interpretation. Preserve current ordering (release
before attack at the same tick), channel convention 1..16, tempo scaling outside
the source timeline and unsupported-file rejection unless separately amended.
Catalog metadata extraction may still parse for indexing; it is not musical
authority for playback, notation or learning. No renderer reparses source MIDI.

| Normalized content | Required semantics |
|---|---|
| Identity | Schema version, opaque song ID, content revision, parser/normalization version; stable event/note IDs within that revision |
| Musical coordinates | Absolute tick plus ticks-per-quarter and tempo segments; derived song seconds from the same map; time-signature changes and derived measure/beat position where justified |
| Events and notes | Note-on/off, pitch 0..127, attack/release velocity, channel, track and deterministic tie order; paired note spans with explicit unmatched/overlap diagnostics |
| Pedal | Timestamped CC64 changes, independently for relevant channels; key-down span is distinct from pedal-extended sounding interpretation |
| Optional notation | Meter/key/voice/spelling and score layout inputs where available; imported versus inferred provenance, strategy version and confidence; no invented certainty |
| Optional teaching | Hand assignment, fingering annotations, sections and learning annotations keyed to score/note IDs; authored, inferred or unknown explicitly marked |

MIDI does not necessarily contain hand assignment, fingering, reliable notation
spelling or pickups. Lack of meter does not justify pretending a measured bar
number is source truth. A governed normalization rule may derive defaults,
but must label them. Note pairing and same-pitch overlaps need deterministic
rules with fixtures before implementation. Renderer coordinates and IK units
are excluded from the musical schema.

Large scores require bounded, immutable chunks by musical range with a manifest;
clients cache by content/schema revision, not path-derived `song_id` alone.
Views share decoded chunks; they do not duplicate parse work. Missing chunks
produce an explicit loading/unavailable view, not a different musical timeline.
No raw source paths, device internals or library files need to enter the UI.

## 5. Core/UI contracts and session evolution

### 5.1 Existing interfaces to retain

Reuse GET `/api/v1/health`, `/state`, `/device`, `/library`,
`/library/songs`, `/library/songs/<song_id>`, `/playback`, `/practice`, `/led`
and existing playback play/pause/resume/stop and Practice start/stop commands
under `/api/v1`. Preserve the [operational API contract](../operations/API.md),
error envelopes, song-ID validation, bounded inputs and activity lock.
LED actions remain semantic Core actions; internal transport is never reused
as a browser protocol. Existing API payloads are not silently renamed.

### 5.2 Proposed contract families (conceptual, not new endpoint declarations)

- **Capabilities:** supported contract versions and operations from Core;
  separately, client rendering capabilities. Effective UI features require both.
  Unsupported seek/loop/track/hand/live-tempo controls stay unavailable.
- **Score manifest/chunks:** normalized timeline revision and metadata coverage,
  bounded sizes/ranges and stable IDs; no browser MIDI parsing.
- **Command intent:** operation and validated parameters, target runtime/session,
  relevant expected control revision and request correlation. Correlation alone
  is not idempotency. Initial v1 uses existing behavior; future guarded commands
  require negotiated server support and explicit conflict responses.
- **Result:** accepted/rejected outcome from the actual Core owner, correlated
  with a revision/snapshot. Request timeout means outcome unknown; no blind retry
  of play/start/seek or an offline command queue. Query authoritative state first.
- **Snapshot:** device descriptor availability, runtime epoch, active session ID,
  score revision, subsystem revisions, activity/transport anchor, physical state,
  actual automated output state where supported, learning state and capability set.
- **Event:** ordered, typed canonical input/output/transport/learning changes,
  with runtime/session and stream sequence; events refer to normalized IDs.
  Physical input and successfully applied output are distinct from planned score
  events. The Core does not stream images, rendered frames or joint poses at 60 Hz.

Session composition delegates accepted actions to current owners under their
arbitration. It does not own another MIDI scheduler or copy Practice transitions.
Seek/loop/live tempo require Core-defined atomic state transitions, release of
owned output and explicit policies for sustained notes, cursors and lesson reset.
Until reviewed, current pause/resume and activity exclusivity remain exact.
Persistent lesson history is future product policy; it must not restore ephemeral
held notes, sustain, running playback or an active lesson after Core restart.

## 6. Synchronization model

### 6.1 One authority, one client presentation clock

The Core monotonic scheduler is authoritative for automated song time. Proposed
anchors carry song position `p0`, Core monotonic sample `t0`, effective rate `r`,
transport state, session ID and transport revision. A clock sample exchange
estimates the Core/client monotonic offset and uncertainty from bounded round
trips; wall-clock timestamps are diagnostic only. Across machines, monotonic
timestamps cannot be subtracted without that mapping.

While PLAYING, visual position is `p0 + r * (estimated_core_now - t0)`, bounded by
duration, loop segment and a finite freshness horizon. PAUSED freezes at the
anchor; WAITING Practice uses its authoritative target rather than advancing
by wall time. All renderers receive the same immutable frame context containing
position, selected session/score revision, physical snapshot and learning state.
No renderer runs its own musical clock. Different frame rates sample the same
position; dropping a visual frame never drops a Core musical event.

### 6.2 Distinct synchronized views

- **MIDI output:** scheduled entirely by Core; graphics/HTTP timing never drives it.
- **Live keyboard:** physical keys from StateStore and live canonical events;
  automated score preview, actual output and learning targets use distinct visual
  channels. Polling may miss short taps and cannot claim realtime fidelity.
- **Falling notes:** place normalized note spans relative to shared position;
  planned future notes are not evidence that MIDI output occurred.
- **Sheet:** one score projection maps note/measure IDs to layout; cursor follows
  the same position or Practice target; no independent parser or quantization truth.
- **Learning:** evaluates canonical physical observations in Core. P015 remains
  self-paced. Any future timing assessment uses Core timestamps, not packet arrival
  or animation time, and requires its own verified strategy.
- **Hands:** finger plan plus note IDs plus shared position yields pose targets;
  anticipatory motion is visual only. If no valid plan exists, omit fingers/hands
  instead of treating pitch-based guesses as teaching truth.

Small drift can be smoothed visually within a bounded error tolerance. Seek,
loop wrap, session change, new score, pause and restart invalidate interpolation
and require an immediate authoritative re-anchor. At excessive clock uncertainty
or stale age, freeze/label the view and resynchronize; never extrapolate indefinitely.
Exact latency, drift and jitter thresholds must be set and tested before each
realtime feature is accepted; 60 fps alone proves none of them.

## 7. Realtime gateway and single recovery path

Retain HTTP commands/queries and the existing poll adapter as compatibility.
For Premium realtime, prefer a semantic WebSocket event/snapshot adapter under
the existing appliance API boundary, consistent with frozen §7. This is a future
bounded extension: `ThreadingHTTPServer` has no implemented WS gateway today.
Select its library/server integration only after proving connection lifecycle,
bounded resources, offline packaging and current HTTP compatibility. Do not
replace the runtime with an async framework or expose the LED/legacy socket.

Proposed connection states: DISCONNECTED → SYNCHRONIZING → LIVE, with STALE
returning through SYNCHRONIZING. Both poll and future stream adapters feed one
client replica reducer and the same snapshot-replacement path. Only one adapter
may drive a replica generation at a time.

1. Start a new client connection generation; discard responses/callbacks from
   older generations. Read capabilities and device descriptor availability.
   Persistent UUID identifies the appliance; a separately generated Core runtime
   epoch distinguishes restarts; activity session ID distinguishes successive
   plays of the same song. Identity unavailable never creates a replacement UUID.
2. Request an authoritative snapshot tied to a stream watermark. The future
   gateway must define a coherent cut of affected subsystem state: serialize
   control transitions and capture versioned immutable observations in a bounded
   publication boundary. It cannot claim separate HTTP polls are an atomic
   global snapshot. Do not hold MIDI locks while doing serialization/network I/O.
3. Install the full snapshot atomically in the client; discard buffered events
   at/before its watermark and apply only contiguous later events from the same
   epoch/session. Bounded catch-up overflow invalidates the candidate snapshot
   and restarts synchronization. Scores/annotations with other revisions wait
   for matching data; they cannot attach to the current session.
4. Duplicate or older sequences are ignored. A gap, epoch change, incompatible
   schema, stale timeout, background-tab resume or client queue overflow returns
   to SYNCHRONIZING and replaces state from a fresh snapshot. Replay logs and
   resumable event history are optional optimizations, not required recovery paths.
5. During uncertainty, show stale/unreachable state, inhibit conflicting musical
   commands and stop predictive animation. Never send automatic STOP/PANIC or
   replay pending commands on reconnect. Existing Core playback and physical
   input continue according to Core lifecycle; a lost browser is not an emergency.

Physical material revision is preserved verbatim. The new stream sequence counts
published events including activity that did not change that revision. Session
control revisions are scoped separately so a key press does not invalidate an
unrelated pause request. Multiple clients submit to one Core arbiter; stale
control mutations are rejected/resnapshotted rather than resolved in the browser.

Gateway observers only enqueue bounded records after canonical processing; no
network writes, large allocation or rendering in MIDI/Practice/scheduler callbacks.
Slow clients lose the stream and resnapshot; they cannot backpressure physical
input. Event delivery does not become a prerequisite for Practice correctness.

| Failure | Required behavior |
|---|---|
| HTTP-only server | Existing poll controls remain usable; realtime keyboard/synchronized Premium claims unavailable |
| Browser disconnect or background throttling | Core remains authoritative; bounded visual freeze, no command replay; fresh snapshot on return |
| Core restart / another appliance | Invalidate epoch-bound clocks, events, pending intents and lesson replica; no local restoration of a musical session |
| MIDI disconnect or output failure | Show actual Core connection/error state; never synthesize physical success or use UI recovery to bypass Core policy |
| LED disconnect | Existing Core degradation applies; UI remains a consumer and does not take over LED control |
| GPU device/context loss or corrupt visual asset | Dispose/recreate renderer or fall back using the same replica/clock; no play, pause, seek or lesson transition |
| Worker/fingering/AI timeout | Drop late revision-mismatched results, show unavailable advice or simpler visuals; musical behavior unchanged |
| Device descriptor unavailable | Report unavailability; no initialization, derived replacement identity or cross-device cache reuse |

## 8. Technology and performance architecture

### 8.1 Technology decisions

Keep native HTML/CSS/ES modules for the application shell. Neither React nor
another frontend framework is required by these boundaries. A future framework
change needs evidence of benefit and a bounded ADR; framework stores remain
replicas. GPU-first applies to dense visual scenes, not to every label/control.

Three.js is a reasonable **renderer candidate**, not a selected domain dependency:
its WebGPURenderer documents WebGPU with WebGL2 fallback. A renderer contract
(`initialize`, attach immutable score/plan, render frame, resize, quality change,
dispose/recover) isolates engine and backend objects from musical schemas.
Raw WebGPU/WebGL could give more control but adds backend/asset/recovery burden;
a custom engine has no repository evidence justifying that cost yet.
See [Three.js documentation](https://threejs.org/docs/pages/WebGPURenderer.html)
(checked 2026-09-15); no dependency is installed by UI-001.

WebGPU requires a secure context and is not universally available. The existing
tablet path is plain HTTP over LAN, so WebGPU cannot be assumed there. A future
secure-origin design requires its own approved network/security scope; UI-001
adds no TLS, authentication, listener or cloud dependency. Probe actual adapter,
feature and resource support; fall back to WebGL2 when usable, then simpler
Canvas/SVG/DOM views with 3D disabled. Unsupported capabilities are explicit.
See [WebGPU platform documentation](https://developer.mozilla.org/en-US/docs/Web/API/WebGPU_API)
(checked 2026-09-15). Fallback support and asset parity require real verification.

### 8.2 Priority and execution placement

Strict order: **1 musical correctness → 2 physical input latency → 3 musical
synchronization → 4 learning correctness → 5 UI responsiveness → 6 graphics quality.**
Learning correctness remains mandatory; priority never licenses wrong grading.
Overload reduces visual work, not MIDI precision or the set of evaluated inputs.

| Execution boundary | Work |
|---|---|
| Authoritative Core | Input reduction, output scheduling/cleanup, activity arbitration, learning transitions, normalization, bounded publication of domain state |
| UI/application | Network adapter, replica reconciliation, one visual clock, controls/accessibility, capability policy; avoid per-note DOM/state updates each frame |
| GPU/rendering | Batched/instanced keyboard and falling notes, skinned hands, shaders and visual transforms; cull outside viewport, cap pixels and scene complexity |
| Possible future workers | Score projection/layout, annotation calculation, motion/IK preparation, asset decoding and offscreen rendering where measured useful; immutable revision-tagged inputs/results |

Workers are optional optimizations, not a second Core. Learning decisions remain
Core-owned even if a strategy calculation is delegated; returned suggestions
are validated before adoption. Browser OffscreenCanvas can support worker-side
rendering, but backend/asset/transfer compatibility must be tested independently
([platform documentation](https://developer.mozilla.org/en-US/docs/Web/API/OffscreenCanvas),
checked 2026-09-15). No worker, shared-memory requirement or cross-origin isolation
is introduced here. Assets remain local/versioned; future build tooling needs
reproducible offline packaging, license review and governed allowlist changes.

### 8.3 Quality profiles

| Class | Conceptual policy |
|---|---|
| AUTO | Start conservatively; use measured sustained frame time, context support, viewport/pixel budget and explicit user preference; downgrade quickly, upgrade with hysteresis |
| PREMIUM | Full available detail; target 3840×2160 at 60 fps only on demonstrated supporting hardware/browser/origin combinations; bounded fallback on overload |
| BALANCED | Reduced resolution/mesh/shadow/effect cost while keeping controls and synchronized views |
| BATTERY | User-selected lower visual frame rate/pixel budget and fewer effects; input/scheduler/learning cadence unchanged |
| COMPATIBILITY | Minimal keyboard/score/status views using available backend, possibly no 3D; no claim of unavailable realtime support |

No vendor/model-name lookup controls quality. Battery API, memory and thermal
telemetry are optional hints, not assumed inputs. Measure sustained performance;
reduce shadows, antialiasing, pixel density, mesh detail, effects and optional
views before responsiveness suffers. Backend failure is distinct from a lower
quality profile. Renderer rebuilds may interrupt visuals but not musical time.

At 60 fps the visual frame interval is approximately 16.67 ms. UI-009 must define
CPU/GPU budgets, p95/p99 frame time, long-task, latency, drift, memory and sustained
load gates for declared capability classes. Test dense MIDI, tempo changes,
recovery and multiple views together. No 4K/60, physical latency or synchronization
performance is measured or promised by UI-001.

## 9. Extension points and rejected coupling

| Replaceable part | Stable boundary / required check |
|---|---|
| MIDI parser | Canonical normalization adapter; fixture-equivalent events, timing, ordering and supported-format/error contracts |
| Sheet renderer | Normalized score + notation projection + note-ID anchors; layout changes do not change musical time |
| Falling-note/keyboard renderer | Immutable frame context and note spans; no source MIDI or physical-state writer |
| 3D engine / GPU backend | Renderer lifecycle and capability contract; context-loss/fallback tests |
| IK solver / rigged hand model | Finger targets → bounded motion/pose data; model-specific bone names and units stay in an adapter |
| Fingering strategy | Score/session settings → versioned per-note advisory hand/finger plan, confidence/provenance; late results rejected |
| Learning strategy | Core canonical observations + score targets → validated lesson transitions; preserve or explicitly amend P015 mode semantics |
| AI teacher | Read governed lesson context and produce revision-bound advice; no direct MIDI/LED/identity authority; external provider/personal-data policy is separate future scope |
| Tablet/browser class | Capability negotiation and responsive layout; no model-specific musical behavior |

Rejected: one parser per visualization; browser/WebAudio scheduling the Roland;
React/renderer state as lesson authority; AI generating authoritative physical
events; IK deciding fingering; video streaming from Core; using LED/legacy WS
as the public UI gateway; forced WebGPU or an unproven framework/server rewrite;
automatic resume after reconnect. Each would duplicate authority, couple an
optional visual choice to musical behavior, or bypass existing boundaries.

## 10. Risks, implementation gates and roadmap ownership

1. **Realtime gap:** UI-002 must specify the bounded score/session/clock capability
   foundations its player needs; UI-003 cannot claim a live keyboard from 1 s
   polling. UI-010 hardens recovery; recovery basics are mandatory from the first
   realtime milestone, never deferred until UI-010.
2. **New musical controls:** seek/loop/live tempo, track/hand selection and future
   teaching modes need explicit Core contracts and regression evidence before UI
   controls activate. Selecting a track is not reliably selecting a human hand.
3. **Score ambiguity/scale:** partial catalog enrichment, MIDI notation ambiguity,
   note overlaps and very large scores need provenance, bounds and fixtures.
4. **Origin/GPU gap:** secure-origin policy, actual renderer choice, backend asset
   parity and sustained capability thresholds remain open implementation choices.
5. **Recovery protocol:** exact envelope encoding, clock-sampling method, bounded
   publication implementation and queue/freshness limits must be fixed and tested
   in the first implementing contract, without a new general-purpose event bus.
6. **Existing governance differences:** frozen versus accepted implementation
   discrepancies in §1.2 are recorded, not resolved by this proposal. Before any
   conflicting implementation, obtain the exact required architectural amendment.
7. **Progress and AI:** persistence/retention, scoring policies, model licensing,
   hand assets and optional external teaching data need their own scoped decisions.

The separate [Premium UI roadmap](../work/PLAN.md#premium-ui-v2-lineage)
registers UI-001…UI-011. Independent review returned `PASS`, and Owner Redion
Cekrezi accepted this architecture on 2026-09-15 against the revision-bound
[acceptance record](../work/evidence/PIANO-UI-001-ACCEPTANCE.md). This acceptance
does not accept a renderer, a performance claim, a new Core behavior or UI-002
implementation. Existing acceptance records remain unchanged.
