#!/bin/bash
# piano-core-deploy — narrowly scoped root helper for controlled Piano Core
# deployment on this appliance.
#
# Canonical source: ops/sudo/piano-core-deploy.sh in the piano-core repo.
# Installed by the ONE-TIME Owner bootstrap to /usr/local/sbin/piano-core-deploy
# (root:root 0755). Invoked ONLY via sudo by user pianopi; the sudoers drop-in
# /etc/sudoers.d/piano-core-deploy grants passwordless execution of EXACTLY
# this script — no other command, no shell, no ALL.
#
# Helper revision: v17 (PIANO-PROD-001: identity-provision,
# identity-init, identity-config, identity-verify — the bounded, audited
# path to provision /var/lib/piano-core/identity, run the explicit
# one-time identity initialization AS THE SERVICE USER, enable
# PIANO_CORE_DEVICE_IDENTITY_DIR in the production env, and verify the
# identity contract read-only).
#
# Subcommands:
#   deploy   <wheel> <sha256>   validate inputs + timestamped rollback backup
#                               (package, dist-info, ALL console entrypoints,
#                               env) + install (--no-index --no-deps
#                               --force-reinstall) + service-user import
#                               check + restart ONLY piano-core.service
#   rollback <backup-dir>       restore a backup created by 'deploy' and
#                               reconcile the console entrypoint set exactly
#                               (removes entrypoints the deployment added,
#                               e.g. piano-core-play, restores pre-deploy set)
#   identity-provision          create/verify /var/lib/piano-core/identity
#                               (piano-core:piano-core, 0700; fail-closed on
#                               any unexpected existing identity content)
#   identity-init               run the installed piano-core-identity
#                               initialize AS THE SERVICE USER (never root);
#                               idempotent; verifies ownership/modes after
#   identity-config             idempotently add
#                               PIANO_CORE_DEVICE_IDENTITY_DIR to the env
#                               file + restart ONLY piano-core.service
#   identity-verify             read-only identity contract verification
#                               (stat, sha256, env line, descriptor, API)
#   status                      query piano-core + legacy service state (read-only)
#
# Hard guarantees (by construction):
#   - the only systemd unit name in this file is piano-core.service;
#     pluto-piano-server.service is checked read-only (is-active) and is
#     NEVER started, stopped, restarted, or otherwise modified
#   - never reboots or powers off; never touches any other unit
#   - never touches the MIDI library (/home/pianopi/Music) or any file
#     under /home; writes only to: the venv, /etc/piano-core/piano-core.env
#     (restores only, in rollback), the backup root, the log file, and
#     (v17) the EXACT identity directory /var/lib/piano-core/identity —
#     never recursively, never /var/lib/piano-core itself, and NEVER
#     deleting or regenerating an existing identity record
#   - wheels are accepted only from
#     /var/lib/piano-core-incoming/piano_core-*.whl (persistent across
#     reboots/tmp cleanup; canonical path re-checked after symlink
#     resolution; staging directory must be pianopi-owned, mode 0700,
#     never group/world-writable)
#   - root NEVER executes deployed package code: the post-install import
#     check runs as the unit's configured service user (read live from
#     systemctl show -p User; refused if it cannot be determined); the
#     v17 identity initializer likewise runs AS THE SERVICE USER
#   - backups live only under /var/lib/piano-core-deploy/backups (root-only)
#   - every invocation is logged to syslog (tag piano-core-deploy) and
#     /var/log/piano-core-deploy.log; failures never auto-rollback — the
#     exact rollback command is printed and the caller decides explicitly

set -euo pipefail

readonly INCOMING_DIR="/var/lib/piano-core-incoming"
readonly VENV="/opt/piano-core/venv"
readonly SITES="$VENV/lib/python3.13/site-packages"
readonly UNIT="piano-core.service"
readonly BACKUP_ROOT="/var/lib/piano-core-deploy/backups"
readonly WORK_ROOT="/var/lib/piano-core-deploy/work"
readonly LOCKFILE="/run/lock/piano-core-deploy.lock"
readonly LOGFILE="/var/log/piano-core-deploy.log"
readonly ENVFILE="/etc/piano-core/piano-core.env"

# PIANO-PROD-001: persistent device identity (narrowly scoped constant;
# this EXACT path is the only filesystem addition v17 can touch — never
# /var/lib/piano-core itself, never recursive).
readonly IDENTITY_DIR="/var/lib/piano-core/identity"

# PIANO-P011: production library service access (narrowly scoped constants).
readonly LIB_MOUNTPOINT="/srv/piano-core/midi"
readonly LIB_SRV_DIR="/srv/piano-core"
readonly LIB_STATE_DIR="/var/lib/piano-core/library"
readonly LIB_MOUNT_UNIT_NAME="srv-piano\\x2dcore-midi.mount"
readonly LIB_MOUNT_UNIT="/etc/systemd/system/$LIB_MOUNT_UNIT_NAME"
readonly LIB_OLD_DROPIN="/etc/systemd/system/piano-core.service.d/piano-core-library.conf"
readonly LIB_SCAN_UNIT="/etc/systemd/system/piano-core-library-scan.service"
readonly LIB_VERIFY_UNIT="/etc/systemd/system/piano-core-library-verify.service"
readonly LIB_SOURCE="/home/pianopi/Music/MIDI"

log() {
  printf '%s\n' "$*" >&2
  /usr/bin/logger -t piano-core-deploy -- "$*" 2>/dev/null || true
  printf '%s piano-core-deploy: %s\n' "$(/usr/bin/date -Is)" "$*" >>"$LOGFILE" 2>/dev/null || true
}

die() {
  log "ABORT: $*"
  exit 1
}

# Serialize ALL privileged mutations (deploy/rollback): one exclusive,
# non-blocking flock on a root-controlled lockfile (created root:root 0600
# by the bootstrap; pianopi can neither write nor replace it). No wait, no
# queueing: a concurrent mutation is refused fail-closed. 'status' never
# takes this lock (read-only).
acquire_mutation_lock() {
  exec 9>>"$LOCKFILE"
  if ! /usr/bin/flock -n 9; then
    log "REFUSED: another deploy/rollback holds $LOCKFILE"
    die "another deploy/rollback operation is active"
  fi
  log "mutation lock acquired: $LOCKFILE"
}

usage() {
  cat >&2 <<'USAGE'
usage: piano-core-deploy deploy <wheel> <sha256>
       piano-core-deploy rollback <backup-dir>
       piano-core-deploy provision
       piano-core-deploy api-config
       piano-core-deploy led-config
       piano-core-deploy hf2-network-config
       piano-core-deploy identity-provision
       piano-core-deploy identity-init
       piano-core-deploy identity-config
       piano-core-deploy identity-verify
       piano-core-deploy library-scan
       piano-core-deploy library-verify
       piano-core-deploy status
USAGE
  exit 2
}

require_legacy_active() {
  /usr/bin/systemctl is-active --quiet pluto-piano-server.service || die "pluto-piano-server.service not active (refusing to operate on a degraded system)"
}

require_active() {
  /usr/bin/systemctl is-active --quiet "$UNIT" || die "$UNIT not active"
  require_legacy_active
}

# Resolve a non-root execution wrapper for the service-user import check.
# Returns (prints) the wrapper path; never executes it.
resolve_runas() {
  if [ -x /usr/sbin/runuser ]; then
    printf '%s\n' /usr/sbin/runuser
  elif [ -x /usr/bin/runuser ]; then
    printf '%s\n' /usr/bin/runuser
  else
    printf '%s\n' /usr/bin/su
  fi
}

# Run a simple command as the given user (never as root).
run_as_user() {
  local user="$1"
  shift
  local runner
  runner="$(resolve_runas)"
  case "$runner" in
    /usr/sbin/runuser | /usr/bin/runuser | /usr/bin/su) ;;
    *) die "resolved run-as wrapper is not a sanctioned binary: ${runner:-<empty>}" ;;
  esac
  case "$runner" in
    */runuser)
      "$runner" -u "$user" -- "$@"
      ;;
    *)
      # su has no -- separator: re-quote every argument shell-safely.
      local quoted=""
      local arg
      for arg in "$@"; do
        printf -v arg "%q" "$arg"
        quoted="$quoted $arg"
      done
      "$runner" -s /bin/sh -c "exec$quoted" "$user"
      ;;
  esac
}

# Print the Piano Core console entrypoint file names recorded in a
# dist-info RECORD (one per line). RECORD lists scripts as
# "../../../../bin/<name>".
list_record_entrypoints() {
  /usr/bin/awk -F',' 'index($1, "../../../bin/") > 0 { print $1 }' \
    "$1/RECORD" 2>/dev/null \
    | /usr/bin/sed 's|.*/bin/||' \
    | /usr/bin/sort -u \
    || true
}

# Validate an entrypoint name before any rm/cp uses it (defensive).
safe_name() {
  case "$1" in
    "" | *[!A-Za-z0-9._-]*) return 1 ;;
    *) return 0 ;;
  esac
}

# Idempotently ensure one "KEY=value" line in the env file. A present-but-
# different line aborts — this helper never silently rewrites production
# configuration. Used by 'provision' (P011) and 'api-config' (P012).
ensure_env_line() {
  local line="$1"
  local key="${line%%=*}"
  if /usr/bin/grep -q "^${key}=" "$ENVFILE"; then
    /usr/bin/grep -qxF "$line" "$ENVFILE" || die "env file has a different $key value; refusing to change it"
  else
    printf '%s\n' "$line" >> "$ENVFILE"
  fi
}

# Idempotently force one "KEY=value" line in the env file: a present line
# with a DIFFERENT value is replaced in place (unlike ensure_env_line, which
# refuses). Used ONLY by 'hf2-network-config' (P015-HF2) for the audited
# one-time 192.168.1.50 -> 0.0.0.0 bind migration; the key name is fixed
# by the caller and never derived from input.
replace_env_line() {
  local key="$1" value="$2"
  if /usr/bin/grep -q "^${key}=" "$ENVFILE"; then
    if ! /usr/bin/grep -qxF "${key}=${value}" "$ENVFILE"; then
      log "hf2-network-config: replacing ${key} with ${key}=${value}"
      /usr/bin/sed -i "s|^${key}=.*|${key}=${value}|" "$ENVFILE" \
        || die "cannot replace ${key} in $ENVFILE"
    fi
  else
    printf '%s\n' "${key}=${value}" >> "$ENVFILE"
  fi
}

# Strict Piano Core dist-info pattern: the distribution name is fixed
# ("piano_core"), the version starts with a digit (PEP 440) and may only
# contain PEP 440 version characters. Anything else is NOT a Piano Core
# dist-info and must never be touched by this helper.
valid_dist_info_name() {
  case "$1" in
    piano_core-[0-9]*.dist-info)
      local v="${1#piano_core-}"
      v="${v%.dist-info}"
      case "$v" in
        "" | *[!A-Za-z0-9._!+~-]*) return 1 ;;
        *) return 0 ;;
      esac
      ;;
    *) return 1 ;;
  esac
}

[ "$(id -u)" -eq 0 ] || die "must run as root (via sudo)"
# === BEGIN PROD-001 identity helper functions (v17 r2; H17-1..H17-5) ===
# These functions back ONLY the four PROD-001 subcommands. Seams marked
# below exist so the sandbox harness (ops/sudo/test-piano-core-deploy-
# sandbox.sh) can exercise the REAL shipped logic without root/systemd;
# production values are the fixed defaults.

# Seam: the unit's configured service user (production: systemctl).
identity_service_user() {
  /usr/bin/systemctl show "$UNIT" -p User --value
}

# Seam: fixed contract account lookups (production: id).
identity_account_uid() {
  /usr/bin/id -u piano-core
}

identity_account_group() {
  /usr/bin/id -gn piano-core
}

identity_group_exists() {
  /usr/bin/getent group piano-core >/dev/null
}

# Seam: service activity (production: systemctl; used read-only by
# identity-verify, mutation commands use require_active instead).
identity_service_active() {
  /usr/bin/systemctl is-active --quiet "$UNIT"
}

# Seam: env file location (production: /etc/piano-core/piano-core.env).
identity_env_file() {
  printf '%s\n' "$ENVFILE"
}

# H17-2 preflight failures must happen before the mutation lock or the
# normal log()/die() infrastructure.  This diagnostic is deliberately
# stderr-only: it must not call logger or append the helper logfile.
identity_preflight_error() {
  printf '%s\n' "H17-2 STOP: $*; no identity action taken" >&2
}

# H17-2: the service contract is FIXED — exactly User=piano-core,
# exactly primary group piano-core, an existing piano-core group, and
# UID != 0. Never derive arbitrary ownership from whatever the unit
# reports. Callers invoke this before entering any mutating/logging
# helper infrastructure. Prints the verified service user on success.
identity_require_service_contract() {
  local service_user account_uid account_group
  service_user="$(identity_service_user)" || {
    identity_preflight_error "cannot determine $UNIT User= (required: exactly 'piano-core')"
    return 1
  }
  if [ "$service_user" != "piano-core" ]; then
    identity_preflight_error "$UNIT User= is '${service_user:-<empty>}' (required: exactly 'piano-core')"
    return 1
  fi
  account_uid="$(identity_account_uid)" || {
    identity_preflight_error "the piano-core account does not exist"
    return 1
  }
  if [ "$account_uid" = "0" ]; then
    identity_preflight_error "piano-core has UID 0"
    return 1
  fi
  account_group="$(identity_account_group)" || {
    identity_preflight_error "the piano-core account has no resolvable group"
    return 1
  }
  if [ "$account_group" != "piano-core" ]; then
    identity_preflight_error "piano-core primary group is '$account_group' (required: exactly 'piano-core')"
    return 1
  fi
  if ! identity_group_exists; then
    identity_preflight_error "the piano-core group does not exist"
    return 1
  fi
  printf '%s\n' "$service_user"
}

# H17-2 HARD GATE: EVERY identity package execution goes through this
# wrapper. If the fixed service-account contract fails, it returns
# non-zero WITHOUT ever calling run_as_user/runuser — package code can
# therefore never execute (as piano-core or, worse, as root) after a
# failed contract. Callers must treat a non-zero return as a hard stop.
identity_run_as_service_account() {
  local verified_user
  verified_user="$(identity_require_service_contract)" || return 1
  # Captured (not printed): the wrapper keeps package-command stdout
  # clean (H17-3 contract — the descriptor is the only stdout output).
  run_as_user "piano-core" "$@"
}

# H17-4: inspect ALL occurrences of PIANO_CORE_DEVICE_IDENTITY_DIR in
# the env file BEFORE any mutation. Prints APPEND (zero declarations)
# or OK (exactly one canonical declaration); rejects duplicates,
# conflicting values and any unexpected combination with a non-zero
# exit — before the file is written and before any restart.
identity_env_scan() {
  local canonical="PIANO_CORE_DEVICE_IDENTITY_DIR=$IDENTITY_DIR"
  local env_file declarations=0 canonical_declarations=0 first_declaration=""
  env_file="$(identity_env_file)"
  if [ ! -f "$env_file" ]; then
    log "H17-4 STOP: env file missing: $env_file — nothing written, nothing restarted"
    return 1
  fi
  # H17-4: process EVERY logical line INCLUDING a final unterminated
  # one — the plain `while read` pattern silently drops the last line
  # when the file lacks a trailing newline.
  while IFS= read -r idenv_line || [ -n "$idenv_line" ]; do
    case "$idenv_line" in
      PIANO_CORE_DEVICE_IDENTITY_DIR=*)
        declarations=$((declarations + 1))
        if [ -z "$first_declaration" ]; then
          first_declaration="$idenv_line"
        fi
        if [ "$idenv_line" = "$canonical" ]; then
          canonical_declarations=$((canonical_declarations + 1))
        fi
        ;;
    esac
  done < "$env_file"
  if [ "$declarations" -eq 0 ]; then
    printf 'APPEND\n'
    return 0
  fi
  if [ "$declarations" -eq 1 ] && [ "$canonical_declarations" -eq 1 ]; then
    printf 'OK\n'
    return 0
  fi
  if [ "$declarations" -eq 1 ]; then
    log "H17-4 STOP: conflicting env declaration '$first_declaration' (canonical: '$canonical') — env file NOT modified, service NOT restarted"
    return 1
  fi
  log "H17-4 STOP: $declarations duplicate/conflicting PIANO_CORE_DEVICE_IDENTITY_DIR declarations — env file NOT modified, service NOT restarted"
  return 1
}

# H17-4: PROD-001-specific append that preserves the existing bytes
# exactly: if the file does not end in a newline, exactly one line
# break is inserted BEFORE the canonical line (never concatenated onto
# an unterminated line). The generic env helpers are untouched.
identity_env_append_canonical() {
  local canonical="PIANO_CORE_DEVICE_IDENTITY_DIR=$IDENTITY_DIR"
  local env_file last_byte
  env_file="$(identity_env_file)"
  if [ -s "$env_file" ]; then
    # tail -c 1 prints the final byte; command substitution strips a
    # trailing newline, so a NON-empty result means the file does NOT
    # end with a newline.
    last_byte="$(/usr/bin/tail -c 1 "$env_file" 2>/dev/null || true)"
    if [ -n "$last_byte" ]; then
      printf '\n' >> "$env_file"
    fi
  fi
  printf '%s\n' "$canonical" >> "$env_file"
}

# H17-1: descriptor-authoritative provisioning. The identity path is
# FIXED (/var/lib/piano-core/identity); every parent component is
# opened O_DIRECTORY|O_NOFOLLOW, the target is inspected with lstat
# semantics (dangling AND live symlinks rejected, fail-closed on any
# unexpected identity content), and creation plus ownership/mode
# enforcement happen through the validated descriptors (mkdir/fchown/
# fchmod via dir_fd) — a raced pathname swap CANNOT redirect the
# operation and nothing outside the exact identity path can be
# mutated. Runs as root (ownership operations); pure stdlib filesystem
# work only.
identity_provision_fs() {
  /usr/bin/python3 - "${1:-$IDENTITY_DIR}" "${2:-piano-core}" "${3:-piano-core}" "${4:-0}" <<'PY'
import os
import pwd
import grp
import stat
import sys

IDENTITY_DIR = sys.argv[1]
EXPECTED_USER = sys.argv[2]
EXPECTED_GROUP = sys.argv[3]
EXPECTED_PARENT_UID = int(sys.argv[4]) if len(sys.argv) > 4 else 0


def die(message):
    sys.stderr.write("IDENTITY-PROVISION STOP: %s\n" % message)
    sys.exit(1)


# Fixed ownership contract (H17-2): the named service account, never
# UID 0, with primary group exactly as required.
try:
    account = pwd.getpwnam(EXPECTED_USER)
except KeyError:
    die("service account %r does not exist" % EXPECTED_USER)
if account.pw_uid == 0:
    die("service account %r has UID 0" % EXPECTED_USER)
try:
    primary_group_name = grp.getgrgid(account.pw_gid).gr_name
except KeyError:
    die("primary group of %r does not resolve" % EXPECTED_USER)
if primary_group_name != EXPECTED_GROUP:
    die(
        "primary group of %r is %r (required: %r)"
        % (EXPECTED_USER, primary_group_name, EXPECTED_GROUP)
    )
uid, gid = account.pw_uid, account.pw_gid

# Walk EVERY parent component without following symlinks (H17-1);
# parents are never created and never modified.
components = [part for part in IDENTITY_DIR.split("/") if part]
if len(components) < 2:
    die("identity path must have a parent component")
try:
    walk_fd = os.open(
        "/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    )
except OSError:
    die("cannot open the filesystem root")
try:
    for component in components[:-1]:
        next_fd = os.open(
            component,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=walk_fd,
        )
        os.close(walk_fd)
        walk_fd = next_fd
except FileNotFoundError:
    os.close(walk_fd)
    die("parent path component missing (creating parents is not authorized)")
except OSError as exc:
    os.close(walk_fd)
    die(
        "unsafe parent path component (errno %s: symlink or non-directory)"
        % exc.errno
    )

parent_st = os.fstat(walk_fd)
if not stat.S_ISDIR(parent_st.st_mode):
    os.close(walk_fd)
    die("parent is not a directory")
if parent_st.st_uid != EXPECTED_PARENT_UID:
    os.close(walk_fd)
    die("parent directory owned by uid %d (expected uid %d)" % (parent_st.st_uid, EXPECTED_PARENT_UID))
if parent_st.st_mode & 0o022:
    os.close(walk_fd)
    die("parent directory is group/world-writable")

target = components[-1]

# Inspect the target THROUGH the parent descriptor with lstat
# semantics: dangling and live symlinks are both rejected.
try:
    target_st = os.stat(target, dir_fd=walk_fd, follow_symlinks=False)
except FileNotFoundError:
    target_st = None
except OSError:
    os.close(walk_fd)
    die("target inspection failed (unsafe object at the identity path)")

if target_st is not None:
    if stat.S_ISLNK(target_st.st_mode):
        os.close(walk_fd)
        die("identity path is a symlink (dangling or live)")
    if not stat.S_ISDIR(target_st.st_mode):
        os.close(walk_fd)
        die("identity path exists and is not a directory")
    try:
        dir_fd = os.open(
            target,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=walk_fd,
        )
    except OSError:
        os.close(walk_fd)
        die("identity directory cannot be opened safely (race or substitution)")
    os.close(walk_fd)
    final_st = os.fstat(dir_fd)
    if not stat.S_ISDIR(final_st.st_mode):
        os.close(dir_fd)
        die("identity path is not a directory (descriptor check)")
    for record_name in ("device.json", ".initialize.lock"):
        try:
            os.stat(record_name, dir_fd=dir_fd, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            os.close(dir_fd)
            die("uninspectable object inside the identity directory")
        os.close(dir_fd)
        die("STOP: unexpected existing %s" % record_name)
    try:
        entries = os.listdir(dir_fd)
    except OSError:
        os.close(dir_fd)
        die("identity directory listing failed")
    for entry in entries:
        if entry.startswith(".device-") and entry.endswith(".tmp"):
            os.close(dir_fd)
            die("STOP: unexpected temp identity artifact %s" % entry)
    if final_st.st_uid != uid or final_st.st_gid != gid:
        os.close(dir_fd)
        die(
            "identity directory owner is %d:%d (required %d:%d)"
            % (final_st.st_uid, final_st.st_gid, uid, gid)
        )
    if stat.S_IMODE(final_st.st_mode) != 0o700:
        os.close(dir_fd)
        die(
            "identity directory mode is %04o (required 0700)"
            % stat.S_IMODE(final_st.st_mode)
        )
    os.close(dir_fd)
    print("IDENTITY-PROVISION OK (existing directory conforms to the contract)")
    sys.exit(0)

# Create THROUGH the parent descriptor: a raced rename of the PATHNAME
# cannot redirect this, and the follow-up open re-validates O_NOFOLLOW.
try:
    os.mkdir(target, 0o700, dir_fd=walk_fd)
except OSError as exc:
    os.close(walk_fd)
    die("descriptor-relative mkdir failed (errno %s)" % exc.errno)
try:
    new_fd = os.open(
        target,
        os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        dir_fd=walk_fd,
    )
except OSError:
    os.close(walk_fd)
    die("created directory failed re-validation (unexpected object at the name)")
os.close(walk_fd)
created_st = os.fstat(new_fd)
if not stat.S_ISDIR(created_st.st_mode):
    os.close(new_fd)
    die("created object is not a directory")
os.fchown(new_fd, uid, gid)
os.fchmod(new_fd, 0o700)
final_st = os.fstat(new_fd)
os.close(new_fd)
if final_st.st_uid != uid or final_st.st_gid != gid:
    die("post-create owner verification failed (%d:%d)" % (final_st.st_uid, final_st.st_gid))
if stat.S_IMODE(final_st.st_mode) != 0o700:
    die("post-create mode verification failed (%04o)" % stat.S_IMODE(final_st.st_mode))
print(
    "IDENTITY-PROVISION OK (created %s %s:%s 0700)"
    % (IDENTITY_DIR, EXPECTED_USER, EXPECTED_GROUP)
)
PY
}

# H17-3: pure passthrough wrapper — the initializer's stdout stays
# stdout, its stderr stays stderr (no redirection, no capture), and
# the exit code is returned unchanged.
identity_init_run() {
  set +e
  # H17-2: through the hard gate — no runuser/package execution on a
  # failed contract.
  identity_run_as_service_account \
    "$VENV/bin/piano-core-identity" initialize --directory "$IDENTITY_DIR"
  local idi_rc=$?
  set -e
  return "$idi_rc"
}

# H17-5: strictly read-only acceptance verification. Zero mutation: no
# log()/syslog/logfile writes, no chmod/chown, no restart, no env
# writes — stdout/stderr only. Every acceptance check must genuinely
# pass; any missing/invalid state exits non-zero.
identity_verify_run() {
  local failed=0 env_file
  local stat_dir stat_rec stat_lock declare_count canonical_count

  # Pure read-only service-contract check (NO log() on failure — the
  # verify command must never write to syslog/the helper logfile).
  verify_service_contract() {
    local su uid grp
    su="$(identity_service_user)"
    [ "$su" = "piano-core" ] || return 1
    uid="$(identity_account_uid)" || return 1
    [ "$uid" = "0" ] && return 1
    grp="$(identity_account_group)" || return 1
    [ "$grp" = "piano-core" ] || return 1
    return 0
  }

  verify_ok() {
    printf 'ok: %s\n' "$1"
  }

  verify_fail() {
    printf 'IDENTITY-VERIFY FAIL: %s\n' "$1" >&2
    failed=1
  }

  printf 'IDENTITY-VERIFY (read-only acceptance verification)\n'

  # Service contract and liveness. H17-2: the service-account
  # contract is a HARD GATE — on failure verify returns non-zero
  # IMMEDIATELY, before any code path that could execute package code
  # (no run_as_user/runuser/python/CLI invocation happens).
  if identity_service_active; then
    verify_ok "service is active"
  else
    verify_fail "service is not active"
    printf 'IDENTITY-VERIFY ABORT: service inactive — package execution suppressed\n' >&2
    return 1
  fi
  if verify_service_contract; then
    verify_ok "service user is exactly piano-core (UID!=0, primary group piano-core)"
  else
    verify_fail "service user contract violated (must be exactly piano-core, UID!=0, group piano-core)"
    printf 'IDENTITY-VERIFY ABORT: service-account contract failed — package execution suppressed\n' >&2
    return 1
  fi

  # Identity directory: exists, no symlink, correct owner/group/mode.
  if [ -d "$IDENTITY_DIR" ] && [ ! -L "$IDENTITY_DIR" ]; then
    verify_ok "identity directory exists and is not a symlink"
    stat_dir="$(/usr/bin/stat -c '%U:%G %a' "$IDENTITY_DIR" 2>/dev/null)"
    if [ "$stat_dir" = "piano-core:piano-core 700" ]; then
      verify_ok "identity directory owner/group/mode is piano-core:piano-core 0700"
    else
      verify_fail "identity directory owner/group/mode is [$stat_dir] (required: piano-core:piano-core 0700)"
    fi
    if [ -f "$IDENTITY_DIR/device.json" ] && [ ! -L "$IDENTITY_DIR/device.json" ]; then
      verify_ok "device.json exists, regular, not a symlink"
      stat_rec="$(/usr/bin/stat -c '%U:%G %a' "$IDENTITY_DIR/device.json" 2>/dev/null)"
      if [ "$stat_rec" = "piano-core:piano-core 600" ]; then
        verify_ok "device.json owner/group/mode is piano-core:piano-core 0600"
      else
        verify_fail "device.json owner/group/mode is [$stat_rec] (required: piano-core:piano-core 0600)"
      fi
      if /usr/bin/sha256sum "$IDENTITY_DIR/device.json" 2>/dev/null; then
        verify_ok "device.json is readable and hashable (read-only)"
      else
        verify_fail "device.json could not be read/hashed"
      fi
      if [ -f "$IDENTITY_DIR/.initialize.lock" ] && [ ! -L "$IDENTITY_DIR/.initialize.lock" ]; then
        verify_ok ".initialize.lock exists, regular, not a symlink"
        stat_lock="$(/usr/bin/stat -c '%U:%G %a' "$IDENTITY_DIR/.initialize.lock" 2>/dev/null)"
        if [ "$stat_lock" = "piano-core:piano-core 600" ]; then
          verify_ok ".initialize.lock owner/group/mode is piano-core:piano-core 0600"
        else
          verify_fail ".initialize.lock owner/group/mode is [$stat_lock] (required: piano-core:piano-core 0600)"
        fi
      else
        verify_fail ".initialize.lock missing or unsafe"
      fi
      # The installed CLI must describe the identity AS THE SERVICE USER.
      if identity_run_as_service_account "$VENV/bin/python3" -c 'from piano_core.device_identity import load_identity; i = load_identity("/var/lib/piano-core/identity"); assert i.identity_schema == 1; print(i.device_id)' 2>/dev/null; then
        verify_ok "installed identity CLI reads/describes the identity as the service user"
      else
        verify_fail "installed identity CLI could not read/describe the identity as the service user"
      fi
    else
      verify_fail "device.json missing or unsafe"
    fi
  else
    verify_fail "identity directory missing or unsafe"
  fi

  # Configuration: exactly one canonical declaration is required.
  env_file="$(identity_env_file)"
  if [ -f "$env_file" ]; then
    declare_count="$(/usr/bin/grep -c '^PIANO_CORE_DEVICE_IDENTITY_DIR=' "$env_file" 2>/dev/null)"
    canonical_count="$(/usr/bin/grep -cx 'PIANO_CORE_DEVICE_IDENTITY_DIR=/var/lib/piano-core/identity' "$env_file" 2>/dev/null)"
    if [ "$declare_count" = "1" ]; then
      verify_ok "env declares exactly one PIANO_CORE_DEVICE_IDENTITY_DIR"
    else
      verify_fail "env declares ${declare_count:-unreadable} PIANO_CORE_DEVICE_IDENTITY_DIR line(s) (required: exactly 1)"
    fi
    if [ "$canonical_count" = "1" ]; then
      verify_ok "env declaration is the canonical value"
    else
      verify_fail "env declaration is not the canonical value (canonical matches: ${canonical_count:-0})"
    fi
  else
    verify_fail "env file missing/unreadable: $env_file"
  fi

  if [ "$failed" -ne 0 ]; then
    printf 'IDENTITY-VERIFY FAILED (one or more acceptance checks did not pass)\n' >&2
    return 1
  fi
  printf 'IDENTITY-VERIFY OK (all acceptance checks passed; nothing was modified)\n'
}
# === END PROD-001 identity helper functions ===

[ $# -ge 1 ] || usage
CMD="$1"
shift

case "$CMD" in
  deploy)
    [ $# -eq 2 ] || usage
    WHEEL_SRC="$1"
    EXPECTED_SHA="$2"

    # Serialize privileged mutations before touching anything.
    acquire_mutation_lock

    printf '%s' "$EXPECTED_SHA" | /usr/bin/grep -Eq '^[0-9a-f]{64}$' \
      || die "sha256 must be exactly 64 lowercase hex characters"

    case "$WHEEL_SRC" in
      "$INCOMING_DIR"/piano_core-*.whl) ;;
      *) die "wheel path must match $INCOMING_DIR/piano_core-*.whl (got: $WHEEL_SRC)" ;;
    esac
    [ -d "$INCOMING_DIR" ] || die "staging directory missing: $INCOMING_DIR"
    PERMS="$(/usr/bin/stat -c '%a' "$INCOMING_DIR")"
    if [ "$((8#$PERMS & 8#022))" -ne 0 ]; then
      die "staging directory must not be group/world-writable (mode $PERMS; expected e.g. 700)"
    fi
    WHEEL="$(/usr/bin/realpath -e "$WHEEL_SRC")" || die "wheel file not found"
    case "$WHEEL" in
      "$INCOMING_DIR"/piano_core-*.whl) ;;
      *) die "wheel canonical path escaped the incoming directory" ;;
    esac
    [ -f "$WHEEL" ] && [ -s "$WHEEL" ] || die "wheel is not a regular non-empty file"

    INCOMING_SHA="$(/usr/bin/sha256sum "$WHEEL" | /usr/bin/awk '{print $1}')"
    [ "$INCOMING_SHA" = "$EXPECTED_SHA" ] || die "wheel hash mismatch (expected $EXPECTED_SHA, got $INCOMING_SHA)"

    # TOCTOU elimination: the incoming file is user-writable, so it is
    # NEVER handed to pip. Copy it into a fresh ROOT-ONLY work directory
    # (mktemp -d: root:root 0700, collision-safe), re-verify the hash of
    # the root-owned copy, and install exclusively from that copy. The
    # work directory is removed on every exit path; rollback backups are
    # never touched by this cleanup.
    [ -d "$WORK_ROOT" ] || /usr/bin/mkdir -p "$WORK_ROOT"
    /usr/bin/chown root:root "$WORK_ROOT" 2>/dev/null || true
    /usr/bin/chmod 0700 "$WORK_ROOT" 2>/dev/null || true
    WORK_DIR="$(/usr/bin/mktemp -d "$WORK_ROOT/deploy-XXXXXX")"
    trap '/usr/bin/rm -rf "$WORK_DIR"' EXIT
    # Preserve the original (pattern-validated) wheel FILENAME: pip
    # rejects wheels whose filename is not name-version-tags.whl, so the
    # root-owned copy must keep the exact basename of the staged wheel.
    INSTALL_WHEEL="$WORK_DIR/$(/usr/bin/basename "$WHEEL")"
    /usr/bin/cp "$WHEEL" "$INSTALL_WHEEL"
    COPY_SHA="$(/usr/bin/sha256sum "$INSTALL_WHEEL" | /usr/bin/awk '{print $1}')"
    [ "$COPY_SHA" = "$EXPECTED_SHA" ] || die "root-owned deployment copy hash mismatch (expected $EXPECTED_SHA, got $COPY_SHA) — incoming source changed under us?"
    log "deployment copy verified: source=$WHEEL copy=$INSTALL_WHEEL sha256=$COPY_SHA"

    # The service user is read LIVE from the unit (never guessed): the
    # post-install import check must run as this user, never as root.
    SERVICE_USER="$(/usr/bin/systemctl show "$UNIT" -p User --value)"
    [ -n "$SERVICE_USER" ] || die "cannot determine $UNIT User= (refusing to run any import check as root)"
    [ "$SERVICE_USER" != "root" ] || die "$UNIT runs as root; refusing to execute the import check as root"

    [ -d "$SITES" ] || die "site-packages not found: $SITES"
    DISTINFO_SRC="$(/usr/bin/ls -d "$SITES"/piano_core-*.dist-info 2>/dev/null | /usr/bin/head -n1 || true)"
    [ -n "$DISTINFO_SRC" ] || die "installed piano_core dist-info not found"
    DISTINFO_NAME="$(/usr/bin/basename "$DISTINFO_SRC")"
    valid_dist_info_name "$DISTINFO_NAME" || die "unexpected dist-info name: $DISTINFO_NAME"

    require_active

    # Timestamped rollback backup: mktemp -d guarantees a unique,
    # collision-safe, root-created directory (never mkdir -p over an
    # existing backup; with mutation locking this cannot race anyway).
    TS="$(/usr/bin/date -u +%Y%m%d-%H%M%S)"
    BACKUP="$(/usr/bin/mktemp -d "$BACKUP_ROOT/piano-core-rollback-$TS.XXXXXXXX")"
    /usr/bin/mkdir -p "$BACKUP/bin"
    /usr/bin/cp -a "$SITES/piano_core" "$BACKUP/piano_core"
    /usr/bin/cp -a "$DISTINFO_SRC" "$BACKUP/dist-info"
    /usr/bin/cp -a /etc/piano-core/piano-core.env "$BACKUP/piano-core.env"

    # Back up the COMPLETE pre-deploy console entrypoint set: every bin
    # file this dist owns. Presence is recorded per name; an entrypoint
    # that does not exist pre-deploy is therefore absent from META and
    # will be REMOVED by rollback (e.g. a pre-P009 backup must remove
    # piano-core-play).
    : >"$BACKUP/META"
    printf 'dist_info_name=%s\nwheel=%s\nsha256=%s\ncreated_utc=%s\n' \
      "$DISTINFO_NAME" "$INSTALL_WHEEL" "$COPY_SHA" "$TS" >>"$BACKUP/META"
    ENTRY_COUNT=0
    while IFS= read -r name; do
      [ -n "$name" ] || continue
      safe_name "$name" || { log "WARNING: skipping unsafe entrypoint name in RECORD: $name"; continue; }
      if [ -f "$VENV/bin/$name" ]; then
        /usr/bin/cp -a "$VENV/bin/$name" "$BACKUP/bin/$name"
        printf 'entrypoint=%s\n' "$name" >>"$BACKUP/META"
        ENTRY_COUNT=$((ENTRY_COUNT + 1))
      fi
    done < <(list_record_entrypoints "$DISTINFO_SRC")
    log "pre-deploy entrypoints backed up: $ENTRY_COUNT"

    [ -d "$BACKUP/piano_core" ] && [ -d "$BACKUP/dist-info" ] \
      && [ -d "$BACKUP/bin" ] && [ -s "$BACKUP/piano-core.env" ] \
      && [ -s "$BACKUP/META" ] || die "backup incomplete"
    /usr/bin/chmod -R go-rwx "$BACKUP"
    log "backup complete: $BACKUP (dist_info_name=$DISTINFO_NAME sha256=$COPY_SHA entrypoints=$ENTRY_COUNT)"

    # pip installs ONLY the root-owned, re-verified work copy — never the
    # user-writable incoming file (no TOCTOU window).
    "$VENV/bin/pip" install --no-index --no-deps --force-reinstall "$INSTALL_WHEEL" \
      || { log "install failed — package may be inconsistent; explicit rollback: piano-core-deploy rollback $BACKUP"; exit 1; }

    # Record the post-deploy entrypoint set in META so rollback can remove
    # newly introduced launchers even if the later RECORD is unreadable.
    NEW_DISTINFO="$(/usr/bin/ls -d "$SITES"/piano_core-*.dist-info 2>/dev/null | /usr/bin/head -n1 || true)"
    if [ -n "$NEW_DISTINFO" ]; then
      while IFS= read -r name; do
        [ -n "$name" ] || continue
        safe_name "$name" || continue
        printf 'after_deploy_entrypoint=%s\n' "$name" >>"$BACKUP/META"
      done < <(list_record_entrypoints "$NEW_DISTINFO")
    fi

    # Post-install import check as the SERVICE USER (never as root).
    if ! run_as_user "$SERVICE_USER" "$VENV/bin/python3" -c 'import piano_core'; then
      log "post-install import check FAILED as service user $SERVICE_USER — explicit rollback: piano-core-deploy rollback $BACKUP"
      exit 1
    fi
    log "post-install import check passed as service user $SERVICE_USER"

    /usr/bin/systemctl restart "$UNIT"
    /usr/bin/sleep 3
    /usr/bin/systemctl is-active --quiet "$UNIT" \
      || { log "$UNIT DOWN after restart — explicit rollback: piano-core-deploy rollback $BACKUP"; exit 1; }
    /usr/bin/systemctl is-active --quiet pluto-piano-server.service \
      || { log "legacy service inactive after restart (this helper NEVER touches it) — investigate"; exit 1; }

    log "deploy complete: wheel=$INSTALL_WHEEL source=$WHEEL sha256=$COPY_SHA backup=$BACKUP service_user=$SERVICE_USER"
    echo "DEPLOY OK backup=$BACKUP sha256=$COPY_SHA"
    ;;

  rollback)
    [ $# -eq 1 ] || usage
    BACKUP_SRC="$1"

    # Serialize privileged mutations before touching anything.
    acquire_mutation_lock

    case "$BACKUP_SRC" in
      "$BACKUP_ROOT"/piano-core-rollback-*) ;;
      *) die "backup path must match $BACKUP_ROOT/piano-core-rollback-*" ;;
    esac
    BACKUP="$(/usr/bin/realpath -e "$BACKUP_SRC")" || die "backup directory not found"
    case "$BACKUP" in
      "$BACKUP_ROOT"/piano-core-rollback-*) ;;
      *) die "backup canonical path escaped the backup root" ;;
    esac
    for item in piano_core dist-info bin piano-core.env META; do
      [ -e "$BACKUP/$item" ] || die "backup incomplete: missing $item"
    done
    DISTINFO_NAME="$(/usr/bin/sed -n 's/^dist_info_name=//p' "$BACKUP/META")"
    [ -n "$DISTINFO_NAME" ] || die "META lacks dist_info_name"
    valid_dist_info_name "$DISTINFO_NAME" || die "META dist_info_name fails the strict Piano Core pattern: $DISTINFO_NAME"

    # Rollback is the recovery path: the legacy service must be healthy,
    # but piano-core itself may be down (that is what rollback fixes).
    require_legacy_active

    # Names that existed BEFORE the backed-up deployment (must exist again
    # afterwards). Absence in META records absence pre-deploy.
    PRE_NAMES="$(/usr/bin/grep '^entrypoint=' "$BACKUP/META" | /usr/bin/sed 's/^entrypoint=//' || true)"

    # Identify ALL current Piano Core dist-info directories BEFORE any
    # deletion, validate each name strictly, and capture their entrypoint
    # RECORD sets. A deployment may have replaced the backed-up version
    # with a newer one (e.g. backup 0.1.0.dev0 -> deployed 0.2.0), so
    # rollback must remove the CURRENT dist-info(s), not the backed-up
    # name — otherwise a later-version dist-info would survive and the
    # restored package would carry wrong metadata.
    CUR_DISTINFOS=""
    CUR_NAMES=""
    while IFS= read -r di; do
      [ -n "$di" ] || continue
      diname="$(/usr/bin/basename "$di")"
      if ! valid_dist_info_name "$diname"; then
        die "refusing to operate: unexpected dist-info '$diname' (strict Piano Core pattern mismatch)"
      fi
      CUR_DISTINFOS="${CUR_DISTINFOS}${di}"$'\n'
      CUR_NAMES="${CUR_NAMES}$(list_record_entrypoints "$di")"$'\n'
    done < <(/usr/bin/ls -d "$SITES"/piano_core-*.dist-info 2>/dev/null || true)
    log "current Piano Core dist-infos identified: $(echo "$CUR_DISTINFOS" | /usr/bin/sed '/^$/d' | /usr/bin/wc -l)"

    /usr/bin/systemctl stop "$UNIT"
    # Remove the current Piano Core package directory and ALL current
    # Piano Core dist-info directories (the deployed version and any
    # strays). Unrelated distributions are never touched: only names
    # passing the strict piano_core-<version>.dist-info pattern are
    # removed, and only under $SITES.
    /usr/bin/rm -rf "$SITES/piano_core"
    while IFS= read -r di; do
      [ -n "$di" ] || continue
      /usr/bin/rm -rf "$di"
    done <<<"$CUR_DISTINFOS"

    # Remove every console entrypoint the deployment(s) introduced: the
    # union of the current RECORD set and the after_deploy set recorded at
    # deploy time, MINUS the pre-deploy set (which is restored below).
    REMOVED=0
    remove_if_new() {
      local name="$1"
      safe_name "$name" || { log "WARNING: skipping unsafe entrypoint name: $name"; return 0; }
      if /usr/bin/grep -qx "entrypoint=$name" "$BACKUP/META"; then
        return 0
      fi
      if [ -e "$VENV/bin/$name" ]; then
        /usr/bin/rm -f "$VENV/bin/$name"
        REMOVED=$((REMOVED + 1))
        log "removed entrypoint introduced after backup: $name"
      fi
    }
    while IFS= read -r name; do
      [ -n "$name" ] || continue
      remove_if_new "$name"
    done <<<"$CUR_NAMES"
    while IFS= read -r name; do
      [ -n "$name" ] || continue
      remove_if_new "$name"
    done < <(/usr/bin/grep '^after_deploy_entrypoint=' "$BACKUP/META" | /usr/bin/sed 's/^after_deploy_entrypoint=//' || true)

    # Restore the exact pre-deploy set.
    while IFS= read -r name; do
      [ -n "$name" ] || continue
      if [ -f "$BACKUP/bin/$name" ]; then
        /usr/bin/cp -a "$BACKUP/bin/$name" "$VENV/bin/$name"
      else
        log "WARNING: backup missing entrypoint file $name (recorded in META)"
      fi
    done <<<"$PRE_NAMES"

    /usr/bin/cp -a "$BACKUP/piano_core" "$SITES/piano_core"
    /usr/bin/cp -a "$BACKUP/dist-info" "$SITES/$DISTINFO_NAME"
    /usr/bin/cp -a "$BACKUP/piano-core.env" /etc/piano-core/piano-core.env
    /usr/bin/systemctl start "$UNIT"
    /usr/bin/sleep 3
    /usr/bin/systemctl is-active --quiet "$UNIT" || die "$UNIT DOWN after rollback restore"
    /usr/bin/systemctl is-active --quiet pluto-piano-server.service || die "legacy service inactive after rollback (NOT touched by this helper)"

    log "rollback complete from $BACKUP (dist_info_name=$DISTINFO_NAME entrypoints_removed=$REMOVED)"
    echo "ROLLBACK OK from $BACKUP (entrypoints_removed=$REMOVED)"
    ;;

  provision)
    # PIANO-P011: one idempotent, serialized provisioning pass for the
    # production library service access + runtime integration.
    [ $# -eq 0 ] || usage
    acquire_mutation_lock
    log "provision: starting"

    # [P1] validate the source exists (bind source) — never created/modified
    [ -d "$LIB_SOURCE" ] || die "MIDI source directory missing: $LIB_SOURCE"
    SOURCE_REAL="$(/usr/bin/realpath -e "$LIB_SOURCE")" || die "cannot resolve $LIB_SOURCE"
    [ "$SOURCE_REAL" = "$LIB_SOURCE" ] || die "source is a symlink or non-canonical: $SOURCE_REAL (refusing)"

    # [P2] service-facing mountpoint (root:root 0755) — created EMPTY; the
    # HOST-LEVEL read-only bind mount appears here when the .mount unit starts.
    /usr/bin/mkdir -p "$LIB_SRV_DIR"
    /usr/bin/chown root:root "$LIB_SRV_DIR"
    /usr/bin/chmod 0755 "$LIB_SRV_DIR"
    if [ -e "$LIB_MOUNTPOINT" ] && [ ! -d "$LIB_MOUNTPOINT" ]; then
      die "$LIB_MOUNTPOINT exists and is not a directory"
    fi
    /usr/bin/mkdir -p "$LIB_MOUNTPOINT"
    # Mountpoint ownership/mode matter ONLY pre-mount (the mounted view's
    # permissions come from the source filesystem, and a read-only mount
    # rejects chown/chmod): apply them only while the mount is not active —
    # re-provision over an ACTIVE mount is a no-op here (idempotence).
    if ! /usr/bin/systemctl is-active --quiet "$LIB_MOUNT_UNIT_NAME" 2>/dev/null; then
      /usr/bin/chown root:root "$LIB_MOUNTPOINT"
      /usr/bin/chmod 0755 "$LIB_MOUNTPOINT"
    fi
    # Bare-mountpoint rule, applied ONLY while the mount is not yet active:
    # on re-provision over an ACTIVE mount the view legitimately contains the
    # source contents (idempotence), but an unmounted non-empty mountpoint
    # means foreign content — refuse.
    if ! /usr/bin/systemctl is-active --quiet "$LIB_MOUNT_UNIT_NAME" 2>/dev/null; then
      if [ -n "$(/usr/bin/find "$LIB_MOUNTPOINT" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]; then
        die "$LIB_MOUNTPOINT is not empty (refusing: it must be a bare mountpoint)"
      fi
    fi

    # [P3] production index directory (service-writable, not world-writable)
    /usr/bin/mkdir -p "$LIB_STATE_DIR"
    /usr/bin/chown piano-core:piano-core "$LIB_STATE_DIR"
    /usr/bin/chmod 0750 "$LIB_STATE_DIR"

    # [P4] HOST-LEVEL read-only bind mount unit (systemd-escaped name;
    # canonical source ops/systemd/srv-piano\\x2dcore-midi.mount).
    /usr/bin/tee "$LIB_MOUNT_UNIT" > /dev/null <<'MOUNTUNIT'
# PIANO-P011: host-level read-only bind mount exposing the authoritative MIDI
# source collection to Piano Core at /srv/piano-core/midi.
#
# Source (authoritative, physically unchanged): /home/pianopi/Music/MIDI
# Destination (service-facing):                 /srv/piano-core/midi
#
# The mount is kernel-enforced READ-ONLY ("bind,ro"): no user — including
# the piano-core service user — can create/modify/delete anything under
# /srv/piano-core/midi. The source tree itself is never chmod/chown-ed,
# never written, and never migrated.
#
# Installed/managed by `piano-core-deploy provision` (PIANO-P011); idempotent.
# Unit name derived with systemd-escape -p --suffix=mount /srv/piano-core/midi
# (verified on the real appliance, systemd 257).

[Unit]
Description=Piano Core MIDI source library (read-only bind mount)
# Refuse start when the source is missing (fail closed). Mount units are
# implicitly ordered before local-fs.target; an explicit After= here would
# create an ordering cycle (caught by systemd-analyze on the appliance).
ConditionPathIsDirectory=/home/pianopi/Music/MIDI

[Mount]
What=/home/pianopi/Music/MIDI
Where=/srv/piano-core/midi
Type=none
Options=bind,ro

[Install]
WantedBy=multi-user.target
MOUNTUNIT
    /usr/bin/chown root:root "$LIB_MOUNT_UNIT"
    /usr/bin/chmod 0644 "$LIB_MOUNT_UNIT"

    # [P5] Piano-Core-owned oneshot units (inventory-only scan; verify suite)
    /usr/bin/tee "$LIB_SCAN_UNIT" > /dev/null <<'SCANUNIT'
# PIANO-P011: production library inventory/scan unit (Piano-Core-owned).
#
# Oneshot, runs as the piano-core service user against the host-level
# read-only service-facing view of the MIDI source library.
#
# INVENTORY-ONLY BY DEFAULT: full deep enrichment of ~169k files would take
# ~19 hours and is a separate bounded/resumable maintenance operation
# (P010 semantics; run `piano-core-library scan --limit N` manually as the
# service user if/when authorized). Unchanged files are never reparsed.
#
# Invoked by `piano-core-deploy library-scan`; result in the journal.
# Idempotent; safe to re-run.

[Unit]
Description=Piano Core library production inventory scan (no enrichment)
After=srv-piano\x2dcore-midi.mount
Requires=srv-piano\x2dcore-midi.mount

[Service]
Type=oneshot
User=piano-core
Group=piano-core
EnvironmentFile=/etc/piano-core/piano-core.env
ExecStart=/opt/piano-core/venv/bin/piano-core-library scan --no-enrich --library-root /srv/piano-core/midi --index-path /var/lib/piano-core/library/library.sqlite3
TimeoutStartSec=30min
SCANUNIT
    /usr/bin/chown root:root "$LIB_SCAN_UNIT"
    /usr/bin/chmod 0644 "$LIB_SCAN_UNIT"

    /usr/bin/tee "$LIB_VERIFY_UNIT" > /dev/null <<'VERIFYUNIT'
# PIANO-P011: service-context library verification unit (Piano-Core-owned).
#
# Oneshot proof suite run as the piano-core service user against the
# host-level read-only view: index access, catalog query, exact lookup,
# resolve, source readability, P009 load_timeline boundary, kernel
# read-only proof, index writability probe.
#
# Invoked by `piano-core-deploy library-verify`; per-step PASS/FAIL in the
# journal. Idempotent; performs no source mutations (the deliberate
# creation probe inside the read-only view is expected to FAIL).

[Unit]
Description=Piano Core library service-context verification
After=srv-piano\x2dcore-midi.mount
Requires=srv-piano\x2dcore-midi.mount

[Service]
Type=oneshot
User=piano-core
Group=piano-core
EnvironmentFile=/etc/piano-core/piano-core.env
ExecStart=/opt/piano-core/venv/bin/python3 -m piano_core.library.verify
VERIFYUNIT
    /usr/bin/chown root:root "$LIB_VERIFY_UNIT"
    /usr/bin/chmod 0644 "$LIB_VERIFY_UNIT"

    # [P6] REMOVE the superseded namespace drop-in (one canonical access
    # mechanism only: the host-level .mount unit).
    if [ -e "$LIB_OLD_DROPIN" ]; then
      /usr/bin/rm -f "$LIB_OLD_DROPIN"
      log "removed superseded namespace drop-in: $LIB_OLD_DROPIN"
    fi
    /usr/bin/rmdir "$(/usr/bin/dirname "$LIB_OLD_DROPIN")" 2>/dev/null || true

    # [P7] production library environment lines (idempotent ensure; a
    # present-but-different line aborts — never silently rewrite config)
    /usr/bin/mkdir -p "$(/usr/bin/dirname "$ENVFILE")"
    ensure_env_line "PIANO_CORE_LIBRARY_ENABLED=true"
    ensure_env_line "PIANO_CORE_LIBRARY_ROOT=$LIB_MOUNTPOINT"
    ensure_env_line "PIANO_CORE_LIBRARY_INDEX_PATH=$LIB_STATE_DIR/library.sqlite3"

    # [P8] systemd re-read, ENABLE + START the mount, verify it, restart
    # ONLY piano-core.service (no reboot; legacy NEVER touched)
    /usr/bin/systemctl daemon-reload
    /usr/bin/systemctl enable --now "$LIB_MOUNT_UNIT_NAME" \
      || die "failed to enable/start $LIB_MOUNT_UNIT_NAME"
    /usr/bin/systemctl is-active --quiet "$LIB_MOUNT_UNIT_NAME" \
      || die "$LIB_MOUNT_UNIT_NAME is not active after enable --now"
    if ! /usr/bin/mountpoint -q "$LIB_MOUNTPOINT"; then
      die "$LIB_MOUNTPOINT is not a mountpoint after starting $LIB_MOUNT_UNIT_NAME"
    fi
    # Verify the mount is READ-ONLY: a create attempt (as root here, but the
    # kernel enforces ro for everyone) MUST fail.
    if touch "$LIB_MOUNTPOINT/.provision-ro-probe" 2>/dev/null; then
      /usr/bin/rm -f "$LIB_MOUNTPOINT/.provision-ro-probe"
      die "$LIB_MOUNTPOINT is WRITABLE — read-only mount contract violated"
    fi
    # Verify the service user can traverse the mounted view.
    RESOLVED_RUNAS="$(resolve_runas)"
    "$RESOLVED_RUNAS" -u piano-core -- test -r "$LIB_MOUNTPOINT" \
      || die "service user cannot read $LIB_MOUNTPOINT through the mount"
    /usr/bin/systemctl restart "$UNIT"
    /usr/bin/sleep 3
    /usr/bin/systemctl is-active --quiet "$UNIT" \
      || { log "$UNIT DOWN after provision restart — library will degrade on next start"; exit 1; }
    /usr/bin/systemctl is-active --quiet pluto-piano-server.service \
      || { log "legacy service inactive after restart (this helper NEVER touches it) — investigate"; exit 1; }

    log "provision complete: mount=$LIB_MOUNT_UNIT_NAME source=$LIB_SOURCE view=$LIB_MOUNTPOINT index_dir=$LIB_STATE_DIR units=$LIB_SCAN_UNIT,$LIB_VERIFY_UNIT"
    echo "PROVISION OK (restart piano-core.service via deploy to activate the bind mount)"
    ;;

  api-config)
    # PIANO-P012 (values updated by PIANO-P015-HF2): one idempotent,
    # serialized pass enabling the local control API and the canonical
    # MIDI output it plays through. EXPLICIT production configuration
    # decision (recorded in docs/operations/API.md): the API binds
    # 0.0.0.0 (all interfaces) — network-independent since P015-HF2, so
    # the bind succeeds before the network is up at cold boot and
    # follows DHCP address changes — and remains an unauthenticated
    # trusted-LAN surface by design. On appliances provisioned before
    # HF2, run 'hf2-network-config' instead of re-running this.
    #
    # ORDERING GUARD: the env keys are only valid once the INSTALLED
    # package understands them (AppConfig fails closed on unknown env).
    # If the pre-check fails, deploy the P012 wheel FIRST and re-run.
    [ $# -eq 0 ] || usage
    acquire_mutation_lock
    require_active
    log "api-config: starting"

    SERVICE_USER_API="$(/usr/bin/systemctl show "$UNIT" -p User --value)"
    [ -n "$SERVICE_USER_API" ] || die "cannot determine $UNIT User="
    if ! run_as_user "$SERVICE_USER_API" "$VENV/bin/python3" -c 'from piano_core.config import KNOWN_KEYS as K; assert {"api_enabled","api_host","api_port"} <= set(K)'; then
      die "installed piano_core does not support the API config keys yet; deploy the P012 wheel first"
    fi

    ensure_env_line "PIANO_CORE_MIDI_OUTPUT_ENABLED=true"
    ensure_env_line "PIANO_CORE_API_ENABLED=true"
    ensure_env_line "PIANO_CORE_API_HOST=0.0.0.0"
    ensure_env_line "PIANO_CORE_API_PORT=8788"

    /usr/bin/systemctl restart "$UNIT"
    /usr/bin/sleep 3
    /usr/bin/systemctl is-active --quiet "$UNIT" \
      || { log "$UNIT DOWN after api-config restart — investigate: journalctl -u $UNIT"; exit 1; }
    /usr/bin/systemctl is-active --quiet pluto-piano-server.service \
      || { log "legacy service inactive after restart (this helper NEVER touches it) — investigate"; exit 1; }
    log "api-config complete: api=0.0.0.0:8788 output=enabled"
    echo "API-CONFIG OK (api enabled on 0.0.0.0:8788; service restarted)"
    ;;

  led-config)
    # PIANO-P014 (values updated by PIANO-P015-HF2): one idempotent,
    # serialized pass enabling the keyboard LED subsystem (ESP32
    # WebSocket client endpoint). EXPLICIT production configuration
    # decision (ADR-LED-KEYBOARD-V1): the endpoint binds 0.0.0.0 (all
    # interfaces) — network-independent since P015-HF2, so the endpoint
    # is listening before the network is up at cold boot — and is an
    # unauthenticated trusted-LAN surface by design (same model as the
    # API bind). Safe default remains disabled everywhere else. On
    # appliances provisioned before HF2, run 'hf2-network-config'
    # instead of re-running this.
    #
    # ORDERING GUARD: the env keys are only valid once the INSTALLED
    # package understands them (AppConfig fails closed on unknown env).
    # If the pre-check fails, deploy the P014 wheel FIRST and re-run.
    [ $# -eq 0 ] || usage
    acquire_mutation_lock
    require_active
    log "led-config: starting"

    SERVICE_USER_LED="$(/usr/bin/systemctl show "$UNIT" -p User --value)"
    [ -n "$SERVICE_USER_LED" ] || die "cannot determine $UNIT User="
    if ! run_as_user "$SERVICE_USER_LED" "$VENV/bin/python3" -c 'from piano_core.config import KNOWN_KEYS as K; assert {"led_enabled","led_ws_host","led_ws_port","led_mode","led_brightness"} <= set(K)'; then
      die "installed piano_core does not support the LED config keys yet; deploy the P014 wheel first"
    fi

    ensure_env_line "PIANO_CORE_LED_ENABLED=true"
    ensure_env_line "PIANO_CORE_LED_WS_HOST=0.0.0.0"
    ensure_env_line "PIANO_CORE_LED_WS_PORT=8787"
    ensure_env_line "PIANO_CORE_LED_MODE=all"
    ensure_env_line "PIANO_CORE_LED_BRIGHTNESS=100"

    /usr/bin/systemctl restart "$UNIT"
    /usr/bin/sleep 3
    /usr/bin/systemctl is-active --quiet "$UNIT" \
      || { log "$UNIT DOWN after led-config restart — investigate: journalctl -u $UNIT"; exit 1; }
    /usr/bin/systemctl is-active --quiet pluto-piano-server.service \
      || { log "legacy service inactive after restart (this helper NEVER touches it) — investigate"; exit 1; }
    log "led-config complete: led=0.0.0.0:8787 mode=all"
    echo "LED-CONFIG OK (led enabled on 0.0.0.0:8787; service restarted)"
    ;;

  hf2-network-config)
    # PIANO-P015-HF2: one idempotent, serialized pass migrating the
    # production binds from the appliance's DHCP address
    # (192.168.1.50) to the network-independent 0.0.0.0 for BOTH the
    # control API (:8788) and the LED endpoint (:8787). Fixes the
    # cold-boot production defect 'api unavailable (bind failed):
    # [Errno 99] Cannot assign requested address' — INADDR_ANY binds
    # before any interface address exists and follows DHCP changes
    # without reconfiguration. Same unauthenticated trusted-LAN surface
    # as before (explicit production decision; the code default stays
    # loopback). Requires the HF2 wheel FIRST (the DEGRADED health
    # semantics must exist on the installed package).
    #
    # ORDERING GUARD: the HF2 health model must be installed already.
    [ $# -eq 0 ] || usage
    acquire_mutation_lock
    require_active
    log "hf2-network-config: starting"

    SERVICE_USER_HF2="$(/usr/bin/systemctl show "$UNIT" -p User --value)"
    [ -n "$SERVICE_USER_HF2" ] || die "cannot determine $UNIT User="
    if ! run_as_user "$SERVICE_USER_HF2" "$VENV/bin/python3" -c 'from piano_core.health import HealthState; assert hasattr(HealthState, "DEGRADED")'; then
      die "installed piano_core lacks the HF2 DEGRADED health state; deploy the HF2 wheel first"
    fi

    replace_env_line "PIANO_CORE_API_HOST" "0.0.0.0"
    replace_env_line "PIANO_CORE_LED_WS_HOST" "0.0.0.0"

    /usr/bin/systemctl restart "$UNIT"
    /usr/bin/sleep 3
    /usr/bin/systemctl is-active --quiet "$UNIT" \
      || { log "$UNIT DOWN after hf2-network-config restart — investigate: journalctl -u $UNIT"; exit 1; }
    /usr/bin/systemctl is-active --quiet pluto-piano-server.service \
      || { log "legacy service inactive after restart (this helper NEVER touches it) — investigate"; exit 1; }
    log "hf2-network-config complete: api=0.0.0.0:8788 led=0.0.0.0:8787"
    echo "HF2-NETWORK-CONFIG OK (api and led bound to 0.0.0.0; service restarted)"
    ;;

  identity-provision)
    # PIANO-PROD-001: create/verify the identity directory under the
    # exact production contract (owner piano-core:piano-core, mode
    # 0700). H17-1: ALL filesystem operations run through the embedded
    # descriptor-authoritative python routine — the PATHNAME is never
    # mkdir/chown/chmod'ed directly, every parent component is walked
    # O_NOFOLLOW, inspection uses lstat semantics (dangling AND live
    # symlinks rejected), creation/chown/fchmod happen through the
    # validated parent descriptor, and nothing outside the EXACT
    # identity path can ever be mutated. H17-2: the service contract
    # (exactly User=piano-core, UID!=0, primary group piano-core) is
    # enforced BEFORE any mutation. Fail-closed on ANY unexpected
    # existing identity content; never recursive; never deletes.
    [ $# -eq 0 ] || usage
    SERVICE_USER_IDP="$(identity_require_service_contract)" || exit 1
    acquire_mutation_lock
    require_active
    log "identity-provision: starting"

    if ! identity_run_as_service_account "$VENV/bin/python3" -c 'from piano_core.config import KNOWN_KEYS as K; assert "device_identity_dir" in K'; then
      die "installed piano_core does not support the device identity config key yet; deploy the PROD-001 wheel first"
    fi

    identity_provision_fs
    log "identity-provision complete"
    ;;

  identity-init)
    # PIANO-PROD-001: the explicit one-time identity initialization,
    # executed AS THE SERVICE USER (never root) via the INSTALLED
    # reviewed CLI. H17-3: the CLI contract is preserved EXACTLY — the
    # initializer's stdout (the public descriptor) stays stdout, its
    # stderr (the stable reason) stays stderr, and its exit code is
    # propagated unchanged (0 stays 0, 2 stays 2, 3 stays 3 — never
    # collapsed to 1); helper diagnostics go to stderr only.
    [ $# -eq 0 ] || usage
    SERVICE_USER_IDI="$(identity_require_service_contract)" || exit 1
    acquire_mutation_lock
    require_active
    log "identity-init: starting (as the piano-core service user)"

    [ -d "$IDENTITY_DIR" ] || die "identity directory missing; run identity-provision first"

    set +e
    identity_init_run
    IDI_RC=$?
    set -e
    log "identity-init: initializer exit=$IDI_RC"
    if [ "$IDI_RC" -ne 0 ]; then
      # H17-3: propagate the initializer's exit code EXACTLY.
      exit "$IDI_RC"
    fi

    # Helper-side post-run contract verification (diagnostics on stderr).
    IDI_DIR_STAT="$(/usr/bin/stat -c '%U:%G %a' "$IDENTITY_DIR")"
    IDI_REC_STAT="$(/usr/bin/stat -c '%U:%G %a' "$IDENTITY_DIR/device.json")"
    IDI_LOCK_STAT="$(/usr/bin/stat -c '%U:%G %a' "$IDENTITY_DIR/.initialize.lock")"
    [ "$IDI_DIR_STAT" = "piano-core:piano-core 700" ] || die "directory contract violated: [$IDI_DIR_STAT] (required: piano-core:piano-core 700)"
    [ "$IDI_REC_STAT" = "piano-core:piano-core 600" ] || die "record contract violated: [$IDI_REC_STAT] (required: piano-core:piano-core 600)"
    [ "$IDI_LOCK_STAT" = "piano-core:piano-core 600" ] || die "lock contract violated: [$IDI_LOCK_STAT] (required: piano-core:piano-core 600)"
    IDI_HASH="$(/usr/bin/sha256sum "$IDENTITY_DIR/device.json" | /usr/bin/awk '{print $1}')"
    log "identity-init complete: dir=[$IDI_DIR_STAT] record=[$IDI_REC_STAT] lock=[$IDI_LOCK_STAT] sha256=$IDI_HASH"
    printf '%s\n' "IDENTITY-INIT OK dir=[$IDI_DIR_STAT] record=[$IDI_REC_STAT] lock=[$IDI_LOCK_STAT] record_sha256=$IDI_HASH" >&2
    ;;

  identity-config)
    # PIANO-PROD-001: idempotently add ONLY
    # PIANO_CORE_DEVICE_IDENTITY_DIR to the production env file, then
    # restart ONLY piano-core.service. H17-4: ALL existing declarations
    # of the variable are inspected BEFORE any write — duplicates and
    # conflicting values are REJECTED before the file is touched and
    # before any restart. Requires the identity record to exist already
    # (identity-init first).
    [ $# -eq 0 ] || usage
    SERVICE_USER_IDC="$(identity_require_service_contract)" || exit 1
    acquire_mutation_lock
    require_active
    log "identity-config: starting"

    if ! identity_run_as_service_account "$VENV/bin/python3" -c 'from piano_core.config import KNOWN_KEYS as K; assert "device_identity_dir" in K'; then
      die "installed piano_core does not support the device identity config key yet; deploy the PROD-001 wheel first"
    fi
    [ -f "$IDENTITY_DIR/device.json" ] || die "no identity record yet; run identity-init first"

    IDCONF_ACTION="$(identity_env_scan)" \
      || die "identity-config: env declaration scan rejected the mutation (see above)"

    case "$IDCONF_ACTION" in
      OK)
        log "identity-config: canonical declaration already present (idempotent; env unchanged, no restart)"
        echo "IDENTITY-CONFIG OK (already canonical; env unchanged, no restart)"
        ;;
      APPEND)
        identity_env_append_canonical
        /usr/bin/systemctl restart "$UNIT"
        /usr/bin/sleep 3
        /usr/bin/systemctl is-active --quiet "$UNIT" \
          || { log "$UNIT DOWN after identity-config restart — investigate: journalctl -u $UNIT"; exit 1; }
        /usr/bin/systemctl is-active --quiet pluto-piano-server.service \
          || { log "legacy service inactive after restart (this helper NEVER touches it) — investigate"; exit 1; }
        log "identity-config complete: env=PIANO_CORE_DEVICE_IDENTITY_DIR=$IDENTITY_DIR"
        echo "IDENTITY-CONFIG OK (env line added; service restarted)"
        ;;
      *)
        die "identity-config: unexpected scan action '$IDCONF_ACTION' — nothing written, nothing restarted"
        ;;
    esac
    ;;

  identity-verify)
    # PIANO-PROD-001: READ-ONLY acceptance verification (H17-5): zero
    # mutation — no log()/syslog/logfile writes, no chmod/chown, no
    # restart, no env writes; stdout/stderr only. Every acceptance
    # check must genuinely pass; ANY missing/invalid state exits
    # non-zero. No '|| true' on acceptance-critical checks.
    [ $# -eq 0 ] || usage
    identity_require_service_contract >/dev/null || exit 1
    set +e
    identity_verify_run
    VERIFY_RC=$?
    set -e
    exit "$VERIFY_RC"
    ;;

  library-scan)
    # Production inventory/enrichment scan in the service context (as the
    # piano-core service user inside the read-only service-facing view).
    [ $# -eq 0 ] || usage
    acquire_mutation_lock
    log "library-scan: starting production scan unit"
    /usr/bin/systemctl start --wait piano-core-library-scan.service \
      || die "production library scan failed (see journal)"
    log "library-scan complete"
    echo "LIBRARY-SCAN OK (see journalctl -u piano-core-library-scan for the ScanResult)"
    ;;

  library-verify)
    # Service-context proof suite (read-only w.r.t. helper state; no lock
    # needed — the unit itself only reads/probes).
    [ $# -eq 0 ] || usage
    log "library-verify: starting service-context verification unit"
    /usr/bin/systemctl start --wait piano-core-library-verify.service       || die "service-context library verification FAILED (see journal)"
    log "library-verify complete"
    echo "LIBRARY-VERIFY OK (see journalctl -u piano-core-library-verify for per-step results)"
    ;;

  status)
    [ $# -eq 0 ] || usage
    printf 'piano-core: '
    /usr/bin/systemctl is-active "$UNIT" || true
    printf 'legacy:     '
    /usr/bin/systemctl is-active pluto-piano-server.service || true
    /usr/bin/systemctl show "$UNIT" -p User -p ActiveState -p SubState -p ExecMainStartTimestamp --no-pager
    ;;

  *)
    usage
    ;;
esac
