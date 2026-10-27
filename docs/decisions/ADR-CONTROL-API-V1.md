# ADR-CONTROL-API-V1: Local Control API Foundation (PIANO-P012)

> **Status:** ACCEPTED (implemented and verified under PIANO-P012, 2026-09-13)
> **Owner:** Redion Cekrezi
> **Context documents:** frozen spec §1/§3/§17 (API consumers);
> `docs/operations/API.md` (operational contract)

## Context

P001–P011 established canonical subsystems (Application lifecycle,
StateStore, MIDI input/output, Playback Core, LibraryService with
production service access). A local control surface was always planned
("future API/LED/HUGO/UI consumers read the StateStore") but not yet
implemented. P012 authorizes the smallest production-quality LOCAL
control API over these subsystems without duplicating business logic.

## Decisions

### D1 — HTTP stack: Python standard library `http.server.ThreadingHTTPServer` (no new dependency)

- The appliance deployment model is offline/reproducible (controlled
  artifact set, `--no-index` installs). A third-party framework
  (FastAPI/uvicorn, Flask, aiohttp) would introduce a second
  controlled-artifact provisioning chain of the kind P004/P005 showed to
  be costly (python-rtmidi aarch64/py3.13 build), for no functional
  gain at this surface size (10 routes, low traffic, trusted LAN).
- The project core is deliberately synchronous/threaded (P001 deferred
  asyncio). ThreadingHTTPServer integrates directly with that model:
  one thread per connection, daemon threads, bounded joins.
- The typed request/response boundary, router, bounded bodies, and
  deterministic error model are implemented in-repo
  (`piano_core/api/`), keeping the boundary explicit and testable.
- Consequence: no WebSocket/live-event stream in P012; a future task
  may revisit the stack if the UI requires push, without changing the
  canonical subsystems.

### D2 — Network exposure: loopback default; explicit LAN binding in production; no authentication in P012 (recorded limitation)

- Safe default: `PIANO_CORE_API_ENABLED=false`, `api_host=127.0.0.1`.
  No hidden `0.0.0.0` fallback; the host validator accepts IPv4
  literals or `localhost` only.
- Production decision (explicit): bind the appliance LAN IPv4
  `192.168.1.50:8788` so trusted local clients can control playback.
- The API is unauthenticated — an explicitly recorded trusted-LAN
  limitation, not an Internet-safe surface (no port forwarding/TLS/
  cloud). Mitigations in P012: no PANIC route, owned-only STOP,
  song_id-only playback (no arbitrary paths), bounded bodies.

### D3 — PANIC is intentionally NOT exposed through the API

Global MIDI silence (CC64=0/CC123/CC120 × 16 channels) stays an
operator-side action. No route can reach PANIC; normal STOP remains
owned-only. Recorded here and enforced by boundary tests.

### D4 — Tempo contract: supplied at play time only

`tempo_multiplier` is validated (0.25..4.0 inclusive) and fixed for the
session. A mid-play tempo-change endpoint would require new engine
semantics — deliberately out of P012 scope.

### D5 — One automated playback per appliance (service + CLI share one advisory lock)

The P009 CLI lock moves from per-process `/tmp` (the service's
`PrivateTmp=true` would silently decouple them) to the shared
`/run/lock/piano-core-playback.lock`; the service's PlaybackManager
participates in the same lock. Conflicts surface as
`409 playback_already_active`. Cross-user locking works via a
read-only-descriptor fallback (flock does not require write access).

### D6 — API failure is auxiliary/degraded (service stays healthy)

A bind failure logs `api unavailable (bind failed): …` and the
application starts healthy without an API — identical to the library
degrade contract (P011). Shutdown ordering: API server first, then any
active playback session (owned-note release), then the existing
input/output teardown.

### D7 — Production playback requires the canonical output to be open

`PIANO_CORE_MIDI_OUTPUT_ENABLED=true` in production (explicit decision,
installed by the `api-config` helper subcommand). Startup still emits
nothing (P007); the one service-owned RtMidiOut is the sanctioned
playback surface, not an "idle" client; P007 multi-subscriber
coexistence with the legacy service remains verified.

## Consequences

- Zero new runtime dependencies; offline wheel deployment unchanged.
- The full `/api/v1` contract, error model, and operations guidance live
  in `docs/operations/API.md`.
- Future tasks (UI, auth, WebSocket/streaming, search pagination
  upgrades) extend this foundation without changing canonical
  subsystems.
