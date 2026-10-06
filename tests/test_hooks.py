"""End-to-end hook tests: feed recorded-shape payloads through hook.py in a temp project.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "src" / "conductor" / "hook.py"
CLI = ROOT / "src" / "conductor" / "cli.py"
sys.path.insert(0, str(ROOT / "src"))

from conductor import core, render  # noqa: E402

SID = "sess-0001"
ENV = '<conductor-task id="{id}" plan="P1@v1">\ncriteria: {c}\n</conductor-task>\n'
REPORT = '\n<conductor-report task="{t}" status="{s}">\nsummary: did it\nfiles: none\nneeds: {n}\n</conductor-report>'


class HookHarness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run([sys.executable, str(CLI), "init", str(self.root)], check=True, capture_output=True)
        cfg = json.loads((self.root / ".conductor/config.json").read_text())
        cfg["render"] = {"enabled": False}
        (self.root / ".conductor/config.json").write_text(json.dumps(cfg))
        (self.root / "src").mkdir()
        (self.root / "src/app.py").write_text("x = 1\n")

    def set_gate(self, **kw):
        p = self.root / ".conductor/config.json"
        cfg = json.loads(p.read_text())
        cfg.setdefault("gate", {}).update(kw)
        p.write_text(json.dumps(cfg))

    def tearDown(self):
        self.tmp.cleanup()

    def hook(self, event: str, agent: str | None = None, agent_type: str | None = None, **kw) -> dict | None:
        payload = {"session_id": SID, "hook_event_name": event, "cwd": str(self.root),
                   "transcript_path": str(self.root / f"{SID}.jsonl"), **kw}
        if agent:
            payload.update(agent_id=agent, agent_type=agent_type)
        r = subprocess.run([sys.executable, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True,
                           env={**os.environ, "CLAUDE_PROJECT_DIR": str(self.root)})
        self.assertEqual(r.returncode, 0, r.stderr)
        errs = self.root / ".conductor/state/hook-errors.log"
        self.assertFalse(errs.exists() and errs.read_text().strip(), errs.read_text() if errs.exists() else "")
        return json.loads(r.stdout) if r.stdout.strip() else None

    def dispatch(self, tuid, prompt, stype="conductor:worker", agent=None, agent_type=None, **ti):  # noqa: D401
        return self.hook("PreToolUse", agent, agent_type, tool_name="Agent", tool_use_id=tuid,
                         tool_input={"prompt": prompt, "subagent_type": stype, "description": f"d-{tuid}", **ti})

    def stop(self, agent, atype, message, bg=None):
        return self.hook("SubagentStop", agent, atype, last_assistant_message=message,
                         background_tasks=bg or [], stop_hook_active=False)

    def status(self):
        return render.build_status(self.root / ".conductor")

    # --------------------------------------------------------------------------------
    def test_full_nested_flow(self):
        self.set_gate(require_claims=False, check_evidence=False, require_verdicts=False)
        self.hook("SessionStart", source="startup")
        self.hook("UserPromptSubmit", prompt="Build the thing")

        # no envelope -> refused
        out = self.dispatch("tu0", "just do it")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        # exempt built-in helper -> allowed without envelope
        self.assertIsNone(self.dispatch("tuX", "look around", stype="Explore"))

        self.assertIsNone(self.dispatch("tu1", ENV.format(id="T1", c="tests pass")))
        self.hook("SubagentStart", "A1", "conductor:worker")          # FIFO-bound to tu1
        self.hook("PostToolUse", "A1", "conductor:worker", tool_name="Read",
                  tool_input={"file_path": str(self.root / "src/app.py")}, tool_response={})

        # nested dispatch is forced to the foreground
        out = self.dispatch("tu2", ENV.format(id="T2", c="sub"), agent="A1", agent_type="conductor:worker")
        self.assertIs(out["hookSpecificOutput"]["updatedInput"]["run_in_background"], False)
        self.hook("SubagentStart", "A2", "conductor:worker")

        # depth 3 worker refused, auditor allowed
        out = self.dispatch("tu3", ENV.format(id="T3", c="x"), agent="A2", agent_type="conductor:worker")
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        out = self.dispatch("tu4", '<conductor-review for="T2">\nclaims:\n- C1: x | evidence: file:a\n</conductor-review>', stype="conductor:auditor",
                            agent="A2", agent_type="conductor:worker")
        self.assertNotEqual((out or {}).get("hookSpecificOutput", {}).get("permissionDecision"), "deny")

        # gate: A1 cannot finish while A2 runs; A2 cannot finish without a report
        out = self.stop("A1", "conductor:worker", "done" + REPORT.format(t="T1", s="done", n="none"),
                        bg=[{"id": "A2", "status": "running"}])
        self.assertEqual(out["decision"], "block")
        self.assertIn("still running", out["reason"])
        out = self.stop("A2", "conductor:worker", "I did it")
        self.assertEqual(out["decision"], "block")
        self.assertIn("report block", out["reason"])
        self.assertIsNone(self.stop("A2", "conductor:worker", "ok" + REPORT.format(t="T2", s="done", n="none")))
        self.hook("PostToolUse", "A1", "conductor:worker", tool_name="Agent", tool_use_id="tu2",
                  tool_input={"subagent_type": "conductor:worker"}, tool_response={"agentId": "A2", "status": "completed"})
        self.assertIsNone(self.stop("A1", "conductor:worker", "ok" + REPORT.format(t="T1", s="done", n="none")))
        self.hook("PostToolUse", tool_name="Agent", tool_use_id="tu1", tool_input={"subagent_type": "conductor:worker"},
                  tool_response={"agentId": "A1", "status": "completed"})

        st = self.status()
        ag = {a["id"]: a for a in st["agents"]}
        self.assertEqual(ag["A1"]["parent"], "main")
        self.assertEqual(ag["A2"]["parent"], "A1")
        self.assertEqual(ag["A2"]["depth"], 2)
        self.assertEqual(ag["A1"]["state"], "done")
        self.assertEqual(ag["A1"]["gate"]["blocks"], 1)
        self.assertEqual(ag["A1"]["gate"]["result"], "passed")
        self.assertEqual(ag["A1"]["files_read"], 1)
        self.assertEqual({t["id"]: t["state"] for t in st["tasks"]}, {"T1": "done", "T2": "done"})
        self.assertEqual(st["orchestrator_task"], "Build the thing")
        self.assertTrue((self.root / ".conductor/outputs/A1.md").exists())

    def test_orchestrator_write_scope(self):
        out = self.hook("PreToolUse", tool_name="Edit", tool_input={"file_path": str(self.root / "src/app.py")})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIsNone(self.hook("PreToolUse", tool_name="Write",
                                    tool_input={"file_path": str(self.root / ".conductor/plans/P1.md")}))
        out = self.hook("PreToolUse", "R9", "conductor:auditor", tool_name="Write", tool_input={"file_path": str(self.root / "src/app.py")})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        # sub-agents may edit the project
        self.assertIsNone(self.hook("PreToolUse", "A9", "conductor:worker", tool_name="Edit",
                                    tool_input={"file_path": str(self.root / "src/app.py")}))

    def test_audit_cycle_and_anti_fabrication(self):
        w = "conductor:worker"
        self.dispatch("tu1", ENV.format(id="T1", c="app works"))
        self.hook("SubagentStart", "A1", w)
        self.hook("PostToolUse", "A1", w, tool_name="Read", tool_input={"file_path": str(self.root / "src/app.py")}, tool_response={})
        self.hook("PostToolUse", "A1", w, tool_name="Bash", tool_input={"command": "python3 -m pytest -q"}, tool_response={})
        claims = ("claims:\n- C1: app.py sets x to 1 | evidence: file:src/app.py:1\n"
                  "- C2: the tests pass | evidence: cmd:python3 -m pytest -q\n")
        done = lambda cl: '<conductor-report task="T1" status="done">\nsummary: s\n' + cl + 'needs: none\n</conductor-report>'

        # done without any review -> blocked, with instructions to call an auditor
        out = self.stop("A1", w, done(claims))
        self.assertIn("not been reviewed", out["reason"])
        self.assertIn("conductor:auditor", out["reason"])

        # review with no claims is refused at dispatch
        out = self.dispatch("tuR0", '<conductor-review for="T1">look</conductor-review>', stype="conductor:auditor",
                            agent="A1", agent_type=w)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        out = self.dispatch("tuR", f'<conductor-review for="T1">\ncriteria: app works\n{claims}</conductor-review>',
                            stype="conductor:auditor", agent="A1", agent_type=w)
        self.assertIs(out["hookSpecificOutput"]["updatedInput"]["run_in_background"], False)  # reviews run in the foreground
        self.hook("SubagentStart", "R1", "conductor:auditor")
        rep = lambda v: '<conductor-report task="review:T1" status="done">\nsummary: s\nverdicts:\n' + v + 'needs: none\n</conductor-report>'
        # auditor: missing verdict, then rubber-stamping without looking -> blocked
        self.assertIn("No verdict for C2", self.stop("R1", "conductor:auditor", rep("- C1: verified | ok\n"))["reason"])
        out = self.stop("R1", "conductor:auditor", rep("- C1: verified | ok\n- C2: verified | ok\n"))
        self.assertIn("without reading", out["reason"])
        self.hook("PostToolUse", "R1", "conductor:auditor", tool_name="Read",
                  tool_input={"file_path": str(self.root / "src/app.py")}, tool_response={})
        self.assertIsNone(self.stop("R1", "conductor:auditor", rep("- C1: verified | x = 1 on line 1\n- C2: refuted | 2 failures\n")))

        # worker: refuted claim blocks; withdrawing it passes; C1 is verified on the dashboard
        self.assertIn("C2 was refuted", self.stop("A1", w, done(claims))["reason"])
        only_c1 = "claims:\n- C1: app.py sets x to 1 | evidence: file:src/app.py:1\n"
        self.assertIsNone(self.stop("A1", w, done(only_c1)))
        st = self.status()
        self.assertEqual([(f["id"], f["state"]) for f in st["findings"]], [("T1/C1", "verified")])
        r = subprocess.run([sys.executable, str(CLI), "provenance", "T1/C1"], cwd=self.root, capture_output=True, text=True,
                           env={**os.environ, "CLAUDE_PROJECT_DIR": str(self.root)})
        self.assertIn("file:src/app.py:1 — read by worker·A1", r.stdout)
        self.assertIn("verified by auditor·R1", r.stdout)
        self.assertIn("worker·A1 → orchestrator", r.stdout)

    def test_fabricated_evidence_is_caught(self):
        self.dispatch("tu1", ENV.format(id="T1", c="c"))
        self.hook("SubagentStart", "A1", "conductor:worker")
        out = self.stop("A1", "conductor:worker", '<conductor-report task="T1" status="done">\nsummary: s\nclaims:\n'
                        '- C1: tests pass | evidence: cmd:python3 -m src.test_calc\n- C2: two functions | evidence: file:src/app.py\n'
                        '- C3: it is fine | evidence: I checked\n</conductor-report>')
        self.assertIn("C1 cites cmd:python3 -m src.test_calc but you never ran it.", out["reason"])
        self.assertIn("C2 cites file:src/app.py but you never read or wrote it", out["reason"])
        self.assertIn("C3 has no checkable evidence", out["reason"])

    def test_claim_changed_after_review(self):
        self.set_gate(check_evidence=False)
        self.dispatch("tu1", ENV.format(id="T1", c="c"))
        self.hook("SubagentStart", "A1", "conductor:worker")
        self.dispatch("tuR", '<conductor-review for="T1">\nclaims:\n- C1: x is 1 | evidence: file:src/app.py\n</conductor-review>',
                      stype="conductor:auditor", agent="A1", agent_type="conductor:worker")
        self.hook("SubagentStart", "R1", "conductor:auditor")
        self.hook("PostToolUse", "R1", "conductor:auditor", tool_name="Read", tool_input={"file_path": str(self.root / "src/app.py")}, tool_response={})
        self.stop("R1", "conductor:auditor", '<conductor-report task="review:T1" status="done">\nverdicts:\n- C1: verified | yes\n</conductor-report>')
        out = self.stop("A1", "conductor:worker", '<conductor-report task="T1" status="done">\nclaims:\n- C1: x is 2 | evidence: file:src/app.py\n</conductor-report>')
        self.assertIn("C1 changed after its review", out["reason"])

    def test_commit_trailers_are_stamped_and_accepted_by_git(self):
        self.hook("SessionStart", source="startup")
        self.dispatch("tu1", ENV.format(id="T1", c="c"))
        self.hook("SubagentStart", "A1", "conductor:worker")
        out = self.hook("PreToolUse", "A1", "conductor:worker", tool_name="Bash",
                        tool_input={"command": 'git add -A && git commit -q -m "feat: x"'})
        cmd = out["hookSpecificOutput"]["updatedInput"]["command"]
        self.assertIn("Conductor-Task: T1", cmd)
        g = lambda *a: subprocess.run(["git", *a], cwd=self.root, capture_output=True, text=True, check=True)
        g("init", "-q"); g("config", "user.email", "t@t"); g("config", "user.name", "t")
        subprocess.run(cmd, shell=True, cwd=self.root, check=True)
        msg = g("log", "-1", "--format=%B").stdout
        self.assertIn("Conductor-Agent: A1", msg)
        self.assertIn("Conductor-Plan: P1@v1", msg)
        self.assertIsNone(self.hook("PreToolUse", "A1", "conductor:worker", tool_name="Bash", tool_input={"command": "git status"}))
        out = self.hook("PreToolUse", "A1", "conductor:worker", tool_name="Bash",
                        tool_input={"command": 'git -c user.name="A B" -c user.email=a@b --no-pager commit -m x'})
        self.assertRegex(out["hookSpecificOutput"]["updatedInput"]["command"],
                         r'^git -c user.name="A B" -c user.email=a@b --no-pager commit --trailer')

    def test_plan_validation_feedback_and_aliases(self):
        bad = self.root / ".conductor/plans/P1-x.md"
        bad.write_text("---\nid: P1\nversion: 1\nstatus: active\ngoal: g\n---\n## Phases\n| Phase | Description |\n|---|---|\n| 1 | Audit |\n"
                       "## Tasks\n| ID | Phase | Description | Status |\n|---|---|---|---|\n| T1 | 1 | Read it | pending |\n")
        out = self.hook("PostToolUse", tool_name="Write", tool_input={"file_path": str(bad)}, tool_response={})
        self.assertEqual(out["decision"], "block")
        self.assertIn("criteria", out["reason"])
        self.assertIn("remove the status column", out["reason"])
        st = self.status()  # tolerant parser still shows the plan
        self.assertEqual([p["name"] for p in st["phases"]], ["Audit"])
        self.assertEqual(st["tasks"][0]["title"], "Read it")

    def set_cfg(self, section, **kw):
        p = self.root / ".conductor/config.json"
        cfg = json.loads(p.read_text())
        cfg.setdefault(section, {}).update(kw)
        p.write_text(json.dumps(cfg))

    def test_cache_matches_full_replay_and_survives_truncation(self):
        from conductor import cache
        from conductor.tree import Tree
        self.test_full_nested_flow()  # leaves a realistic log and a cache behind
        state = self.root / ".conductor"
        self.assertTrue((state / "state/tree.cache").exists())
        cached, offset = cache.load(state)
        full = Tree(core.read_events(state))
        strip = lambda ags: [{k: v for k, v in a.items() if k != "duration_s"} for a in ags]  # noqa: E731
        self.assertEqual(strip(cached.agent_list()), strip(full.agent_list()))
        self.assertEqual(offset, (state / "events.jsonl").stat().st_size)
        # replace the log with a shorter, different one: the cache must notice and rebuild
        (state / "events.jsonl").write_text(json.dumps({"ts": "x", "event": "run_start", "session": "z"}) + "\n")
        rebuilt, _ = cache.load(state)
        self.assertEqual(rebuilt.agents, {})

    def test_mcp_calls_count_as_evidence(self):
        self.set_gate(require_verdicts=False)
        self.dispatch("tu1", ENV.format(id="T1", c="c"))
        self.hook("SubagentStart", "A1", "conductor:worker")
        self.hook("PostToolUse", "A1", "conductor:worker", tool_name="mcp__labdb__query",
                  tool_input={"sql": "select count(*) from runs"}, tool_response={"rows": [[42]]})
        report = lambda ev: ('<conductor-report task="T1" status="done">\nclaims:\n'  # noqa: E731
                             f'- C1: there are 42 runs | evidence: {ev}\n</conductor-report>')
        out = self.stop("A1", "conductor:worker", report("mcp:otherdb__query"))
        self.assertIn("C1 cites mcp:otherdb__query but you never called it.", out["reason"])
        self.assertIsNone(self.stop("A1", "conductor:worker", report("mcp:labdb__query")))
        st = self.status()
        self.assertEqual(st["findings"][0]["evidence"], "mcp:labdb__query")

    def test_review_skip_per_task(self):
        env = '<conductor-task id="T1" plan="P1@v1" review="skip">\ncriteria: c\n</conductor-task>\n'
        self.dispatch("tu1", env)
        self.hook("SubagentStart", "A1", "conductor:worker")
        self.hook("PostToolUse", "A1", "conductor:worker", tool_name="Read",
                  tool_input={"file_path": str(self.root / "src/app.py")}, tool_response={})
        done = ('<conductor-report task="T1" status="done">\nclaims:\n'
                '- C1: x is 1 | evidence: file:src/app.py\n</conductor-report>')
        self.assertIsNone(self.stop("A1", "conductor:worker", done))  # no auditor needed
        st = self.status()
        self.assertEqual(st["agents"][0]["gate"]["result"], "passed (review skipped)")
        self.assertEqual(st["findings"][0]["state"], "asserted")  # never shown as verified
        # evidence is still checked when review is skipped
        self.dispatch("tu2", env.replace("T1", "T2"))
        self.hook("SubagentStart", "A2", "conductor:worker")
        out = self.stop("A2", "conductor:worker", done.replace("T1", "T2"))
        self.assertIn("never read or wrote", out["reason"])
        # a project can forbid skipping
        self.set_gate(allow_review_skip=False)
        out = self.dispatch("tu3", env.replace("T1", "T3"))
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_model_per_agent_type(self):
        self.set_cfg("dispatch", models={"conductor:auditor": "haiku", "conductor:worker": "opus"})
        out = self.dispatch("tu1", ENV.format(id="T1", c="c"))
        self.assertEqual(out["hookSpecificOutput"]["updatedInput"]["model"], "opus")
        self.hook("SubagentStart", "A1", "conductor:worker")
        out = self.dispatch("tuR", '<conductor-review for="T1">\nclaims:\n- C1: x | evidence: file:a\n</conductor-review>',
                            stype="conductor:auditor", agent="A1", agent_type="conductor:worker")
        upd = out["hookSpecificOutput"]["updatedInput"]
        self.assertEqual((upd["model"], upd["run_in_background"]), ("haiku", False))

    # --- issue #1 ------------------------------------------------------------------------------------------------
    def _report(self, task="T1", ev="file:src/app.py"):
        return (f'<conductor-report task="{task}" status="done">\nsummary: s\nclaims:\n'
                f'- C1: x is 1 | evidence: {ev}\nfiles: none\nneeds: none\n</conductor-report>')

    def test_f1_gate_runs_at_handback_and_its_refusal_reaches_the_agent(self):
        self.dispatch("tu1", ENV.format(id="T1", c="c"))
        self.hook("SubagentStart", "A1", "conductor:worker")
        # hand-back without evidence ever being read -> refused while the agent can still act on it
        out = self.hook("PreToolUse", "A1", "conductor:worker", tool_name="SubagentHandback",
                        tool_input={"message": self._report()})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("never read or wrote", out["hookSpecificOutput"]["permissionDecisionReason"])
        # the agent fixes it (reads the file, gets the review skipped for this test) and hands back again
        self.set_gate(require_verdicts=False)
        self.hook("PostToolUse", "A1", "conductor:worker", tool_name="Read",
                  tool_input={"file_path": str(self.root / "src/app.py")}, tool_response={})
        self.assertIsNone(self.hook("PreToolUse", "A1", "conductor:worker", tool_name="SubagentHandback",
                                    tool_input={"message": self._report()}))
        self.hook("PostToolUse", "A1", "conductor:worker", tool_name="SubagentHandback",
                  tool_input={"message": self._report()}, tool_response={})
        # SubagentStop arrives with an empty last message: the logged hand-back is the report
        self.assertIsNone(self.stop("A1", "conductor:worker", ""))
        a = self.status()["agents"][0]
        self.assertEqual((a["state"], a["gate"]["result"]), ("done", "passed"))

    def test_f1_f2_report_read_from_transcript_and_never_blocked_after_handback(self):
        """The reporter's minirepro: no PreToolUse ran (older plugin), transcript holds the hand-back."""
        self.dispatch("tu1", ENV.format(id="T1", c="c"))
        self.hook("SubagentStart", "A1", "conductor:worker")
        sub = self.root / SID / "subagents"
        sub.mkdir(parents=True)
        (sub / "agent-A1.jsonl").write_text(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "SubagentHandback", "input": {"message": self._report(ev="file:a")}}]}}) + "\n")
        self.assertIsNone(self.stop("A1", "conductor:worker", ""))  # a block here would never be delivered
        a = self.status()["agents"][0]
        self.assertEqual((a["state"], a["gate"]["result"]), ("unverified", "escalated"))
        self.assertNotIn("Missing or malformed report", " ".join(a["gate"]["reasons"]))

    def test_f2_completed_link_closes_a_child_with_no_stop(self):
        self.dispatch("tu1", ENV.format(id="T1", c="c"))
        self.hook("SubagentStart", "A1", "conductor:worker")
        self.hook("PostToolUse", tool_name="Agent", tool_use_id="tu1", tool_input={"subagent_type": "conductor:worker"},
                  tool_response={"agentId": "A1", "status": "completed"})
        a = self.status()["agents"][0]
        self.assertEqual((a["state"], a["gate"]["result"]), ("unverified", "no stop recorded"))

    def test_f4_parallel_background_dispatches_keep_their_tasks(self):
        for tu, t in (("tu1", "T1"), ("tu2", "T2")):
            self.dispatch(tu, ENV.format(id=t, c="c"), run_in_background=True)
        self.hook("PostToolUse", tool_name="Agent", tool_use_id="tu2", tool_input={"subagent_type": "conductor:worker"},
                  tool_response={"agentId": "A1", "status": "async_launched"})
        self.hook("SubagentStart", "A1", "conductor:worker")
        self.hook("SubagentStart", "A2", "conductor:worker")
        self.hook("PostToolUse", tool_name="Agent", tool_use_id="tu1", tool_input={"subagent_type": "conductor:worker"},
                  tool_response={"agentId": "A2", "status": "async_launched"})
        ag = {a["id"]: a["task"] for a in self.status()["agents"]}
        self.assertEqual(ag, {"A1": "T2", "A2": "T1"})

    def test_f5_internal_helpers_leave_no_trace(self):
        self.hook("SubagentStop", "H1", "", last_assistant_message="yes, commit and push", background_tasks=[])
        self.hook("PostToolUse", "H1", "", tool_name="Read", tool_input={"file_path": str(self.root / "src/app.py")}, tool_response={})
        self.assertEqual(self.status()["agents"], [])
        self.assertFalse((self.root / ".conductor/outputs/H1.md").exists())

    def test_f6_late_init_starts_the_run_and_briefs_once(self):
        out = self.hook("UserPromptSubmit", prompt="hello")
        self.assertIn("You are the orchestrator", out["hookSpecificOutput"]["additionalContext"])
        self.assertIsNone(self.hook("UserPromptSubmit", prompt="again"))
        st = self.status()
        self.assertTrue(st["sessions"][0]["run"])
        # hand-backs and task notifications are not the human
        self.hook("UserPromptSubmit", prompt='<agent-message from="a1">done</agent-message>')
        self.assertEqual([m for m in st["sessions"][0]["other"]["log"] if "agent-message" in m["text"]], [])

    def test_bash_guard_for_the_orchestrator(self):
        deny = lambda cmd: (self.hook("PreToolUse", tool_name="Bash", tool_input={"command": cmd}) or {}) \
            .get("hookSpecificOutput", {}).get("permissionDecision")  # noqa: E731
        for cmd in ["cat > notes.md <<'X'\nhi\nX", "echo hi >> src/app.py", "sed -i '' 's/1/2/' src/app.py",
                    "printf x | tee src/out.txt", "cp /etc/hosts src/", "rm src/app.py"]:
            self.assertEqual(deny(cmd), "deny", cmd)
        for cmd in ["ls -la", "cat src/app.py", "python3 -m pytest -q 2>&1 | tail", "echo hi > /dev/null",
                    "echo plan > .conductor/plans/notes.md", "grep -rn x src > /tmp/out.txt", "git status"]:
            self.assertIsNone(deny(cmd), cmd)
        self.assertIsNone(self.hook("PreToolUse", "A9", "conductor:worker", tool_name="Bash",
                                    tool_input={"command": "echo hi > src/app.py"}))  # workers may write

    def test_partial_reaudit_and_bash_reads_and_line_lists(self):
        w = "conductor:worker"
        self.dispatch("tu1", ENV.format(id="T1", c="c"))
        self.hook("SubagentStart", "A1", w)
        self.hook("PostToolUse", "A1", w, tool_name="Bash", tool_input={"command": "sed -n '1,5p' src/app.py"}, tool_response={})
        claims = "claims:\n- C1: a | evidence: file:src/app.py:1,3\n- C2: b | evidence: file:src/app.py:2-4\n"
        self.dispatch("tuR1", f'<conductor-review for="T1">\n{claims}</conductor-review>', stype="conductor:auditor", agent="A1", agent_type=w)
        self.hook("SubagentStart", "R1", "conductor:auditor")
        self.hook("PostToolUse", "R1", "conductor:auditor", tool_name="Read", tool_input={"file_path": str(self.root / "src/app.py")}, tool_response={})
        self.assertIsNone(self.stop("R1", "conductor:auditor", '<conductor-report task="review:T1" status="done">\nverdicts:\n'
                                    '- C1: verified | ok\n- C2: refuted | no\n</conductor-report>'))
        # re-audit of only the changed claim C2: the second auditor answers for C2 alone
        claims2 = "claims:\n- C2: b, corrected | evidence: file:src/app.py:2-4\n"
        self.dispatch("tuR2", f'<conductor-review for="T1">\n{claims2}</conductor-review>', stype="conductor:auditor", agent="A1", agent_type=w)
        self.hook("SubagentStart", "R2", "conductor:auditor")
        self.hook("PostToolUse", "R2", "conductor:auditor", tool_name="Read", tool_input={"file_path": str(self.root / "src/app.py")}, tool_response={})
        self.assertIsNone(self.stop("R2", "conductor:auditor", '<conductor-report task="review:T1" status="done">\nverdicts:\n'
                                    '- C2: verified | ok\n</conductor-report>'))
        final = ('<conductor-report task="T1" status="done">\nclaims:\n- C1: `a` | evidence: file:src/app.py:1,3\n'
                 '- C2: b, corrected. | evidence: file:src/app.py:2-4\n</conductor-report>')
        self.assertIsNone(self.stop("A1", w, final))  # Bash read counts; markdown/full-stop wording differences ignored
        self.assertEqual(sorted(f["state"] for f in self.status()["findings"]), ["verified", "verified"])

    def test_plan_qualified_keys_and_sessions_view(self):
        for tu, plan in (("tu1", "P1@v1"), ("tu2", "P2@v1")):
            self.dispatch(tu, f'<conductor-task id="T1" plan="{plan}">\ncriteria: c\n</conductor-task>\nwork')
        self.hook("SubagentStart", "A1", "conductor:worker")
        self.hook("SubagentStart", "A2", "conductor:worker")
        for tu, a in (("tu1", "A1"), ("tu2", "A2")):
            self.hook("PostToolUse", tool_name="Agent", tool_use_id=tu, tool_input={"subagent_type": "conductor:worker"},
                      tool_response={"agentId": a, "status": "completed"})
        st = self.status()
        self.assertEqual({a["id"]: a["task_key"] for a in st["agents"]}, {"A1": "P1/T1", "A2": "P2/T1"})
        sess = st["sessions"][0]
        self.assertEqual([t["key"] for t in sess["tasks"]], ["P1/T1", "P2/T1"])
        self.assertEqual([t["id"] for t in sess["tasks"]], ["P1/T1", "P2/T1"])  # ambiguous short id -> full key
        self.assertIn("work", sess["tasks"][0]["prompt"])

    def test_artifact_build_is_redacted_and_never_polls(self):
        from conductor import render as r
        self.hook("UserPromptSubmit", prompt="secret plan for the paper")
        self.dispatch("tu1", ENV.format(id="T1", c="c") + "private instructions")
        self.hook("SubagentStart", "A1", "conductor:worker")
        st = r.build_artifact(self.root / ".conductor")
        blob = json.dumps(st)
        self.assertEqual(st["mode"], "artifact")
        self.assertNotIn("secret plan", blob)
        self.assertNotIn("private instructions", blob)

    def test_gate_escalates_after_max_blocks(self):
        self.dispatch("tu1", ENV.format(id="T1", c="c"))
        self.hook("SubagentStart", "A1", "conductor:worker")
        for _ in range(3):
            self.assertEqual(self.stop("A1", "conductor:worker", "no report")["decision"], "block")
        self.assertIsNone(self.stop("A1", "conductor:worker", "still no report"))
        st = self.status()
        self.assertEqual(st["agents"][0]["state"], "unverified")
        self.assertEqual(len(st["review_queue"]), 1)
        self.assertEqual(st["summary"]["unverified"], 1)

    def test_blocked_report_opens_decision(self):
        self.dispatch("tu1", ENV.format(id="T1", c="c"))
        self.hook("SubagentStart", "A1", "conductor:worker")
        self.stop("A1", "conductor:worker", REPORT.format(t="T1", s="blocked", n="Which DB should I use?"))
        st = self.status()
        self.assertEqual(st["decisions"][0]["state"], "open")
        self.assertIn("Which DB", st["decisions"][0]["q"])

    def test_uninitialised_project_is_silent(self):
        with tempfile.TemporaryDirectory() as d:
            r = subprocess.run([sys.executable, str(HOOK)], capture_output=True, text=True,
                               input=json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Edit", "cwd": d}),
                               env={**os.environ, "CLAUDE_PROJECT_DIR": d})
            self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_plan_tasks_and_phases(self):
        (self.root / ".conductor/plans/P1-demo.md").write_text(
            "---\nid: P1\nversion: 1\nstatus: active\ngoal: Demo\nintent: Prove it works\n---\n"
            "## Phases\n| id | name | note |\n|---|---|---|\n| PH1 | Build | |\n| PH2 | Ship | |\n"
            "## Tasks\n| id | title | phase | agent | depends | criteria |\n|---|---|---|---|---|---|\n"
            "| T1 | Core | PH1 | worker | - | tests |\n| T2 | Docs | PH2 | worker | T1 | readable |\n")
        self.dispatch("tu1", ENV.format(id="T1", c="c"))
        self.hook("SubagentStart", "A1", "conductor:worker")
        st = self.status()
        self.assertEqual(st["intent"], "Prove it works")
        self.assertEqual([(p["id"], p["state"]) for p in st["phases"]], [("PH1", "running"), ("PH2", "todo")])
        self.assertEqual({t["id"]: t["state"] for t in st["tasks"]}, {"T1": "running", "T2": "todo"})


class EventLogTest(unittest.TestCase):
    def test_torn_line_is_skipped(self):
        with tempfile.TemporaryDirectory() as d:
            s = Path(d)
            core.append_event(s, {"event": "a"})
            with (s / "events.jsonl").open("a") as f:
                f.write('{"event": "tor')
            self.assertEqual([e["event"] for e in core.read_events(s)], ["a"])


if __name__ == "__main__":
    unittest.main()


class ProtocolTest(unittest.TestCase):
    def test_evidence_keeps_semicolons_inside_commands(self):
        from conductor import protocol
        ev = protocol.parse_evidence('cmd:python3 -c "import calc; print(calc.mul(2,3))"; file:src/calc.py:5,6')
        self.assertEqual([(e["kind"], e["ref"]) for e in ev],
                         [("cmd", 'python3 -c "import calc; print(calc.mul(2,3))"'), ("file", "src/calc.py")])
        self.assertEqual(ev[1]["lines"], "5,6")

    def test_envelope_uses_first_block_report_uses_last(self):
        from conductor import protocol
        p = '<conductor-task id="T1" plan="P1@v1">\ncriteria: a\n</conductor-task>\nthen send <conductor-task id="T1.1">x</conductor-task>'
        self.assertEqual(protocol.parse_task(p)["id"], "T1")
        m = '<conductor-report task="T1" status="blocked">x</conductor-report> fixed: <conductor-report task="T1" status="done">y</conductor-report>'
        self.assertEqual(protocol.parse_report(m)["status"], "done")
