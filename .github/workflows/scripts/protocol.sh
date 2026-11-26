#!/usr/bin/env bash
#
# Verifies the wire-protocol contract between the Python server and Swift app.
#
# Both sides assert against fixtures/crypto_vectors.json. Regenerating it from
# the server (checked out at ./server) must not change a byte: a difference
# means the fixtures and the server disagree.
#
# Requirements: pip install -r server/requirements.txt
#
# Usage:
#   protocol.sh verify  Regenerate the vectors and reject any difference.
#   protocol.sh server  Run the server's tests against these fixtures.
#
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

readonly FIXTURE="fixtures/crypto_vectors.json"
readonly SERVER="${HOPPIN_SERVER_DIR:-$ROOT/server}"

cmd_verify() {
    HOPPIN_SERVER_DIR="$SERVER" python make_vectors.py

    if ! git diff --exit-code -- "$FIXTURE"; then
        echo "::error::$FIXTURE does not match the server. Run make_vectors.py and commit the result."
        return 1
    fi
}

cmd_server() {
    (cd "$SERVER" && HOPPIN_ENV=dev HOPPIN_LIB_DIR="$ROOT" python -m unittest discover -s tests)
}

dispatch "$@"
