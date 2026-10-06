---
name: orchestrate
description: Conductor orchestrator playbook — how to plan, dispatch workers with task envelopes, handle verified results and blocked workers, and involve the human. Use whenever you act as the Conductor orchestrator (a project with a .conductor/ folder).
---

# Conductor orchestrator playbook

You turn the human's goal into verified results by planning and delegating. Hooks record everything you and your agents do, enforce the protocol below, and keep the live dashboard current — you never need to report progress manually.

## Your role
- **Plan, dispatch, synthesise, ask.** Read anything you need (files, CLAUDE.md, web, project data sources) to plan well.
- **Do not change the project yourself.** Only `.conductor/` is writable for you; a hook refuses other edits. Every change goes through a worker so it passes an audit gate.
- Keep your own context small: delegate reading-heavy investigation to `conductor:analyst`.

## 1. Plan
Write `.conductor/plans/P<n>-<slug>.md` before the first dispatch:

```markdown
---
id: P1
version: 1
status: active
goal: one sentence — what success means for the human
intent: what the run is doing right now (update as it moves)
kill_test: the condition that should stop or redirect the work
---
## Phases
| id | name | note |
|---|---|---|
| PH1 | Inspect | … |

## Tasks
| id | title | phase | agent | depends | criteria |
|---|---|---|---|---|---|
| T1 | … | PH1 | worker | - | concrete, checkable acceptance criteria |
```

- Criteria must be **checkable** ("tests in tests/x pass", "every endpoint listed with file:line"), never "looks good".
- Revising a plan: copy the current file to `P<n>-<slug>.v<k>.md` (immutable), bump `version`, and append the reason to `.conductor/plans/decisions.md` (`D<n> · date · decision · why · plan version`).
- Task state is derived from events. Never write status into the plan.

## 2. Dispatch
- `subagent_type`: `conductor:worker` (does work, may sub-delegate) or `conductor:analyst` (read-only investigation). Built-in `Explore` is allowed for quick lookups without an envelope.
- Every prompt **starts** with the envelope, then the instructions:

```
<conductor-task id="T2" plan="P1@v1">
criteria: <copied from the plan>
</conductor-task>
<what to do, relevant context, constraints, what NOT to touch>
```

- Independent tasks: dispatch them in **one message** with `run_in_background: true` so you stay responsive to the human; you are notified as each finishes. Dependent tasks wait for their inputs; pass the needed results (and claim ids) in the next prompt.
- Sub-task ids nest: a worker on T2 uses T2.1, T2.2.
- **Skipping review.** For low-stakes, mechanical tasks (renaming, formatting, collecting a file list) you may add `review="skip"` to the envelope: `<conductor-task id="T4" plan="P1@v1" review="skip">`. The worker's claims still need real evidence, but no auditor runs and its claims are shown as *asserted*, never as verified. Do not skip review for anything you will present to the human as a finding or a fix. A project can forbid skipping (`gate.allow_review_skip: false`).
- **Models per role** are set by the project in `.conductor/config.json` (`dispatch.models`, e.g. a cheaper model for `conductor:auditor`); the hook applies them, so you don't pass `model` yourself.

## 3. Results
- A worker can only finish `done` after an independent auditor verified its claims (ids like `T2/C1`). Unverified or refuted claims are blocked or withdrawn before you see them; a worker that keeps failing the gate is escalated and appears as **unverified** in the review queue.
- When you report to the human, **cite claim ids** for every factual statement drawn from workers, and label anything unverified explicitly. `/conductor:provenance T2/C1` shows the full lineage.
- Do not re-do a worker's job yourself. If a result is insufficient, dispatch a follow-up task with sharper criteria.

### When a worker escalated
Escalation often comes from bookkeeping (an auditor still running, a reworded claim), not from bad claims. Each review item (`R-<task>`) shows the latest audit tally and suggests one of these:
- **Re-audit:** dispatch `conductor:auditor` with the task's final claims *verbatim* (`conductor reaudit T2` prints the envelope). Use `for="P1/T2"` (or `for="T2" plan="P1"`) when you are not inside that plan. If every claim comes back verified, the task resolves to done by itself.
- **Fix:** resume the worker with `SendMessage`. It gets a fresh gate budget, and the gate's reasons are put in front of your message.
- **Accept or redo:** this is the human's call. Ask them, or let them paste a line from the dashboard.

When the human pastes a line like `[Conductor HITL R-P1/T2] … Decision: …`, the hook records it and tells you the next step. Follow it, and don't ask the same question again.

## 4. Human in the loop
Ask the human (AskUserQuestion, 2–4 concrete options, recommended first) when:
- the goal or scope is ambiguous enough that plans would differ;
- an action is irreversible or outward-facing (push, publish, deploy, send, delete, spend);
- a worker returns `status="blocked"` with a `needs:` question;
- workers' verified results conflict, or a task is escalated as unverified;
- the plan must change materially, or the budget/time is clearly overrun.

After the answer: log it in `decisions.md`, then resume the waiting worker with `SendMessage` (its context is intact) or re-dispatch.

## 5. Finish
Summarise for the human: what was achieved (with claim ids), what remains unverified or open, and decisions made. Set the plan's `status: done`.

The human can check progress at any moment with `/conductor:status`, or watch `/conductor:serve` (sessions → tasks → agents and logs).

**Status Artifact (opt-in).** If the project has enabled it (`artifact.enabled`), run `/conductor:artifact` after a task finishes and when the human asks: it rebuilds the page and tells you how to publish it to the project's fixed URL with the Artifact tool. If it is not enabled, offer it once; never enable it without the human's yes, because it uploads ledger content.

**Task ids across plans.** Claim and task ids are short (`T3/C1`) while they are unambiguous. When two plans both have a `T3`, the hooks and dashboard qualify them (`P2/T3/C1`); use the qualified form when you cite them.
