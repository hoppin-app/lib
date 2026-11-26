# hoppin — lib

Two things live here: the **shared CI/CD platform** every hoppin repository
uses, and the shared test material for the app and the server.

## CI platform

Each repo carries two small files and nothing else:

```
<repo>/.github/workflows/ci.yml   # calls lib's pipeline (copy of templates/ci.yml)
<repo>/.github/pipeline.yml       # which checks to run, and their thresholds
```

All logic (jobs, tool versions, lint rules, schema) lives in this repo.

```yaml
# <repo>/.github/pipeline.yml — only what differs from configs/defaults.yml
version: 1
python:
  enable: true
  versions: ["3.12", "3.13"]
  test: { coverage_min: 75 }
security:
  sast: { enable: true }
```

| Path | Role |
|---|---|
| `.github/workflows/pipeline.yml` | Entry point repos call: loads config, fans out, one `gate` job |
| `.github/workflows/{python,node,ios,security}.yml` | Reusable workflows, one per stack |
| `actions/` | Composite actions: config loader, Python/Node setup, ruff, env export |
| `configs/defaults.yml` | Organisation defaults; repo files are deep-merged over it |
| `configs/pipeline.schema.json` | Validates `pipeline.yml`; unknown keys fail loudly |
| `configs/ruff.toml` | Python lint and format baseline |
| `tools/load_config.py` | Merge, validate, publish the config |
| `templates/` | Files to copy into a repo |
| `tests/test_load_config.py` | Tests for the loader, schema and templates |

What runs, all free and open source: ruff (lint, format, code smells via the
`PL` rules), mypy, coverage.py with a threshold, bandit, pip-audit, ESLint,
`tsc`, `npm audit`, SwiftLint, `xcodebuild test`, gitleaks (secrets, full
history), osv-scanner (known vulnerabilities in lockfiles), Semgrep OSS
(static analysis), plus optional CodeQL and dependency review.

Start with [docs/PIPELINE.md](docs/PIPELINE.md): setup, every option, examples.

```bash
python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt
.venv/bin/python -m unittest discover -s tests              # lib's own tests
.venv/bin/python tools/load_config.py --config ../server/.github/pipeline.yml   # dry-run a repo's config
```

## Shared test material

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
