# Worked example: calc-review

A real run (Claude Code 2.1.285, Sonnet orchestrator, plugin loaded with `--plugin-dir`) on a two-function toy project.

- `project/` — the toy project.
- `run/plans/P1-calc-review.md` — the plan the orchestrator executed (3 tasks, 2 phases).
- `run/events.jsonl` — every hook event: dispatches, agent starts/stops, file reads, commands, gate results.
- `run/outputs/` — each agent's final report, verbatim.
- `run/status.html` — the dashboard as it looked at the end (open in a browser).

What it shows:
- T1 and T2 ran in parallel; T3 waited for both and delegated T3.1 to a sub-worker (depth 2).
- T3 twice tried to dispatch without a task envelope; the PreToolUse hook refused with instructions and the third attempt was correct.
- Every agent passed the stop gate (sub-agents finished, well-formed report present). This run predates claim review, so it shows dispatch, nesting and linkage only.

Local paths are replaced with `<project>` / `<tmp>` / `<home>`.

The agents called `reflector` and `researcher` when these runs were recorded are now named `auditor` and `analyst`; the logs use the current names.
