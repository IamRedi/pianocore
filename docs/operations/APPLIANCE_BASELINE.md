# Piano Core Appliance Baseline

> **Task:** PIANO-P002 (Package and Appliance Service Baseline)  
> **Status:** Local contract verified; **PIANO-P002 DOES NOT CLAIM RASPBERRY PI DEPLOYMENT.**  
> **Scope note:** This document defines the intended appliance layout and service contract. Everything here is verified locally on the development host (offline build, isolated install, installed-runtime smoke, unit syntax check). Raspberry Pi / real-Python-3.11 execution is a separate, later task (currently PIANO-P003, NOT AUTHORIZED).

---

## 1. Intended filesystem layout

```
/opt/piano-core/              appliance application root (root-owned)
    venv/                     dedicated virtual environment (root-owned)
    share/                    deployed documentation (e.g. APPLIANCE_BASELINE.md)
/etc/piano-core/
    piano-core.env            appliance configuration (systemd EnvironmentFile)
```

- `/opt/piano-core` holds the application and its isolated virtual environment; it is **not** a shared system Python environment.
- `/etc/piano-core/piano-core.env` is the single configuration surface of the skeleton; it is root-owned and read by the service at start.
- The canonical LED mapping (`hardware/led/keyboard_mapping.csv`, frozen spec §13) is **not** part of P002 — no LED/hardware artifacts are deployed by this baseline.

## 2. Python virtual environment location

`/opt/piano-core/venv` — one dedicated venv per appliance, created by the installation procedure. The application never runs from a source checkout on the appliance.

## 3. Package installation model

- The `piano-core` wheel is built from a tagged/committed repository revision and installed into `/opt/piano-core/venv` (offline, `--no-deps`; the application has zero runtime dependencies).
- The console entrypoint `piano-core` (defined in `pyproject.toml` as `piano_core.app:main`) is the service process.
- Upgrades replace the wheel inside the venv; configuration in `/etc/piano-core` is never overwritten by a package upgrade.

## 4. Configuration file location

`/etc/piano-core/piano-core.env` — systemd `EnvironmentFile` (plain `KEY=value`). Template: [`ops/systemd/piano-core.env.example`](../../ops/systemd/piano-core.env.example). Only `PIANO_CORE_MODE` and `PIANO_CORE_LOG_LEVEL` are supported by `AppConfig` (fail closed on unknown/invalid values; no secrets).

## 5. systemd service location

Unit source: [`ops/systemd/piano-core.service`](../../ops/systemd/piano-core.service). On an appliance it is installed as `/etc/systemd/system/piano-core.service` (deployment task). Contract: dedicated `piano-core` system user/group created by the installation procedure (the unit references it explicitly — it is part of the installation contract, not an assumption about an existing user); `After=local-fs.target`; `WantedBy=multi-user.target`.

## 6. Process lifecycle

`Type=simple`, `ExecStart=/opt/piano-core/venv/bin/piano-core`. The runtime starts (config validation → logging → READY), waits efficiently on SIGTERM/SIGINT, and exits cleanly (STARTING→READY→STOPPING→STOPPED). There is no daemonization, no shell wrapper, no internal supervision.

## 7. Logging destination

stderr → **systemd journal** (journald captures the service's standard error). Deterministic single-line records with UTC ISO-8601 timestamps. No files, no external telemetry. Journal access/retention uses host defaults; no journald tuning is part of this baseline.

## 8. Restart policy

`Restart=on-failure`, `RestartSec=5s` (bounded restart delay), `StartLimitIntervalSec=300` / `StartLimitBurst=5` (failure rate limiting). Clean exits are not restarted. `KillSignal=SIGTERM`, `TimeoutStopSec=15s` — the runtime normally stops in milliseconds; the timeout is a generous bound. Failure restarts re-enter the lifecycle from STARTING with configuration re-validated (fail closed).

## 9. Upgrade model (high level)

1. Build the wheel from the accepted revision (offline).
2. Stop the service (`systemctl stop piano-core`) — graceful SIGTERM stop.
3. Install the wheel into `/opt/piano-core/venv` (`pip install --no-deps`).
4. Refresh `/opt/piano-core/share/` documentation.
5. Start the service; confirm READY via journal.
Configuration and user are unchanged by upgrades. Versioned rollback = reinstall the previous wheel.

## 10. What PIANO-P002 verifies locally

- Offline wheel build from this repository using locally available tooling (system setuptools/wheel, build isolation disabled, `PIP_NO_INDEX=1`);
- offline install of that wheel into an isolated temporary venv (`--no-deps --no-index`);
- the **installed** console entrypoint: `piano-core --version`, bounded startup/shutdown, invalid-configuration fail-closed exit;
- installed-package import resolves inside the venv (not the source tree);
- wheel contains the `piano_core` package;
- systemd unit syntax (non-destructive `systemd-analyze` on the development host);
- service/env contract enforced by automated tests.

## 11. What remains unverified until a later task (PIANO-P003 or deployment)

- Execution on **real Python 3.11** (verification host runs 3.14; P001 accepted limitation, still open);
- **Raspberry Pi** execution, `/opt` + `/etc` layout on the appliance, dedicated `piano-core` system user creation, systemd enable/start on a real host;
- journal behavior under systemd supervision;
- any hardware (Roland/MIDI/LED/GPIO) concerns — intentionally absent from P002.

**PIANO-P002 DOES NOT CLAIM RASPBERRY PI DEPLOYMENT.**
