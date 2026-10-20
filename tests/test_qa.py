#!/usr/bin/env python3
"""Unit tests for bin/qa. Run: python3 tests/test_qa.py (uses a temporary knowledge repo)."""

import importlib.machinery
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
QA = KIT / "bin" / "qa"

loader = importlib.machinery.SourceFileLoader("qa", str(QA))
spec = importlib.util.spec_from_loader("qa", loader)
qa = importlib.util.module_from_spec(spec)
loader.exec_module(qa)


def cli(kb, *args):
    r = subprocess.run([sys.executable, str(QA), "--kb", str(kb), *args], capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr


def make_kb(root: Path) -> Path:
    kb = root / "kb"
    for d in qa.KB_DIRS:
        (kb / d).mkdir(parents=True, exist_ok=True)
    (kb / ".qa-denylist").write_text("acmecorp\nre:\\bwidgetco\\b\n")
    return kb


class YamlTests(unittest.TestCase):
    def test_flow_and_block(self):
        doc = qa.yaml_load(
            "a: {x: 1, y: [p, \"q, r\"], z: {n: null}}\n"
            "b:\n  - one\n  - k: v\n    k2: 'it''s'\n"
            "c: |\n  line1\n  line2\n"
            "d: text # comment\n"
            "e: src/x.ts#fn\n")
        self.assertEqual(doc["a"], {"x": 1, "y": ["p", "q, r"], "z": {"n": None}})
        self.assertEqual(doc["b"], ["one", {"k": "v", "k2": "it's"}])
        self.assertEqual(doc["c"], "line1\nline2\n")
        self.assertEqual(doc["d"], "text")
        self.assertEqual(doc["e"], "src/x.ts#fn")

    def test_flow_roundtrip(self):
        v = {"applied": 1, "last_hit_at": None, "t": "a: b"}
        self.assertEqual(qa.yaml_load("s: " + qa.yaml_flow(v))["s"], v)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kb = make_kb(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_cycle(self):
        kb = self.kb
        self.assertEqual(cli(kb, "init", "demo")[0], 0)
        self.assertTrue((kb / "projects/demo/runs").is_dir())
        self.assertTrue((kb / "evals/demo/results").is_dir())

        code, out = cli(kb, "pattern", "new", "PAT-late-webhook", "--title", "늦게 온 승인 웹훅",
                        "--class", "error_propagation", "--severity", "critical")
        self.assertEqual(code, 0, out)
        self.assertIn("PAT-late-webhook", (kb / "INDEX.md").read_text())
        code, out = cli(kb, "check")
        self.assertEqual(code, 0, out)

        self.assertEqual(cli(kb, "finding", "applied", "--project", "demo", "--run", "r1",
                             "--patterns", "PAT-late-webhook")[0], 0)
        code, out = cli(kb, "finding", "add", "--project", "demo", "--run", "r1", "--title", "double charge",
                        "--severity", "critical", "--evidence", "L1", "--patterns", "PAT-late-webhook")
        self.assertEqual(code, 0, out)
        self.assertIn("F001", out)
        code, out = cli(kb, "finding", "judge", "--project", "demo", "--run", "r1", "--finding", "F001",
                        "--verdict", "false_positive", "--reason", "row lock exists", "--severity", "low")
        self.assertEqual(code, 0, out)
        st = qa.load_pattern(kb / "patterns/PAT-late-webhook.md").meta["stats"]
        self.assertEqual((st["applied"], st["findings"], st["false_positive"], st["accepted"]), (1, 1, 1, 0))

        # Re-judging supersedes the earlier verdict; counters stay consistent.
        cli(kb, "finding", "judge", "--project", "demo", "--run", "r1", "--finding", "F001",
            "--verdict", "accepted", "--reason", "reproduced on staging", "--severity", "high")
        st = qa.load_pattern(kb / "patterns/PAT-late-webhook.md").meta["stats"]
        self.assertEqual((st["false_positive"], st["accepted"]), (0, 1))
        self.assertRegex(st["last_hit_at"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(cli(kb, "check")[0], 0)

    def test_unknown_pattern_rejected(self):
        cli(self.kb, "init", "demo")
        code, out = cli(self.kb, "finding", "add", "--project", "demo", "--run", "r1", "--title", "x",
                        "--severity", "low", "--evidence", "L0", "--patterns", "PAT-nope")
        self.assertNotEqual(code, 0)
        self.assertIn("unknown pattern", out)

    def test_leaks_blocked_in_global_patterns(self):
        cli(self.kb, "pattern", "new", "PAT-leaky", "--title", "AcmeCorp checkout race")
        p = self.kb / "patterns/PAT-leaky.md"
        p.write_text(p.read_text() + "\nSee apps/server/src/pay.service.ts and ask dev@example.com\n"
                     "decision " + "T" + "-1a2b3c\n")
        code, out = cli(self.kb, "check")
        self.assertEqual(code, 1)
        for needle in ("denylist", "code path", "email address", "internal record id"):
            self.assertIn(needle, out)

    def test_project_folder_may_name_paths(self):
        cli(self.kb, "init", "demo")
        case = self.kb / "evals/demo/demo-1.yaml"
        case.write_text("id: demo-1\npre_fix_commit: abcdef1\nfailure_class: concurrency\n"
                        "scope: {feature: checkout, paths: [apps/server/src/pay.service.ts]}\n"
                        "expected_finding: {location: pay.service.ts#capture, symptom: charged twice}\n")
        code, out = cli(self.kb, "check")
        self.assertEqual(code, 0, out)

    def test_secret_blocked_everywhere(self):
        cli(self.kb, "init", "demo")
        (self.kb / "projects/demo/notes.md").write_text("api_key = sk-live-abcdefghijklmnop1234\n")
        code, out = cli(self.kb, "check")
        self.assertEqual(code, 1)
        self.assertIn("API key", out)

    def test_bad_ledger_line(self):
        cli(self.kb, "init", "demo")
        lf = self.kb / "projects/demo/runs/r1/findings.jsonl"
        lf.parent.mkdir(parents=True)
        lf.write_text(json.dumps({"type": "judgment", "finding": "F009", "verdict": "maybe"}) + "\n")
        code, out = cli(self.kb, "check")
        self.assertEqual(code, 1)
        self.assertIn("unknown finding", out)

    def test_eval_prompt_hides_answer(self):
        case = {"id": "c1", "issue": 1234, "scope": {"feature": "refund", "paths": ["a/b.ts"]},
                "expected_finding": {"location": "SECRET_LOCATION", "symptom": "SECRET_SYMPTOM"},
                "fix_commits": ["deadbeef"]}
        prompt = qa.eval_prompt(case)
        self.assertIn("a/b.ts", prompt)
        for leak in ("SECRET_LOCATION", "SECRET_SYMPTOM", "1234", "deadbeef"):
            self.assertNotIn(leak, prompt)


if __name__ == "__main__":
    unittest.main(verbosity=1)
