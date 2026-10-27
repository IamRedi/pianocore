# ADR-DEVICE-IDENTITY-V1: Persistent Device Identity and Read-Only Descriptor (PIANO-PROD-001)

> **Status:** ACCEPTED FOR IMPLEMENTATION (locked specification authored by the
> Product Architect / Independent Reviewer; implemented under PIANO-PROD-001,
> 2026-09-14; pending independent review and Owner acceptance)
> **Owner:** Redion Cekrezi
> **Context documents:** frozen spec §1 (one logical piano device);
> `docs/operations/DEVICE_IDENTITY.md` (operational contract);
> `docs/operations/API.md` (`GET /api/v1/device`)

## Context

Piano Core runs as one appliance (the Raspberry Pi hosting the
`piano-core` runtime). Future productization items (QR Wi-Fi onboarding,
`.local` discovery, support flows) need a stable way to refer to "this
particular appliance" — but the appliance so far had no persistent
identity: hostname, MAC, and IP are all mutable or renumbered, and
none of them is a safe public descriptor.

A device identity must survive reboots and software updates, must be
created exactly once per physical appliance, must never be silently
regenerated (a new identity silently replacing the old one would break
every future binding), and must be presentable over the local API
without leaking hardware, network, path, or environment details.

## Decisions

### D1 — Identity = random UUIDv4; the record is minimal and versioned

- The identity is a random UUIDv4 in its canonical lowercase hyphenated
  form, stored at `<directory>/device.json` (production:
  `/var/lib/piano-core/identity/device.json`) as exactly
  `{"identity_schema": 1, "device_id": "..."}` — two fields, integer
  schema `1`, nothing else. Future record formats bump
  `identity_schema`; this implementation accepts ONLY `1` (older and
  newer schema integers are rejected, never migrated in place).
- Parsing is strict and fail-closed: duplicate JSON keys, extra or
  missing fields, non-object JSON, invalid UTF-8, NaN/Infinity
  constants, trailing data, records above 4096 bytes, non-canonical
  spellings, and wrong version/variant UUIDs are all rejected. UUID
  parsing is used only as a checker — the round-trip comparison
  guarantees the stored representation is never silently rewritten.
- The human presentation code `PC-` + first 12 hex digits (upper-case)
  is derived ONLY, never persisted; the full UUID remains the device
  identity. The display code is a convenience only: two different UUIDs
  may share the same prefix/display code, and such a collision does NOT
  imply the same device.

### D2 — Creation is explicit and exclusive; loading never writes

- The ONLY creation path is the operator-run command
  `piano-core-identity initialize --directory <absolute-directory>`.
  Normal application startup calls `load_identity()` once and NEVER
  generates, repairs, replaces, or imports an identity. There is no
  HTTP initialize, no reset, no repair, no force replacement, no
  supplied-UUID input, and no silent regeneration after corruption.
- Initialization validates the storage security first (existing
  directory, current-user ownership, directory mode `0700`, record and
  lock modes `0600`, no symlinks, regular files only), then takes a
  NONBLOCKING exclusive `flock` on `.initialize.lock`: a losing
  contender gets `identity_busy` (exit code 3) instead of waiting.
- Publication is atomic: the record is written to a unique temporary
  file in the same directory, flushed and `fsync`ed, published with a
  NO-OVERWRITE hard link (`link(2)`, never `rename(2)` over an existing
  file), and the directory is `fsync`ed. A reader therefore sees either
  no record or a complete record. Cleanup removes only the invocation's
  own temporary file — never a published record, never the lock file.
  Concurrent/retried initializations converge on the winner's identity
  (idempotent).
- Corrupt or unsafe state is an operator decision, not an
  auto-repair trigger: a corrupt record keeps the identity unavailable
  until the Owner explicitly replaces the file.

### D3 — Application lifecycle: one cached load, auxiliary failure semantics

- `Application.start()` loads the identity exactly once — after logging
  initialization and before any subsystem (in particular before the API
  starts). Identity I/O never happens in `__init__`, imports, request
  handlers, MIDI callbacks, playback, or practice.
- When `PIANO_CORE_DEVICE_IDENTITY_DIR` is unset/empty the feature is
  unconfigured: no identity I/O anywhere, startup unchanged.
- Any identity failure (missing, corrupt, unreadable, unsafe storage)
  is logged with its stable reason, cached as
  `device_identity_error`, and the musical appliance starts unchanged:
  `HealthState`, `/api/v1/health`, MIDI, playback, practice, library
  and LED are NEVER altered by identity problems. There is no retry
  thread, watcher, or reload.

### D4 — Read-only API descriptor with a privacy allowlist

- `GET /api/v1/device` returns exactly
  `{product, device_id, display_code, identity_schema}` on 200, served
  purely from the cached startup state (no filesystem access from
  request handling, no query parameters). When no valid identity is
  available it returns the deterministic 503 envelope
  `device_identity_unavailable`; the internal reason never reaches the
  client.
- The endpoint never exposes MAC, hardware serial, IP, hostname,
  filesystem paths, Wi-Fi details, setup secrets, bootstrap
  credentials, environment, or raw errors. The UUID is a PUBLIC device
  identity — explicitly NOT an authentication secret.

### D5 — Golden-image and clone policy

- A manufacturing golden image must contain NO `device.json`, NO
  `.initialize.lock`, and no temporary identity files, and no generated
  device ID in config or package assets. Initialization happens only
  AFTER the image is placed on an individual appliance.
- Clones made BEFORE initialization are simply two uninitialized
  appliances — no problem. Clones made AFTER initialization share the
  same identity; Piano Core does NOT claim automatic clone detection.
  Operators must initialize after imaging, not before.

## Consequences

- Future productization (QR provisioning, `.local` discovery, support
  flows) can key off `device_id`/`display_code` without touching
  musical subsystems.
- Rollback to a pre-PROD-001 release requires removing ONLY the
  `PIANO_CORE_DEVICE_IDENTITY_DIR` environment line (older releases
  reject unknown `PIANO_CORE_*` keys); the identity directory and
  record stay untouched and are picked up again when PROD-001 returns.
  See `docs/operations/DEVICE_IDENTITY.md` §7.
- Production provisioning of `/var/lib/piano-core/identity` (owner
  `piano-core:piano-core`, mode `0700`) is a real-device acceptance
  step, separately Owner-authorized after independent review.
- A future factory reset must PRESERVE the physical device identity —
  the appliance UUID is never silently regenerated (a reset may clear
  ownership, pairing, network profiles, user configuration, and
  progress, but the appliance remains the SAME device). The
  factory-reset flow itself is explicitly deferred and out of scope; if
  introduced later it must be a separate explicit, Owner-authorized
  operation.
