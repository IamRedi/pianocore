# ADR-019 — Premium Tablet Experience

- **Status:** SPECIFICATION OWNER-APPROVED — U8-P1 OWNER ACCEPTED (2026-09-28);
  U8-P2 OWNER ACCEPTED (2026-10-01); U8-P3 OWNER ACCEPTED (2026-10-01) at
  implementation `c14c52a…` after REVIEW BLOCKED (U8P3-R1) → bounded
  remediation → `U8-P3 REREVIEW PASS`; U8-P4 OWNER ACCEPTED (2026-10-01) at
  implementation `d335ff2…` after `U8-P4 REVIEW PASS — READY FOR OWNER
  ACCEPTANCE` (premium Learn experience); U8-P5 OWNER ACCEPTED (2026-10-02)
  at implementation `a2f6b94…` after `U8-P5 REVIEW PASS`; U8-P6 OWNER
  ACCEPTED (2026-10-02) at implementation `d66cd28…` after `U8-P6 REVIEW
  PASS — READY FOR OWNER ACCEPTANCE` (final integrated premium tablet
  experience; candidate `docs/work/evidence/PIANO-UI-008-U8-P6.md`;
  acceptance record `docs/work/evidence/PIANO-UI-008-U8-P6-ACCEPTANCE.md`).
  **PIANO-UI-008 is COMPLETE.** Real physical touch/UI interaction: PASS
  (Owner-verified on a real Samsung SM-S928B / Galaxy S24 Ultra). Real
  song-dependent physical Player/Learn: NOT_VERIFIED (kept separate; not a
  Player/Learn/touch failure). Real Pi / MIDI piano / physical LED: NOT_RUN.
  Target hardware GPU: NOT_VERIFIED. PIANO-UI-009 and the future
  AUDIO/APP/LEDKIT directions remain NOT_AUTHORIZED.
- **Task:** PIANO-UI-008
- **Specification authorization:**
  `USER_REQUEST_PIANO-UI-008-PREMIUM-TABLET-EXPERIENCE-ARCHITECTURE-SPECIFICATION`
  (2026-09-28; source directive SHA-256
  `f3602a29487d9688243b91caa4ebbb262ebbaea5f37fc1ff7d2714915dc0a45b`)
- **Specification base:** `3153ae4719b3e82fb46812b719f067513bf41cb8`
- **Primary target:** tablet landscape, operated by touch from normal piano-playing distance
- **Builds on:** ADR-014 through ADR-018 and the accepted UI-002 through UI-007 contracts
- **Release boundary:** the Fal asset remains `LEGAL_RELEASE_GATE_PENDING`; this specification neither clears nor changes that gate

## 0. Owner implementation decision — 2026-09-28

The Owner approved this specification and selected one implementation branch,
`feat/ui008-premium-tablet`, with six separately accepted checkpoints. Only
U8-P1 is authorized by
`USER_REQUEST_PIANO-UI-008-U8-P1-IMPLEMENTATION-AUTHORIZATION` (directive
SHA-256
`b0ff65e996cf06e3e02d947de04058e9716975b0353a7969073c5ef01a416354`).

The binding choices for U8-P1 are bookmarkable routing within the existing web
entry point, the existing palette normalized into tokens without a rebrand,
unsupported capabilities absent/capability-gated, and deterministic correction
of the hands-card layout collapse. Real touch-device validation belongs to
U8-P6; Raspberry Pi and hardware-GPU validation remain outside UI-008 unless
separately authorized. U8-P2 through U8-P6 and UI-009 remain unauthorized.

U8-P1 was locally implemented on the authorized branch and exact base under
the bounded contract above. Its candidate evidence is
`docs/work/evidence/PIANO-UI-008-U8-P1.md`. This implementation status is not
independent review, acceptance, deployment authority, or authorization for
U8-P2.

**Owner acceptance update (2026-09-28):** the first P1 candidate `0ee8162…`
was REVIEW BLOCKED (U8P1-R1, U8P1-R2); the bounded remediation candidate
`a4eedb093ff0a26219509a92cfea2ce456eff5bb` passed the independent targeted
rereview with zero open technical blockers (U8P1-R1 CLOSED, U8P1-R2 CLOSED)
and was **ACCEPTED BY OWNER** (Redion Cekrezi, 2026-09-28). This accepts the
U8-P1 scope only; U8-P2 through U8-P6 and UI-009 remain unauthorized, the
physical-device and Fal boundaries above are unchanged, and the blocked-review
history is preserved. Acceptance record:
`docs/work/evidence/PIANO-UI-008-U8-P1-ACCEPTANCE.md`.

**Owner acceptance update (2026-10-01):** U8-P2 (premium Home + curated
scalable Library, consumer target ~5,000 curated songs, bounded-DOM
architecture scalable beyond) was implemented at
`d2fd3818fd0d98eff6680d935686653e5f6f1dc9` on the accepted P1 checkpoint,
passed independent review (`U8-P2 REVIEW PASS`, findings U8P2-N1/U8P2-N2
accepted non-blocking) and was **ACCEPTED BY OWNER** (Redion Cekrezi,
2026-10-01). Real Core library capabilities remain the verified contract
only; favorites/artist/genre/difficulty/durable history/queue are absent,
not simulated. Acceptance record:
`docs/work/evidence/PIANO-UI-008-U8-P2-ACCEPTANCE.md`. U8-P3 through U8-P6
and UI-009 remain unauthorized.

**Owner acceptance update (2026-10-01):** U8-P3 (professional Player +
unified visual workspace) was implemented at `06b3989…` on the accepted P2
checkpoint, was REVIEW BLOCKED on the single blocker U8P3-R1 (Performance
Mode transport not pinned/visible — the review verified the rest of the
P3 scope clean, including the timeline math), was bounded-remediated at
`c14c52a…` (presentation CSS only: viewport-bounded performance frame with
`.layout` as the real scroll container, fixed-size pinned transport,
single-column performance shell; strengthened viewport-containment +
hit-test assertions; corrected evidence transcription), passed the
targeted rereview (`U8-P3 REREVIEW PASS`; U8P3-R1 CLOSED; U8P3-N1/N2
DISCHARGED) and was **ACCEPTED BY OWNER** (Redion Cekrezi, 2026-10-01).
The accepted Player capability boundary is play/pause/resume/stop,
read-only position/duration, start-only tempo, the existing LED path,
sheet/falling/hybrid, UI-007 hands, the canonical 88-key keyboard and
Performance Mode presentation; seek, transpose, A/B loop, metronome,
count-in, sections, queue, favorites, hand-selection and live tempo remain
absent (no Core contract, never simulated). The blocked-review history,
root cause and environment events are preserved. Acceptance record:
`docs/work/evidence/PIANO-UI-008-U8-P3-ACCEPTANCE.md`. U8-P4 through
U8-P6 and UI-009 remain unauthorized.

**Owner acceptance update (2026-10-01):** U8-P4 (premium Learn experience)
was implemented at `d335ff2…` on the accepted P3 checkpoint `7b8f4a5…`
(capability audit first; presentation-only `ui008/learn.js`; premium
lesson card leading the Learn route; start panel moved into Learn;
visual/hands/keyboard surfaced on Learn), passed independent review
(`U8-P4 REVIEW PASS — READY FOR OWNER ACCEPTANCE`, zero open blockers;
findings U8P4-N1/N2 non-blocking and U8P4-N3..N6 notes, all preserved)
and was **ACCEPTED BY OWNER** (Redion Cekrezi, 2026-10-01). The accepted
Learn capability boundary is start/stop/guarded restarts, the three real
modes (WAIT_FOR_NOTE, SECTION_PRACTICE with section seconds,
REPEAT_UNTIL_CORRECT with required_repetitions 1–16), authoritative
targets/accepted_notes/latest_outcome, real counters, MIDI connection and
pressed-key observation, LED indicator and the shared Playback/Practice
mutex with practice silence; practice pause/resume, tempo mutation, hand
selection, Watch/Listen/Follow, adaptive/timing scoring, persistence and
score/accuracy/stars/streaks/mastery remain absent (no Core contract,
never simulated). Piano Core remains the sole Learning authority; the
browser renders authoritative state only. Acceptance record:
`docs/work/evidence/PIANO-UI-008-U8-P4-ACCEPTANCE.md`. U8-P5 through
U8-P6 and UI-009 remain unauthorized.

**Owner acceptance update (2026-10-02):** U8-P5 (premium Piano/Device,
Settings, connection/recovery presentation and consumer-copy polish) was
implemented at `a2f6b94…` on the accepted P4 checkpoint `5482c4f…`
(capability audit first; presentation-only `ui008/piano.js` +
`ui008/settings.js`; premium Piano and Settings surfaces; honest
LED/local-preference handling over the REAL device contracts), passed
independent review (`U8-P5 REVIEW PASS — READY FOR OWNER ACCEPTANCE`,
zero open blockers; U8P5-N1 non-blocking hygiene debt and U8P5-N2..N5
notes, all preserved) and was **ACCEPTED BY OWNER** (Redion Cekrezi,
2026-10-02). Piano Core remains authoritative for MIDI state, physical
keys, sustain, playback, Learning/Practice, LED state and device
identity; the browser presents, sends accepted commands and reconciles
— no note→LED calculation, no second state store, no inferred
correctness, no independent reconnect semantics. The accepted Settings
boundary is exactly Visuals (accepted browser-local preferences) /
Guidance (read-only status + Piano link) / About (read-only identity);
persisted server settings, manual reconnect, pairing, theme, reset,
audio/microphone, BLE LED Kit, ESP32-S3 and app-runtime extraction
remain absent. U8P4-N2 and U8P4-N3 are CLOSED IN U8-P5. Acceptance
record: `docs/work/evidence/PIANO-UI-008-U8-P5-ACCEPTANCE.md`. U8-P6
and UI-009 remain unauthorized.

## 1. Decision and authority boundary

PIANO-UI-008 will replace the current long, dashboard-like page with a
responsive product shell organized around six user-facing surfaces: **Home,
Library, Player, Learn, Piano / Device, and Settings**. Tablet landscape is the
canonical composition. Phone, tablet portrait, desktop, monitor, and large
display layouts are deliberate adaptations, not scaled copies.

This ADR is a complete implementation proposal, not implementation authority.
No product file may be changed until the Owner authorizes an implementation
phase with an exact revision and file scope. UI-009 remains planned and
unauthorized.

The authority chain remains:

```text
Physical Piano / MIDI
  -> Piano Core authoritative state
  -> canonical musical timeline
  -> Playback / Practice / Learning
  -> realtime API and events
  -> premium client UI and renderers
```

The browser remains a consumer and presentation surface. It MUST NOT parse or
reinterpret MIDI independently, own playback time, grade correctness, advance
Guided Practice, replace StateStore, select fingering, or change UI-007 motion,
IK, rig, skinning, contact, or collision semantics. One canonical state and
timeline drive every renderer. A visualization or layout change is never a
transport, Learning, score, fingering, or hand-state mutation.

## 2. Current-experience findings that bind implementation

The revision-bound audit is recorded in
`docs/work/evidence/PIANO-UI-008-CURRENT-UX-AUDIT.md`. Its binding findings are:

1. The current UI has no primary product navigation; all functions form one
   long card stream.
2. At CSS widths at or above 900px, the hands card has no explicit 12-column
   placement. It collapsed to about 68–85px wide and more than 1,200px tall in
   the audited tablet-landscape and desktop layouts. Existing overflow tests
   did not detect this unusable-but-non-overflowing state.
3. Tablet landscape underuses the available canvas while utility cards compete
   with the musical workspace. Phone requires about 3,088px of vertical scroll
   before all controls are reachable.
4. User-facing copy exposes implementation concepts such as StateStore, MIDI
   internals, WebSocket host/port, authoritative snapshots, and trusted LAN.
5. Several secondary controls are 36px high and checkboxes are smaller; these
   do not meet this contract's 44px touch-target floor.
6. Loading, unavailable, and empty visual regions can reserve large blank
   areas. The 88-key view becomes difficult to read on a phone.
7. Existing strengths are retained: coherent dark visual language, strong
   state messaging, visible focus treatment, reduced-motion support,
   injection-safe text handling, canonical keyboard geometry, and robust
   identity/recovery behavior.

## 3. Information architecture and screen map

### 3.1 Global surface map

```text
Piano Core shell
├── Home
│   ├── Continue playing / continue practice
│   ├── Recent songs
│   ├── Favorites
│   └── concise device readiness
├── Library
│   ├── Search and filters
│   ├── Favorites / Recent / All
│   ├── Virtualized result collection
│   └── Song action sheet: Play / Learn / Queue / Favorite
├── Player
│   ├── Transport and authoritative timeline
│   ├── Visualization workspace
│   ├── Queue
│   └── Context panels: playback, hands/parts, sound, LEDs
├── Learn
│   ├── Lesson header and progress
│   ├── Mode picker, capability-gated
│   ├── Visualization workspace
│   ├── Current target and feedback
│   └── Section / retry / progression controls
├── Piano / Device
│   ├── Piano connection
│   ├── LED connection
│   ├── Live 88-key activity
│   └── Guided recovery actions
└── Settings
    ├── Musical preferences
    ├── Visual preferences
    ├── Device and onboarding
    ├── LEDs
    ├── Accessibility
    └── Advanced / diagnostics
```

### 3.2 Navigation

- **Tablet landscape (primary):** persistent 72px left navigation rail with
  icon plus short label for Home, Library, Player, Learn, Piano, and Settings.
  The active destination has shape, color, and text—not color alone. The rail
  never contains transport controls or diagnostics.
- **Desktop:** the same rail may expand to 208px when at least 1,366px wide and
  the musical workspace retains its minimum width. Expansion is a presentation
  preference; collapsing it changes no route or state.
- **Tablet portrait:** compact 64px top app bar plus five-item bottom navigation
  (Home, Library, Player, Learn, More). More opens a sheet containing Piano and
  Settings. The bar respects safe-area insets.
- **Phone:** the same five-item bottom navigation; labels remain visible. Piano
  and Settings are under More. No hover-only access path is allowed.
- **Large display:** rail defaults collapsed. Fullscreen Performance Mode may
  hide it after a clear transition and restore it on Escape, browser back, or
  a visible exit affordance.

Browser back closes the topmost transient layer first, then returns to the
previous in-app destination without changing playback or Learning state.
Direct routes restore the destination, not a modal. Closing or switching a
drawer does not reset its parent screen.

### 3.3 Layer model

Layer order is fixed: base surface; sticky app/transport chrome; nonmodal side
panel; modal dialog or bottom sheet; urgent global error. Only one modal layer
may be active. Drawers and sheets trap focus while open, restore focus on close,
close on Escape, and have a labeled close action. Routine status never uses a
blocking modal.

## 4. Canonical responsive shell

### 4.1 Breakpoint contract

| Class | CSS width | Navigation | Composition | Required behavior |
|---|---:|---|---|---|
| Phone portrait | 0–599px | Bottom, 5 items | One column | Compact app bar; panels become full-width sheets; 88-key view uses an explicit overview/active-range presentation rather than illegible compression |
| Tablet portrait | 600–899px | Bottom + top bar | One or two columns | Musical workspace first; utility content below or in sheets; hands use a deliberate portrait crop/fallback |
| Tablet landscape — primary | 900–1365px | 72px rail | 12-column grid | Workspace occupies 8–9 columns; contextual panel 3–4 columns; no musical canvas below 560px wide |
| Desktop | 1366–1919px | 72px or 208px rail | 12-column grid | Workspace plus optional persistent inspector; content capped to prevent excessive line length |
| Large display | ≥1920px | Collapsed rail by default | Centered performance canvas | Content maximum 1,600px except purposeful full-bleed visuals; typography does not scale without bound |

The mandatory browser viewport matrix is 390×844, 768×1024, 1024×768,
1440×900, and 1920×1080 CSS pixels. Tests also exercise the boundary widths
599/600, 899/900, 1365/1366, and 1919/1920.

### 4.2 Grid, safe area, and dimensions

- Root insets use `env(safe-area-inset-*)` with zero-safe fallbacks.
- Phone page padding: 12px; tablet portrait: 16px; tablet landscape: 20px;
  desktop/large display: 24px. Grid gap: 12px compact, 16px otherwise.
- Tablet landscape content is the viewport minus rail and safe areas. A
  workspace must never be squeezed to preserve an inspector: the inspector
  becomes a sheet before the workspace falls below 560px.
- Musical visualization minimum height is 300px in tablet landscape and 240px
  in portrait/phone. It may expand to the remaining viewport in Performance
  Mode.
- A 3D-hands or keyboard region must have an explicit grid span and minimum
  size. Merely avoiding horizontal overflow is not sufficient.
- Sticky chrome must never cover transport, current target, feedback, or the
  active keyboard region. Tests measure overlap, not only bounding widths.

### 4.3 Canonical tablet-landscape frame

```text
┌───────┬────────────────────────────────────────────────────────────┐
│ 72px  │ 64px contextual app bar: title · connection · global action│
│ rail  ├───────────────────────────────────┬────────────────────────┤
│       │ musical workspace (8–9 columns)  │ context (3–4 columns) │
│       │ score/falling/keys/hands/hybrid  │ queue/options/progress │
│       │                                   │ becomes sheet if tight │
│       ├───────────────────────────────────┴────────────────────────┤
│       │ transport or lesson controls, 64–80px                     │
└───────┴────────────────────────────────────────────────────────────┘
```

## 5. Surface contracts

### 5.1 Home

Home is a launch surface, not a systems dashboard. In order:

1. **Continue**: the one most recent resumable playback or practice activity,
   with song, mode, last position/section when authoritative data exists, and a
   single primary action.
2. **Quick actions**: Play a song and Start learning. Device recovery appears
   here only when it blocks the requested action.
3. **Recent songs** and **Favorites**: horizontally scrollable on phone,
   two-column collections on tablet, no more than six initial items each.
4. **Piano readiness**: a compact consumer status such as “Piano connected”,
   “Looking for your piano”, or “Piano unavailable”. Technical detail links to
   Piano / Device.

If no favorites, recents, or continuation capability exists, use one concise
empty state and a Library action. Never fabricate history in local UI state
unless a separately authorized contract defines ownership and synchronization.

### 5.2 Library

The reference collection is about 169,000 MIDI songs, so the client never
renders the whole result set.

- Search is the primary entry and starts after 250–350ms debounce or explicit
  submit. Each request has an identity; stale responses cannot replace newer
  results.
- Filter chips open a bottom sheet (phone/portrait) or side panel
  (landscape/desktop). Proposed facets: composer/artist, genre, difficulty,
  duration, hand/part availability, and source. A facet is enabled only when a
  Core query contract exists.
- Top tabs are All, Favorites, and Recent. Favorites and Recent require an
  authoritative persistence contract; until then they are absent, not locally
  simulated as a durable feature.
- Sort choices are Relevance (search only), Title, Recently added, Recently
  played, and Difficulty, each capability-gated by server support.
- Results use windowed rendering or bounded page append. The DOM target is at
  most two visible viewports plus overscan and at most 100 attached result rows
  unless profiling justifies another bound. Offset/cursor state is owned by the
  query, not the global musical state.
- A result shows title first, then compact trustworthy metadata. It exposes
  direct Play and Learn actions at tablet sizes; phone opens a labeled action
  sheet. Add to queue and Favorite appear only when those capabilities exist.
- Loading uses skeleton rows with stable geometry. Empty and error results do
  not erase the query. Retry repeats the same query identity.

The current API supports a bounded first-page search and unfiltered offset
pagination only. Full filtering, sorting, queue, favorites, and recents need
separate Core/API authorization before their controls may be enabled.

### 5.3 Player

Always visible in the Player workspace:

- song title and concise secondary metadata;
- Play/Pause as a 56px primary touch target, Stop as a separated 48px action;
- authoritative current and total time;
- an accessible timeline that is read-only until a Core seek command exists;
- visualization switcher and Performance Mode;
- compact connection state only when degraded.

Contextual controls are grouped, not spread across a button wall:

| Panel | Proposed controls | Capability boundary |
|---|---|---|
| Playback | seek, tempo, transpose, A/B loop, sections, metronome, count-in | Enable only with authoritative Core command/state contracts |
| Parts | left, right, both | Requires authoritative playback/learning part-selection semantics; never inferred from UI-006 fingering |
| Queue | upcoming songs, reorder/remove | Requires queue persistence and command contract |
| Visuals | sheet/falling/keys/hands/hybrid, camera, quality | Presentation-only controls may use accepted renderer APIs |
| Piano | LEDs on/off and supported LED presentation options | Existing semantic device command only; never low-level transport controls |

Current accepted transport supports Play/Pause/Resume/Stop and start-time
tempo. It does not authorize seek, live tempo mutation, transpose, loop,
metronome, count-in, queue, or hand selection. Phase implementation must hide
or mark unavailable any unsupported action; it must never emulate musical
authority in the browser.

Performance Mode is a presentation shell: it expands the workspace, retains a
minimal transport bar, hides navigation after an announced transition, and
restores it without changing position or state. Browser fullscreen denial is a
recoverable inline state; an in-page expanded fallback remains usable.

### 5.4 Learn

The Learn workspace contains:

- lesson header: song, active mode, section, authoritative progress;
- mode picker with plain-language descriptions;
- canonical visualization workspace;
- persistent current-target feedback;
- correct/mistake feedback that is immediate, nonblocking, and not color-only;
- section navigation and retry/restart controls where the Core supports them;
- tempo/progression controls only when backed by authoritative Learning state.

Planned visual taxonomy and authority status:

| Product label | Core relationship at specification base | UI rule |
|---|---|---|
| Watch | Playback presentation concept | Do not represent as a Learning mode without a mode/session contract |
| Listen | Playback presentation concept | Same boundary as Watch |
| Wait For Me | Existing `WAIT_FOR_NOTE` | May map label to the accepted mode without changing semantics |
| Follow | No accepted mode | Hidden until authorized |
| Left Hand / Right Hand / Both Hands | No accepted Learning part selector | Hidden until Core contract exists; fingering data is not authority |
| Section Practice | Existing `SECTION_PRACTICE` | Supported using accepted semantics |
| Repeat Until Correct | Existing `REPEAT_UNTIL_CORRECT` | Supported using accepted semantics |
| Slow Practice | No live tempo/mode contract | Hidden until authorized |
| Progressive Tempo | No progression contract | Hidden until authorized |

Mode switching is an explicit Core command and completes only after the
authoritative session/version response. A pending switch disables duplicate
submits but leaves feedback readable. Stale/version-mismatched responses are
ignored. Retry and restart cannot be synthesized as local score movement.

### 5.5 Visualization and realistic hands

The switcher offers **Sheet, Notes, Keys, Hands, and Hybrid** according to
capabilities. It is a segmented control when up to five labels fit; otherwise
it becomes a labeled menu. Switching preserves song identity, authoritative
position, transport state, Learning session/version, target, fingering plan,
and hand motion input. It performs no fetch unless a missing presentation asset
is lazily loaded; such loading never blocks or resets audio/state.

Hybrid is a composition of existing authoritative consumers, not a new musical
model. Only one high-cost 3D renderer is live. Offscreen heavy surfaces are
paused or disposed according to accepted lifecycle rules; they must resync from
the current canonical snapshot on return rather than replaying missed frames.

UI-008 may change the hands container, crop, camera-control placement, and
fullscreen composition. It may not change rigging, bones, weights, adapters,
MotionEngine, IK, fingering, contacts, collision priority, source/plan identity,
or quality semantics. Landscape defaults to a useful keyboard-and-hands crop;
portrait uses FULL or a deterministic reduced-height crop, with FOLLOW
available only through the accepted presentation control. Quality mode belongs
in Visual settings, not the primary transport bar.

### 5.6 Piano / Device and connection language

User-facing states are finite and consumer-readable:

| Internal condition | Primary copy | Allowed action |
|---|---|---|
| Ready | Piano connected | None or Test keys |
| Piano unavailable | Piano disconnected | Try again / setup help |
| Realtime reconnection | Reconnecting… | Keep current screen usable; cancel only if supported |
| LED unavailable | Piano lights unavailable | Retry / continue without lights |
| Core offline | Piano Core is offline | Retry connection / setup help |
| Snapshot stale | Updating piano state… | No destructive action; replace atomically when fresh |
| Initial fetch | Loading your piano… | Stable skeleton/progress |
| Recoverable command error | That action did not complete | Retry when idempotent; keep current truth visible |

Normal surfaces never expose Raspberry Pi, Linux, SSH, IP address, port,
systemd, WebSocket, MIDI routing, or StateStore. These may appear under an
explicit Advanced / Diagnostics disclosure, with copyable values and no
unlabeled mutation controls.

Reconnect semantics remain those already accepted: retain the last clearly
marked state while reconnecting; obtain a bounded fresh snapshot; atomically
replace replicas only when identity/version checks pass; never replay buffered
commands, auto-Stop, auto-Panic, reset playback, or advance Learning as a
recovery side effect.

### 5.7 Settings

Settings categories are:

1. **Music:** default tempo/display preferences that are genuinely supported.
2. **Visuals:** visualization default, hands visibility/camera/quality, theme
   options when implemented.
3. **Piano & Device:** onboarding and connection summary.
4. **Piano Lights:** user-level LED preferences and availability.
5. **Accessibility:** reduced motion, contrast/theme, text/display options.
6. **Advanced / Diagnostics:** opt-in technical identifiers and read-only
   diagnostics.

Preferences that do not alter musical truth may be browser-local only when
their type, default, bounds, failure behavior, and reset behavior are defined.
Device- or account-wide settings require a Core persistence contract.

## 6. Lightweight design-system contract

No UI framework dependency is approved by this ADR. Implementation should use
semantic HTML, CSS custom properties, and small testable JavaScript modules.
Any proposed third-party dependency requires an explicit justification,
license/provenance record, payload measurement, and Owner approval.

### 6.1 Tokens

```text
spacing: 4, 8, 12, 16, 20, 24, 32, 40, 48, 64px
radii:   8px control, 12px panel, 16px card/sheet, 999px pill
type:    12 caption, 14 supporting, 16 body/control, 20 section,
         28 screen title, 40 performance title (responsive cap)
line:    1.2 headings/controls, 1.45 body, 1.55 long help
layers:  base 0, sticky 10, panel 20, modal 30, urgent 40
motion:  120ms micro, 180ms control/panel, 240ms destination transition
```

Color tokens are semantic: canvas, surface-1/2/3, text-primary/secondary,
accent, focus, success, warning, danger, unavailable, and scrim. Text contrast
must meet WCAG 2.2 AA (4.5:1 normal, 3:1 large); meaningful non-text controls
and focus indicators meet 3:1 against adjacent colors. Status is never conveyed
by color alone.

### 6.2 Components

- **Buttons:** primary, secondary, quiet, destructive. Minimum hit area 44×44;
  transport primary 56×56. Disabled controls state why through adjacent copy
  or an accessible description; unsupported features are normally omitted.
- **Icon buttons:** accessible name is mandatory; tooltips supplement rather
  than replace names. No icon-only destructive action without confirmation or
  safe undo.
- **Sliders:** labeled value, keyboard steps, min/max, and authoritative pending
  state. A musical slider sends bounded commands; it never locally declares
  success.
- **Segmented controls:** one selected value, roving keyboard focus, labels
  visible; overflow becomes a menu rather than shrinking targets.
- **Cards:** group one intent; avoid nested cards. A whole-card action and
  embedded buttons cannot have competing click targets.
- **Dialogs/sheets:** title, described purpose, focus trap, Escape/back close,
  focus restoration, safe-area padding. Destructive confirmation names the
  consequence.
- **Tooltips:** hover/focus supplemental help only; never the sole mobile path.
- **States:** skeleton for initial shape, inline progress for bounded action,
  specific empty message, recoverable error with retry, and persistent offline
  banner. Layout remains stable between states.

## 7. Interaction, motion, and accessibility

- Navigation transitions use opacity plus no more than 8px translation in
  180–240ms. Panels use 180ms. Press/selection feedback begins within the next
  animation frame; musical feedback is never delayed for decorative motion.
- Visualization changes crossfade containers only after the incoming surface
  is ready; the outgoing surface remains until then. No spin/zoom or prolonged
  skeleton obscures the current musical state.
- Correct/mistake feedback uses short color, icon, and text changes. It does not
  shake the keyboard, move targets, steal focus, or queue animations.
- `prefers-reduced-motion: reduce` removes translation, parallax, animated
  scrolling, and nonessential crossfades; state changes remain immediate.
- Every action is keyboard reachable in logical DOM order. Visible focus is at
  least 2px and not clipped. Landmarks, headings, navigation labels, live-region
  priority, button state, tabs, dialogs, and progress semantics are explicit.
- Routine clock updates are not announced every frame. Connection changes and
  Learning results use concise, deduplicated live announcements.
- Zoom to 200% and text spacing overrides must not hide actions or cause
  two-dimensional scrolling of ordinary content.

## 8. UI-008 performance boundary

UI-008 is not the UI-009 4K/60 milestone. Its regression guardrails are:

- navigation/tap visual acknowledgement: next animation frame; route content
  shell present within 100ms when data is already local;
- no synchronous UI task above 50ms in deterministic browser interaction tests;
- search input remains responsive while results update;
- no more than one live 3D renderer and one active animation loop;
- heavy renderer modules/assets load only when a visualization first needs
  them, without blocking transport or realtime event processing;
- result rendering is bounded as specified in §5.2;
- resize/observer work is coalesced to one presentation frame and has no layout
  feedback loop;
- hidden surfaces do not keep polling or rendering unless required for the
  accepted authoritative replica.

These are UI regressions gates, not device/GPU or 4K/60 claims. Physical
tablet, Raspberry Pi, and hardware-GPU performance remains NOT_RUN /
NOT_VERIFIED until separately authorized. UI-009 owns sustained frame-time,
4K, GPU, thermal, memory, and long-run performance acceptance.

## 9. Acceptance-test matrix

All implementation phases require exact-revision evidence. Deterministic tests
must assert state and geometry; screenshots supplement but do not replace them.

| ID | Test | Viewports / environment | Required result |
|---|---|---|---|
| U8-NAV-01 | Navigate all six surfaces, direct route, back, reload | full matrix | Correct active item/title; back closes transient layer first; musical state unchanged |
| U8-NAV-02 | Rail, bottom nav, More sheet, expanded rail | breakpoint boundaries | Exactly the contracted navigation mode; no duplicate/inaccessible destination |
| U8-LAY-01 | Shell geometry and overlap | full matrix + boundaries | No horizontal overflow; no sticky occlusion; workspace and hands meet minimum sizes |
| U8-LAY-02 | Safe-area simulation and 200% zoom | phone/tablets | Controls remain reachable; no clipped focus or two-dimensional ordinary-content scroll |
| U8-TCH-01 | Interactive target measurement | full matrix | Every enabled target ≥44×44; primary transport ≥56×56 |
| U8-HOME-01 | Populated, empty, loading, offline Home | tablet/phone | Priority order stable; no technical dashboard language; actions capability-correct |
| U8-LIB-01 | Debounced search with out-of-order responses | tablet/phone | Stale response ignored; query retained on error; retry identity correct |
| U8-LIB-02 | 169k-scale synthetic result metadata | tablet/desktop | Bounded DOM/windowing; pagination/cursor correct; input stays responsive |
| U8-LIB-03 | Capability-gated filters/favorites/recent/queue | all | Unsupported controls absent or clearly unavailable; no fabricated persistence |
| U8-PLY-01 | Play/Pause/Resume/Stop command outcomes | tablet primary | Pending/success/rejection reconcile to authoritative snapshot; no optimistic truth drift |
| U8-PLY-02 | Timeline and advanced-control capability gates | tablet/phone | Read-only timeline without seek contract; unsupported commands never emitted |
| U8-LRN-01 | Accepted three-mode switching | tablet primary | Exact Core mode/session/version; stale response ignored; target/progress preserved correctly |
| U8-LRN-02 | Planned-mode visibility | all | Unsupported Watch/Listen/Follow/part/slow/progressive modes do not masquerade as working modes |
| U8-VIS-01 | Switch Sheet/Notes/Keys/Hands/Hybrid during play, pause, waiting | full matrix | No transport restart/seek; no Learning reset; same score/fingering identity |
| U8-HND-01 | Hands framing and lifecycle | tablet landscape/portrait/desktop | Useful crop; explicit grid span; accepted UI-007 contacts/identity/fallback/recovery unchanged |
| U8-FS-01 | Enter/exit browser fullscreen; denial fallback | tablet/desktop | Minimal controls reachable; state unchanged; Escape/back and visible exit work |
| U8-CON-01 | Disconnect, reconnect, stale snapshot, offline, retry | tablet/phone | Consumer copy; last state marked; atomic fresh replacement; no replay/Stop/Panic/advance |
| U8-A11Y-01 | Keyboard, focus trap/restore, names, live regions | desktop/tablet keyboard | WCAG interaction semantics; no focus loss or repeated clock announcements |
| U8-A11Y-02 | Contrast and reduced motion | all | AA/3:1 requirements; nonessential motion removed |
| U8-PERF-01 | Long-task, active-loop, lazy-load, bounded-DOM probes | Chromium | §8 guardrails met; no hardware/4K claim |
| U8-REG-01 | Existing canonical Python, Node, Chromium suites | accepted environment | No musical, recovery, UI-005/006, or UI-007 regression |

The full gate also includes `compileall`, TASKS YAML parsing, static asset
allowlist/package-data validation, `git diff --check`, and an exact diff audit.
Any test requiring an API capability absent from the implementation revision is
recorded `NOT_APPLICABLE_CAPABILITY_ABSENT`, not silently passed.

## 10. Proposed bounded implementation phases

Each phase requires its own Owner authorization or an Owner-approved combined
contract. Completion never starts the next phase automatically.

### U8-P1 — Shell, routes, and design-system foundation

Create the responsive navigation shell, route/state model, semantic tokens,
core components, safe-area behavior, consumer status vocabulary, and geometry/
accessibility tests. Preserve current surface behavior inside the new shell.

### U8-P2 — Home and large-library experience

Implement Home and the Library presentation using only existing API abilities.
Add bounded result rendering, request-identity protection, empty/loading/error
states, and action sheets. Advanced filters, durable favorites/recents, and
queue remain capability-gated until separate Core/API contracts are authorized.

### U8-P3 — Player and unified visual workspace

Compose transport, authoritative timeline, visualization switcher, contextual
panels, and in-page/browser fullscreen. Preserve accepted Player coordinator,
one presentation clock, keyboard/score/fingering/hands identities, and command
outcome recovery. Do not add unsupported musical controls.

### U8-P4 — Learn workspace

Present the three accepted Learning modes with lesson header, progress,
feedback, section/retry controls, and shared visualization workspace. Planned
additional modes remain absent until their separate Core contracts exist.

### U8-P5 — Piano / Device, Settings, and recovery polish

Move technical state behind Advanced, implement consumer connection/device
states, preference categories, accessible sheets/dialogs, and deterministic
reconnect/offline coverage. No onboarding network mutation is authorized by
this phase unless separately contracted.

### U8-P6 — Integrated regression, accessibility, and candidate evidence

Run the complete matrix, cross-surface identity/recovery adversarial tests,
existing regression suites, exact scope audit, and revision-bound evidence.
Physical device/GPU results remain NOT_RUN unless a separate authorization
provides exact hardware and actions.

Recommended order is P1 → P2 → P3 → P4 → P5 → P6. P1 is the dependency for
all presentation work; P2 exercises scalable stateful collections before the
more authority-sensitive Player/Learn phases; P3 precedes P4 so Learning reuses
the single visual workspace rather than creating another renderer path.

## 11. Proposed implementation file scope

This is a proposal for the Owner's later implementation authorization, not
current write authority.

### Existing product files that may be authorized

- `src/piano_core/web/static/index.html`
- `src/piano_core/web/static/styles.css`
- `src/piano_core/web/static/app.js`
- new web-native UI modules under `src/piano_core/web/static/ui008/`
- `src/piano_core/web/__init__.py` and `pyproject.toml` only if required to
  allowlist/package newly authorized static UI-008 files

### Tests that may be authorized

- existing UI browser/contract test files only where assertions must follow the
  new shell without weakening accepted behavior
- new focused files under `tests/weblogic/` named `ui008_*.test.mjs` or
  `browser_ui008_*.mjs`
- bounded Python tests for static allowlist/package-data contracts only

### Documentation and evidence that may be authorized

- this ADR and `docs/decisions/README.md`
- `docs/operations/UI.md`
- PIANO-UI-008 entries/records in `docs/work/TASKS.yaml`, `STATUS.md`,
  `PLAN.md`, `docs/work/evidence/`, and `docs/work/handoffs/`

Any Core/API, Playback, Practice, Learning, score, fingering, device, LED,
asset, rig, or renderer-semantic file is outside this proposed scope. If an
advanced control needs such a change, it requires a separate explicit contract
before UI-008 can enable that control.

## 12. Explicit non-scope

- Musical authority, MIDI interpretation, Playback engine semantics,
  StateStore, Practice grading, Learning advancement, score projection, or
  fingering decisions.
- UI-007 MotionEngine, IK, rig, bones, weights, asset, adapter, contact,
  collision, or quality semantics. Only container/framing/presentation is in
  scope.
- Production GLB replacement, Fal distribution, licensing determination, or
  legal release approval. `LEGAL_RELEASE_GATE_PENDING` remains separate.
- New API or Core semantics for seek, live tempo, transpose, A/B loop, parts,
  metronome, count-in, queue, favorites, recents, filters, sorting, onboarding,
  or network mutation unless separately authorized.
- Full 4K/60, hardware-GPU, thermal, sustained frame pacing, or physical tablet/
  Pi acceptance; those belong to UI-009 or a separate hardware task.
- Deployment, kiosk/system configuration, remote push, main integration, or
  commercial distribution.
- UI-009 implementation or authorization.

## 13. Risks and dependencies

| Risk / dependency | Consequence | Required disposition |
|---|---|---|
| Advanced product controls lack Core contracts | Attractive UI could imply false authority | Hide/capability-gate; authorize Core work separately before enablement |
| Current monolithic `app.js` | Shell changes can couple unrelated coordinators | Extract only presentation modules; retain coordinator contracts and tests |
| 169k collection with current bounded APIs | Client-only filters cannot be complete or scalable | Define server query/persistence work separately; bounded rendering now |
| Multiple heavy visual surfaces | Duplicate loops/GPU memory and stale state | One active renderer; lazy lifecycle; canonical resync |
| Reflow around keyboard/hands | Usable regions may collapse without overflow | Minimum-size and overlap assertions at boundary widths |
| Consumer copy can obscure actionable diagnosis | Users need help without internals | Progressive disclosure; exact error mapping and Advanced diagnostics |
| Fullscreen/browser differences | Denial or exit can strand controls | In-page expanded fallback and deterministic exit paths |
| Accessibility regressions in custom widgets | Touch success but keyboard/screen-reader failure | Prefer native semantics; component-level accessibility gates |
| Fal legal gate pending | Accepted asset is not cleared for commercial distribution | Preserve gate; support future Owner-owned/licensed asset without shell redesign |
| Physical hardware remains unverified | Browser evidence cannot prove piano-distance/device performance | Record NOT_RUN / NOT_VERIFIED; separate authorized hardware validation |

Dependencies are the accepted UI-002 through UI-007 contracts, a clean exact
implementation base chosen by the Owner, and explicit Core/API capability
contracts for any advanced control the Owner elects to include.

## 14. Owner decisions required before implementation authorization

1. Choose whether implementation is authorized as six sequential checkpoints
   or one bounded branch with mandatory P1–P6 gates.
2. Decide which advanced capabilities receive separate Core/API work now:
   seek, live tempo, transpose, A/B loop, part selection, metronome/count-in,
   queue, favorites/recents, library facets/sorts, and additional Learn modes.
   Recommended default: ship the premium shell using accepted abilities and
   keep unsupported controls absent.
3. Decide whether URL routes may be bookmarkable or whether a single-page
   history model is sufficient. Recommended: bookmarkable client routes with
   server fallback only if the existing web server can support them without a
   broader routing change; otherwise hash/history state within the existing
   entry point.
4. Choose the visual identity direction within the accepted dark foundation
   (retain current palette, or authorize a bounded token refresh). Recommended:
   retain palette initially and improve hierarchy/components before restyling.
5. Decide whether physical tablet testing is part of UI-008 acceptance or a
   separate device gate. It is currently NOT_RUN / NOT_VERIFIED.

## 15. Specification conclusion

The architecture, surface map, responsive behavior, design system, interaction
and motion rules, acceptance matrix, implementation phases, proposed file
scope, non-scope, and dependency gates are defined. No product code was changed
to produce this specification. The specification status is:

**READY FOR OWNER IMPLEMENTATION AUTHORIZATION**

Implementation remains **NOT AUTHORIZED** until the Owner records a bounded
contract and resolves or defers the decisions in §14.
