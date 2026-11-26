# Pipeline guide

Every hoppin repo runs the same CI. The logic lives in this repo (`lib`). A repo
only says which checks it wants.

## How it works

A repo has two files:

- `.github/workflows/ci.yml` calls the pipeline in lib. It is the same in every repo.
- `.github/pipeline.yml` lists what to run and the thresholds.

The pipeline reads `pipeline.yml`, merges it over `configs/defaults.yml`, checks it
against `configs/pipeline.schema.json`, and runs only the stacks you enabled
(Python, Node, iOS, security). A typo in `pipeline.yml` fails the run with a clear
message. The last job, `gate`, passes only if everything that ran passed.

## When it runs

| Event | What runs |
|---|---|
| Push to `main` or `development` | Everything enabled in `pipeline.yml` |
| Pull request into `main` or `development` | Everything enabled in `pipeline.yml` |
| Push to an `ft/*` branch | Light version: lint, format and the secret scan |

To run everything on `ft/*` branches in a repo, set this in its `pipeline.yml`:

```yaml
feature_branches:
  full: true
```

## What is in lib

| Path | What it is |
|---|---|
| `.github/workflows/pipeline.yml` | The entry point that repos call |
| `.github/workflows/python.yml`, `node.yml`, `ios.yml`, `security.yml` | One workflow per stack |
| `.github/workflows/ci.yml` | lib's own CI (runs the pipeline on itself) |
| `actions/` | Small shared steps (config loader, Python and Node setup, ruff) |
| `configs/` | Defaults, schema and the ruff baseline |
| `tools/load_config.py` | Reads, merges and validates `pipeline.yml` |
| `templates/` | Files to copy into a repo |
| `tests/` | Tests for the loader |

## Tools used

| Purpose | Tool |
|---|---|
| Python lint, format, code smells | ruff |
| Python types | mypy (off by default) |
| Python tests and coverage | unittest or pytest, coverage.py |
| Python security | bandit, pip-audit |
| Node lint, types, build, audit | ESLint, tsc, `npm run build`, `npm audit` |
| iOS | SwiftLint, `xcodebuild test` |
| Leaked secrets | gitleaks |
| Known vulnerabilities | osv-scanner |
| Static analysis | Semgrep |
| Deeper analysis | CodeQL (free on public repos) |
| Risky new dependencies in PRs | dependency review (free on public repos) |

## Add the pipeline to a repo

1. Copy `templates/ci.yml` to `.github/workflows/ci.yml`.
2. Copy the matching `templates/pipeline.<python|node|ios>.yml` to `.github/pipeline.yml` and edit it.
3. Copy `templates/dependabot.yml` to `.github/dependabot.yml`.
4. Check your config without pushing:
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt
   .venv/bin/python tools/load_config.py --config ../my-repo/.github/pipeline.yml
   ```
5. In GitHub, set branch protection to require one check: `pipeline / gate`.

## One-time setup

1. Keep `lib` public. A public repo can only call workflows from a public repo.
2. Tag lib `v1` so repos can use `@v1`:
   ```bash
   git tag v1 && git push origin v1
   ```
3. To ship a fix to every repo, test it on a branch, then move the tag:
   `git tag -f v1 && git push -f origin v1`. For a breaking change, create `v2` and
   move repos one at a time.

## Adding it to existing code

A new check should start green, then get stricter over time.

- Lint: put today's failing rule codes in `python.lint.extend_ignore` and remove them one by one (see `server/.github/pipeline.yml`).
- Coverage: set `coverage_min` just under today's number and only raise it.
- Security findings you have not reviewed: set `security.sast.fail_on_findings: false` until they are fixed.

## Options

Every key is optional except `version: 1`. Unknown keys are rejected. Maps merge
with the defaults; lists and single values replace them. The full defaults are in
`configs/defaults.yml`.

### python

| Key | Default | Meaning |
|---|---|---|
| `enable` | false | Run the Python stack |
| `working_directory` | `.` | Folder of the project |
| `versions` | `["3.12"]` | Tests run once per version |
| `requirements` | `[requirements.txt]` | Files installed before tests |
| `env` | `{}` | Environment variables for tests and type checks |
| `lint.enable` | true | ruff check |
| `lint.config` | `lib` | `lib` uses the shared rules, `repo` uses the repo's own |
| `lint.extend_ignore` | `[]` | Extra rule codes to ignore |
| `format.enable` | true | ruff format check |
| `typecheck.enable` | false | Run mypy (`typecheck.command` to change it) |
| `test.enable` | true | Run tests with coverage |
| `test.runner` | `unittest` | or `pytest` |
| `test.tests_dir` | `tests` | Where the tests are |
| `test.coverage_min` | 0 | Fail below this percent |
| `security.bandit.enable` | true | bandit (`severity`: low, medium, high) |
| `security.audit.enable` | true | pip-audit on the requirements files |

The tests can find lib's shared fixtures at `$HOPPIN_LIB_DIR`.

### node

| Key | Default | Meaning |
|---|---|---|
| `enable` | false | Run the Node stack |
| `working_directory` | `.` | Folder of the project |
| `node_version` | `22` | Node version |
| `env` | `{}` | Environment variables for all steps |
| `lint.enable`, `lint.script` | true, `lint` | `npm run lint` |
| `typecheck.enable`, `typecheck.command` | true, `npx tsc --noEmit` | Type check |
| `test.enable`, `test.script` | false, `test` | `npm run test` |
| `build.enable`, `build.script` | true, `build` | `npm run build` |
| `audit.enable`, `audit.level`, `audit.omit_dev` | true, `high`, true | `npm audit` |

Node needs a `package-lock.json` because it installs with `npm ci`.

### ios

| Key | Default | Meaning |
|---|---|---|
| `enable` | false | Run the iOS stack |
| `project` | none | Xcode project. Required when enabled |
| `scheme` | none | Scheme to test. Required when enabled |
| `destination` | iPhone 17 simulator | `xcodebuild` destination |
| `runner` | `macos-latest` | Runner image |
| `xcode_version` | empty | Empty uses the runner's default |
| `lint.enable`, `lint.strict` | true, false | SwiftLint |
| `lint.config` | `lib` | `lib` uses the shared baseline, `repo` uses the repo's own `.swiftlint.yml` |
| `lint.disable_rules` | `[]` | SwiftLint rules to switch off, for existing debt |
| `format.enable` | false | swift-format check using the shared style |
| `test.enable`, `test.extra_args` | true, empty | Extra `xcodebuild` arguments |
| `test.coverage_min` | 0 | Minimum line coverage in percent. 0 means no check |

lib is checked out at `./lib` so the shared test fixtures are found.

### security

Works for any language.

| Key | Default | Meaning |
|---|---|---|
| `secrets.enable` | true | gitleaks over the full git history |
| `vulnerabilities.enable` | true | osv-scanner on lockfiles |
| `sast.enable` | true | Semgrep |
| `sast.rulesets` | `[p/default]` | Semgrep rule packs |
| `sast.fail_on_findings` | true | false means report only |
| `dependency_review.enable` | true | Pull requests only |
| `codeql.enable` | false | Needs `codeql.languages`, for example `[python]` |
| `codeql.runner` | `ubuntu-latest` | Use a macOS runner for Swift |
| `codeql.build_mode` | `none` | `none`, `autobuild` or `manual`. Swift cannot use `none` |
| `codeql.build_command` | empty | Required when `build_mode` is `manual` |

## Example

```yaml
version: 1

python:
  enable: true
  versions: ["3.12", "3.13"]
  env:
    HOPPIN_ENV: dev
  test:
    coverage_min: 75

security:
  codeql:
    enable: true
    languages: [python]
```

## Notes

- Dependabot waits 3 days before proposing new versions of pinned actions.
- Semgrep rule packs from the registry send anonymous usage metrics to semgrep.dev.
- If lib ever becomes private, public repos can no longer call it. Private repos would also need a read token (a `lib_token` secret) because the workflows check lib out.
