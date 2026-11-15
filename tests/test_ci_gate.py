"""Required-check policy regressions; runnable without the application extras."""

import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ci_gate", ROOT / "scripts/ci_gate.py")
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)

EVENT = {"number": 42, "pull_request": {"head": {"sha": "a" * 40}, "base": {"ref": "main"}}}
PATH = ".github/workflows/tests.yml"


def run(state="success", **overrides):
    return {"id": 100, "run_attempt": 1, "path": PATH, "event": "pull_request",
            "head_sha": "a" * 40, "pull_requests": [{"number": 42}],
            "status": "completed", "conclusion": state, **overrides}


class GatePolicyTests(unittest.TestCase):
    def test_recursive_patterns_include_root_files(self):
        self.assertTrue(gate.glob_matches("**/*.py", "a.py"))
        self.assertTrue(gate.glob_matches("src/**/*.py", "src/a.py"))
        self.assertTrue(gate.glob_matches("src/**/*.py", "src/pkg/a.py"))
        self.assertFalse(gate.glob_matches("tests/test_*.py", "tests/nested/test_a.py"))

    def test_ordered_exclusions_and_reinclusion(self):
        patterns = ["web/**", "!web/**/*.md", "web/required.md"]
        self.assertTrue(gate.selected(patterns, "web/src/App.tsx"))
        self.assertFalse(gate.selected(patterns, "web/README.md"))
        self.assertTrue(gate.selected(patterns, "web/required.md"))

    def test_branch_and_path_filters(self):
        trigger = {"branches": ["main"], "paths": ["tests/**", "!tests/**/*.md"]}
        self.assertFalse(gate.applicable(trigger, "main", ["tests/README.md"]))
        self.assertFalse(gate.applicable(trigger, "other", ["tests/a.py"]))
        self.assertTrue(gate.applicable(trigger, "main", ["tests/a.py"]))
        self.assertTrue(gate.applicable({}, "main", ["README.md"]))
        self.assertFalse(gate.applicable({"paths-ignore": ["docs/**"]}, "main", ["docs/a.rst"]))
        self.assertFalse(gate.applicable({"branches-ignore": ["main"]}, "main", ["a"]))

    def test_unsupported_filters_fail_closed(self):
        with self.assertRaises(ValueError):
            gate.applicable({"types": ["opened"]}, "main", ["a"])
        with self.assertRaises(ValueError):
            gate.glob_matches("[ab].py", "a.py")

    def test_stale_unrelated_or_push_runs_do_not_count(self):
        for overrides in ({"head_sha": "b" * 40}, {"event": "push"}, {"pull_requests": [{"number": 43}]}):
            self.assertEqual(gate.run_states({PATH}, [run(**overrides)], EVENT), {PATH: "pending"})

    def test_newest_run_and_rerun_take_precedence(self):
        self.assertEqual(gate.run_states({PATH}, [run(), run("failure", id=101)], EVENT), {PATH: "failure"})
        self.assertEqual(gate.run_states({PATH}, [run(), run(None, run_attempt=2, status="in_progress")], EVENT), {PATH: "pending"})

    def test_late_workflow_registration(self):
        batches = iter([[], [run(None, status="queued")], [run()]])
        gate.wait_for_workflows({PATH}, EVENT, lambda: next(batches), clock=lambda: 0, sleep=lambda _: None)

    def test_failures_cancellation_and_skipped_workflows_block(self):
        for state in ("failure", "cancelled", "skipped", "timed_out", "neutral", None):
            with self.subTest(state=state), self.assertRaises(RuntimeError):
                gate.wait_for_workflows({PATH}, EVENT, lambda: [run(state)])

    def test_missing_workflow_times_out(self):
        with self.assertRaises(TimeoutError):
            gate.wait_for_workflows({PATH}, EVENT, lambda: [], timeout=0)

    @unittest.skipUnless(importlib.util.find_spec("yaml"), "gate workflow installs PyYAML")
    def test_real_workflow_selection(self):
        readme = gate.required_workflows(ROOT, "main", ["README.md"])
        self.assertIn(".github/workflows/documentation-checks.yml", readme)
        self.assertNotIn(PATH, readme)
        self.assertIn(PATH, gate.required_workflows(ROOT, "main", ["src/gestaltdb/graphdb.py"]))
        self.assertNotIn(".github/workflows/required-ci.yml", readme)
        # Validate every configured pattern even when a representative path does
        # not match it; unsupported future syntax must never be silently ignored.
        import yaml
        for path in (ROOT / ".github/workflows").glob("*.y*ml"):
            workflow = yaml.load(path.read_text(), Loader=yaml.BaseLoader)
            trigger = workflow.get("on", {}).get("pull_request") or {}
            for key in ("paths", "paths-ignore", "branches", "branches-ignore"):
                for pattern in trigger.get(key, []):
                    gate.glob_matches(pattern.lstrip("!"), "probe")

    def test_event_input_is_not_mutated(self):
        original = copy.deepcopy(EVENT)
        gate.run_states({PATH}, [run()], EVENT)
        self.assertEqual(EVENT, original)


if __name__ == "__main__":
    unittest.main()
