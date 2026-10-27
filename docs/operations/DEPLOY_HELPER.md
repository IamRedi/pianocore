# Piano Core Deployment Helper (`piano-core-deploy`)

> **Purpose:** maximum executor automation for controlled Piano Core
> deployments on the real appliance (`pianopi`) without Owner terminal
> work — while granting **no unrestricted sudo**.
> **Authorized by:** Redion Cekrezi (2026-09-12, "one-time, narrowly
> scoped sudo bootstrap" directive; security/recovery corrections and the
> final permanent-helper review corrections applied the same day:
> executor-writable staging, service-user import check, complete
> console-entrypoint rollback, bootstrap atomicity, **persistent staging
> path**, **version-crossing dist-info rollback**).
> **Canonical source files:** [`ops/sudo/piano-core-deploy.sh`](../../ops/sudo/piano-core-deploy.sh)
> (the helper) and [`ops/sudo/piano-core-deploy.sudoers`](../../ops/sudo/piano-core-deploy.sudoers)
> (the rule). Installed by a ONE-TIME Owner bootstrap; the bootstrap block
> embeds byte-identical copies of both files.

## 1. Design

One root-owned, non-user-writable script at
`/usr/local/sbin/piano-core-deploy` (root:root `0755`), invoked only via:

```text
sudo /usr/local/sbin/piano-core-deploy <subcommand>
```

The sudoers drop-in `/etc/sudoers.d/piano-core-deploy` (root:root `0440`,
visudo-validated at install) grants **exactly one command** to user
`pianopi`:

```text
pianopi ALL=(root) NOPASSWD: /usr/local/sbin/piano-core-deploy
```

No wildcards, no argument whitelists in sudoers (argument/path validation
happens inside the helper and fails closed), no `ALL`, no shell, no
`sudoedit`. `sudo` remains password-required for everything else.

### Subcommands

| Subcommand | Effect |
|---|---|
| `deploy <wheel> <sha256>` | validates inputs → timestamped rollback backup (package, dist-info, **all console entrypoints**, env file, META manifest) → `pip install --no-index --no-deps --force-reinstall` into `/opt/piano-core/venv` → post-install import check **as the service user** → restart **only** `piano-core.service` → post-checks (piano-core active; legacy still active). Prints the backup path and hash. |
| `rollback <backup-dir>` | restores exactly the pre-deploy state for **any current deployed version**: removes the current package directory and **all current Piano Core dist-info directories** (strictly pattern-validated), removes entrypoints introduced by the deployment(s), restores the backed-up package + dist-info + env + the exact pre-deploy entrypoint set. Recovery path: works even when piano-core is down (legacy health still required). |
| `status` | read-only: `is-active` for piano-core and legacy, plus piano-core unit properties. |

### Key security properties

- **Persistent executor-writable staging without sudo:**
  `/var/lib/piano-core-incoming` is created ONCE by the bootstrap owned
  `pianopi:pianopi` mode `0700` — it survives reboots and `/tmp` cleanup,
  so staging never reintroduces an Owner sudo dependency. The helper
  re-verifies at deploy time that the directory is not group/world-
  writable (fail closed), and re-checks the wheel's canonical path after
  symlink resolution. The backup root
  `/var/lib/piano-core-deploy/backups` remains separately root-owned and
  root-only (`0700`).
- **No arbitrary root execution of deployed package code:** root touches
  the wheel only through pip's file unpacking (`pip install` — trusted
  tooling, no package code run). The post-install `import piano_core`
  check runs as the unit's configured service user, read LIVE from
  `systemctl show piano-core.service -p User` (inspected 2026-09-12:
  `piano-core`); if the user cannot be determined or is `root`, the helper
  refuses to run the check at all.
- **Version-crossing, entrypoint-complete rollback:** at backup time the
  helper enumerates the pre-deploy console entrypoint set from the
  installed dist-info `RECORD`, copies each launcher into the backup, and
  records each name in META (absence = recorded absence) together with
  the backed-up dist-info name. Post-install, the new entrypoint set is
  recorded (`after_deploy_entrypoint=`). At rollback, BEFORE deleting
  anything, the helper identifies ALL current `piano_core-*.dist-info`
  directories under site-packages, validates each against the strict
  pattern (`piano_core-<version>.dist-info`, PEP 440 version charset) —
  refusing to operate on anything else — and captures their entrypoint
  RECORD sets. It then removes the current package directory and ALL
  current Piano Core dist-info directories (so a deployed
  `piano_core-0.2.0.dist-info` cannot survive a rollback to a
  `0.1.0.dev0` backup), removes every entrypoint in (captured current
  sets ∪ after-deploy set) minus the pre-deploy set, and restores exactly
  the backed-up package, dist-info (under its original name), env file,
  and pre-deploy entrypoints. Unrelated distributions are never touched;
  names are strictly validated before any file operation. This is generic
  for any future Piano Core version or entrypoint.
- **Legacy service never modified:** the only systemd unit name in the
  helper is `piano-core.service`; `pluto-piano-server.service` appears
  only in read-only `is-active` guards. No reboot/poweroff path exists.
- **Auditability:** every invocation logs to syslog (tag
  `piano-core-deploy`) and `/var/log/piano-core-deploy.log`; each backup
  records a META manifest (dist-info name, wheel path, SHA-256, UTC
  timestamp, entrypoint sets). Backups live under
  `/var/lib/piano-core-deploy/backups` (root-only `0700`), are never
  deleted by the helper, and their paths are printed and recorded in task
  evidence — preserving the existing rollback/evidence discipline.
- A failed deploy **never** auto-rolls back: the exact rollback command is
  printed and the executor decides explicitly.

## 2. Bootstrap atomicity

The one-time Owner bootstrap is fail closed (v5 correction, 2026-09-12):
**before any privilege probe**, the bootstrap invalidates pianopi's cached
sudo timestamp (`runuser -u pianopi -- sudo -k` — touches only pianopi's
own credential cache, no sudoers change). Without this, the global
timestamp (`timestamp_type=global`, `010_global-tty`) from the Owner's own
bootstrap authentication would make the generic-sudo negative probe
false-positive for ~15 minutes and revoke a perfectly valid grant
(observed and root-caused in the read-only sudo audit of 2026-09-12: **no
NOPASSWD rule exists on the appliance**; the only sudo grant is the stock
password-required `%sudo` group rule). With the cache invalidated first,
the positive probe proves the narrow grant works via NOPASSWD, and the
negative probes prove generic sudo still requires a password.

- an invalid sudoers drop-in is removed before anything takes effect;
- if ANY post-install verification fails (full `visudo -c` re-parse,
  passwordless helper invocation, generic-sudo probe, unknown-subcommand
  probe, staging-dir writability probe), the newly installed grant file
  `/etc/sudoers.d/piano-core-deploy` is removed before exiting;
- the only file the bootstrap can ever remove is its own grant file; no
  unrelated pre-existing sudo configuration is read, modified, or removed;
- negative probes are harmless by construction (`sudo -n /usr/bin/true`
  executes nothing; the unknown-subcommand probe is rejected inside the
  helper).

## 3. One-time bootstrap

Executed ONCE by the Owner on `pianopi` (2026-09-12). Verbatim block
recorded in `docs/work/handoffs/PIANO-P009.md` §7.3. After it completes,
Piano Core deployment and rollback are fully executor-automated:

```text
scp wheel -> /var/lib/piano-core-incoming/  (executor, ssh, no sudo)
sudo -n /usr/local/sbin/piano-core-deploy deploy /var/lib/piano-core-incoming/<wheel> <sha256>
sudo -n /usr/local/sbin/piano-core-deploy status
sudo -n /usr/local/sbin/piano-core-deploy rollback /var/lib/piano-core-deploy/backups/<backup>   # only if needed
```

## 4. Verification battery (static + functional, applied 2026-09-12)

Applied to the canonical files before the bootstrap was handed over:

1. helper `bash -n` PASS; bootstrap `bash -n` PASS; embedded content
   byte-identical to the repo files PASS.
2. **No root execution of deployed package code:** the helper contains
   exactly one `python3` invocation, wrapped in `run_as_user
   "$SERVICE_USER"` (runuser/su; user read live from the unit, root
   refused).
3. **Legacy never modified:** every `systemctl` line uses only
   `is-active` / `show` / `restart "$UNIT"` / `stop "$UNIT"` /
   `start "$UNIT"`; `pluto-piano-server.service` appears only in
   `is-active` guards and log/die text.
4. **No reboot path:** zero command-context matches for
   reboot/shutdown/poweroff/halt in the helper; the bootstrap's only
   `reboot` string is a rejected-subcommand probe.
5. **Persistent staging without sudo:** `/var/lib/piano-core-incoming`
   created by the bootstrap (`pianopi:pianopi` `0700`); bootstrap verifies
   pianopi writability; helper rejects group/world-writable staging.
6. **Version-crossing entrypoint-complete rollback:** functional
   simulation against synthetic pre/post RECORDs (0.1.0.dev0 → 0.2.0,
   using the helper's real `list_record_entrypoints`, `safe_name`, and
   `valid_dist_info_name` functions) proved: after rollback ONLY
   `piano_core-0.1.0.dev0.dist-info` remains, the deployed
   `piano_core-0.2.0.dist-info` is gone, an unrelated distribution
   (`someotherdist-1.0.dist-info`) is untouched, exactly the pre-deploy
   entrypoint set is restored, and the newly introduced
   `piano-core-play`/`piano-core-future` entrypoints are removed.
7. **Generic sudo remains unavailable:** single-command rule + bootstrap
   probes; any broader grant detected at bootstrap aborts with revocation.
8. **TOCTOU protection:** functional simulation — after validation the
   source wheel was mutated; the root-owned verified copy remained
   unchanged and the pip path pointed exclusively at the work copy.
9. **Mutation serialization:** functional simulation using the helper's
   real `acquire_mutation_lock` — the first mutation held the lock, a
   second deploy/rollback was refused without any state change.

## 5. Deployment incident record (2026-09-12)

The first real deploy (12:13:29) failed **fail-closed** at backup creation:
the v4 `mktemp` restructure had dropped the explicit
`mkdir -p "$BACKUP/bin"`, so the entrypoint backup loop aborted on its
first copy (`set -e`). Consequences: NO install, NO restart, NO completed
backup; services/uptime/input/output-state unchanged; the work copy was
cleaned by the trap. One inert partial backup directory
(`piano-core-rollback-20260912-101329.ZXY4FhRv`) remains under the
root-only backup root — rollback refuses incomplete backups, so it is
harmless and is retained as evidence. Fix: one line
(`mkdir -p "$BACKUP/bin"` directly after `mktemp -d`), canonical source
updated, version-simulation updated to model the exact backup sequence
(the earlier simulation had pre-created the fixture `bin/` directory,
masking the bug). Installing the fixed helper requires one Owner bootstrap
re-run (idempotent; `/usr/local/sbin` is root-owned and the narrow grant
covers only executing the installed helper, which cannot rewrite itself).

**Third fail-closed abort (deploy attempt #3, 12:16:04):** backup COMPLETED,
pip install SUCCEEDED (P009 package + `piano-core-play` entry point
installed), then the post-install import check crashed BEFORE executing:
`resolve_runas` executed the wrapper binary (bare `runuser` → empty stdout)
instead of printing its path, so `run_as_user` built an empty command.
Consequence: the appliance holds P009 on disk while the running service
still has the pre-P009 code in memory (no restart occurred). Fix:
`resolve_runas` prints the wrapper path, `run_as_user` enforces a
sanctioned-binary allowlist (`/usr/sbin/runuser`, `/usr/bin/runuser`,
`/usr/bin/su`) and fails closed otherwise; functionally simulated (real
functions, stubbed runners: argv wiring, quoting, refusal paths).

**Second fail-closed abort (deploy attempt #2, 11:36:26):** backup COMPLETED
(valid rollback point), then pip rejected the root-owned work copy because
the helper had renamed it to a fixed name (`deploy.whl`) — wheels must keep
their canonical filename. No install/restart occurred (pip validates the
filename pre-flight; the installed package was untouched). Fix: the work
copy preserves the staged wheel's basename (already strict-pattern
validated); functionally verified with real pip — the old name reproduces
the exact error, the preserved basename installs successfully. One inert
complete backup from this attempt exists (valid rollback point; harmless).

**Helper v14 — PIANO-P012 `api-config` subcommand (2026-09-13):** additive,
narrowly scoped evolution (P011's `provision`/`library-*` semantics
untouched; the `ensure_env_line` function was hoisted from the `provision`
branch to top level with a byte-identical body). New `api-config`
subcommand: fail-closed pre-check that the INSTALLED package already
supports the API config keys (AppConfig rejects unknown env, so the wheel
must be deployed first), then idempotent `ensure_env_line` for
`PIANO_CORE_MIDI_OUTPUT_ENABLED/ API_ENABLED/ API_HOST/ API_PORT`, then
restart of ONLY `piano-core.service` with the usual active checks (legacy
never touched). Validated before the Owner bootstrap: `bash -n` PASS and a
functional sandbox simulation (stubbed systemctl/runuser/venv, sandboxed
paths) covering fresh append, idempotence (no duplicate lines, restart
exactly once per run), pre-check failure (env untouched, no restart), and
present-but-different-value refusal. Canonical source SHA-256
(`ops/sudo/piano-core-deploy.sh`):
`aafaf36fb4a6c387025b91906642fd6b51751742c687215027c74fde17f20a6f`.
This supersedes the v13 hash recorded in the P011
`privileged_installed_state_parity` (that record remains the historical
P011 acceptance value; the four systemd unit files are unchanged by v14).

## 6. Removal

To revoke the grant entirely: `sudo rm /etc/sudoers.d/piano-core-deploy`
(and optionally `sudo rm /usr/local/sbin/piano-core-deploy`). Backups and
logs are historical records and are kept.

**Helper v15 — PIANO-P014 `led-config` subcommand (2026-09-13):** additive
evolution identical in shape to v14's `api-config`. Fail-closed pre-check
that the INSTALLED package already supports the LED config keys
(`led_enabled/led_ws_host/led_ws_port/led_mode/led_brightness`; AppConfig
rejects unknown env, so the P014 wheel must be deployed first), then
idempotent `ensure_env_line` for
`PIANO_CORE_LED_ENABLED=true`, `PIANO_CORE_LED_WS_HOST=192.168.1.50`,
`PIANO_CORE_LED_WS_PORT=8787`, `PIANO_CORE_LED_MODE=all`,
`PIANO_CORE_LED_BRIGHTNESS=100`, then restart of ONLY
`piano-core.service` with the usual active checks (legacy never touched;
read-only is-active re-verified). Production LED endpoint is an explicit
configuration decision binding `192.168.1.50:8787` (never 0.0.0.0;
unauthenticated trusted-LAN surface — same model and recorded limitation
as the P012 API bind; ADR-LED-KEYBOARD-V1). Validated before the Owner
bootstrap: `bash -n` PASS and a functional sandbox simulation (stubbed
systemctl/runuser/venv, sandboxed paths) covering: pre-check failure
(env untouched, no restart), success (exactly the 5 lines, restart only
piano-core.service, legacy is-active re-checked), idempotence (no
duplicate lines, exactly one restart per run), and present-but-different
value refusal. Canonical source SHA-256 (`ops/sudo/piano-core-deploy.sh`):
`4396dcf42e3b273a9a525fc9d9eeaf43391f646906d8ecac5369f9e09dd1d3a4`.
This supersedes the v14 hash recorded for P012 (that record remains the
historical P012 acceptance value; the four systemd unit files are
unchanged by v15).


## U8-P7-R1 release record — 2026-10-10

The accepted narrow helper deployed the reviewed wheel `4ea13b8f478405636ac0cbace2b62efe5fc8e1478d36d3ff63ee3deba6d2e4e2`; 105 installed sources and 28 served assets matched. The first mandatory catalog/progress load failed with process descriptor exhaustion; the load was interrupted and the helper restored `/var/lib/piano-core-deploy/backups/piano-core-rollback-20261010-072014.fSDgNiir`. All 103 earlier source files and runtime catalogs/progress were preserved. The new Studio candidate is not deployed or accepted. The backup remains valid; no helper or privilege changes were made.

Future bounded recovery must verify complete catalog pages plus progress fanout under native descriptor limits before deployment. Retain partial load failure evidence and stop promptly on resource exhaustion. This record grants no new deployment authority. [Full R1 evidence](../work/evidence/PIANO-UI-008-U8-P7-R1-RECOVERY.md).
