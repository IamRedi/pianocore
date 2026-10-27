# ADR-WEB-UI-V1: Responsive Web / Phone UI Foundation (PIANO-P013)

> **Status:** ACCEPTED (implemented and verified under PIANO-P013, 2026-09-13)
> **Owner:** Redion Cekrezi
> **Context documents:** frozen spec §1/§3 (UI consumers, appliance model);
> `docs/operations/UI.md` (operational contract);
> `docs/operations/API.md` (P012 API contract — unchanged by P013)

## Context

P012 delivered the verified `/api/v1` control API (trusted-LAN,
unauthenticated, bound explicitly to `192.168.1.50:8788`). P013
authorizes the smallest usable product frontend over that API: system
status, library browse/search/select, play/pause/resume/stop, bounded
tempo, playback state, and physical piano status — usable on phone,
tablet, and desktop browsers on the local LAN. The browser must stay a
CLIENT of the API: no second truth store, no backend logic reimplementation.

## Decisions

### D1 — First-party static frontend; zero build tooling, zero npm/CDN/external assets

- Vanilla HTML + CSS + browser-native ES modules (`piano_core/web/static/`),
  served from inside the wheel via `importlib.resources`. No Node.js
  runtime, no npm dependency chain, no bundler, no CDN assets, no
  externally hosted fonts (system font stack only).
- Rationale: the appliance must work offline; deployment stays ONE
  Piano Core wheel; the UI surface is bounded; P012's API already
  exists; a second frontend build/dependency ecosystem would multiply
  the controlled-artifact cost P004/P005 showed, for no functional gain
  at this surface size.
- Consequence: no framework-level reactivity; the UI uses a small
  fetch→render→reconcile loop with the canonical API as the only source
  of truth. A future task may revisit only with an overwhelming
  architectural reason.

### D2 — Same-origin serving by the EXISTING HTTP server; allowlisted assets only

- The UI is served by the P012 `ThreadingHTTPServer` (no second
  listener, no new port, no CORS): `GET /` and `/ui/` serve the UI
  document; `/ui/<asset>` serves exactly the allowlisted asset names in
  `piano_core.web.UI_ASSETS` (index.html, styles.css, app.js,
  favicon.svg, manifest.webmanifest).
- The allowlist IS the path-security boundary: resolution is a
  membership check on the decoded asset name (`importlib.resources`
  read inside the installed package). No filesystem join, no directory
  listing, no arbitrary path serving; traversal and percent-encoded
  escapes never resolve and fall through to the deterministic JSON
  `404 not_found` error model.
- Non-GET methods on UI paths → `405 method_not_allowed` (same error
  model). `/api/v1/...` routing is untouched and always takes
  precedence in its namespace.
- Consequence: adding a UI asset requires a wheel change (deliberate —
  assets ship with the product, versioned together with the API).

### D3 — `ui_enabled` defaults to TRUE (serving gate only, no new exposure)

- `PIANO_CORE_UI_ENABLED` (default `true`) gates only whether non-API
  paths serve static assets on the already-configured API bind. The UI
  adds no listener, no dependency, and no network surface of its own;
  when `api_enabled=false` nothing binds regardless.
- Defaulting to true avoids a production env change and helper revision
  for the P013 deployment (the trusted-LAN/no-auth security model is
  byte-for-byte unchanged). `false` restores the bare P012 API-only
  surface (verified by tests).

### D4 — Polling, not WebSockets; bounded request hygiene in the browser

- Canonical backend state stays authoritative: the UI polls
  `/api/v1/health` + `/api/v1/state` at 1 s while the tab is visible
  (visibility-aware, overlap-guarded, one failed poll degrades to an
  "unreachable" state instead of destroying the UI), searches on
  debounced user input (300 ms) with monotonic stale-response tokens,
  and reconciles every command with the API's returned snapshot.
- Control enablement is always derived from the CANONICAL playback
  snapshot (IDLE/COMPLETED/FAILED → play; PLAYING → pause+stop; PAUSED →
  resume+stop; STOPPING/unavailable → nothing) — never from a stale
  command response (defect found and fixed during real-Pi verification).
- Consequence: no push latency guarantees in P013 (acceptable for a
  playback appliance; WebSockets remain explicitly out of scope).

### D5 — Security posture: trusted-LAN unchanged; hostile strings rendered inert

- P012's model is preserved exactly: no auth, no TLS, trusted LAN only,
  not Internet-safe, no new listener. P013 introduces no secrets, no
  environment dumps, no eval, no dynamic script injection, and no
  third-party code.
- All server-derived strings (song filenames/titles, error messages)
  are rendered with `createElement`/`textContent` ONLY; markup
  construction for server data is forbidden and enforced by a static
  contract test suite. Search queries are URL-encoded via
  `URLSearchParams`; the search limit is clamped client-side to the
  server's bound; tempo is clamped to the canonical 0.25..4.0 before
  any request. PANIC is not referenced anywhere in the frontend.
- Asset caching uses conservative `Cache-Control: no-cache` (early
  appliance phase: a deployment can never strand a browser on stale
  JS/CSS; deterministic asset versioning is deferred until needed).

## Consequences

- Product wheel grows by the `piano_core.web` package (5 static assets
  + resolver); zero new runtime dependencies; API routes unchanged.
- Verification is three-layered: backend serving tests (real sockets),
  node-run unit tests for the pure frontend logic, static contract
  tests for the security/coupling invariants, plus real-browser
  responsive and workflow verification on the live appliance.
- Known limitation (recorded): library search matches catalog
  filenames/relpaths, not enriched titles — a title-only query may
  return no results even though the song is listed. This is P012 API
  semantics, not a P013 defect; a future task may extend search scope.
