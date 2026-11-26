"""Tests for tools/load_config.py and the shipped configs and templates."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import load_config  # noqa: E402


def load_text(text: str) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "pipeline.yml"
        path.write_text(text, encoding="utf-8")
        return load_config.load(path)


def problems_for(text: str) -> list[str]:
    try:
        load_text(text)
    except load_config.ConfigError as err:
        return err.problems
    return []


class MergeTests(unittest.TestCase):
    def test_minimal_file_gets_defaults(self):
        cfg = load_text("version: 1\n")
        self.assertFalse(cfg["python"]["enable"])
        self.assertTrue(cfg["security"]["enable"])
        self.assertEqual(cfg["python"]["versions"], ["3.12"])

    def test_override_merges_into_nested_maps(self):
        cfg = load_text("version: 1\npython:\n  enable: true\n  test:\n    coverage_min: 80\n")
        self.assertTrue(cfg["python"]["enable"])
        self.assertEqual(cfg["python"]["test"]["coverage_min"], 80)
        self.assertEqual(cfg["python"]["test"]["runner"], "unittest")

    def test_lists_replace_rather_than_append(self):
        cfg = load_text('version: 1\npython:\n  enable: true\n  versions: ["3.13"]\n')
        self.assertEqual(cfg["python"]["versions"], ["3.13"])

    def test_defaults_file_is_not_mutated_by_merge(self):
        before = load_config.read_yaml(load_config.DEFAULTS)
        load_text("version: 1\npython:\n  enable: true\n")
        self.assertEqual(before, load_config.read_yaml(load_config.DEFAULTS))


class ValidationTests(unittest.TestCase):
    def test_typo_in_key_is_rejected(self):
        problems = problems_for("version: 1\npython:\n  enabel: true\n")
        self.assertTrue(any("enabel" in p for p in problems), problems)

    def test_wrong_type_is_rejected(self):
        self.assertTrue(problems_for('version: 1\npython:\n  enable: "yes"\n'))

    def test_missing_version_is_rejected(self):
        self.assertTrue(problems_for("python:\n  enable: true\n"))

    def test_unsupported_version_is_rejected(self):
        self.assertTrue(problems_for("version: 2\n"))

    def test_missing_file_reports_path(self):
        with self.assertRaises(load_config.ConfigError) as ctx:
            load_config.load(Path("/nonexistent/pipeline.yml"))
        self.assertIn("not found", ctx.exception.problems[0])

    def test_working_directory_cannot_escape(self):
        self.assertTrue(
            problems_for("version: 1\nnode:\n  enable: true\n  working_directory: ../x\n")
        )
        self.assertTrue(problems_for("version: 1\nnode:\n  working_directory: a/../../x\n"))

    def test_absolute_working_directory_is_rejected(self):
        self.assertTrue(problems_for("version: 1\nnode:\n  working_directory: /etc\n"))

    def test_env_keys_cannot_target_runner_internals(self):
        self.assertTrue(problems_for("version: 1\npython:\n  env:\n    GITHUB_ENV: x\n"))
        self.assertTrue(problems_for("version: 1\npython:\n  env:\n    'A B': x\n"))
        self.assertFalse(problems_for("version: 1\npython:\n  env:\n    HOPPIN_ENV: dev\n"))

    def test_ios_requires_project_and_scheme(self):
        problems = problems_for("version: 1\nios:\n  enable: true\n")
        self.assertEqual(len(problems), 2, problems)
        ok = "version: 1\nios:\n  enable: true\n  project: A.xcodeproj\n  scheme: A\n"
        self.assertFalse(problems_for(ok))

    def test_ios_requirements_ignored_while_disabled(self):
        self.assertFalse(problems_for("version: 1\nios:\n  enable: false\n"))

    def test_codeql_needs_languages(self):
        text = "version: 1\nsecurity:\n  codeql:\n    enable: true\n"
        self.assertTrue(problems_for(text))
        self.assertFalse(problems_for(text + "    languages: [python]\n"))

    def test_codeql_swift_needs_a_build_mode(self):
        base = "version: 1\nsecurity:\n  codeql:\n    enable: true\n    languages: [swift]\n"
        self.assertTrue(problems_for(base))
        self.assertTrue(problems_for(base + "    build_mode: manual\n"))
        self.assertFalse(problems_for(base + "    build_mode: manual\n    build_command: make\n"))
        self.assertFalse(problems_for(base + "    build_mode: autobuild\n"))

    def test_feature_branch_runs_the_light_version(self):
        text = "version: 1\npython:\n  enable: true\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pipeline.yml"
            path.write_text(text, encoding="utf-8")
            light = load_config.load(path, ref="refs/heads/ft/login")
            main = load_config.load(path, ref="refs/heads/main")
            full = load_config.load(path.with_name("pipeline.yml"), ref="refs/pull/3/merge")
        self.assertTrue(light["python"]["lint"]["enable"])
        self.assertFalse(light["python"]["test"]["enable"])
        self.assertFalse(light["security"]["sast"]["enable"])
        self.assertTrue(light["security"]["secrets"]["enable"])
        self.assertTrue(main["python"]["test"]["enable"])
        self.assertTrue(full["python"]["test"]["enable"])

    def test_feature_branch_full_keeps_everything(self):
        text = "version: 1\nfeature_branches:\n  full: true\npython:\n  enable: true\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pipeline.yml"
            path.write_text(text, encoding="utf-8")
            cfg = load_config.load(path, ref="refs/heads/ft/login")
        self.assertTrue(cfg["python"]["test"]["enable"])

    def test_python_with_every_check_disabled_is_rejected(self):
        text = (
            "version: 1\npython:\n  enable: true\n  lint: {enable: false}\n"
            "  format: {enable: false}\n  test: {enable: false}\n"
            "  security: {enable: false}\n"
        )
        self.assertTrue(problems_for(text))

    def test_sonar_is_not_a_supported_section(self):
        self.assertTrue(problems_for("version: 1\nsonar:\n  enable: true\n"))

    def test_every_problem_is_reported_at_once(self):
        problems = problems_for("version: 1\npython:\n  enabel: true\nnode:\n  nope: 1\n")
        self.assertGreaterEqual(len(problems), 2)


class OutputTests(unittest.TestCase):
    def test_outputs_have_json_sections_and_flags(self):
        cfg = load_text("version: 1\npython:\n  enable: true\n")
        out = load_config.outputs(cfg)
        self.assertEqual(out["python_enabled"], "true")
        self.assertEqual(out["node_enabled"], "false")
        self.assertEqual(json.loads(out["python"])["versions"], ["3.12"])
        for value in out.values():
            self.assertNotIn("\n", value)

    def test_cli_writes_github_output_and_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            cfg = tmp_path / "pipeline.yml"
            cfg.write_text("version: 1\nnode:\n  enable: true\n", encoding="utf-8")
            out, summary = tmp_path / "out", tmp_path / "summary"
            code = load_config.main(
                [
                    "--config",
                    str(cfg),
                    "--github-output",
                    str(out),
                    "--github-summary",
                    str(summary),
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("node_enabled=true", out.read_text())
            self.assertIn("`node`", summary.read_text())

    def test_cli_returns_failure_on_invalid_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "pipeline.yml"
            cfg.write_text("version: 1\npython:\n  enabel: true\n", encoding="utf-8")
            self.assertEqual(load_config.main(["--config", str(cfg)]), 1)


class ShippedFilesTests(unittest.TestCase):
    def test_defaults_alone_are_a_valid_config(self):
        self.assertEqual(
            load_config.schema_problems(load_config.read_yaml(load_config.DEFAULTS)), []
        )

    def test_every_template_pipeline_config_is_valid(self):
        found = sorted((ROOT / "templates").glob("pipeline.*.yml"))
        self.assertTrue(found, "no template pipeline configs found")
        for path in found:
            with self.subTest(template=path.name):
                load_config.load(path)

    def test_every_workflow_input_default_matches_lib_ref_everywhere(self):
        refs = set()
        for path in (ROOT / ".github" / "workflows").glob("*.yml"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("default: v"):
                    refs.add(line.strip())
        self.assertLessEqual(len(refs), 1, refs)


if __name__ == "__main__":
    unittest.main()
