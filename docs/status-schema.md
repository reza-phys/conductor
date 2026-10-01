# `status.json` schema

`status.json` is the contract between the renderer (`src/conductor/render.py`, which rebuilds it from `events.jsonl` and the plans after every event) and the dashboard (`dashboard/template.html`). Only the renderer writes it. Timestamps are ISO-8601 UTC.

```jsonc
{
  "schema": 1,
  "program": "my-project",                         // .conductor/config.json "program", default: the project folder name
  "subtitle": "Conductor run R-20261001-392b7a · plan P1@v1",
  "updated": "2026-10-01T19:32:14.08Z",            // changes on every render; the live page polls on it
  "live": true,                                    // false once the session has ended
  "intent": "…",                                   // plan front-matter `intent` (or `goal`)
  "orchestrator_task": "…",                        // the human's first message of the run (or the plan goal)
  "kill_test": {"state": "open", "text": "…"},     // plan front-matter `kill_test`, or null

  "summary": {
    "phases_done": 1, "phases_total": 2,
    "agents_running": 0, "agents_done": 3, "agents_total": 3,
    "open_human": 0,                               // open decisions + review items
    "unverified": 0,                               // agents let through after max gate blocks
    "claims_verified": 3, "claims_total": 3
  },

  "phases": [{"id": "PH1", "name": "Inspect", "note": "", "state": "done", "tasks": ["T1", "T2"]}],

  "tasks": [{
    "id": "T1", "title": "Inventory functions", "phase": "PH1", "agent_type": "worker",
    "depends_on": [], "criteria": "…",
    "state": "done",                               // derived from the agents bound to the task
    "agents": ["a4718a…"],
    "in_plan": true                                // false for sub-tasks dispatched ad hoc (e.g. T3.1)
  }],

  "agents": [{                                     // flat list ordered by start; the tree comes from `parent`
    "id": "a4718a…", "parent": "main", "depth": 1,
    "type": "conductor:worker", "task": "T1",      // auditors have task "review:T1"
    "description": "…",
    "state": "running",                            // running | done | blocked | failed | unverified | queued
    "started": "…", "ended": null, "duration_s": 41,
    "last_action": "Read src/calc.py", "last_ts": "…",
    "gate": {"blocks": 1, "result": "passed", "reasons": ["…"]},   // result: passed | passed (review skipped) | blocked | escalated | exempt | null
    "report": {"status": "done", "summary": "…", "files": "…", "needs": "none"},
    "files_read": 2, "files_written": ["src/calc.py"],
    "output_path": ".conductor/outputs/a4718a….md",
    "model": "claude-haiku-4-5-20251001"         // as resolved by Claude Code at dispatch
  }],

  "findings": [{                                   // final claims of every task (rendered as "Claims")
    "id": "T1/C2", "text": "mul returns a + b instead of a * b", "task": "T1",
    "state": "verified",                           // verified | refuted | unverified | asserted (review skipped or not required)
    "evidence": "file:src/calc.py:5-6",
    "note": "by analyst·ad9064 · verified by auditor·acb570: …"
  }],

  "decisions": [{                                  // human in the loop
    "id": "N-a4718a", "q": "Which database should I use?", "state": "open",   // open: a blocked report's `needs:`
    "asked_by": "a4718a…", "task": "T2", "ts": "…"
  }, {
    "id": "H1", "q": "…", "state": "decided", "asked_by": "main", "ts": "…",  // decided: AskUserQuestion + answer
    "resolution": "…"
  }],

  "review_queue": [{"task": "T4", "agent": "…", "state": "open", "reason": "gate escalated: …"}],

  "results": [],                                   // optional headline cards {title, value, note, main}; not filled by the renderer yet

  "log": [{"ts": "…", "kind": "dispatch", "agent": "orchestrator", "text": "dispatch → conductor:worker [T1] …"}],  // newest first
  "log_visible": 8,

  "empty_states": {"decisions": "Nothing needs you right now.", "agents": "No agents dispatched yet."},
  "footer": "Generated from .conductor/events.jsonl (45 events)"
}
```

**State vocabulary.** Each state has one chip style: `running blocked queued pending open failed done decided todo unverified verified refuted`. An unknown state renders as raw text on a neutral chip.

**Optional sections.** `findings`, `results`, `review_queue` and `kill_test` are hidden, and left out of the page's contents list, when empty.
