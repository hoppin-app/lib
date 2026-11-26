#!/usr/bin/env python3
"""Load, merge and validate a repository's pipeline.yml.

Merges the repo's file over configs/defaults.yml, validates the result's
inputs against configs/pipeline.schema.json, applies the checks a schema
cannot express, and publishes one JSON document per section as step outputs
so the entry workflow can fan out to the reusable workflows.
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import yaml
from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parent.parent
DEFAULTS = ROOT / "configs" / "defaults.yml"
SCHEMA = ROOT / "configs" / "pipeline.schema.json"

SECTIONS = ("python", "node", "ios", "security")

FEATURE_PREFIX = "refs/heads/ft/"

# What a push to an ft/* branch keeps: only the fast checks.
LIGHT_OFF = (
    ("python", "typecheck"),
    ("python", "test"),
    ("python", "security"),
    ("node", "typecheck"),
    ("node", "test"),
    ("node", "build"),
    ("node", "audit"),
    ("ios", "test"),
    ("security", "vulnerabilities"),
    ("security", "dependency_review"),
    ("security", "sast"),
    ("security", "codeql"),
)


class ConfigError(Exception):
    """The repository's pipeline.yml is invalid; carries every problem found."""

    def __init__(self, problems: list[str]):
        super().__init__("\n".join(problems))
        self.problems = problems


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return base with override merged in. Maps merge; everything else replaces."""
    merged = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError([f"{path}: the top level must be a mapping"])
    return data


def schema_problems(user: dict[str, Any]) -> list[str]:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    problems = []
    for error in sorted(validator.iter_errors(user), key=lambda e: list(e.absolute_path)):
        where = ".".join(str(p) for p in error.absolute_path) or "<root>"
        problems.append(f"{where}: {error.message}")
    return problems


def semantic_problems(cfg: dict[str, Any]) -> list[str]:
    """Rules the schema cannot express: cross-field requirements and paths."""
    problems: list[str] = []

    for name in ("python", "node", "ios"):
        section = cfg[name]
        if ".." in PurePosixPath(section["working_directory"]).parts:
            problems.append(f"{name}.working_directory: must not contain '..'")

    if cfg["ios"]["enable"]:
        for key in ("project", "scheme"):
            if not cfg["ios"][key]:
                problems.append(f"ios.{key}: required when ios.enable is true")

    codeql = cfg["security"]["codeql"]
    if cfg["security"]["enable"] and codeql["enable"] and not codeql["languages"]:
        problems.append("security.codeql.languages: required when codeql is enabled")
    if cfg["security"]["enable"] and codeql["enable"]:
        if "swift" in codeql["languages"] and codeql["build_mode"] == "none":
            problems.append("security.codeql.build_mode: swift needs autobuild or manual")
        if codeql["build_mode"] == "manual" and not codeql["build_command"].strip():
            problems.append("security.codeql.build_command: required when build_mode is manual")

    if cfg["python"]["enable"] and not (
        cfg["python"]["lint"]["enable"]
        or cfg["python"]["format"]["enable"]
        or cfg["python"]["typecheck"]["enable"]
        or cfg["python"]["test"]["enable"]
        or cfg["python"]["security"]["enable"]
    ):
        problems.append("python: enabled but every check inside it is disabled")

    return problems


def is_light(cfg: dict[str, Any], ref: str) -> bool:
    return ref.startswith(FEATURE_PREFIX) and not cfg["feature_branches"]["full"]


def apply_light(cfg: dict[str, Any]) -> None:
    for section, check in LIGHT_OFF:
        cfg[section][check]["enable"] = False


def load(config_path: Path, defaults_path: Path = DEFAULTS, ref: str = "") -> dict[str, Any]:
    if not config_path.is_file():
        raise ConfigError([f"{config_path}: not found. Add it to the repository."])
    user = read_yaml(config_path)
    problems = schema_problems(user)
    if problems:
        raise ConfigError(problems)
    cfg = deep_merge(read_yaml(defaults_path), user)
    problems = semantic_problems(cfg)
    if problems:
        raise ConfigError(problems)
    if is_light(cfg, ref):
        apply_light(cfg)
    return cfg


def enabled(cfg: dict[str, Any], name: str) -> bool:
    return bool(cfg[name]["enable"])


def outputs(cfg: dict[str, Any]) -> dict[str, str]:
    out = {"config": json.dumps(cfg, separators=(",", ":"))}
    for name in SECTIONS:
        out[name] = json.dumps(cfg[name], separators=(",", ":"))
        out[f"{name}_enabled"] = "true" if enabled(cfg, name) else "false"
    return out


def summary(cfg: dict[str, Any], light: bool = False) -> str:
    lines = ["### Pipeline configuration", ""]
    if light:
        lines += ["Feature branch: running the light version (lint, format, secret scan).", ""]
    lines += ["| Section | Enabled |", "|---|---|"]
    for name in SECTIONS:
        lines.append(f"| `{name}` | {'yes' if enabled(cfg, name) else 'no'} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--github-summary", type=Path)
    parser.add_argument("--ref", default="", help="full git ref, e.g. refs/heads/ft/login")
    args = parser.parse_args(argv)

    try:
        cfg = load(args.config, ref=args.ref)
    except ConfigError as err:
        for problem in err.problems:
            # Workflow command: shows up as an annotation on the run.
            print(f"::error file={args.config}::{problem}")
        print(f"{args.config} is invalid:", file=sys.stderr)
        for problem in err.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as fh:
            for key, value in outputs(cfg).items():
                fh.write(f"{key}={value}\n")
    else:
        print(json.dumps(cfg, indent=2))

    if args.github_summary:
        with args.github_summary.open("a", encoding="utf-8") as fh:
            fh.write(summary(cfg, is_light(cfg, args.ref)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
