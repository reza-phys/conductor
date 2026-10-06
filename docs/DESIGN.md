# Conductor: design

Conductor is a multi-agent orchestration harness for Claude Code, packaged as a plugin. Work flows down, only verified and traceable claims flow up, and hooks record everything.

![architecture](architecture.svg)

## Principles

1. **Deterministic where it matters, model where it helps.** Anything that must *always* happen is done by hooks and scripts, never by asking a model to remember: logging, agent linkage, gates, provenance stamping, dashboard refresh. Models plan, do the work, and judge it.
2. **Separation of powers.**
   - The orchestrator plans and delegates, but cannot change the project.
   - Workers make changes, but cannot approve their own output.
   - Analysts and auditors read and check, but cannot edit.
3. **Nothing unverified reaches a parent silently.** A result is either verified, or it arrives labelled `unverified` with a review item for the human.
4. **Every claim is traceable** to an agent, a task, a plan version, and the concrete inputs it was based on.
5. **Zero dependencies, opt-in per project.** Stdlib Python 3.9+. Conductor is active only where `.conductor/` exists.

## Components

| Component | Role | Implemented as |
|---|---|---|
| **Orchestrator** | Plans, dispatches, asks the human, synthesises | The main Claude Code session: soft mode (normal session + injected brief) or strict mode (`claude --agent conductor:conductor`) |
| **Workers** | Do the work; may delegate to sub-agents | Subagent `conductor:worker` |
| **Analysts** | Read-only investigation; same protocol as workers | Subagent `conductor:analyst` (writes refused by hook) |
| **Auditor** | Independent verifier with a fresh context | Subagent `conductor:auditor` (writes and dispatch refused); its model can be set per project (`dispatch.models`) |
| **Hooks** | Record every event, link agents, enforce the protocol and gates, stamp provenance | `hooks/hooks.json` → `src/conductor/hook.py` |
| **Event log** | The single append-only record | `.conductor/events.jsonl` |
| **Plans** | Intent: goals, phases, tasks, acceptance criteria, decisions | `.conductor/plans/*.md` |
| **Dashboard** | Live view derived from the log and the plans | `status.json` → self-checked `status.html`; `conductor serve` |
| **Provenance** | Lineage queries and git trailers derived from the log | `conductor provenance`, PreToolUse(Bash) trailer stamping |

### Orchestrator tool policy

The orchestrator gets read tools and control tools, and nothing that changes the project.

| Allowed | Why |
|---|---|
| `Read, Grep, Glob, WebSearch, WebFetch`, the project's MCP sources | Plan with real context instead of guessing |
| `Agent(conductor:worker, conductor:analyst, conductor:auditor, Explore)` | Dispatch only known agent types |
| `AskUserQuestion` | Only the main session can ask the human |
| `SendMessage` | Resume a paused agent after the human answers |
| `Write/Edit` under `.conductor/` only | Plans and decisions. Project edits are refused by a hook in both modes. |
| no `Bash`, no project edits | Every change goes through a worker, and so through an audit gate. This also keeps the orchestrator's context small. |

- **Strict mode** applies this exact tool list.
- `--agent` replaces Claude Code's system prompt, so `agents/conductor.md` is a complete orchestrator prompt with the `orchestrate` skill preloaded.
- **Soft mode** keeps Claude Code's prompt. A `SessionStart` hook injects a short orchestrator brief, and the write-scope hook still applies.

## Inside the harness

![internals](internals.svg)

Claude Code passes every hook event to `hook.py` as JSON on stdin. `hook.py` runs once per event, uses the modules below, appends to `events.jsonl`, and answers on stdout with a decision. Everything else (the dashboard, `/conductor:status`, provenance queries) only reads the log.

| Module | Job |
|---|---|
| `hook.py` | Entry point: route the event, apply the rules, print the decision, trigger a re-render |
| `protocol.py` | Parse task and review envelopes, report blocks, claims and verdicts |
| `tree.py` | Replay `events.jsonl` into the agent tree, task bindings, observations and claims ledger |
| `cache.py` | Keep the replayed tree between hook calls and fold in only new lines, so a hook stays ~50 ms however long the log gets |
| `gate.py` | `SubagentStop` checks: children finished, evidence observed, verdicts present |
| `plans.py` | Parse plan files and validate them after the orchestrator writes one |
| `core.py` | Config, paths, and the locked append to the event log |
| `render.py` + `page.py` | Build `status.json` and the self-checked `status.html` (debounced, in a detached process) |
| `serve.py` | Serve the live page on 127.0.0.1 |
| `cli.py` + `provenance.py` | `init`, `status`, `render`, `serve`, `provenance` |

## Identity and linkage

There are two ID systems, joined by hooks:

- **Intent IDs**, chosen by the orchestrator: plan `P1@v2`, task `T3`, sub-task `T3.1`.
- **Runtime IDs**, supplied by Claude Code in every hook payload: `session_id`, `agent_id`, `agent_type`.

Every `Agent` call must begin with a task envelope:

```
<conductor-task id="T3" plan="P1@v1" parent="T1">
criteria: what must be true for this task to count as done
</conductor-task>
```

1. **`PreToolUse(Agent)`** refuses a dispatch without an envelope, and explains the format in the refusal. Read-only built-ins like `Explore` are exempt. It also:
   - enforces the **depth budget**: workers stop one level short of Claude Code's subagent depth limit (default 3), so there is always room for an auditor;
   - forces nested dispatches into the **foreground**, so a parent never reports before its children finish;
   - records a `dispatch` event.
2. **`SubagentStart`** binds the new agent to its dispatch. It uses the spawning tool-use id when Claude Code exposes it, and falls back to matching the oldest pending dispatch of the same type.
3. **`PostToolUse(Agent)`** fires in the parent's context and carries the child's id. It records a `link` event.

The result is a complete agent tree in which every node is bound to a plan task, built from facts rather than from what agents report about themselves.

## Event log

Hooks append one JSON line per event to `.conductor/events.jsonl`, under a file lock:
- dispatches and links;
- every file read (with a content hash), search, fetch, command and write;
- claims, verdicts and gate decisions;
- human messages and answers.

The full event table is in [provenance.md](provenance.md). Each hook call takes about 50 ms, and stays there as the log grows: the replayed state is cached in `.conductor/state/tree.cache` and only new lines are folded in (measured: 0.06 s at 50,000 events, against 0.24 s for a full replay). Any doubt about the cache (other version, truncated or replaced log) falls back to a full replay. Every event triggers a debounced re-render of the dashboard in a detached process, so the dashboard is live by construction, with no model in that loop.

## Audit gate

```
worker does the work, producing evidence as it goes (reads, commands, fetches are logged)
  → dispatches conductor:auditor with
      <conductor-review for="T3"> criteria + claims (each with evidence) </conductor-review>
    (refused if it lists no claims; the claims are recorded as submitted)
  → the auditor re-checks each claim against its source and ends with one verdict per claim:
      verified | refuted | unverified
  → the worker fixes or withdraws refuted claims, then reports:
      <conductor-report task="T3" status="done|blocked|failed"> summary, claims, files, needs </conductor-report>
```

The gate runs at the moment the agent reports. Agents report in one of two ways, and the gate runs where a refusal can still reach the agent:

- **Final message** (command-line runs): the `SubagentStop` hook reads `last_assistant_message`, and a refusal (`decision: block`) makes the agent continue.
- **Hand-back tool** (the desktop app's `SubagentHandback`): the `PreToolUse` hook reads the hand-back `message` and denies the call, so the agent gets the reason and hands back again. `SubagentStop` then never blocks, because the agent has already ended and a block would not reach it. If the hand-back still fails after the last attempt, the result is recorded as unverified.

Either way, a worker may finish only if all of these hold:

1. none of its sub-agents is still running;
2. the report block is present and well formed, and names the right task;
3. a `done` report asserts at least one claim, and every claim cites checkable evidence (`file:`, `cmd:`, `url:`, `mcp:`, `claim:`, `hitl:`);
4. **the evidence was actually observed.** Every cited file, command, URL and MCP call must appear in the log as read, run, fetched or called by this agent or its non-auditor descendants. A file also counts if a shell command the agent ran named it (`cat`, `sed -n`, a script); that is weaker than a `Read`, which records the file's hash. This is the anti-fabrication check;
5. every claim has a verdict from an auditor other than the claiming agent, unless the task envelope says `review="skip"` (allowed by default for low-stakes work; its claims are then shown as *asserted*, never as verified);
6. nothing is still claimed after being refuted, and no claim was reworded after its review.

An auditor may finish only if it gave a verdict for every claim in its own review (a re-audit may cover only the claims that changed), and, if it marked anything verified, the log shows it actually read, ran or fetched something.

When a check fails, the agent gets the exact reason and fixes it. If an agent's stop is never recorded but its parent already has the result, the agent is closed as unverified rather than left running. After `gate.max_blocks` (3) failed attempts the agent is let through, but marked `unverified`, and a review item is opened for the human. A `blocked` report with a `needs:` question becomes an open item for the human. Reports with status `blocked` or `failed` need no review.

Two kinds of block are told apart. A **transient** block (a sub-agent still running, or an auditor of this task still running, so verdicts are on their way) is a wait, not a defect: it does not count toward `max_blocks` (it has its own, larger `gate.max_transient_blocks`). When a parent resumes a stopped agent with `SendMessage`, the agent's budget starts again, and if it had escalated, the reasons are put in front of the parent's message so the agent can fix them.

**Escalation can be superseded.** Claim, task and review-item state is a pure function of the log, recomputed on every render. Each claim's wording is fingerprinted (`text_sha`, the hash of its normalised text). A claim's state is the latest verdict, from an agent other than the worker, on its *current* wording. If every final claim of an escalated task is verified that way, by the worker's auditor or by a later re-audit from any dispatcher, the task shows `done`, resolved by re-audit. A human can also accept it (`review_resolved`). The same independent verdict on the exact wording also satisfies the evidence check, since the auditor has already checked the source.

A re-audit envelope may name the task as `for="T2"`, `for="P1/T2"` or `for="T2" plan="P1"`. The key resolves to the exact key first, then the plan-qualified key, then a unique suffix match. If the id exists in several plans, the review is refused. `conductor reaudit T2` prints the envelope with the task's final claims verbatim.

Evidence references are parsed leniently: trailing notes such as `— verified` or `(ok)` are dropped, and `path:12-20` or `path:3,9` are split into path and lines. Observations follow honest shell workflows: URLs passed to `curl` or `wget` count as fetched, and the destinations of `cp`, `mv`, `tar -C`, `unzip -d`, `git clone` and redirects count as written. A small shell tokenizer (`shell.py`) handles this. It respects quotes and heredocs, and tracks `cd` within a command. The orchestrator's Bash write guard uses the same tokenizer, and only covers the project root outside `.conductor/`.

## Human in the loop

- Only the main session can ask questions, so sub-agents escalate by reporting `status="blocked"` with a concrete `needs:` question.
- The orchestrator asks the human with `AskUserQuestion`, logs the decision in `.conductor/plans/decisions.md`, then resumes the agent with `SendMessage` or re-dispatches.
- The orchestrator must ask before:
  - irreversible or outward-facing actions;
  - scope changes or budget overruns;
  - acting on conflicting results;
  - accepting anything escalated as unverified.

Every item that needs the human has one shape (schema in [status-schema.md](status-schema.md)): an id (`R-<task>` for an escalated review, `N-<agent>` for a `needs:` question, `H<n>` for an `AskUserQuestion`), a one-line title, a short problem with the latest audit tally, two or three options with consequences and a suggested one, and the raw gate reasons under *Details*. Each option carries a copyable line:

```
[Conductor HITL R-P1/T2] Problem: … Decision: Accept T2 as done. Note: …
```

A `UserPromptSubmit` hook recognises a pasted line and records a `hitl_decision`. It then tells the orchestrator what to do next: accept is recorded at once (`review_resolved`); re-audit comes with the ready-made envelope; redo means a new worker. `conductor decide <id> <option>` does the same from a terminal. A decided item leaves the open queue and shows under *Decisions* with a one-line record. It reopens only if something newer contradicts it, such as a fresh escalation of the same task or a requested re-audit that does not verify every claim.

## Plans

```
.conductor/plans/P1-<slug>.md      current version
.conductor/plans/P1-<slug>.v1.md   earlier versions, immutable
.conductor/plans/decisions.md      append-only decision log
```

Each plan has front-matter (`id, version, status, goal, intent, kill_test`) and two tables:
- Phases: `id | name | note`
- Tasks: `id | title | phase | agent | depends | criteria`

When the orchestrator writes a plan, a hook validates it and returns precise fixes if something is wrong, such as a missing criteria column. Plans hold **intent only**. Task and phase state is derived from events, so a plan can't drift from what actually happened.

## Provenance

The log maps onto W3C PROV:
- agents: the human, the orchestrator and every sub-agent;
- activities: tasks and tool calls;
- entities: file versions, sources, claims, verdicts and plan versions.

`conductor provenance <claim|task|agent|file>` prints lineage. Agent-made git commits carry `Conductor-Run/Task/Agent/Plan` trailers. Details are in [provenance.md](provenance.md).

## Dashboard

```
events.jsonl + plans ──▶ build_status ──▶ status.json ──▶ status.html (self-checked) ──▶ conductor serve
```

The page is one self-contained file:
- **Sections:** current state, claims, plan and tasks, agent tree, human-in-the-loop, log. Optional sections appear only when they have data.
- **Behaviour:** search plus state filters; light and dark themes; works at phone width; never shows "done" before it's verified.
- **Live mode:** served locally, it polls `status.json` and re-renders in place. Opened as a file, it's a snapshot.

A sidebar lists every session in the project (newest first) and, under each, the tasks given in it, plus an "other activity" entry for work outside any task. A task view shows the task header (envelope, dispatch prompt, the human prompt it followed), its agent tree, claims and verdicts, gate events, the log for that task, and the files written.

Tasks are keyed by plan and id (`P2/T3`), so tasks with the same id in different plans never merge. The short id is shown wherever it is unambiguous.

**Artifact (opt-in).** `/conductor:artifact` builds the same page in artifact mode (embedded data, no polling) and has Claude publish it to one fixed claude.ai URL per project, saved in `.conductor/state/artifact.json`. Hooks cannot publish, so Claude does it: after a task finishes and on request. It is off until the human agrees, and leaves prompts and human messages out unless `artifact.include_prompts` is set.

The data contract is in [status-schema.md](status-schema.md).

## Repository layout

```
.claude-plugin/   plugin.json · marketplace.json
agents/           conductor (strict-mode orchestrator) · worker · analyst · auditor
skills/           orchestrate (the orchestrator playbook)
commands/         init · status · serve · provenance
hooks/hooks.json  every event → src/conductor/hook.py
src/conductor/    hook · tree (replay) · gate · protocol · plans · render · page · provenance · serve · cli
dashboard/        template.html · sample-status.json · preview.py
templates/        the .conductor/ scaffold created by `conductor init`
examples/         three real runs with their full event logs
tests/            end-to-end hook tests
docs/             this document · provenance.md · status-schema.md · diagrams (architecture, internals) · logo
```

## Claude Code behaviour this relies on

Verified against Claude Code 2.1.285:

- **Nesting:** subagents can spawn subagents up to `CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH` (default 3); at the limit the `Agent` tool is removed.
- **Hook payloads identify the agent:**
  - every hook fired inside a subagent carries `agent_id` and `agent_type`;
  - `PostToolUse(Agent)` fires in the parent and includes the child's `agentId`;
  - `SubagentStop` includes `last_assistant_message` and `background_tasks`.
- **Hooks can steer agents:**
  - `SubagentStop` can block with `{"decision": "block", "reason": …}`, and the agent continues with that reason, but only if it ended with a final message; after a `SubagentHandback` call (desktop app) the agent has already ended;
  - hooks fire for `SubagentHandback` like any tool, and its `message` holds the report;
  - `PreToolUse` can deny a call or rewrite its input (`updatedInput`).
- **Defaults Conductor works around or relies on:**
  - nested agents run in the background by default, which Conductor overrides;
  - `AskUserQuestion` exists only in the main session;
  - `--agent` replaces the system prompt and restricts tools, and `Agent(type, …)` allowlists apply only to a main-session agent;
  - file-path permission rules work only as `Edit(path)`, and are ignored in untrusted folders, so Conductor enforces write scope with a hook as well, including obvious Bash writes (redirects, `tee`, `sed -i`, `cp`/`mv`/`rm`…); a determined shell command can still get past it;
  - `subagents/agent-<id>.meta.json` appears a few tens of milliseconds after `SubagentStart`; when several dispatches of the same type are pending, the hook waits briefly for it rather than guess;
  - Claude Code's own helpers (e.g. prompt suggestions) run as sub-agents with an empty type; they are ignored;
  - if `/conductor:init` runs mid-session, the next prompt starts the run and delivers the orchestrator brief.

## Limitations

- **The auditor is a model.** The gate guarantees a check happened, against sources the agents actually touched. It does not guarantee the check was right. Pinning the auditor to a different model helps.
- **Evidence matching is syntactic.** It proves a command was run, not that the command proves the claim; that judgement is the auditor's.
- **Platform dependence:**
  - it relies on hook payload fields and on one undocumented file, `subagents/*.meta.json`, for exact dispatch binding (with a fallback);
  - a Claude Code update may need a patch, and there is no automatic version check yet.
- **Waiting on the human:** a blocked sub-agent waits until the orchestrator asks the human and resumes it.
- **The Bash write guard is best effort.** It catches the usual ways of writing files from a shell, not every possible one.
- **Platforms:** macOS and Linux only (`python3`, `fcntl`).
