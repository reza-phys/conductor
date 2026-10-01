# Provenance in Conductor

Every claim Conductor shows you can answer three questions:
1. **Who** produced it: which agent, under which parent, from which human request.
2. **From what**: the exact files, file versions, commands and sources behind it.
3. **Checked by whom**: the independent verdicts and what each reviewer looked at.

The answers come from hooks (deterministic and outside the model's control), not from the agents' own account.

## The record: `.conductor/events.jsonl`

One append-only JSON line per event, written under a file lock by the hook process. Commit it; it *is* the provenance.

```json
{"ts":"2026-10-01T19:29:23.04Z","event":"observe_file","session":"…","agent":"ad906451b2017c4c0",
 "agent_type":"conductor:analyst","task":"T1","summary":"Read src/calc.py",
 "data":{"path":"src/calc.py","sha256":"862a1e56d376…","offset":null,"limit":null}}
```

Common fields: `ts`, `event`, `session`, `agent` (`main` = orchestrator, otherwise Claude Code's `agent_id`), `agent_type`, `task`, `summary` (one human-readable line), `data`.

| Event | Written by hook | `data` |
|---|---|---|
| `run_start` / `run_end` | SessionStart / SessionEnd | source, reason |
| `human_message` | UserPromptSubmit | text, `notification` flag |
| `dispatch` / `dispatch_denied` | PreToolUse(Agent) | tool_use_id, subagent_type, task, plan, parent_task, criteria, depth, prompt_sha, reason |
| `agent_start` | SubagentStart | tool_use_id (binds agent → dispatch) |
| `link` | PostToolUse(Agent) | tool_use_id, child agent id, status |
| `observe_file` | PostToolUse(Read) | path, sha256 at read time |
| `observe_search` | PostToolUse(Grep/Glob) | pattern, path |
| `observe_source` | PostToolUse(WebFetch/WebSearch) | url or query |
| `exec` | PostToolUse(Bash) | command, description |
| `observe_mcp` | PostToolUse(mcp__…) | tool, input (clipped), input and response hashes |
| `produce_file` | PostToolUse(Write/Edit/…) | path, sha256 after write |
| `write_denied` | PreToolUse(Write/Edit) | path (orchestrator or read-only agent) |
| `claims_submitted` | PreToolUse(Agent) for a review | task, claims[] (text + evidence) |
| `verdicts` | SubagentStop of an auditor | task, verdicts[] (id, verdict, evidence) |
| `gate_block` | SubagentStop | reasons[], attempt |
| `agent_stop` | SubagentStop | report, claims[] with final state, output_path, output_sha256, gate (`passed`/`escalated`/`exempt`) |
| `hitl` | PostToolUse(AskUserQuestion) | questions, answers |
| `commit_stamped` | PreToolUse(Bash) on `git commit` | trailers |

Each agent's final message is also stored verbatim in `.conductor/outputs/<agent_id>.md`, and its hash is recorded in `agent_stop`.

## Identity: why the model can't forge it

- `agent`, `agent_type` and `session` come from Claude Code's hook payload, never from model output.
- Parent → child links come from `PostToolUse(Agent)`, which fires in the parent's context and carries the child's id.
- Claims are parsed from blocks the model writes, but they are **attributed** using the hook's identity. An agent can't submit a claim as someone else.
- Evidence is cross-checked against `observe_*` and `exec` events from the claiming agent and its non-auditor descendants. A citation of something the agent never touched is rejected at the gate.

## Claims and verdicts

```
claims:
- C1: mul returns a + b instead of a * b | evidence: file:src/calc.py:5-6
- C2: the test suite passes | evidence: cmd:python3 -m src.test_calc
```

Global id: `<task>/<Cn>` (e.g. `T1/C2`). Evidence kinds:

| Kind | Checked against |
|---|---|
| `file:<path>[:<lines>]` | `observe_file` / `produce_file` / Grep path of the agent's work subtree |
| `cmd:<command>` | `exec` commands (whitespace-normalised containment) |
| `url:<url>` | `observe_source` URLs |
| `mcp:<server>__<tool>` | `observe_mcp` calls (e.g. a database query or an API) |
| `claim:<task>/<Cn>` | the ledger: that claim's latest verdict must be `verified` |
| `hitl:<id>` | human answers logged by the orchestrator |

Claim lifecycle: `submitted` (review envelope) → `verified | refuted | unverified` (auditor) → final state in `agent_stop`. A claim reworded after review counts as unreviewed. A worker that exhausts its gate attempts has its unreviewed claims marked `unverified`.

## W3C PROV mapping

| PROV | Conductor |
|---|---|
| `prov:Agent` | the human, `main` (orchestrator), each `agent_id` (+ type, model) |
| `prov:Activity` | run (session), task (`T1`), each tool call |
| `prov:Entity` | file version (path + sha256), source (url + time), claim, verdict, plan version, report |
| `wasAssociatedWith` | activity ↔ agent (every event's `agent`) |
| `actedOnBehalfOf` | child agent → parent agent (`link`) → … → human (`human_message`) |
| `used` | `observe_file`, `observe_source`, `observe_mcp`, `exec` |
| `wasGeneratedBy` | `produce_file`, `claims_submitted`, `verdicts`, `agent_stop` |
| `wasDerivedFrom` | claim → its evidence; parent claim → `claim:` citations |
| `wasInformedBy` | task → parent task (`dispatch.parent_task`) |

## Querying

```bash
conductor provenance T1/C2       # claim: text, state, agent chain, criteria, evidence trace, verdicts, report
conductor provenance T1          # task: work + review agents, gates, final claims
conductor provenance ad9064      # agent: chain, files read/written, commands, gate history
conductor provenance src/calc.py # file: every read/write with hashes, claims citing it
```

Example (from [examples/audit-review](../examples/audit-review/run/provenance-T1-C2.txt)):

```
Claim T1/C2: "mul (line 5-6) is buggy — it returns `a + b` instead of `a * b` …"  [verified]
  asserted by: analyst·ad9064 · task T1 · plan P1@v1
  chain:       analyst·ad9064 → orchestrator → human: "Audit src/calc.py: check every function…"
  evidence:
    file:src/calc.py:5-6 — read by analyst·ad9064 at 2026-10-01T19:29:23Z sha256 862a1e56d376…
  verdicts:
    verified by auditor·ab8d8a: Read lines 5-6 … Executed mul(2,3) → 5 (expected 6)
    verified by auditor·acb570: … mul(0,5)=5 vs correct 0. Matches claim exactly …
```

Git: agent-made commits carry trailers, so `git log --format='%h %(trailers:key=Conductor-Task)'` links code history back to tasks and the event log.
