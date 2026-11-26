# hoppin — lib

Shared test material for the app and the server, and the end-to-end test run.

| | |
|---|---|
| `fixtures/crypto_vectors.json` | Cross-language vectors. The Swift tests (ios) and the Python tests (server) both assert against these bytes, so the two sides can never drift. |
| `make_vectors.py` | Regenerates the fixtures from the server's implementation. Deterministic: re-running it must leave the file byte-identical. |
| `run_tests.sh` | The whole suite end to end: starts a real dev server, runs the server tests, then the app's unit, UI and integration tests against it. |

Part of the [hoppin](https://github.com/hoppin-app/hoppin) project. These
scripts expect `server/` and `ios/` next to `lib/`, as in the parent checkout;
CI points at them with `HOPPIN_SERVER_DIR` and `HOPPIN_IOS_DIR` instead.

```bash
lib/run_tests.sh                     # from the parent checkout
SKIP_XCODE=1 lib/run_tests.sh        # server only
../server/venv/bin/python make_vectors.py   # regenerate, from lib/
```

## Changing the protocol

1. Change `DESIGN_CONTRACT.md` in the docs repo.
2. Change the server, regenerate the fixtures here, and tag a new version.
3. Point the server, then the app, at the new tag; both test suites must pass.
