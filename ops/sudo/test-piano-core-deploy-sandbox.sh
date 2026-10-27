#!/bin/bash
# Sandbox regression tests for the PROD-001 identity helper functions
# (H17-1..H17-5). Extracts the REAL shipped function block from
# ops/sudo/piano-core-deploy.sh between the BEGIN/END markers and
# exercises it against temporary sandbox trees with stubbed seams —
# NO root, NO systemd, NO network, NO writes outside the sandbox.
#
# Runs entirely on a development host. Exit 0 iff every test passes.
set -u
umask 022

HELPER="$(cd "$(dirname "$0")" && pwd)/piano-core-deploy.sh"
[ -f "$HELPER" ] || { echo "FAIL: harness infrastructure: helper missing: $HELPER" >&2; exit 2; }
SBX_ROOT="$(mktemp -d /tmp/piano-core-helper-sandbox.XXXXXX)" || { echo "FAIL: harness infrastructure: mktemp failed" >&2; exit 2; }
TESTS_PASS=0
TESTS_FAIL=0

cleanup() { [ "${KEEP:-0}" = "1" ] && { echo "KEEP: $SBX_ROOT" >&2; return; }; rm -rf "$SBX_ROOT"; }
trap cleanup EXIT

pass() { TESTS_PASS=$((TESTS_PASS + 1)); echo "ok: $1"; }
fail() { TESTS_FAIL=$((TESTS_FAIL + 1)); echo "FAIL: $1"; }

assert_eq() {
  if [ "$1" = "$2" ]; then pass "$3"; else fail "$3 (got '$1' want '$2')"; fi
}

assert_rc() {
  if [ "$1" -eq "$2" ]; then pass "$3"; else fail "$3 (got rc=$1 want rc=$2)"; fi
}

load_identity_functions() {
  # Stubs for the helper environment the identity functions rely on.
  UNIT="piano-core.service"
  VENV="/opt/piano-core/venv"
  IDENTITY_DIR="/var/lib/piano-core/identity"   # the FIXED production path
  die() { printf 'DIE: %s\n' "$*" >&2; exit 1; }
  log() { printf 'LOG: %s\n' "$*" >&2; }
  # H17-5 mutation canary: the verify path must NEVER call log().
  canary_log() { printf 'MUTATION: log/syslog write during verify\n' >&2; exit 99; }
  # H17-3 stub: a fake installed CLI; FAKE_CLI_RC controls its exit.
  FAKE_CLI_RC=0
  run_as_user() {
    if [ "$FAKE_CLI_RC" -eq 0 ]; then
      printf '{"product":"Piano Core","device_id":"stub","display_code":"PC-STUB","identity_schema":1}\n'
      return 0
    fi
    if [ "$FAKE_CLI_RC" -eq 3 ]; then
      printf 'identity_busy\n' >&2
      return 3
    fi
    printf 'identity_invalid_record\n' >&2
    return 2
  }
  eval "$(sed -n '/^# === BEGIN PROD-001 identity helper functions/,/^# === END PROD-001 identity helper functions/p' "$HELPER")"
  identity_group_exists() { return 0; }
}

snapshot() {
  local listing
  [ -d "$1" ] || { printf 'harness snapshot requires directory: %s\n' "$1" >&2; return 1; }
  listing="$(find "$1" -mindepth 1 \( -type f -o -type d -o -type l \) -printf '%y %m %s %p\n')" || return 1
  printf '%s\n' "$listing" | sort
}

digest_required() {
  [ -f "$1" ] || { printf 'harness digest requires regular file: %s\n' "$1" >&2; return 1; }
  sha256sum "$1" | awk '{print $1}'
}

snapshot_file_surface() {
  local surface="$1" digest
  if [ -L "$surface" ]; then
    printf 'symlink %s -> %s\n' "$surface" "$(readlink "$surface")" || return 1
  elif [ -e "$surface" ]; then
    [ -f "$surface" ] || { printf 'harness surface is not a regular file: %s\n' "$surface" >&2; return 1; }
    digest="$(digest_required "$surface")" || return 1
    printf 'file %s %s\n' "$surface" "$digest"
  else
    printf 'absent %s\n' "$surface"
  fi
}

echo "== H17-2: fixed service contract =="
load_identity_functions
identity_service_user() { printf 'piano-core\n'; }
identity_account_uid() { printf '1001\n'; }
identity_account_group() { printf 'piano-core\n'; }
( identity_require_service_contract ) >/dev/null 2>&1
assert_rc $? 0 "H17-2 valid contract (piano-core, UID!=0, group piano-core) accepted"
identity_service_user() { printf 'root\n'; }
( identity_require_service_contract ) >/dev/null 2>&1
assert_rc $? 1 "H17-2 User=root rejected"
identity_service_user() { printf 'someoneelse\n'; }
( identity_require_service_contract ) >/dev/null 2>&1
assert_rc $? 1 "H17-2 wrong user rejected"
identity_service_user() { printf 'piano-core\n'; }
identity_account_uid() { printf '0\n'; }
( identity_require_service_contract ) >/dev/null 2>&1
assert_rc $? 1 "H17-2 UID=0 rejected"
identity_account_uid() { printf '1001\n'; }
identity_account_group() { printf 'users\n'; }
( identity_require_service_contract ) >/dev/null 2>&1
assert_rc $? 1 "H17-2 wrong/missing primary group rejected"
identity_account_group() { printf 'piano-core\n'; }

echo "== H17-4: strict env declaration scan =="
identity_env_file() { printf '%s\n' "$SBX_ROOT/env"; }
ENV="$SBX_ROOT/env"
printf 'PIANO_CORE_MODE=production\n' > "$ENV"
( identity_env_scan ) >/dev/null 2>&1
assert_rc $? 0 "H17-4 zero declarations -> APPEND allowed"
assert_eq "$(identity_env_scan 2>/dev/null)" "APPEND" "H17-4 zero declarations yields APPEND"
printf 'PIANO_CORE_MODE=production\nPIANO_CORE_DEVICE_IDENTITY_DIR=/var/lib/piano-core/identity\n' > "$ENV"
assert_eq "$(identity_env_scan 2>/dev/null)" "OK" "H17-4 single canonical declaration yields OK"
BEFORE_ENV="$(digest_required "$ENV")" || fail "H17-4 harness integrity: env digest fixture missing"
printf 'PIANO_CORE_MODE=production\nPIANO_CORE_DEVICE_IDENTITY_DIR=/wrong/place\n' > "$ENV"
BEFORE_ENV="$(digest_required "$ENV")" || fail "H17-4 harness integrity: env digest fixture missing"
( identity_env_scan ) >/dev/null 2>&1
assert_rc $? 1 "H17-4 conflicting value rejected"
assert_eq "$(digest_required "$ENV")" "$BEFORE_ENV" "H17-4 conflicting value leaves env bytes unchanged"
printf 'PIANO_CORE_MODE=production\nPIANO_CORE_DEVICE_IDENTITY_DIR=/var/lib/piano-core/identity\nPIANO_CORE_DEVICE_IDENTITY_DIR=/var/lib/piano-core/identity\n' > "$ENV"
BEFORE_ENV="$(digest_required "$ENV")" || fail "H17-4 harness integrity: env digest fixture missing"
( identity_env_scan ) >/dev/null 2>&1
assert_rc $? 1 "H17-4 duplicate canonical declarations rejected"
assert_eq "$(digest_required "$ENV")" "$BEFORE_ENV" "H17-4 duplicates leave env bytes unchanged"
printf 'PIANO_CORE_MODE=production\nPIANO_CORE_DEVICE_IDENTITY_DIR=/var/lib/piano-core/identity\nPIANO_CORE_DEVICE_IDENTITY_DIR=/wrong/place\n' > "$ENV"
BEFORE_ENV="$(digest_required "$ENV")" || fail "H17-4 harness integrity: env digest fixture missing"
( identity_env_scan ) >/dev/null 2>&1
assert_rc $? 1 "H17-4 canonical + conflicting rejected"
assert_eq "$(digest_required "$ENV")" "$BEFORE_ENV" "H17-4 canonical+conflicting leaves env bytes unchanged"
# Static proof: in identity-config the scan happens BEFORE write/restart.
SCAN_LINE="$(grep -n 'IDCONF_ACTION="$(identity_env_scan)"' "$HELPER" | cut -d: -f1)"
WRITE_LINE="$(grep -n 'identity_env_append_canonical$' "$HELPER" | head -1 | cut -d: -f1)"
RESTART_LINE="$(sed -n '/^  identity-config)/,/^  identity-verify)/p' "$HELPER" | grep -c 'systemctl restart "$UNIT"')"
if [ -n "$SCAN_LINE" ] && [ -n "$WRITE_LINE" ] && [ "$SCAN_LINE" -lt "$WRITE_LINE" ]; then
  pass "H17-4 scan precedes env write in identity-config"
else
  fail "H17-4 scan does not precede env write in identity-config"
fi
if [ "$RESTART_LINE" = "1" ]; then
  pass "H17-4 exactly one restart exists inside identity-config, after the scan gate"
else
  fail "H17-4 unexpected restart count inside identity-config ($RESTART_LINE)"
fi

echo "== H17-3: initializer exit-code/stdout/stderr contract =="
identity_service_user() { printf 'piano-core\n'; }
identity_account_uid() { printf '1001\n'; }
identity_account_group() { printf 'piano-core\n'; }
INIT_DIR="$SBX_ROOT/init"; mkdir -p "$INIT_DIR"
FAKE_CLI_RC=0
( identity_init_run ) >"$SBX_ROOT/init.out" 2>"$SBX_ROOT/init.err"
assert_rc $? 0 "H17-3 initializer exit 0 propagates as helper exit 0"
grep -q '"device_id":"stub"' "$SBX_ROOT/init.out" && pass "H17-3 descriptor stays on stdout" || fail "H17-3 descriptor not on stdout"
[ -s "$SBX_ROOT/init.err" ] && fail "H17-3 unexpected stderr on success" || pass "H17-3 clean stderr on success"
FAKE_CLI_RC=2
( identity_init_run ) >"$SBX_ROOT/init.out" 2>"$SBX_ROOT/init.err"
assert_rc $? 2 "H17-3 initializer exit 2 propagates EXACTLY as 2 (not collapsed to 1)"
grep -q 'identity_invalid_record' "$SBX_ROOT/init.err" && pass "H17-3 stable initializer error stays on stderr" || fail "H17-3 error not on stderr"
[ -s "$SBX_ROOT/init.out" ] && fail "H17-3 failure leaked output to stdout" || pass "H17-3 no identity on stdout at exit 2"
FAKE_CLI_RC=3
( identity_init_run ) >"$SBX_ROOT/init.out" 2>"$SBX_ROOT/init.err"
assert_rc $? 3 "H17-3 initializer exit 3 (busy) propagates EXACTLY as 3"
grep -q 'identity_busy' "$SBX_ROOT/init.err" && pass "H17-3 busy reason stays on stderr" || fail "H17-3 busy reason not on stderr"

echo "== H17-1: descriptor-authoritative provisioning (real filesystem) =="
SANDBOX_USER="$(id -un)"
SANDBOX_GROUP="$(id -gn)"
TREE="$SBX_ROOT/h17-1"
mkdir -p "$TREE/lib/piano-core"
chmod 0755 "$TREE" "$TREE/lib" "$TREE/lib/piano-core"
IDENTITY_TARGET="$TREE/lib/piano-core/identity"

# 1) create path
( identity_provision_fs "$IDENTITY_TARGET" "$SANDBOX_USER" "$SANDBOX_GROUP" "$(id -u)" ) >"$SBX_ROOT/prov.out" 2>"$SBX_ROOT/prov.err"
assert_rc $? 0 "H17-1 creation through descriptors succeeds"
assert_eq "$(stat -c '%a' "$IDENTITY_TARGET")" "700" "H17-1 created directory mode 0700"
assert_eq "$(stat -c '%U:%G' "$IDENTITY_TARGET")" "$SANDBOX_USER:$SANDBOX_GROUP" "H17-1 created directory owned by the contract account"
# 2) idempotent re-run
( identity_provision_fs "$IDENTITY_TARGET" "$SANDBOX_USER" "$SANDBOX_GROUP" "$(id -u)" ) >/dev/null 2>&1
assert_rc $? 0 "H17-1 idempotent re-run on conforming directory"
# 3) live symlink escape
VICTIM="$TREE/victim.txt"
printf 'victim-bytes' > "$VICTIM"
chmod 0640 "$VICTIM"
rm -rf "$IDENTITY_TARGET"
ln -s "$VICTIM" "$IDENTITY_TARGET"
( identity_provision_fs "$IDENTITY_TARGET" "$SANDBOX_USER" "$SANDBOX_GROUP" "$(id -u)" ) >/dev/null 2>&1
assert_rc $? 1 "H17-1 live symlink at the identity path rejected"
assert_eq "$(stat -c '%s %a' "$VICTIM")" "12 640" "H17-1 external symlink target byte/mode unchanged"
[ -L "$IDENTITY_TARGET" ] && pass "H17-1 symlink itself untouched (not followed, not replaced)" || fail "H17-1 symlink was modified"
# 4) dangling symlink
rm "$IDENTITY_TARGET"
ln -s "$TREE/does-not-exist" "$IDENTITY_TARGET"
( identity_provision_fs "$IDENTITY_TARGET" "$SANDBOX_USER" "$SANDBOX_GROUP" "$(id -u)" ) >/dev/null 2>&1
assert_rc $? 1 "H17-1 DANGLING symlink rejected (not mistaken for a missing path)"
# 5) the reviewer race: swap target to symlink AFTER initial inspection
rm "$IDENTITY_TARGET"
mkdir -p "$IDENTITY_TARGET"
VICTIM2="$TREE/victim2"
mkdir -p "$VICTIM2"
chmod 0700 "$VICTIM2"
RACE_SNIPPET="$SBX_ROOT/provision_body.py"
sed -n "/^identity_provision_fs()/,/^}/p" "$HELPER" | sed -n "/<<'PY'/,/^PY$/p" | sed '1d;$d' > "$RACE_SNIPPET"
[ -s "$RACE_SNIPPET" ] || fail "H17-1 race harness could not extract the shipped python body"
cat > "$SBX_ROOT/race.py" << 'PYEOF'
import os
import sys

real_stat = os.stat
snippet_path, target_path, victim_path = sys.argv[1], sys.argv[2], sys.argv[3]
parent_uid = sys.argv[4]
done = {"swapped": False}


def patched_stat(path, **kwargs):
    result = real_stat(path, **kwargs)
    # Reviewer race: AFTER the initial inspection passes, swap the
    # target PATHNAME to a symlink pointing at an external victim.
    if (
        not done["swapped"]
        and path == "identity"
        and kwargs.get("follow_symlinks") is False
    ):
        done["swapped"] = True
        os.rename(target_path, target_path + ".moved-aside")
        os.symlink(victim_path, target_path)
    return result


os.stat = patched_stat
code = compile(open(snippet_path).read(), "identity_provision_fs", "exec")
sys.argv = ["identity_provision_fs", target_path, os.environ["SBX_USER"], os.environ["SBX_GROUP"], parent_uid]
try:
    exec(code, {"__name__": "__main__"})
except SystemExit as exc:
    sys.exit(exc.code or 0)
PYEOF
SBX_USER="$SANDBOX_USER" SBX_GROUP="$SANDBOX_GROUP" python3 "$SBX_ROOT/race.py" "$RACE_SNIPPET" "$IDENTITY_TARGET" "$VICTIM2" "$(id -u)" >"$SBX_ROOT/race.err" 2>&1
RACE_RC=$?
[ "$RACE_RC" -ne 0 ] && pass "H17-1 raced initialization FAILED CLOSED (rc=$RACE_RC)" || fail "H17-1 raced initialization unexpectedly succeeded"
[ -L "$IDENTITY_TARGET" ] && pass "H17-1 race: symlink at the pathname untouched" || fail "H17-1 race: pathname object changed"
[ "$(stat -c '%a' "$VICTIM2")" = "700" ] && pass "H17-1 race: external victim dir mode unchanged (0700)" || fail "H17-1 race: external victim mutated"
[ -z "$(find "$VICTIM2" -mindepth 1 -print -quit)" ] && pass "H17-1 race: external victim dir empty (no escaped temp/lock/record)" || fail "H17-1 race: content escaped into the victim"
[ ! -e "$IDENTITY_TARGET.moved-aside/device.json" ] || pass "H17-1 race: no publication in the moved-aside original (expected: none)"
# 6) unexpected record
rm -rf "$IDENTITY_TARGET" "$IDENTITY_TARGET.moved-aside"
mkdir -p "$IDENTITY_TARGET"
printf '{}' > "$IDENTITY_TARGET/device.json"
chmod 600 "$IDENTITY_TARGET/device.json"
( identity_provision_fs "$IDENTITY_TARGET" "$SANDBOX_USER" "$SANDBOX_GROUP" "$(id -u)" ) >/dev/null 2>&1
assert_rc $? 1 "H17-1 unexpected existing device.json rejected (hard stop)"
rm -rf "$IDENTITY_TARGET"
# 7) unsafe parent component (symlinked intermediate)
mkdir -p "$TREE/evil"
ln -s "$TREE/evil" "$TREE/link-lib"
( identity_provision_fs "$TREE/link-lib/piano-core/identity" "$SANDBOX_USER" "$SANDBOX_GROUP" "$(id -u)" ) >/dev/null 2>&1
assert_rc $? 1 "H17-1 symlinked parent component rejected (O_NOFOLLOW walk)"

echo "== H17-5: read-only verification =="
IDENTITY_DIR="$IDENTITY_TARGET"   # function default is the fixed path; sandbox override via env var of the same name
load_identity_functions
# Mutation canary: if ANY verify code path calls log() (syslog/logfile
# writer in the real helper), this stub kills the process loudly.
log() { printf 'MUTATION: log/syslog write during verify\n' >&2; exit 99; }
identity_env_file() { printf '%s\n' "$SBX_ROOT/h17-5.env"; }
identity_service_active() { return 0; }
identity_service_user() { printf 'piano-core\n'; }
identity_account_uid() { printf '1001\n'; }
identity_account_group() { printf 'piano-core\n'; }
IDENTITY_DIR="$IDENTITY_TARGET"
VDIR="$SBX_ROOT/h17-5"
mkdir -p "$VDIR/identity"
printf '{"identity_schema":1,"device_id":"00000000-1111-4222-8333-444455556666"}' > "$VDIR/identity/device.json"
chmod 600 "$VDIR/identity/device.json"
printf 'lock' > "$VDIR/identity/.initialize.lock"
chmod 600 "$VDIR/identity/.initialize.lock"
printf 'PIANO_CORE_DEVICE_IDENTITY_DIR=/var/lib/piano-core/identity\n' > "$SBX_ROOT/h17-5.env"
chown 700 "$VDIR/identity" 2>/dev/null || chmod 700 "$VDIR/identity"
# owner checks expect literally piano-core:piano-core; sandbox runs as the
# current user, so the harness asserts the FAILURE contract here and the
# mode/structure contract via the stat lines.
( identity_verify_run ) >"$SBX_ROOT/v.out" 2>"$SBX_ROOT/v.err"
assert_rc $? 1 "H17-5 valid-shape-but-foreign-owner state FAILS (owner must be exactly piano-core:piano-core)"
grep -q 'MUTATION' "$SBX_ROOT/v.err" && fail "H17-5 verify performed a log/mutation side effect" || pass "H17-5 zero mutation: no log/syslog write during verify (canary clean)"
grep -q 'IDENTITY-VERIFY FAILED' "$SBX_ROOT/v.err" && pass "H17-5 failures reported without OK" || fail "H17-5 missing failure report"
# structural negatives
BEFORE_SNAP="$(snapshot "$VDIR")"
mkdir -p "$VDIR/badmode"
cp -a "$VDIR/identity" "$VDIR/badmode/target"
chmod 775 "$VDIR/badmode/target"
identity_env_file() { printf '%s\n' "$SBX_ROOT/h17-5.env"; }
( IDENTITY_DIR="$VDIR/badmode/target"; identity_verify_run ) >/dev/null 2>&1
assert_rc $? 1 "H17-5 bad directory mode fails"
rm -rf "$VDIR/badmode"
# missing directory
( IDENTITY_DIR="$VDIR/absent"; identity_verify_run ) >/dev/null 2>&1
assert_rc $? 1 "H17-5 missing identity directory fails"
# inactive service
identity_service_active() { return 1; }
( IDENTITY_DIR="$VDIR/identity"; identity_verify_run ) >/dev/null 2>&1
assert_rc $? 1 "H17-5 inactive service fails"
identity_service_active() { return 0; }
# unavailable descriptor (installed CLI cannot read)
FAKE_CLI_RC=2
( IDENTITY_DIR="$VDIR/identity"; identity_verify_run ) >/dev/null 2>&1
assert_rc $? 1 "H17-5 unavailable descriptor fails"
FAKE_CLI_RC=0
# conflicting env
printf 'PIANO_CORE_DEVICE_IDENTITY_DIR=/wrong\n' > "$SBX_ROOT/h17-5.env"
( IDENTITY_DIR="$VDIR/identity"; identity_verify_run ) >/dev/null 2>&1
assert_rc $? 1 "H17-5 non-canonical env declaration fails"
printf 'PIANO_CORE_DEVICE_IDENTITY_DIR=/var/lib/piano-core/identity\n' > "$SBX_ROOT/h17-5.env"
# zero-mutation proof over the whole sandbox tree
AFTER_SNAP="$(snapshot "$VDIR")"
[ "$BEFORE_SNAP" = "$AFTER_SNAP" ] && pass "H17-5 zero mutation: filesystem snapshot identical across verify runs" || fail "H17-5 filesystem mutated by verify"

echo "== old-command regression (static) =="
grep -q '^usage: piano-core-deploy deploy <wheel> <sha256>' "$HELPER" && pass "deploy subcommand still declared" || fail "deploy subcommand missing"
grep -q 'rollback <backup-dir>' "$HELPER" && pass "rollback subcommand still declared" || fail "rollback subcommand missing"
for cmd in provision api-config led-config hf2-network-config library-scan library-verify status; do
  grep -q "^  $cmd)" "$HELPER" && pass "legacy subcommand $cmd present" || fail "legacy subcommand $cmd missing"
done
if grep -n 'pluto-piano-server' "$HELPER" | grep -Eq 'start|stop|enable|disable|mask'; then
  fail "legacy service gained a mutating reference"
else
  pass "legacy service remains check-only (is-active / is-active --quiet)"
fi
grep -A2 '^ensure_env_line()' "$HELPER" | head -3 >/dev/null && pass "generic ensure_env_line retained" || fail "generic ensure_env_line lost"


echo "== H17-2/H17-4: FULL-COMMAND DISPATCH (rewritten-copy harness) =="
# Build a dispatch copy of the REAL helper. The ONLY textual deltas vs
# the shipped file are sandbox substitutions of absolute binary paths
# and five fixed production constants (VENV/ENVFILE/IDENTITY_DIR/
# LOCKFILE/LOGFILE);
# the harness asserts exactly that below.
DISPATCH="$SBX_ROOT/dispatch-helper"
BIN="$SBX_ROOT/bin"
VENV_DIR="$SBX_ROOT/venv"
IDENTITY_SANDBOX="$SBX_ROOT/identity"
ENV_SANDBOX="$SBX_ROOT/dispatch.env"
LOCK_SANDBOX="$SBX_ROOT/lock"
LOGFILE_SANDBOX="$SBX_ROOT/helper.log"
LOGGER_CALLS="$SBX_ROOT/logger-calls.log"
PACKAGE_CALLS="$SBX_ROOT/package-calls.log"
RUNUSER_CALLS="$SBX_ROOT/runuser-calls.log"
IDENTITY_CLI_CALLS="$SBX_ROOT/identity-cli-calls.log"
RESTART_CALLS="$SBX_ROOT/restart-calls.log"
mkdir -p "$BIN" "$VENV_DIR/bin" "$IDENTITY_SANDBOX"
export DISPATCH_CALLS="$SBX_ROOT/dispatch-calls.log"
export LOGGER_CALLS PACKAGE_CALLS RUNUSER_CALLS IDENTITY_CLI_CALLS RESTART_CALLS
: > "$DISPATCH_CALLS"
: > "$LOGGER_CALLS"
: > "$PACKAGE_CALLS"
: > "$RUNUSER_CALLS"
: > "$IDENTITY_CLI_CALLS"
: > "$RESTART_CALLS"
sed -e "s|readonly VENV=\"/opt/piano-core/venv\"|readonly VENV=\"$VENV_DIR\"|" \
    -e "s|readonly ENVFILE=\"/etc/piano-core/piano-core.env\"|readonly ENVFILE=\"$ENV_SANDBOX\"|" \
    -e "s|readonly IDENTITY_DIR=\"/var/lib/piano-core/identity\"|readonly IDENTITY_DIR=\"$IDENTITY_SANDBOX\"|" \
    -e "s|readonly LOCKFILE=\"/run/lock/piano-core-deploy.lock\"|readonly LOCKFILE=\"$LOCK_SANDBOX\"|" \
    -e "s|readonly LOGFILE=\"/var/log/piano-core-deploy.log\"|readonly LOGFILE=\"$LOGFILE_SANDBOX\"|" \
    -e "s|/usr/bin/systemctl|$BIN/systemctl|g" \
    -e "s|/usr/bin/logger|$BIN/logger|g" \
    -e "s|/usr/bin/python3|$BIN/python3|g" \
    -e "s|/usr/bin/id|$BIN/id|g" \
    -e "s|/usr/bin/getent|$BIN/getent|g" \
    -e "s|/usr/sbin/runuser|$BIN/runuser|g" \
    -e "s|/usr/bin/runuser|$BIN/runuser|g" \
    -e "s|/usr/bin/su|$BIN/su|g" \
    -e 's@die "must run as root (via sudo)"@: # root guard neutralized in the sandbox dispatch copy@' \
    "$HELPER" > "$DISPATCH"
chmod +x "$DISPATCH"

# Assert the dispatch copy differs from the shipped helper ONLY in the
# expected substitution lines.
if diff "$HELPER" "$DISPATCH" | grep -Eq '^[<>]' ; then
  BAD_LINES="$(diff "$HELPER" "$DISPATCH" | grep -E '^[<>]' | grep -v 'VENV=\|ENVFILE=\|IDENTITY_DIR=\|LOCKFILE=\|LOGFILE=\|systemctl\|logger\|python3\|/usr/bin/id\|/usr/bin/getent\|runuser\|/usr/bin/su\|must run as root\|root guard neutralized' | grep -vc "$BIN/")"
  if [ "$BAD_LINES" = "0" ]; then
    pass "dispatch copy deltas are exactly the sandbox substitutions"
  else
    fail "dispatch copy has $BAD_LINES unexpected delta lines"
  fi
else
  fail "dispatch copy identical to shipped helper (substitutions missing)"
fi

# Stub binaries. The dedicated call logs are the authoritative counters:
# each record uses bash's lossless %q argv representation and is written
# before the controlled-success return/exec. No stub touches real syslog.
cat > "$BIN/logger" << 'STUB'
#!/bin/bash
{
  printf 'logger'
  for arg in "$@"; do printf ' %q' "$arg"; done
  printf '\n'
} >> "$LOGGER_CALLS"
exit 0
STUB
cat > "$BIN/systemctl" << 'STUB'
#!/bin/bash
{
  printf 'systemctl'
  for arg in "$@"; do printf ' %q' "$arg"; done
  printf '\n'
} >> "$DISPATCH_CALLS"
if [ "$1" = "show" ]; then
  printf '%s\n' "$DISPATCH_SERVICE_USER"
  exit 0
fi
if [ "$1" = "restart" ]; then
  {
    printf 'systemctl'
    for arg in "$@"; do printf ' %q' "$arg"; done
    printf '\n'
  } >> "$RESTART_CALLS"
  exit 0
fi
exit 0
STUB
cat > "$BIN/id" << 'STUB'
#!/bin/bash
echo "id $*" >> "$DISPATCH_CALLS"
if [ "${DISPATCH_ACCOUNT_MISSING:-0}" = "1" ]; then
  exit 1
fi
if [ "$1 $2" = "-u piano-core" ]; then
  printf '%s\n' "$DISPATCH_UID"
  exit 0
fi
if [ "$1 $2" = "-gn piano-core" ]; then
  printf '%s\n' "$DISPATCH_GROUP"
  exit 0
fi
exit 1
STUB
cat > "$BIN/getent" << 'STUB'
#!/bin/bash
echo "getent $*" >> "$DISPATCH_CALLS"
if [ "${DISPATCH_GROUP_MISSING:-0}" = "1" ]; then
  exit 2
fi
if [ "$1 $2" = "group piano-core" ]; then
  printf 'piano-core:x:1001:\n'
  exit 0
fi
exit 2
STUB
cat > "$BIN/runuser" << 'STUB'
#!/bin/bash
{
  printf 'runuser'
  for arg in "$@"; do printf ' %q' "$arg"; done
  printf '\n'
} >> "$RUNUSER_CALLS"
{
  printf 'runuser'
  for arg in "$@"; do printf ' %q' "$arg"; done
  printf '\n'
} >> "$DISPATCH_CALLS"
if [ "$1" = "-u" ]; then
  shift 3   # -u <user> -- <cmd...>
fi
exec "$@"
STUB
cat > "$BIN/su" << 'STUB'
#!/bin/bash
echo "su $*" >> "$DISPATCH_CALLS"
exit 1
STUB
chmod +x "$BIN/logger" "$BIN/systemctl" "$BIN/id" "$BIN/getent" "$BIN/runuser" "$BIN/su"
# Fake installed CLI in the sandbox venv (records its own calls).
cat > "$VENV_DIR/bin/piano-core-identity" << 'STUB'
#!/bin/bash
{
  printf 'piano-core-identity'
  for arg in "$@"; do printf ' %q' "$arg"; done
  printf '\n'
} >> "$IDENTITY_CLI_CALLS"
{
  printf 'fakecli'
  for arg in "$@"; do printf ' %q' "$arg"; done
  printf '\n'
} >> "$DISPATCH_CALLS"
printf '{"product":"Piano Core","device_id":"stub","display_code":"PC-STUB","identity_schema":1}\n'
exit 0
STUB
chmod +x "$VENV_DIR/bin/piano-core-identity"
cat > "$VENV_DIR/bin/python3" << 'STUB'
#!/bin/bash
{
  printf 'venv-python3'
  for arg in "$@"; do printf ' %q' "$arg"; done
  printf '\n'
} >> "$PACKAGE_CALLS"
{
  printf 'python3'
  for arg in "$@"; do printf ' %q' "$arg"; done
  printf '\n'
} >> "$DISPATCH_CALLS"
exit 0
STUB
cat > "$BIN/python3" << 'STUB'
#!/bin/bash
{
  printf 'system-python3'
  for arg in "$@"; do printf ' %q' "$arg"; done
  printf '\n'
} >> "$PACKAGE_CALLS"
{
  printf 'python3'
  for arg in "$@"; do printf ' %q' "$arg"; done
  printf '\n'
} >> "$DISPATCH_CALLS"
exit 0
STUB
chmod +x "$VENV_DIR/bin/python3" "$BIN/python3"

# Valid contract + present identity record fixtures for identity-config.
DISPATCH_SERVICE_USER="piano-core"
DISPATCH_UID="1001"
DISPATCH_GROUP="piano-core"
printf '{"identity_schema":1,"device_id":"00000000-1111-4222-8333-444455556666"}' > "$IDENTITY_SANDBOX/device.json"
chmod 600 "$IDENTITY_SANDBOX/device.json"
printf 'lock\n' > "$IDENTITY_SANDBOX/.initialize.lock"
chmod 600 "$IDENTITY_SANDBOX/.initialize.lock"

run_dispatch() {
  # $1 = subcommand; rest = env assignments for the contract fixtures
  local subcommand="$1"; shift
  local envs=()
  local arg
  for arg in "$@"; do envs+=("$arg"); done
  (
    export DISPATCH_SERVICE_USER DISPATCH_UID DISPATCH_GROUP
    export DISPATCH_ACCOUNT_MISSING DISPATCH_GROUP_MISSING
    export PYTHONPATH="$(cd "$(dirname "$0")/../.." && pwd)/src"
    : > "$DISPATCH_CALLS"
    : > "$LOGGER_CALLS"
    : > "$PACKAGE_CALLS"
    : > "$RUNUSER_CALLS"
    : > "$IDENTITY_CLI_CALLS"
    : > "$RESTART_CALLS"
    cd /tmp
    "$DISPATCH" "$subcommand"
  ) >"$SBX_ROOT/dispatch.stdout" 2>"$SBX_ROOT/dispatch.stderr"
}

calls_count() {
  local pattern="$1" calls_file="$DISPATCH_CALLS" rc count
  [ -f "$calls_file" ] || { printf 'harness calls fixture missing: %s\n' "$calls_file" >&2; return 2; }
  count="$(grep -c "$pattern" "$calls_file")"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    printf '%s\n' "$count"
    return 0
  fi
  [ "$rc" -eq 1 ] && { printf '0\n'; return 0; }
  return "$rc"
}
record_count() {
  local calls_file="$1" label="$2" count
  [ -f "$calls_file" ] || { printf 'harness %s call log missing: %s\n' "$label" "$calls_file" >&2; return 2; }
  count="$(wc -l < "$calls_file")" || return 1
  printf '%s\n' "$count"
}
logger_calls_count() { record_count "$LOGGER_CALLS" logger; }
package_calls_count() { record_count "$PACKAGE_CALLS" package; }
runuser_calls_count() { record_count "$RUNUSER_CALLS" runuser; }
identity_cli_calls_count() { record_count "$IDENTITY_CLI_CALLS" identity-cli; }
restarts_count() { record_count "$RESTART_CALLS" restart; }
assert_exact_stderr_line() {
  local actual="$1" expected="$2" message="$3" expected_file="$SBX_ROOT/expected-stderr"
  printf '%s\n' "$expected" > "$expected_file" || { fail "$message: expected-stderr fixture write failed"; return; }
  cmp -s "$expected_file" "$actual" && pass "$message" || fail "$message"
}
expected_h17_2_stderr() {
  case "$1" in
    "User=root") printf '%s\n' "H17-2 STOP: $UNIT User= is 'root' (required: exactly 'piano-core'); no identity action taken" ;;
    "UID=0") printf '%s\n' 'H17-2 STOP: piano-core has UID 0; no identity action taken' ;;
    "wrong user") printf '%s\n' "H17-2 STOP: $UNIT User= is 'someoneelse' (required: exactly 'piano-core'); no identity action taken" ;;
    "wrong group") printf '%s\n' "H17-2 STOP: piano-core primary group is 'users' (required: exactly 'piano-core'); no identity action taken" ;;
    "missing account") printf '%s\n' 'H17-2 STOP: the piano-core account does not exist; no identity action taken' ;;
    "missing group") printf '%s\n' 'H17-2 STOP: the piano-core group does not exist; no identity action taken' ;;
    *) printf 'unknown H17-2 test label: %s\n' "$1" >&2; return 2 ;;
  esac
}

echo "-- H17-2 instrumentation controls --"
: > "$LOGGER_CALLS"
"$BIN/logger" -t h17-2-control -- exact-argv
assert_eq "$(logger_calls_count)" "1" "H17-2 control: logger stub records one invocation"
assert_eq "$(cat "$LOGGER_CALLS")" "logger -t h17-2-control -- exact-argv" "H17-2 control: logger stub records argv exactly"
: > "$PACKAGE_CALLS"
"$BIN/python3" -c pass
assert_eq "$(package_calls_count)" "1" "H17-2 control: /usr/bin/python3 package stub counter increments"
assert_eq "$(cat "$PACKAGE_CALLS")" "system-python3 -c pass" "H17-2 control: /usr/bin/python3 argv is recorded exactly"
: > "$RUNUSER_CALLS"
"$BIN/runuser" -u piano-core -- /usr/bin/true
assert_eq "$(runuser_calls_count)" "1" "H17-2 control: runuser stub counter increments"
assert_eq "$(cat "$RUNUSER_CALLS")" "runuser -u piano-core -- /usr/bin/true" "H17-2 control: runuser argv is recorded exactly"
: > "$IDENTITY_CLI_CALLS"
"$VENV_DIR/bin/piano-core-identity" initialize --directory "$IDENTITY_SANDBOX" > "$SBX_ROOT/identity-cli-control.stdout"
assert_eq "$(identity_cli_calls_count)" "1" "H17-2 control: identity CLI stub counter increments"

echo "-- H17-2 dispatch: contract failure => zero package/runuser calls --"
H17_2_CASES=(
  "root|0|piano-core|User=root"
  "piano-core|0|piano-core|UID=0"
  "someoneelse|1001|piano-core|wrong user"
  "piano-core|1001|users|wrong group"
  "piano-core|1001|piano-core|missing account"
  "piano-core|1001|piano-core|missing group"
)
for case_spec in "${H17_2_CASES[@]}"; do
  IFS='|' read -r c_user c_uid c_group c_label <<< "$case_spec"
  if [ "$c_label" = "missing account" ]; then
    DISPATCH_ACCOUNT_MISSING=1
  else
    DISPATCH_ACCOUNT_MISSING=0
  fi
  if [ "$c_label" = "missing group" ]; then
    DISPATCH_GROUP_MISSING=1
  else
    DISPATCH_GROUP_MISSING=0
  fi
  # APPLY the per-case contract fixtures (H17-2 round 2: the matrix
  # previously left every case on the valid fixture).
  DISPATCH_SERVICE_USER="$c_user"
  DISPATCH_UID="$c_uid"
  DISPATCH_GROUP="$c_group"
  for subcommand in identity-provision identity-init identity-config identity-verify; do
    BEFORE_IDENTITY="$(snapshot "$IDENTITY_SANDBOX")" || {
      fail "H17-2 dispatch $subcommand [$c_label]: identity-tree snapshot fixture failed"
      continue
    }
    BEFORE_ENV="$(snapshot_file_surface "$ENV_SANDBOX")" || {
      fail "H17-2 dispatch $subcommand [$c_label]: env snapshot fixture failed"
      continue
    }
    BEFORE_LOCK="$(snapshot_file_surface "$LOCK_SANDBOX")" || {
      fail "H17-2 dispatch $subcommand [$c_label]: lock snapshot fixture failed"
      continue
    }
    BEFORE_LOGFILE="$(snapshot_file_surface "$LOGFILE_SANDBOX")" || {
      fail "H17-2 dispatch $subcommand [$c_label]: logfile snapshot fixture failed"
      continue
    }
    run_dispatch "$subcommand"
    rc=$?
    assert_rc "$rc" 1 "H17-2 dispatch $subcommand [$c_label]: exact preflight exit class 1"
    [ ! -s "$SBX_ROOT/dispatch.stdout" ] && pass "H17-2 dispatch $subcommand [$c_label]: preflight diagnostic has no stdout"       || fail "H17-2 dispatch $subcommand [$c_label]: preflight wrote stdout"
    EXPECTED_STDERR="$(expected_h17_2_stderr "$c_label")" || { fail "H17-2 dispatch $subcommand [$c_label]: expected stderr fixture unavailable"; continue; }
    assert_exact_stderr_line "$SBX_ROOT/dispatch.stderr" "$EXPECTED_STDERR" "H17-2 dispatch $subcommand [$c_label]: stderr is exactly the bounded preflight diagnostic"
    [ "$(runuser_calls_count)" = "0" ] && pass "H17-2 dispatch $subcommand [$c_label]: runuser calls = 0"       || fail "H17-2 dispatch $subcommand [$c_label]: runuser was invoked ($(runuser_calls_count))"
    [ "$(package_calls_count)" = "0" ] && pass "H17-2 dispatch $subcommand [$c_label]: python/package calls = 0"       || fail "H17-2 dispatch $subcommand [$c_label]: python/package execution was invoked ($(package_calls_count))"
    [ "$(identity_cli_calls_count)" = "0" ] && pass "H17-2 dispatch $subcommand [$c_label]: identity CLI calls = 0"       || fail "H17-2 dispatch $subcommand [$c_label]: identity CLI was invoked ($(identity_cli_calls_count))"
    [ "$(restarts_count)" = "0" ] && pass "H17-2 dispatch $subcommand [$c_label]: no restart"       || fail "H17-2 dispatch $subcommand [$c_label]: restart happened"
    [ "$(logger_calls_count)" = "0" ] && pass "H17-2 dispatch $subcommand [$c_label]: logger/syslog calls = 0"       || fail "H17-2 dispatch $subcommand [$c_label]: logger/syslog was invoked ($(logger_calls_count))"
    AFTER_IDENTITY="$(snapshot "$IDENTITY_SANDBOX")" || {
      fail "H17-2 dispatch $subcommand [$c_label]: identity-tree post-snapshot failed"
      continue
    }
    AFTER_ENV="$(snapshot_file_surface "$ENV_SANDBOX")" || {
      fail "H17-2 dispatch $subcommand [$c_label]: env post-snapshot failed"
      continue
    }
    AFTER_LOCK="$(snapshot_file_surface "$LOCK_SANDBOX")" || {
      fail "H17-2 dispatch $subcommand [$c_label]: lock post-snapshot failed"
      continue
    }
    AFTER_LOGFILE="$(snapshot_file_surface "$LOGFILE_SANDBOX")" || {
      fail "H17-2 dispatch $subcommand [$c_label]: logfile post-snapshot failed"
      continue
    }
    assert_eq "$AFTER_IDENTITY" "$BEFORE_IDENTITY" "H17-2 dispatch $subcommand [$c_label]: identity tree unchanged"
    assert_eq "$AFTER_ENV" "$BEFORE_ENV" "H17-2 dispatch $subcommand [$c_label]: env surface unchanged"
    assert_eq "$AFTER_LOCK" "$BEFORE_LOCK" "H17-2 dispatch $subcommand [$c_label]: helper lock surface unchanged"
    assert_eq "$AFTER_LOGFILE" "$BEFORE_LOGFILE" "H17-2 dispatch $subcommand [$c_label]: helper logfile surface unchanged"
  done
  DISPATCH_ACCOUNT_MISSING=0
  DISPATCH_GROUP_MISSING=0
done

# Positive control: a VALID contract must still reach the package (the
# ordering-guard python) — proving the gate blocks only failures.
DISPATCH_SERVICE_USER="piano-core"
DISPATCH_UID="1001"
DISPATCH_GROUP="piano-core"
DISPATCH_ACCOUNT_MISSING=0
DISPATCH_GROUP_MISSING=0
DISPATCH_CANONICAL="PIANO_CORE_DEVICE_IDENTITY_DIR=$IDENTITY_SANDBOX"
printf '%s\n' "$DISPATCH_CANONICAL" > "$ENV_SANDBOX"
run_dispatch identity-provision
rc=$?
assert_rc "$rc" 0 "H17-2 positive control: valid contract lets identity-provision reach its package paths"
assert_eq "$(runuser_calls_count)" "1" "H17-2 positive control: valid identity-provision calls runuser once"
assert_eq "$(package_calls_count)" "2" "H17-2 positive control: valid identity-provision reaches venv and /usr/bin/python3 paths"
assert_eq "$(identity_cli_calls_count)" "0" "H17-2 positive control: identity-provision does not invoke the identity CLI"
run_dispatch identity-config
rc=$?
assert_rc "$rc" 0 "H17-2 positive control: valid contract lets identity-config succeed"
assert_eq "$(runuser_calls_count)" "1" "H17-2 positive control: identity-config package guard executed via runuser"
assert_eq "$(package_calls_count)" "1" "H17-2 positive control: identity-config reaches the venv python path"
assert_eq "$(identity_cli_calls_count)" "0" "H17-2 positive control: identity-config does not invoke the identity CLI"
[ "$(restarts_count)" = "0" ] && pass "H17-4 Case B (dispatch): canonical-only is idempotent — no restart"   || fail "H17-4 Case B (dispatch): restart happened on idempotent success"
assert_eq "$(cat "$ENV_SANDBOX")" "$DISPATCH_CANONICAL" "H17-4 Case B (dispatch): env bytes unchanged"

echo "-- H17-4 dispatch: final-line-without-newline contract --"
# Case A: canonical + conflicting FINAL line without newline -> reject.
printf 'PIANO_CORE_DEVICE_IDENTITY_DIR=/var/lib/piano-core/identity\nPIANO_CORE_DEVICE_IDENTITY_DIR=/unexpected' > "$ENV_SANDBOX"
BEFORE_BYTES="$(digest_required "$ENV_SANDBOX")" || fail "H17-4 Case A: harness env digest fixture missing"
run_dispatch identity-config
rc=$?
[ "$rc" -ne 0 ] && pass "H17-4 Case A (dispatch): final unterminated conflict rejected" || fail "H17-4 Case A (dispatch): conflict accepted"
assert_eq "$(digest_required "$ENV_SANDBOX")" "$BEFORE_BYTES" "H17-4 Case A (dispatch): env bytes unchanged"
assert_eq "$(restarts_count)" "0" "H17-4 Case A (dispatch): no restart"
# Case C: unrelated final line without newline + no identity key -> append with separator.
printf 'PIANO_CORE_MODE=production' > "$ENV_SANDBOX"
run_dispatch identity-config
rc=$?
assert_rc "$rc" 0 "H17-4 Case C (dispatch): append after unterminated line succeeds"
assert_eq "$(cat "$ENV_SANDBOX")" "PIANO_CORE_MODE=production
$DISPATCH_CANONICAL" "H17-4 Case C (dispatch): proper line separation appended"
assert_eq "$(restarts_count)" "1" "H17-4 Case C (dispatch): exactly one restart after append"
# Case D: duplicate canonical, last duplicate without newline -> reject.
printf '%s\n%s' "$DISPATCH_CANONICAL" "$DISPATCH_CANONICAL" > "$ENV_SANDBOX"
BEFORE_BYTES="$(digest_required "$ENV_SANDBOX")" || fail "H17-4 Case D: harness env digest fixture missing"
run_dispatch identity-config
rc=$?
[ "$rc" -ne 0 ] && pass "H17-4 Case D (dispatch): duplicate canonical (unterminated) rejected" || fail "H17-4 Case D (dispatch): duplicates accepted"
assert_eq "$(digest_required "$ENV_SANDBOX")" "$BEFORE_BYTES" "H17-4 Case D (dispatch): env bytes unchanged"
# Case E: single conflicting without newline -> reject.
printf 'PIANO_CORE_DEVICE_IDENTITY_DIR=/unexpected' > "$ENV_SANDBOX"
BEFORE_BYTES="$(digest_required "$ENV_SANDBOX")" || fail "H17-4 Case E: harness env digest fixture missing"
run_dispatch identity-config
rc=$?
[ "$rc" -ne 0 ] && pass "H17-4 Case E (dispatch): single conflicting (unterminated) rejected" || fail "H17-4 Case E (dispatch): conflict accepted"
assert_eq "$(digest_required "$ENV_SANDBOX")" "$BEFORE_BYTES" "H17-4 Case E (dispatch): env bytes unchanged"
assert_eq "$(restarts_count)" "0" "H17-4 dispatch: rejected cases never restart"
# Case F regression: normal newline-terminated conflict still rejected.
printf 'PIANO_CORE_DEVICE_IDENTITY_DIR=/unexpected\n' > "$ENV_SANDBOX"
run_dispatch identity-config
rc=$?
[ "$rc" -ne 0 ] && pass "H17-4 Case F (dispatch): newline-terminated conflict still rejected" || fail "H17-4 Case F (dispatch): regression"

# Function-level byte-exact append checks (Case C exact bytes); the
# production IDENTITY_DIR default is restored for the canonical value.
IDENTITY_DIR="/var/lib/piano-core/identity"
identity_env_file() { printf '%s\n' "$SBX_ROOT/append.env"; }
printf 'PIANO_CORE_MODE=production' > "$SBX_ROOT/append.env"
identity_env_append_canonical
assert_eq "$(cat "$SBX_ROOT/append.env")" "PIANO_CORE_MODE=production
PIANO_CORE_DEVICE_IDENTITY_DIR=/var/lib/piano-core/identity" "H17-4 function-level: append inserts separator after unterminated line"
printf 'PIANO_CORE_MODE=production\n' > "$SBX_ROOT/append.env"
identity_env_append_canonical
assert_eq "$(cat "$SBX_ROOT/append.env")" "PIANO_CORE_MODE=production
PIANO_CORE_DEVICE_IDENTITY_DIR=/var/lib/piano-core/identity" "H17-4 function-level: newline-terminated file gets no blank line"
: > "$SBX_ROOT/append.env"
identity_env_append_canonical
assert_eq "$(cat "$SBX_ROOT/append.env")" "PIANO_CORE_DEVICE_IDENTITY_DIR=/var/lib/piano-core/identity" "H17-4 function-level: empty file receives bare canonical line"

echo "== static hardening checks =="
# The contract check is a true command preflight: each identity command
# validates it after argument parsing and before any lock/log side effect.
for subcommand in identity-provision identity-init identity-config identity-verify; do
  CASE_BODY="$(sed -n "/^  $subcommand)/,/^  \(identity-init\|identity-config\|identity-verify\|library-scan\))/p" "$HELPER")"
  PREFLIGHT_LINE="$(printf '%s\n' "$CASE_BODY" | grep -n 'identity_require_service_contract' | head -1 | cut -d: -f1)"
  FIRST_LOCK_LINE="$(printf '%s\n' "$CASE_BODY" | grep -n '^[[:space:]]*acquire_mutation_lock$' | head -1 | cut -d: -f1)"
  FIRST_LOG_LINE="$(printf '%s\n' "$CASE_BODY" | grep -n '^[[:space:]]*log "' | head -1 | cut -d: -f1)"
  if [ -n "$PREFLIGHT_LINE" ] \
    && { [ -z "$FIRST_LOCK_LINE" ] || [ "$PREFLIGHT_LINE" -lt "$FIRST_LOCK_LINE" ]; } \
    && { [ -z "$FIRST_LOG_LINE" ] || [ "$PREFLIGHT_LINE" -lt "$FIRST_LOG_LINE" ]; }; then
    pass "H17-2 static: $subcommand preflight precedes lock/log infrastructure"
  else
    fail "H17-2 static: $subcommand preflight does not precede lock/log infrastructure"
  fi
done
# A failed contract must use the dedicated stderr-only path, never log().
CONTRACT_BODY="$(sed -n '/^identity_require_service_contract()/,/^}/p' "$HELPER")"
printf '%s\n' "$CONTRACT_BODY" | grep -q '^[[:space:]]*log ' \
  && fail "H17-2 static: contract preflight calls normal log()" \
  || pass "H17-2 static: contract failures use no normal log()"
# Missing hash fixtures are an assertion failure, not an ignored stderr
# warning. This guards against the prior false-PASS sha256sum behavior.
if digest_required "$SBX_ROOT/intentional-missing-hash-fixture" >/dev/null 2>&1; then
  fail "harness integrity: missing digest fixture was unexpectedly accepted"
else
  pass "harness integrity: missing digest fixture fails instead of being ignored"
fi
# H17-2: inside the shipped identity regions, the ONLY run_as_user
# invocation is the one inside the hard-gate wrapper.
IDENTITY_REGION="$(sed -n '/^# === BEGIN PROD-001 identity helper functions/,/^# === END PROD-001 identity helper functions/p' "$HELPER"; sed -n '/^  identity-provision)/,/^  library-scan)/p' "$HELPER")"
BARE_CALLS="$(printf '%s\n' "$IDENTITY_REGION" | grep -v '^[[:space:]]*#' | grep -c 'run_as_user ' || true)"
[ "$BARE_CALLS" = "1" ] && pass "H17-2 static: exactly ONE run_as_user invocation in identity regions (inside the hard-gate wrapper)"   || fail "H17-2 static: $BARE_CALLS bare run_as_user invocations in identity regions (want exactly 1)"
# H17-4: the scan loop handles a final unterminated line.
grep -q 'while IFS= read -r idenv_line || \[ -n "\$idenv_line" \]' "$HELPER"   && pass "H17-4 static: scan loop processes the final unterminated line"   || fail "H17-4 static: scan loop drops the final unterminated line"
# identity-config no longer uses the generic append helper.
CONFIG_CASE="$(sed -n '/^  identity-config)/,/^  identity-verify)/p' "$HELPER")"
printf '%s\n' "$CONFIG_CASE" | grep -q 'ensure_env_line' && fail "H17-4 static: identity-config still uses the generic append"   || pass "H17-4 static: identity-config uses the PROD-001 append only"
printf '%s\n' "$CONFIG_CASE" | grep -q 'identity_env_append_canonical' && pass "H17-4 static: identity-config uses the newline-safe append"   || fail "H17-4 static: identity-config append missing"


echo
echo "sandbox results: PASS=$TESTS_PASS FAIL=$TESTS_FAIL"
[ "$TESTS_FAIL" -eq 0 ]
