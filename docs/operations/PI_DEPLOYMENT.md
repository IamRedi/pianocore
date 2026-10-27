# Piano Core Raspberry Pi Deployment (PI_DEPLOYMENT)

> **Task:** PIANO-P003  
> **Status:** EXECUTED AND VERIFIED (2026-09-04). Steps 1–5 were executed by the executor; step 6 (privileged operations) was executed manually by the Owner using the prepared bounded command block; step 7 verification was performed independently over read-only SSH. Step 9 reboot remains NOT_RUN (live legacy workload).  
> **Appliance contract:** `docs/operations/APPLIANCE_BASELINE.md` · Unit: `ops/systemd/piano-core.service`

---

## 1. Prerequisites (VERIFIED 2026-09-04)

- Development host: Python 3.14.4, pip 25.1.1, setuptools 78.1.1, wheel 0.46.3 (offline build proven in P002/P003).
- Target Pi: reachable key-based SSH at `pianopi@192.168.1.50`; Raspberry Pi 4 Model B Rev 1.5; Debian 13 (trixie); arm64; **Python 3.13.5**; venv+pip support present; 15G free disk; user `pianopi` in `sudo` group.
- NOT yet satisfied: privileged access (sudo password / NOPASSWD / root) — see block marker in section 6.

## 2. Artifact build (VERIFIED)

From the clean accepted commit (P003: `47e7a9f7d89ab8c31d244a10bc541a8471284222`):

```
PIP_NO_INDEX=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
  python3 -m pip wheel . --no-deps --no-build-isolation -w /tmp/p003-dist
sha256sum /tmp/p003-dist/piano_core-0.1.0.dev0-py3-none-any.whl
```

Record filename + SHA-256 as deployment provenance. P003 built: `piano_core-0.1.0.dev0-py3-none-any.whl`, SHA-256 `ffff07f1…7053` (full value in `docs/work/evidence/PIANO-P003-DEPLOYMENT.md`).

## 3. Transfer (EXECUTED — files verified on target by SHA-256)

```
ssh pianopi@192.168.1.50 'mkdir -p /tmp/piano-core-p003'
scp /tmp/p003-dist/piano_core-0.1.0.dev0-py3-none-any.whl \
    piano-core.service piano-core.env APPLIANCE_BASELINE.md \
    pianopi@192.168.1.50:/tmp/piano-core-p003/
```

All four files verified on target by SHA-256 (see `docs/work/evidence/PIANO-P003-DEPLOYMENT.md` §3).

## 4. Target layout / service identity (contract)

- `/opt/piano-core/venv` — dedicated virtual environment (root-owned, `piano-core` readable/executable)
- `/etc/piano-core/piano-core.env` — configuration (root-owned; from `ops/systemd/piano-core.env.example`)
- `/etc/systemd/system/piano-core.service` — unit from `ops/systemd/piano-core.service`
- Dedicated system user/group `piano-core` (no login shell)

## 5. Configuration (contract, no secrets)

`/etc/piano-core/piano-core.env` supports only `PIANO_CORE_MODE` and `PIANO_CORE_LOG_LEVEL` (AppConfig surface; fail closed otherwise). Recommended appliance values: `PIANO_CORE_MODE=production`, `PIANO_CORE_LOG_LEVEL=INFO`.

## 6. Privileged install flow — EXECUTED BY THE OWNER (2026-09-04)

The following bounded privileged operations were authorized by the P003 contract. They were **executed manually by the Owner** (the executor held no sudo credential) using this command block, and the results were then independently verified over read-only SSH by the executor (unit hash, ownership, service identity, journal):

```
sudo groupadd --system piano-core                                        # only if absent
sudo useradd --system --gid piano-core --home-dir /opt/piano-core \
             --shell /usr/sbin/nologin piano-core                        # only if absent
sudo mkdir -p /opt/piano-core /etc/piano-core
sudo /usr/bin/python3 -m venv /opt/piano-core/venv
sudo /opt/piano-core/venv/bin/pip install --no-index --no-deps /tmp/piano-core-p003/piano_core-0.1.0.dev0-py3-none-any.whl
sudo install -m 0644 -o root -g root /tmp/piano-core-p003/piano-core.service /etc/systemd/system/piano-core.service
sudo install -m 0644 -o root -g root /tmp/piano-core-p003/piano-core.env /etc/piano-core/piano-core.env
sudo install -m 0644 -o root -g root docs/operations/APPLIANCE_BASELINE.md /opt/piano-core/share/APPLIANCE_BASELINE.md
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/piano-core.service
sudo systemctl enable piano-core.service
sudo systemctl start piano-core.service
```

**Ownership model:** `/opt/piano-core` (including the venv) stays **root-owned** (0755, default umask) — the `piano-core` service user has read/execute only and cannot modify its own code. `/etc/piano-core/piano-core.env` is root-owned 0644 (contains no secrets). This supersedes the earlier draft wording that proposed `chown -R piano-core:piano-core /opt/piano-core`; that draft would have let the service user modify its own code and is not the accepted model.

(The exact command sequence actually executed by the Owner is recorded in `docs/work/evidence/PIANO-P003-DEPLOYMENT.md` once run; deviations will be reported, never silently applied.)

(The exact command sequence will be finalized/executed per this contract once access exists; deviations will be reported, never silently applied.)

## 7. Verification (EXECUTED 2026-09-04)

```
systemctl status piano-core.service --no-pager
systemctl is-active piano-core.service && systemctl is-enabled piano-core.service
systemctl show piano-core.service -p MainPID -p ExecStart -p User
journalctl -u piano-core.service -n 50 --no-pager        # startup / ready evidence
sudo systemctl stop piano-core.service                    # STOPPING/STOPPED evidence
sudo systemctl start piano-core.service
sudo systemctl restart piano-core.service
/opt/piano-core/venv/bin/piano-core --version
```

All of the above were executed and independently verified on 2026-09-04: service active + enabled, MainPID running as `piano-core`, READY journal evidence, graceful stop, clean start and restart (new MainPID, no restart loop). Results: `docs/work/evidence/PIANO-P003-DEPLOYMENT.md` §3b–§3c.

## 8. Rollback concept

Piano Core is additive; nothing legacy is touched. Rollback = `sudo systemctl disable --now piano-core.service` + optionally removing `/etc/systemd/system/piano-core.service`, `/opt/piano-core`, `/etc/piano-core` (Piano Core-owned paths only). Legacy `pluto-piano-server.service` is never stopped, disabled, or deleted by Piano Core operations.

## 9. Limitations

- Privileged step: EXECUTED by the Owner (2026-09-04) using the section-6 block; results independently verified (see `docs/work/evidence/PIANO-P003-DEPLOYMENT.md`).
- Reboot verification NOT performed: legacy `pluto-piano-server.service` and kiosk run live on the target; reboot would interrupt a user-verified production workload. Requires separate Owner authorization. Accepted as a PIANO-P003 limitation.
- Python floor: target Python 3.13.5 satisfies `>=3.11`; **exact Python 3.11 execution is not claimed** and is not possible on the current target OS without system changes (out of scope).
