# ADR-020 — Immersive Premium Stage UI

- **Status:** ACCEPTED AS OWNER DIRECTIVE (2026-10-02) — implementation in
  progress on `feat/immersive-premium-ui`; Owner visual review pending
- **Task:** PIANO-IMMERSIVE-UI (a NEW presentation task; explicitly NOT
  PIANO-UI-009, which remains PLANNED / NOT AUTHORIZED)
- **Base:** UI-008 final acceptance checkpoint
  `498895969e95084858cacc73e52597582d31ca44` (accepted U8-P6 implementation
  `d66cd286e1b03840108dc3e082b1ae16f846257b`)
- **Builds on:** ADR-014 through ADR-019 and the accepted UI-008 experience
- **Directive record:** `docs/work/evidence/PIANO-IMMERSIVE-UI-REQUEST-20261002.md`

## Context

The technically accepted UI-008 experience presents Player and Learn as
card stacks: the visualization, keyboard and hands sit in separate cards
one below another, and essential controls can require page scrolling. In
real use the Owner wants an instrument, not a dashboard.

## Decision (composition mandate)

1. **Stage composition** for Player, Learn and Piano: a compact head
   (title + small status + view selector + "More"), ONE dominant scene
   filling all remaining viewport height, and a compact essential-actions
   strip below. App navigation stays accessible but not dominant. In normal
   use Player/Learn need no page scroll to reach Play/Pause or
   Start/Stop — without browser fullscreen. Home, Library and Settings
   keep normal document scroll; on unusually short viewports the stage
   scrolls as a fallback (controls are never clipped or hidden).
2. **One active view at a time** through a compact view selector
   (Falling | Piano | Hands | Sheet), the SAME mechanism on Player and
   Learn; the Piano route presents the keyboard. Falling is the default.
   Hybrid is NOT a workspace view; the accepted Hybrid renderer engine and
   its sheet/falling/hybrid contract remain untouched and configurable —
   the Falling view presents the configured notes mode. No new value (e.g.
   "hands") is ever sent to that contract.
3. **Shared visual workspace**: the scene is ONE DOM container holding the
   single renderer instances (visual stage, 88-key keyboard, 3D hands),
   moved between per-route slots (accepted node-move pattern; renderer
   instances, canvases, RAF loops and listeners are never duplicated).
   Inactive views stop painting (render gating on the presented view);
   polls and musical engines keep running. View switching never issues a
   musical command, never changes position/session/target/fingering, and
   re-renders current state on return.
4. **Essential-only surfaces**: Learn keeps song, current target, real
   progress, Start/Stop and the view selector visible; mode/section/
   repetition configuration and detailed info move to a secondary panel.
   Player keeps title, scene, transport, real time/progress and the view
   selector; tempo, LED quick toggle and renderer mode move to the
   existing sheet. Piano keeps the keyboard main + compact connection
   status; LED controls open in a panel. Errors needing intervention stay
   visible and are never hidden for aesthetics.
5. **Premium presentation**: the existing design system with a darker,
   contained scene; controlled accent; fewer borders and cards on stage
   surfaces; touch targets stay ≥44×44 CSS px with the primary action
   ≥56×56. No invented metadata, slogans or unsupported mockup controls.
   The Owner's Drive concept images are visual direction only.

## Consequences

- New presentation module `ui008/workspace.js` (view state + placement);
  app.js gains render gating and the route/sheet wiring. No backend,
  framework, database or PWA work; ERR_ADDRESS_UNREACHABLE remains a
  separate concern.
- Old tests that asserted the card layout are updated to the new
  composition with documented expectation changes; no musical or
  functional assertion is weakened.
- Hands positioning inside the scene is presentation placement + resize
  only; GLB, rig, IK, MotionEngine, calibration and contacts are frozen
  (UI-007). Any hands-positioning concern is recorded separately for
  later.
- UI-009 (performance/4K/60) and AUDIO/APP/LEDKIT remain NOT_AUTHORIZED;
  this work makes no performance claim.
