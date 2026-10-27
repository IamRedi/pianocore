# Piano Core Device Identity (PIANO-PROD-001)

> **Status:** Canonical operations contract for the persistent appliance
> device identity.
> **Architecture decision:**
> [`docs/decisions/ADR-DEVICE-IDENTITY-V1.md`](../decisions/ADR-DEVICE-IDENTITY-V1.md)
> **Owner:** Redion Cekrezi

## 1. What the identity is

One appliance owns exactly one stable identity:

- **`device_id`** — a random UUIDv4, canonical lowercase hyphenated
  form (example `a7f32c91-d2e4-4abc-9def-0123456789ab`). This is the
  real device identity.
- **`display_code`** — derived only, never persisted:
  `"PC-" + device_id.hex[:12].upper()` (example `PC-A7F32C91D2E4`).
  **Convenience only:** two different UUIDs may share the same 12-hex
  prefix and therefore the same display code; a display-code collision
  does NOT imply the same device. The full UUID remains the only
  authoritative identity.

The UUID is a **public device identity, not an authentication secret**.
Knowing it grants no control over the appliance.

## 2. Creation vs load (the core rule)

| Operation | Who | What |
|---|---|---|
| **Create** (once per appliance) | Operator, explicitly: `piano-core-identity initialize --directory <absolute-directory>` | Generates exactly one UUIDv4 and publishes it atomically; idempotent — an existing valid identity is returned unchanged |
| **Load** (every startup) | `piano-core` application | `load_identity()` — read-only; NEVER generates, repairs, replaces, or deletes anything |

There is **no** auto-generation at boot, no HTTP initialize, no reset,
no repair, no force replacement, no supplied-UUID input, and no silent
regeneration after corruption. A corrupt record keeps the identity
unavailable until the Owner explicitly replaces the file.

CLI exit codes: `0` = created or existing valid identity; `2` = invalid
arguments / invalid identity / storage error; `3` = initialization lock
busy (`identity_busy` — retry after the other initializer finishes).
Success prints the public identity descriptor to stdout only; failures
print a stable reason code to stderr (never a traceback, never the
descriptor).

## 3. Storage layout and permissions

Production expectation (`PIANO_CORE_DEVICE_IDENTITY_DIR=/var/lib/piano-core/identity`):

```text
/var/lib/piano-core/identity/          owner: piano-core:piano-core  mode: 0700
    device.json                        owner: piano-core:piano-core  mode: 0600
    .initialize.lock                   owner: piano-core:piano-core  mode: 0600
```

Record format (schema 1, exactly two fields, ≤ 4096 bytes):

```json
{"identity_schema": 1, "device_id": "a7f32c91-d2e4-4abc-9def-0123456789ab"}
```

The storage module enforces, fail-closed: current-user ownership,
exact modes above, regular files only, no symlinks (directory, record,
or lock), no FIFO/device/directory substitution, no recursive
chmod/chown, no directory creation or repair, and no fallback to
`$HOME`, `/tmp`, the repository, or `/opt`. It never waits on the
initialization lock: a second initializer gets `identity_busy`
immediately.

## 4. Configuration

```text
PIANO_CORE_DEVICE_IDENTITY_DIR=/var/lib/piano-core/identity
```

- Empty/unset = the identity feature is **unconfigured**: the
  application performs no identity I/O at all and
  `GET /api/v1/device` answers `503 device_identity_unavailable`.
- The value must be an **absolute** path; relative paths, NUL bytes
  and `..` components are rejected at configuration parsing. `~` and
  environment variables are NOT expanded.
- Configuration parsing performs no filesystem validation; storage
  security is validated at load/initialize time.

## 5. Corruption policy

A record that fails strict validation (wrong schema, non-canonical
UUID, extra fields, duplicate keys, truncation, oversize, invalid
UTF-8/JSON, unsafe ownership/mode/type) is an error — the identity is
reported unavailable (startup log carries the stable reason code) and
the appliance otherwise starts normally. The module never rewrites,
repairs, or regenerates. Recovery is an explicit Owner operation:
restore or replace `device.json` with a correctly formatted record
(preserving the original `device_id` whenever it is still readable),
with owner `piano-core:piano-core` and mode `0600`.

## 6. Atomicity and concurrency

Initialization publishes the record with a no-overwrite hard link
after `fsync` of a unique same-directory temporary file, under a
nonblocking exclusive `flock` on `.initialize.lock`:

- readers see either no record or a complete record (never partial);
- two concurrent initializers: exactly one wins, the other gets
  `identity_busy` (CLI exit 3) and, on retry, receives the winner's
  identity;
- a crash mid-write leaves at most an orphan `.device-*.tmp` file.
  Orphan temporary files are NOT authoritative identity records: the
  normal loader ignores them, an initializer removes ONLY its own
  temporary file, and there is no sweeping, promotion, or automatic
  cleanup of another (e.g. crashed) initializer's leftovers — removal
  of orphans is an explicit operator action; a failure AFTER
  publication (e.g. directory `fsync` failure) never deletes the
  published record.

## 7. Rollback behavior (pre-PROD-001 releases)

**Important:** releases older than PIANO-PROD-001 REJECT unknown
`PIANO_CORE_*` environment keys (fail-closed configuration).

To roll the appliance back to a pre-PROD-001 release:

1. Remove ONLY the `PIANO_CORE_DEVICE_IDENTITY_DIR` line from
   `/etc/piano-core/piano-core.env`.
2. Launch the pre-PROD-001 release as documented.

Do NOT touch `/var/lib/piano-core/identity/` — the record and lock
remain untouched on disk. When the PROD-001-or-later release returns
(re-add the env line), it loads the SAME identity again. The deploy
helper is deliberately NOT modified to hide this step: the incompatibility
is a documented operator action.

## 8. Golden manufacturing image rule

A golden image must contain:

- NO `device.json`;
- NO `.initialize.lock`;
- no temporary identity files (`.device-*.tmp`);
- no generated device ID in any config file or package asset.

Identity initialization happens only **after** the image is placed on
an individual appliance (`piano-core-identity initialize`).

## 9. Clone limitations

- Cloning BEFORE initialization: harmless — two uninitialized
  appliances, each initialized individually afterwards.
- Cloning AFTER initialization: both clones carry the SAME identity.
  Piano Core does **not** claim automatic clone detection. Operators
  must initialize after imaging, never before.

## 10. Factory reset (future policy)

A future factory reset PRESERVES the physical device identity: the
appliance UUID must NEVER be silently regenerated. A reset may clear
ownership, pairing, network profiles, user configuration, and progress —
but the `device_id` survives, so the appliance remains the SAME device
for every future consumer. Factory-reset implementation is NOT part of
PIANO-PROD-001 and remains out of scope; if introduced later it must be
a separate, explicit, Owner-authorized operation with its own review —
never an automatic or API-reachable behavior.

## 11. API surface

`GET /api/v1/device` (see [`API.md`](API.md)): read-only descriptor
`{product, device_id, display_code, identity_schema}` from cached
startup state; `503 device_identity_unavailable` when unconfigured or
unavailable. The endpoint never exposes MAC, hardware serial, IP,
hostname, filesystem paths, Wi-Fi details, setup secrets, bootstrap
credentials, environment, or raw internal error reasons.
