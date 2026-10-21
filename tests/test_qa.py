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


FINDING_CONTEXT = ("--assumption", "webhook registered", "--frequency", "rare: needs a ms window",
                   "--severity-basis", "money moves but alerts fire")


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
        self.assertNotEqual(code, 0)
        self.assertIn("--frequency", out)  # severity needs frequency, preconditions and a basis
        code, out = cli(kb, "finding", "add", "--project", "demo", "--run", "r1", "--title", "double charge",
                        "--severity", "critical", "--evidence", "L1", "--patterns", "PAT-late-webhook",
                        *FINDING_CONTEXT)
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

    def test_eval_stage_leave_one_out(self):
        kb = self.kb
        for pid in ("PAT-a", "PAT-b", "PAT-c"):
            cli(kb, "pattern", "new", pid, "--title", pid)
        c = kb / "patterns/PAT-c.md"
        c.write_text(c.read_text().replace("eval_links: []", "eval_links: [demo-1]"))
        case = {"id": "demo-1", "linked_patterns": ["PAT-a"]}
        hidden = qa.eval_hidden_patterns(kb, case)
        self.assertEqual(hidden, ["PAT-a", "PAT-c"])
        stage = Path(self.tmp.name) / "stage"
        shown = qa.stage_patterns(kb, stage, hidden)
        self.assertEqual(shown, ["PAT-b"])
        self.assertEqual(sorted(f.name for f in (stage / "patterns").iterdir()), ["PAT-b.md"])
        self.assertNotIn("PAT-a", (stage / "INDEX.md").read_text())
        needles = qa.eval_leak_needles(kb, kb / "evals/demo/demo-1.yaml", case, "demo", hidden)
        for n in ("qa-knowledge", str(kb), "evals/demo", "demo-1.yaml", "PAT-a", "PAT-c"):
            self.assertIn(n, needles)
        self.assertNotIn("PAT-b", needles)

    def test_stream_final_text(self):
        stream = "\n".join([
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "draft"}]}}),
            json.dumps({"type": "result", "result": "final answer"})])
        self.assertEqual(qa.stream_final_text(stream), "final answer")
        self.assertEqual(qa.stream_final_text(stream.rsplit("\n", 1)[0]), "draft")

    def test_eval_prompt_hides_answer(self):
        case = {"id": "c1", "issue": 1234, "scope": {"feature": "refund", "paths": ["a/b.ts"]},
                "expected_finding": {"location": "SECRET_LOCATION", "symptom": "SECRET_SYMPTOM"},
                "fix_commits": ["deadbeef"]}
        prompt = qa.eval_prompt(case)
        self.assertIn("a/b.ts", prompt)
        for leak in ("SECRET_LOCATION", "SECRET_SYMPTOM", "1234", "deadbeef"):
            self.assertNotIn(leak, prompt)

    def test_headless_runs_default_to_code_mode(self):
        # Headless runs (qa eval run, claude -p --agent qa-breaker) must never stop to ask the mode.
        prompt = qa.eval_prompt({"id": "c1", "scope": {"feature": "refund", "paths": ["a/b.ts"]}})
        self.assertIn("Mode: code", prompt)
        self.assertIn("Do not ask which mode", prompt)
        breaker = (KIT / "agents/qa-breaker.md").read_text()
        self.assertIn("Never ask the user which mode", breaker)
        self.assertIn("run in `code` mode", breaker)
        run_skill = (KIT / "skills/qa-run/SKILL.md").read_text()
        self.assertIn("없으면 `code`로 진행", run_skill)

    def test_device_evidence_level_accepted(self):
        kb = self.kb
        self.assertEqual(cli(kb, "init", "demo")[0], 0)
        code, out = cli(kb, "finding", "add", "--project", "demo", "--run", "r1", "--title", "result lost on return",
                        "--severity", "high", "--evidence", "LD", *FINDING_CONTEXT)
        self.assertEqual(code, 0, out)
        self.assertEqual(cli(kb, "check")[0], 0)
        code, out = cli(kb, "finding", "add", "--project", "demo", "--run", "r1", "--title", "x",
                        "--severity", "low", "--evidence", "L9", *FINDING_CONTEXT)
        self.assertNotEqual(code, 0)

    def test_device_skill_is_client_neutral(self):
        import re
        bad = re.compile("work" + r"log|\b[DQTPBH]-[0-9a-f]{6}\b|\b(?:DP|SL|QA)-[A-Z]+-\d+\b", re.I)
        for f in sorted((KIT / "skills/device-qa").rglob("*")):
            if f.is_file() and f.suffix in (".md", ".py"):
                hits = [m.group(0) for m in bad.finditer(f.read_text())]
                self.assertEqual(hits, [], f"{f.relative_to(KIT)}: {hits}")


def fake_gh(root: Path, issues) -> dict:
    """PATH with a fake read-only `gh` that prints `issues` as JSON for `gh issue list`."""
    bin_dir = root / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    data = root / "issues.json"
    data.write_text(json.dumps(issues))
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/sh\necho \"$@\" >> {root / 'gh-args.log'}\ncat {data}\n")
    gh.chmod(0o755)
    return dict(os.environ, PATH=f"{bin_dir}:/usr/bin:/bin")


def cli_env(kb, env, *args):
    r = subprocess.run([sys.executable, str(QA), "--kb", str(kb), *args], capture_output=True, text=True, env=env)
    return r.returncode, r.stdout + r.stderr


class LedgerAndRetroTests(unittest.TestCase):
    RUN = ("--project", "demo", "--run", "r1")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kb = make_kb(Path(self.tmp.name))
        cli(self.kb, "init", "demo")
        for pid in ("PAT-near", "PAT-other"):
            cli(self.kb, "pattern", "new", pid, "--title", pid, "--class", "concurrency", "--severity", "high")
        for title, sev in (("refunded item still delivered", "high"), ("audit names wrong item", "low")):
            code, out = cli(self.kb, "finding", "add", *self.RUN, "--title", title, "--severity", sev,
                            "--evidence", "L1", *FINDING_CONTEXT)
            self.assertEqual(code, 0, out)

    def tearDown(self):
        self.tmp.cleanup()

    def test_two_review_rounds_issue_and_list(self):
        kb = self.kb
        self.assertEqual(cli(kb, "finding", "review", *self.RUN, "--finding", "F001", "--round", "1",
                             "--verdict", "stands", "--severity", "high", "--reason", "no guard")[0], 0)
        code, out = cli(kb, "finding", "review", *self.RUN, "--finding", "F001", "--round", "2",
                        "--verdict", "downgraded", "--severity", "medium", "--reason", "earlier check blocks it",
                        "--corrections", "removed amplification claim")
        self.assertEqual(code, 0, out)
        self.assertNotEqual(cli(kb, "finding", "review", *self.RUN, "--finding", "F001", "--round", "3",
                                "--verdict", "stands", "--severity", "high", "--reason", "x")[0], 0)
        code, out = cli(kb, "finding", "judge", *self.RUN, "--finding", "F001", "--verdict", "accepted",
                        "--severity", "medium", "--reason", "filed", "--issue", "#999", "--related", "405,406")
        self.assertEqual(code, 0, out)
        code, out = cli(kb, "finding", "list", "--project", "demo")
        self.assertEqual(code, 0, out)
        self.assertIn("r1:stands(high) r2:downgraded(medium)", out)
        self.assertIn("#999", out)
        self.assertIn("2 findings, 1 judged", out)
        ev = [json.loads(x) for x in (kb / "projects/demo/runs/r1/findings.jsonl").read_text().splitlines()]
        self.assertEqual(ev[-1]["issue"], [999])
        self.assertEqual(ev[-1]["related"], [405, 406])
        self.assertEqual(cli(kb, "check")[0], 0)

    def test_calibration_is_client_neutral(self):
        kb = self.kb
        code, out = cli(kb, "calibration", "add", *self.RUN, "--finding", "F001", "--reason", "x")
        self.assertNotEqual(code, 0)
        self.assertIn("no judgment", out)
        cli(kb, "finding", "judge", *self.RUN, "--finding", "F001", "--verdict", "accepted",
            "--severity", "medium", "--reason", "ok")
        code, out = cli(kb, "calibration", "add", *self.RUN, "--finding", "F001", "--reason", "AcmeCorp window")
        self.assertNotEqual(code, 0)
        self.assertIn("client-neutral", out)
        code, out = cli(kb, "calibration", "add", *self.RUN, "--finding", "F001",
                        "--reason", "ms window plus stacked preconditions", "--tags", "ms_window")
        self.assertEqual(code, 0, out)
        self.assertIn("high -> medium (down)", out)
        # Re-adding replaces the row instead of duplicating it.
        cli(kb, "calibration", "add", *self.RUN, "--finding", "F001", "--reason", "ms window", "--tags", "ms_window")
        rows = qa.read_calibration(kb)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("demo", json.dumps(rows))
        code, out = cli(kb, "calibration", "list")
        self.assertIn("down 1", out)
        self.assertEqual(cli(kb, "check")[0], 0)

    def write_proposal(self, body: str) -> Path:
        p = self.kb / "proposals/2026-10-21-test.md"
        p.write_text("# Proposal 2026-10-21 test\n\nsource: project=demo runs=r1\n\n" + body)
        return p

    NEW_PATTERN = """## P1 — new-pattern: PAT-guard-outside-tx
```qa-apply
op: new-pattern
id: PAT-guard-outside-tx
failure_class: concurrency
severity_prior: high
title: "가드가 트랜잭션 밖에서 읽은 값을 본다"
scope: {domains: [concurrency], preconditions: ["읽기와 쓰기가 다른 트랜잭션"]}
probe: ["읽기와 쓰기 사이에 다른 경로를 끼운다"]
oracle: "가드는 쓰는 트랜잭션 안에서 다시 확인한다"
false_positive_traps: ["조건부 갱신이 0행이면 롤백한다"]
attribute: [demo/r1/F002]
body: |
  ## 무엇이 무너지는가
  가드가 오래된 값을 본다.

  ## 흔한 오탐
  - 잠금 아래에서 다시 읽는다
decision: pending
```

## P2 — fp-trap: PAT-near
```qa-apply
op: append
pattern: PAT-near
field: false_positive_traps
value: "유일 제약과 조기 반환이 있다"
decision: pending
```

## P3 — body: PAT-near
```qa-apply
op: body-append
pattern: PAT-near
section: 흔한 오탐
value: 유일 제약과 조기 반환이 있다
decision: pending
```

## P4 — attribute: F001
```qa-apply
op: attribute
project: demo
run: r1
finding: F001
patterns: [PAT-near, PAT-guard-outside-tx]
decision: pending
```

## P5 — notes: won't fix
```qa-apply
op: notes-append
project: demo
section: Won't-fix policy
value: "reissue during refund: accepted risk (owner decision)"
decision: pending
```

## P6 — rejected idea
```qa-apply
op: set
pattern: PAT-other
field: status
value: retired
decision: pending
```

## P7 — note only
No qa-apply block: discussion item.
"""

    def test_proposal_decide_apply_idempotent(self):
        kb = self.kb
        cli(kb, "finding", "judge", *self.RUN, "--finding", "F001", "--verdict", "accepted",
            "--severity", "high", "--reason", "ok")
        p = self.write_proposal(self.NEW_PATTERN)
        code, out = cli(kb, "proposal", "show", p.name)
        self.assertEqual(code, 0, out)
        self.assertIn("pending 6", out)
        self.assertIn("note 1", out)
        self.assertIn("nothing to apply", cli(kb, "proposal", "apply", p.name)[1])
        code, out = cli(kb, "proposal", "decide", p.name, "--all-pending", "--reject", "P6", "--by", "lead")
        self.assertEqual(code, 0, out)
        code, out = cli(kb, "proposal", "apply", p.name, "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertFalse((kb / "patterns/PAT-guard-outside-tx.md").exists())
        code, out = cli(kb, "proposal", "apply", p.name)
        self.assertEqual(code, 0, out)
        new = qa.load_pattern(kb / "patterns/PAT-guard-outside-tx.md").meta
        self.assertEqual(new["status"], "candidate")
        self.assertEqual(new["stats"]["accepted"], 1)  # P4 attributed the accepted F001
        self.assertEqual(new["stats"]["findings"], 2)  # plus F002 from the new-pattern item, still untriaged
        near = qa.load_pattern(kb / "patterns/PAT-near.md")
        self.assertIn("유일 제약과 조기 반환이 있다", near.meta["false_positive_traps"])
        self.assertIn("- 유일 제약과 조기 반환이 있다", near.body)
        self.assertEqual(near.meta["stats"]["accepted"], 1)
        self.assertEqual(qa.load_pattern(kb / "patterns/PAT-other.md").meta["status"], "candidate")
        notes = (kb / "projects/demo/notes.md").read_text()
        self.assertIn("## Won't-fix policy\n\n- reissue during refund", notes)
        self.assertIn("PAT-guard-outside-tx", (kb / "INDEX.md").read_text())
        text = p.read_text()
        self.assertEqual(text.count("applied: "), 5)
        self.assertIn("decision: rejected", text)
        # Second apply is a no-op; ledger gets no duplicate attribution.
        self.assertIn("nothing to apply", cli(kb, "proposal", "apply", p.name)[1])
        ledger = (kb / "projects/demo/runs/r1/findings.jsonl").read_text()
        self.assertEqual(ledger.count('"attribution"'), 2)
        self.assertEqual(cli(kb, "check")[0], 0)

    def test_proposal_rolls_back_on_check_failure(self):
        kb = self.kb
        p = self.write_proposal("""## P1 — new-pattern: PAT-leaky
```qa-apply
op: new-pattern
id: PAT-leaky
failure_class: boundary
severity_prior: low
title: "AcmeCorp only"
oracle: "x"
body: |
  ## 무엇이 무너지는가
  see apps/server/src/pay.service.ts
decision: approved
```

## P2 — fp-trap: PAT-near
```qa-apply
op: append
pattern: PAT-near
field: probe
value: "something"
decision: approved
```
""")
        before = (kb / "patterns/PAT-near.md").read_text()
        code, out = cli(kb, "proposal", "apply", p.name)
        self.assertNotEqual(code, 0)
        self.assertIn("rolled back", out)
        self.assertFalse((kb / "patterns/PAT-leaky.md").exists())
        self.assertEqual((kb / "patterns/PAT-near.md").read_text(), before)
        self.assertNotIn("applied:", p.read_text())

    def test_new_pattern_must_be_candidate(self):
        p = self.write_proposal("""## P1 — new
```qa-apply
op: new-pattern
id: PAT-x
status: active
failure_class: boundary
severity_prior: low
title: x
oracle: x
body: x
decision: approved
```
""")
        code, out = cli(self.kb, "proposal", "apply", p.name)
        self.assertNotEqual(code, 0)
        self.assertIn("candidate", out)

    def test_link_eval_and_audit(self):
        kb = self.kb
        case = kb / "evals/demo/demo-1.yaml"
        case.write_text("id: demo-1\npre_fix_commit: abcdef1\nfailure_class: boundary\n"
                        "scope: {paths: [a.ts]}\nexpected_finding: {symptom: x}\nlinked_patterns: [PAT-near]\n")
        other = kb / "patterns/PAT-other.md"
        other.write_text(other.read_text().replace("eval_links: []", "eval_links: [demo-1]"))
        code, out = cli(kb, "check")
        self.assertIn("linked_patterns misses PAT-other", out)
        p = self.write_proposal("""## P1 — link
```qa-apply
op: link-eval
case: demo-1
patterns: [PAT-other]
decision: approved
```
""")
        code, out = cli(kb, "proposal", "apply", p.name)
        self.assertEqual(code, 0, out)
        self.assertEqual(qa.load_yaml_file(case)["linked_patterns"], ["PAT-near", "PAT-other"])
        code, out = cli(kb, "eval", "audit")
        self.assertEqual(code, 0, out)
        self.assertNotIn("misses PAT-other", cli(kb, "check")[1])


class TrackerTests(unittest.TestCase):
    ISSUES = [
        {"number": 999, "title": "kit found it", "labels": [{"name": "bug"}, {"name": "backend"}],
         "state": "OPEN", "stateReason": "", "createdAt": "2026-10-20T10:00:00Z"},
        {"number": 1010, "title": "customer QA | found this", "labels": [{"name": "bug"}, {"name": "frontend"}],
         "state": "OPEN", "stateReason": "", "createdAt": "2026-10-27T01:00:00Z"},
        {"number": 405, "title": "old decision", "labels": [{"name": "bug"}],
         "state": "CLOSED", "stateReason": "NOT_PLANNED", "createdAt": "2026-09-01T00:00:00Z"},
    ]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.kb = make_kb(self.root)
        cli(self.kb, "init", "demo")
        y = self.kb / "projects/demo/project.yaml"
        y.write_text(y.read_text() + "issue_tracker: {kind: github, repo: acme/demo}\n")
        cli(self.kb, "finding", "add", "--project", "demo", "--run", "r1", "--title", "kit found it",
            "--severity", "high", "--evidence", "L1", *FINDING_CONTEXT)
        cli(self.kb, "finding", "judge", "--project", "demo", "--run", "r1", "--finding", "F001",
            "--verdict", "accepted", "--severity", "high", "--reason", "filed", "--issue", "999")
        self.env = fake_gh(self.root, self.ISSUES)

    def tearDown(self):
        self.tmp.cleanup()

    def test_misses_collect_excludes_kit_issues(self):
        code, out = cli_env(self.kb, self.env, "misses", "collect", "--project", "demo", "--since", "2026-10-20",
                            "--date", "2026-10-27")
        self.assertEqual(code, 0, out)
        self.assertIn("2 issue(s), 1 excluded as kit-reported, 1 candidate(s)", out)
        md = (self.kb / "projects/demo/misses/2026-10-27.md").read_text()
        self.assertIn("| #1010 | customer QA \\| found this | bug, frontend | frontend | open | 2026-10-27 |", md)
        self.assertIn("#999 (r1 F001)", md)
        self.assertNotIn("| #999 |", md)
        self.assertNotIn("#405", md)
        self.assertIn("### #1010", md)
        log = (self.root / "gh-args.log").read_text()
        self.assertIn("issue list -R acme/demo --state all", log)
        self.assertIn("created:>=2026-10-20", log)
        for verb in ("create", "edit", "comment", "close"):
            self.assertNotIn(f"issue {verb}", log)
        code, out = cli_env(self.kb, self.env, "misses", "collect", "--project", "demo", "--since", "2026-10-20",
                            "--date", "2026-10-27")
        self.assertNotEqual(code, 0)
        self.assertIn("--force", out)
        self.assertEqual(cli(self.kb, "check")[0], 0)

    def test_issue_search_marks_existing_decisions(self):
        cli(self.kb, "finding", "add", "--project", "demo", "--run", "r1", "--title", "reissue during refund",
            "--severity", "medium", "--evidence", "L1", *FINDING_CONTEXT)
        cli(self.kb, "finding", "judge", "--project", "demo", "--run", "r1", "--finding", "F002",
            "--verdict", "wont_fix", "--severity", "medium", "--reason", "accepted risk", "--related", "405")
        code, out = cli_env(self.kb, self.env, "issue", "search", "--project", "demo", "reissue", "refund")
        self.assertEqual(code, 0, out)
        self.assertIn("closed as not planned: existing decision", out)
        self.assertIn("reported by kit run r1 F001", out)
        self.assertIn("local   r1 F002 wont_fix: reissue during refund (related #405)", out)

    def test_tracker_missing_config(self):
        y = self.kb / "projects/demo/project.yaml"
        y.write_text(y.read_text().replace("issue_tracker: {kind: github, repo: acme/demo}\n", ""))
        code, out = cli_env(self.kb, self.env, "misses", "collect", "--project", "demo", "--since", "2026-10-20")
        self.assertNotEqual(code, 0)
        self.assertIn("issue_tracker", out)


class DoctorAndEvalReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.kb = make_kb(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_doctor_target_warns_missing_tools(self):
        repo = self.root / "repo"
        (repo / "apps/server").mkdir(parents=True)
        (repo / "package.json").write_text(json.dumps({"scripts": {"test": "bun run test"}}))
        (repo / "bun.lock").write_text("")
        (repo / "apps/server/docker-compose.dev.yml").write_text("")
        (repo / "node_modules/x").mkdir(parents=True)
        (repo / "node_modules/x/pyproject.toml").write_text("")  # ignored: inside node_modules
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        node = bin_dir / "node"
        node.write_text("#!/bin/sh\necho v22.1.0\n")
        node.chmod(0o755)
        env = dict(os.environ, PATH=f"{bin_dir}:/usr/bin:/bin", HOME=str(self.root))
        before = sorted(str(p) for p in repo.rglob("*"))
        code, out = cli_env(self.kb, env, "doctor", "--offline", "--target", str(repo))
        self.assertIn("[ok  ] node", out)
        self.assertIn("[WARN] bun — not found (needed by bun.lock, package.json scripts/packageManager)", out)
        self.assertIn("[WARN] docker — not found (needed by apps/server/docker-compose.dev.yml)", out)
        self.assertIn("L4 장애 주입 불가", out)
        self.assertNotIn("python3", out)
        self.assertIn("qa doctor --target: 2 warning(s)", out)
        self.assertEqual(sorted(str(p) for p in repo.rglob("*")), before)

    def test_eval_report_recall_by_mode(self):
        res = self.kb / "evals/demo/results"
        res.mkdir(parents=True)
        (self.kb / "evals/demo/c1.yaml").write_text(
            "id: c1\npre_fix_commit: abcdef1\nfailure_class: boundary\nscope: {paths: [a.ts]}\nexpected_finding: x\n")

        def result(name, mode, verdict=None, invalid=False):
            v = f"Verdict: `{verdict}` — grader: lead" if verdict else "Verdict: `hit | partial | miss` — grader: —"
            (res / f"{name}.md").write_text(
                f"# Eval result: c1\n\n**Run validity: {'INVALID — leak' if invalid else 'valid'}**\n\n"
                f"| pattern mode | {mode} |\n\n{v}\n")

        result("c1-20261101-100000-r1", "on", "hit")
        result("c1-20261101-100000-r2", "on", "partial")
        result("c1-20261101-110000", "off", "miss")
        result("c1-20261101-120000", "off", invalid=True)
        result("c1-20261101-130000", "on")  # valid, ungraded
        code, out = cli(self.kb, "eval", "report")
        self.assertEqual(code, 0, out)
        self.assertIn("| c1 | on | 3 | 0 | 2 | 1 | 1 | 0 | 0.75 |", out)
        self.assertIn("| c1 | off | 1 | 1 | 1 | 0 | 0 | 1 | 0.00 |", out)
        code, out = cli(self.kb, "eval", "list")
        self.assertIn("runs:4 valid, 1 invalid", out)

    def test_insert_under_heading(self):
        t = "# T\n\n## A\n\n- one\n\n## B\n"
        self.assertEqual(qa.insert_under_heading(t, "A", "- two"), "# T\n\n## A\n\n- one\n- two\n\n## B\n")
        self.assertEqual(qa.insert_under_heading(t, "B", "- x"), "# T\n\n## A\n\n- one\n\n## B\n\n- x\n")
        self.assertEqual(qa.insert_under_heading(t, "C", "- y"), t + "\n## C\n\n- y\n")


if __name__ == "__main__":
    unittest.main(verbosity=1)
