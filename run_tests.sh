#!/usr/bin/env bash
#
# run_tests.sh — the whole hoppin test suite, end to end.
#
#   1. starts bootstrap.py in dev mode on a free loopback port, with a
#      throw-away state file and identity key
#   2. writes /tmp/hoppin-test-server.json so the Swift integration tests can
#      find it (they skip cleanly when it is absent)
#   3. runs the Python server suite
#   4. runs `xcodebuild test` (unit + UI) on the iPhone 17 simulator
#   5. stops the server and removes every temporary file, including on failure
#
# Usage:
#   scripts/run_tests.sh                       # everything
#   SKIP_PYTHON=1 scripts/run_tests.sh         # only the app tests
#   SKIP_XCODE=1  scripts/run_tests.sh         # only the server tests
#   scripts/run_tests.sh -only-testing:HopChat-1Tests/IdentityTests
#
# Environment overrides: PYTHON, DESTINATION, DERIVED_DATA, FIXED_CODE.
#
set -Eeuo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PYTHON:-$ROOT/venv/bin/python}"
FIXED_CODE="${FIXED_CODE:-123456}"
INFO_FILE="${HOPPIN_TEST_SERVER:-/tmp/hoppin-test-server.json}"
DESTINATION="${DESTINATION:-platform=iOS Simulator,name=iPhone 17}"
DERIVED_DATA="${DERIVED_DATA:-${TMPDIR:-/tmp}/hoppin-derived-data}"
export DEVELOPER_DIR="${DEVELOPER_DIR:-/Applications/Xcode.app/Contents/Developer}"

WORK_DIR=""
SERVER_PID=""
STARTED_SERVER=0

log()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m==>\033[0m %s\n' "$*" >&2; }
fail() { printf '\033[1;31m==>\033[0m %s\n' "$*" >&2; exit 1; }

cleanup() {
    local status=$?
    if [[ -n "$SERVER_PID" ]] && kill -0 "$SERVER_PID" 2>/dev/null; then
        log "stopping bootstrap server (pid $SERVER_PID)"
        kill "$SERVER_PID" 2>/dev/null || true
        for _ in $(seq 1 50); do
            kill -0 "$SERVER_PID" 2>/dev/null || break
            sleep 0.1
        done
        kill -9 "$SERVER_PID" 2>/dev/null || true
        wait "$SERVER_PID" 2>/dev/null || true
    fi
    if [[ "$STARTED_SERVER" == "1" ]]; then
        rm -f "$INFO_FILE"
    fi
    if [[ -n "$WORK_DIR" && -d "$WORK_DIR" ]]; then
        if [[ $status -ne 0 && -f "$WORK_DIR/server.log" ]]; then
            warn "server log (last 40 lines):"
            tail -40 "$WORK_DIR/server.log" >&2 || true
        fi
        rm -rf "$WORK_DIR"
    fi
    exit $status
}
trap cleanup EXIT INT TERM

[[ -x "$PYTHON" ]] || fail "python not found at $PYTHON (create the venv, or set PYTHON=...)"

WORK_DIR="$(mktemp -d "${TMPDIR:-/tmp}/hoppin-test-run.XXXXXX")"

# ---------------------------------------------------------------------------
# 1. Start the development bootstrap server
# ---------------------------------------------------------------------------

PORT="$("$PYTHON" - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
)"

IDENTITY_KEY="$WORK_DIR/bootstrap_identity.key"

log "starting bootstrap.py on 127.0.0.1:$PORT (dev mode, fixed code $FIXED_CODE)"
HOPPIN_ENV=dev \
HOPPIN_ALLOW_PLAINTEXT=1 \
HOPPIN_DEV_FIXED_CODE="$FIXED_CODE" \
HOPPIN_PORT="$PORT" \
HOPPIN_BIND=127.0.0.1 \
HOPPIN_STATE="$WORK_DIR/state.json" \
HOPPIN_REPORTS="$WORK_DIR/reports.jsonl" \
HOPPIN_IDENTITY_KEY="$IDENTITY_KEY" \
    "$PYTHON" bootstrap.py >"$WORK_DIR/server.log" 2>&1 &
SERVER_PID=$!
STARTED_SERVER=1

# Wait for the listener to accept connections.
for attempt in $(seq 1 100); do
    kill -0 "$SERVER_PID" 2>/dev/null || fail "bootstrap.py exited early; see $WORK_DIR/server.log"
    if "$PYTHON" - "$PORT" <<'PY' 2>/dev/null
import socket, sys
s = socket.socket()
s.settimeout(0.5)
try:
    s.connect(("127.0.0.1", int(sys.argv[1])))
except OSError:
    sys.exit(1)
finally:
    s.close()
PY
    then
        break
    fi
    sleep 0.1
    [[ $attempt -lt 100 ]] || fail "bootstrap.py did not start listening on port $PORT"
done

# The server's public key comes from its identity key file, which is the same
# value it prints at startup ("server public key ...").
SERVER_KEY="$("$PYTHON" - "$IDENTITY_KEY" <<'PY'
import base64, sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

with open(sys.argv[1], "r", encoding="ascii") as fh:
    seed = base64.b64decode(fh.read().strip())
key = Ed25519PrivateKey.from_private_bytes(seed)
print(base64.b64encode(key.public_key().public_bytes(
    encoding=serialization.Encoding.Raw,
    format=serialization.PublicFormat.Raw,
)).decode("ascii"))
PY
)"
[[ -n "$SERVER_KEY" ]] || fail "could not read the server public key from $IDENTITY_KEY"

# Sanity-check it against the key the server logged.
if grep -q "server public key" "$WORK_DIR/server.log"; then
    LOGGED_KEY="$(sed -n 's/.*server public key[^:]*: //p' "$WORK_DIR/server.log" | tail -1 | tr -d '[:space:]')"
    if [[ -n "$LOGGED_KEY" && "$LOGGED_KEY" != "$SERVER_KEY" ]]; then
        fail "server key mismatch: log says $LOGGED_KEY, identity file says $SERVER_KEY"
    fi
fi

"$PYTHON" - "$INFO_FILE" "$PORT" "$SERVER_KEY" "$FIXED_CODE" <<'PY'
import json, os, sys
path, port, key, code = sys.argv[1:5]
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump({"host": "127.0.0.1", "port": int(port),
               "serverKey": key, "fixedCode": code}, fh)
os.replace(tmp, path)
PY
log "wrote $INFO_FILE (server key ${SERVER_KEY:0:12}…)"

# ---------------------------------------------------------------------------
# 2. Python server suite
# ---------------------------------------------------------------------------

if [[ "${SKIP_PYTHON:-0}" == "1" ]]; then
    warn "SKIP_PYTHON=1 — skipping the server suite"
else
    log "running the server suite"
    HOPPIN_ENV=dev "$PYTHON" -m unittest discover -s server -v
fi

# ---------------------------------------------------------------------------
# 3. App suite (unit + UI)
# ---------------------------------------------------------------------------

if [[ "${SKIP_XCODE:-0}" == "1" ]]; then
    warn "SKIP_XCODE=1 — skipping the app suite"
else
    log "running xcodebuild test on '$DESTINATION'"
    # TEST_RUNNER_-prefixed variables reach the test process (prefix stripped).
    # HOPPIN_REQUIRE_SERVER turns a missing server into a failure, not a skip.
    export TEST_RUNNER_HOPPIN_TEST_SERVER="$INFO_FILE"
    export TEST_RUNNER_HOPPIN_REQUIRE_SERVER=1
    XCODE_LOG="${XCODE_LOG:-${TMPDIR:-/tmp}/hoppin-xcodebuild.log}"
    set +e
    xcodebuild test \
        -project HopChat-1.xcodeproj \
        -scheme HopChat-1 \
        -destination "$DESTINATION" \
        -derivedDataPath "$DERIVED_DATA" \
        -resultBundlePath "$WORK_DIR/result.xcresult" \
        -parallel-testing-enabled NO \
        "$@" >"$XCODE_LOG" 2>&1
    XCODE_STATUS=$?
    set -e
    # Swift Testing and XCTest summaries.
    grep -E "Test run with|Suite .* (passed|failed)|recorded an issue|Executed [0-9]+ test|error:|\*\* TEST" \
        "$XCODE_LOG" | tail -60 || true
    if [[ $XCODE_STATUS -ne 0 ]]; then
        warn "xcodebuild test failed with status $XCODE_STATUS (full log: $XCODE_LOG)"
        exit $XCODE_STATUS
    fi
    log "full xcodebuild log: $XCODE_LOG"
fi

log "all tests passed"
