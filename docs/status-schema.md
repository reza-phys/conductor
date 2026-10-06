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

## Schema 2 additions: sessions, tasks per session, project overview

`schema` becomes `2`. Everything above stays (it is the **project overview**: active plan, phases, open items, all agents). New top-level keys:

```jsonc
{
  "schema": 2,
  "mode": "local",                                 // "local" (served or file) | "artifact" (claude.ai: never poll, outputs are not links)
  "plans": [{"id": "P2", "version": "2", "status": "active", "goal": "…", "file": ".conductor/plans/P2-x.md",
             "sessions": ["<session id>", "…"]}],  // every plan, newest first; which sessions dispatched work under it

  "sessions": [{                                   // newest first (at most render.max_sessions)
    "id": "<session id>", "short": "a1b2c3",       // short = first 6 chars
    "run": "R-20261006-a1b2c3",                    // null if Conductor was initialised mid-session
    "started": "…", "ended": "…" | null, "live": true,
    "title": "first human prompt of the session, clipped to 90 chars",
    "counts": {"tasks": 3, "agents_running": 1, "open_human": 0},
    "tasks": [{                                    // dispatched in this session, in dispatch order
      "key": "P2/T3", "id": "T3", "plan": "P2@v2",
      "title": "…", "state": "running",            // same vocabulary as tasks[]
      "parent_task": "P2/T1" | null, "review": "required" | "skip",
      "criteria": "…", "description": "…",
      "prompt": "the dispatch prompt, clipped" | null,     // null in artifact mode unless artifact.include_prompts
      "asked_after": {"ts": "…", "text": "nearest earlier human prompt, clipped"} | null,
      "agents": ["<agent id>", "…"],              // worker(s), their sub-agents and auditors; details in top-level agents[]
      "claims": [/* same shape as findings[] */],
      "gates": [{"ts": "…", "agent": "<id>", "attempt": 1, "reasons": ["…"]}],
      "files_written": ["src/x.py"],
      "log": [/* same shape as log[], oldest first, ≤ render.task_log_limit */]
    }],
    "other": {                                     // work in this session that belongs to no task
      "agents": ["<agent id>"],                    // e.g. Explore helpers, untracked dispatches
      "log": [/* oldest first: orchestrator turns, human messages, main-session tool use */]
    }
  }]
}
```

`agents[]` entries gain `"session": "<session id>"` and `"task_key": "P2/T3"` (`task` keeps the short id for display). `findings[]` entries gain `"key"` (`P2/T3/C1`) and `"session"`.

**Task keys.** Tasks are keyed by plan and id (`P2/T3`) so that `T3` of plan P1 and `T3` of plan P2 never merge. The short id (`T3`, `T3/C1`) is shown wherever it is unambiguous.

## Human-in-the-loop items (schema 2, from v0.3.0)

`review_queue[]` and `decisions[]` hold items of one shape. `review_queue` contains only **open** items: escalated tasks and worker `needs:` questions. `decisions` is the full history: `AskUserQuestion` answers and every decided review or needs item, newest first. `summary.open_human` counts open items only.

```jsonc
{
  "id": "R-P1/T2",                     // R-<task key> (review) · N-<agent short> (needs) · H<n> (AskUserQuestion)
  "kind": "review",                    // review | needs | decision
  "state": "open",                     // open | decided
  "title": "T2 escalated after 3 gate blocks — accept or re-audit?",   // ≤ 90 chars, phrased as a question
  "problem": "The gate stopped retrying T2. Reasons: evidence not observed (5 claims); claims reworded after review (6). Latest audit: 7/7 claims verified.",
  "context": {"task": "P1/T2", "agent": "af7702…", "claims": "7/7 verified", "since": "2026-10-06T16:35:25Z", "session": "<id>"},
  "categories": [{"label": "evidence not observed", "count": 5}, {"label": "claims reworded after review", "count": 6}],
  "options": [
    {"id": "accept", "label": "Accept T2 as done", "consequence": "Marks T2 done; claims keep their audit state.",
     "recommended": true,
     "paste": "[Conductor HITL R-P1/T2] Problem: T2 escalated after 3 gate blocks (latest audit 7/7 verified). Decision: Accept T2 as done."}
  ],
  "suggested": "accept",
  "why_suggested": "Every final claim has a matching 'verified' verdict from an independent auditor.",
  "details": "raw gate reasons / full question and answer, for a collapsed Details block",
  "decision": null,                    // decided items: the chosen option label, or the human's free text
  "note": null,
  "decided_by": null,                  // human | orchestrator | re-audit
  "decided_ts": null,
  "record": null                       // decided items: one line, e.g. "R-P1/T2 · Problem: … · Decided: Accept T2 as done · by human 16:58"
}
```

A human decides an item by pasting an option's `paste` line into the session (a hook records it as a `hitl_decision` event) or by running `conductor decide <id> <option-id> [--note …]`. A review item is also closed automatically, with `decided_by: "re-audit"`, when a later independent audit verifies every final claim of its task.
