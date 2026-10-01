#!/usr/bin/env python3
"""Single entry point for every Claude Code hook event Conductor listens to.

Reads the hook payload from stdin, appends events to .conductor/events.jsonl, enforces the
dispatch protocol and the stop gate, and triggers a dashboard re-render. A hook must never
break the session: any internal error is logged to .conductor/state/hook-errors.log and the
hook exits 0 with no decision.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conductor import cache, core, gate, protocol, render  # noqa: E402
from conductor.tree import MAIN, Tree  # noqa: E402

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def _rel_claims(c: "Ctx", claims: list[dict]) -> list[dict]:
    """Store file evidence project-relative, so the ledger is portable."""
    for cl in claims:
        for ev in cl.get("evidence", []):
            if ev["kind"] == "file" and os.path.isabs(ev["ref"]):
                ev["ref"] = c.rel(ev["ref"])
    return claims


class Ctx:
    def __init__(self, payload: dict, state: Path, cfg: dict):
        self.p = payload
        self.state = state
        self.cfg = cfg
        self.root = state.parent
        self.agent = payload.get("agent_id") or MAIN
        self._tree: Tree | None = None
        self._offset = 0

    @property
    def tree(self) -> Tree:
        if self._tree is None:
            self._tree, self._offset = cache.load(self.state)
        return self._tree

    def save_tree(self) -> None:
        """Persist the replayed state (up to where it was read; this event's own lines follow next time)."""
        if self._tree is not None:
            cache.save(self.state, self._tree, self._offset)

    def rel(self, path: str | None) -> str | None:
        if not path:
            return path
        try:
            return str(Path(path).resolve().relative_to(self.root.resolve()))
        except (ValueError, OSError):
            return path

    def emit(self, event: str, data: dict | None = None, summary: str | None = None, **extra) -> dict:
        ev = {"event": event, "session": self.p.get("session_id"), "agent": self.agent,
              "agent_type": self.p.get("agent_type"), "task": self.tree.task_of(self.agent)}
        if summary:
            ev["summary"] = core.clip(summary, 160)
        ev.update(extra)
        if data is not None:
            ev["data"] = data
        return core.append_event(self.state, {k: v for k, v in ev.items() if v is not None})


# --- handlers (return a dict to print as the hook's JSON output, or None) -----------------

def on_session_start(c: Ctx):
    sid = c.p.get("session_id") or ""
    c.emit("run_start", {"source": c.p.get("source"), "model": c.p.get("model")},
           run=f"R-{core.utcnow()[:10].replace('-', '')}-{sid[:6]}")
    if c.cfg["orchestrator"]["inject_protocol"]:
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": protocol.ORCHESTRATOR_BRIEF}}


def on_session_end(c: Ctx):
    c.emit("run_end", {"reason": c.p.get("reason")})


def on_user_prompt_submit(c: Ctx):
    text = c.p.get("prompt") or ""
    note = text.lstrip().startswith("<task-notification>")
    c.emit("human_message", {"text": core.clip(text, 4000), "notification": note},
           summary=None if note else "human: " + text)


def on_stop(c: Ctx):
    c.emit("turn_end", {"last": core.clip(c.p.get("last_assistant_message"), 600)})


def _deny(reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": reason}}


def on_pre_tool_use(c: Ctx):
    tool = c.p.get("tool_name")
    ti = c.p.get("tool_input") or {}
    if tool == "Agent":
        return _pre_agent(c, ti)
    if tool == "Bash":
        return _pre_bash(c, ti)
    if tool in WRITE_TOOLS and c.p.get("agent_type") in c.cfg["dispatch"]["read_only_types"]:
        c.emit("write_denied", {"path": c.rel(ti.get("file_path") or ti.get("notebook_path")), "tool": tool},
               summary=f"read-only agent edit refused")
        return _deny(f"Conductor: {c.p.get('agent_type')} is read-only — report the needed change instead of making it.")
    if tool in WRITE_TOOLS and c.agent == MAIN and not c.cfg["orchestrator"]["allow_project_edits"]:
        path = ti.get("file_path") or ti.get("notebook_path") or ""
        target = (c.root / path) if not os.path.isabs(path) else Path(path)
        try:
            target.resolve().relative_to(c.state.resolve())
        except ValueError:
            c.emit("write_denied", {"path": c.rel(path), "tool": tool}, summary=f"orchestrator edit refused: {c.rel(path)}")
            return _deny(f"Conductor: the orchestrator only writes under {core.STATE_DIR}/ — dispatch a worker "
                         f"to change {c.rel(path)} (or set orchestrator.allow_project_edits in "
                         f"{core.STATE_DIR}/config.json).")
    return None


_OPT_VAL = r"""(?:[^\s"']|"[^"]*"|'[^']*')+"""  # a shell word, quotes allowed anywhere inside
# git, then any global options (-C <path>, -c <k=v>, --git-dir=…, --no-pager), then the commit subcommand
_GIT_COMMIT = re.compile(rf"\bgit(?:\s+-[cC]\s+{_OPT_VAL}|\s+--[\w-]+(?:={_OPT_VAL})?)*\s+commit\b")


def _pre_bash(c: Ctx, ti: dict):
    """Stamp provenance trailers onto agent-made git commits."""
    cmd = ti.get("command") or ""
    if not c.cfg["provenance"]["commit_trailers"] or not _GIT_COMMIT.search(cmd) or "Conductor-Run:" in cmd:
        return None
    a = c.tree.agents.get(c.agent, {})
    trailers = {"Conductor-Run": c.tree.run.get("run"), "Conductor-Agent": c.agent,
                "Conductor-Task": a.get("task"), "Conductor-Plan": a.get("plan")}
    flags = " ".join(f"--trailer {shlex.quote(f'{k}: {v}')}" for k, v in trailers.items() if v)
    new = _GIT_COMMIT.sub(lambda m: f"{m.group(0)} {flags}", cmd, count=1)
    c.emit("commit_stamped", {"trailers": {k: v for k, v in trailers.items() if v}})
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {**ti, "command": new}}}


def _pre_agent(c: Ctx, ti: dict):
    dcfg = c.cfg["dispatch"]
    stype = ti.get("subagent_type") or "general-purpose"
    prompt = ti.get("prompt") or ""
    task = protocol.parse_task(prompt)
    review = protocol.parse_review(prompt)
    is_auditor = stype in dcfg["auditor_types"] or bool(review)
    exempt = stype in dcfg["exempt_types"]
    depth = c.tree.depth(c.agent) + 1
    parent_task = c.tree.task_of(c.agent)
    data = {"tool_use_id": c.p.get("tool_use_id"), "subagent_type": stype,
            "description": ti.get("description"), "depth": depth, "exempt": exempt and not task,
            "background": ti.get("run_in_background"), "prompt_sha": core.sha256_text(prompt)}
    skip_review = bool(task) and (task.get("review") or "").lower() in protocol.REVIEW_SKIP
    if task:
        data.update(task=task.get("id"), plan=task.get("plan"), parent_task=task.get("parent") or parent_task,
                    criteria=core.clip(task.get("criteria"), 2000), review="skip" if skip_review else "required")
    review_claims = _rel_claims(c, protocol.parse_claims(review.get("claims"))) if review else []
    if review:
        data.update(review_for=review.get("for"), parent_task=parent_task)

    reason = None
    if dcfg["enforce_envelope"] and not (task or review or exempt):
        reason = "Conductor: dispatch refused — no task envelope.\n" + protocol.ENVELOPE_HELP
    elif skip_review and not c.cfg["gate"]["allow_review_skip"]:
        reason = 'Conductor: dispatch refused — this project does not allow review="skip"; remove it from the envelope.'
    elif review and not review_claims:
        reason = "Conductor: review refused — the review envelope lists no claims.\n" + protocol.REVIEW_HELP
    elif depth >= dcfg["max_depth"] and not is_auditor:
        reason = (f"Conductor: dispatch refused — a worker at depth {depth} would leave no room for its "
                  f"auditor (max depth {dcfg['max_depth']}). Do this work yourself or report back.")
    if reason:
        data["reason"] = reason
        c.emit("dispatch_denied", data, summary=f"dispatch refused: {stype}")
        return _deny(reason)

    if review_claims:
        c.emit("claims_submitted", {"task": review.get("for"), "claims": review_claims, "tool_use_id": c.p.get("tool_use_id")},
               summary=f"submitted {len(review_claims)} claims of {review.get('for')} for review")
    label = task.get("id") if task else (f"review {review.get('for')}" if review else "untracked")
    c.emit("dispatch", data, summary=f"dispatch → {stype} [{label}] {ti.get('description') or ''}")
    updated = dict(ti)
    if c.agent != MAIN and dcfg["force_foreground_nested"] and ti.get("run_in_background") is not False:
        updated["run_in_background"] = False
    model = (dcfg.get("models") or {}).get(stype)
    if model and ti.get("model") != model:
        updated["model"] = model
    if updated != ti:
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": updated}}
    return None


def _tool_summary(c: Ctx, tool: str, ti: dict) -> tuple[str, dict, str]:
    """(event name, data, one-line summary) for a finished tool call."""
    if tool == "Read":
        path = ti.get("file_path")
        return "observe_file", {"path": c.rel(path), "sha256": core.sha256_file(path) if path else None,
                                "offset": ti.get("offset"), "limit": ti.get("limit")}, f"Read {c.rel(path)}"
    if tool in ("Grep", "Glob"):
        return "observe_search", {"tool": tool, "pattern": ti.get("pattern"), "path": c.rel(ti.get("path"))}, \
            f"{tool} {core.clip(ti.get('pattern'), 60)}"
    if tool == "WebFetch":
        return "observe_source", {"url": ti.get("url")}, f"WebFetch {ti.get('url')}"
    if tool == "WebSearch":
        return "observe_source", {"query": ti.get("query")}, f"WebSearch {core.clip(ti.get('query'), 80)}"
    if tool == "Bash":
        cmd = ti.get("command") or ""
        return "exec", {"command": core.clip(cmd, 1000), "description": ti.get("description")}, \
            f"Bash: {core.clip(ti.get('description') or cmd, 100)}"
    if tool.startswith("mcp__"):
        inp = json.dumps(ti, sort_keys=True, ensure_ascii=False)
        tr = c.p.get("tool_response")
        return "observe_mcp", {"tool": tool, "input": core.clip(inp, 300), "input_sha": core.sha256_text(inp),
                               "response_sha": core.sha256_text(json.dumps(tr, sort_keys=True, default=str))}, \
            f"MCP {tool[5:]}"
    if tool in WRITE_TOOLS:
        path = ti.get("file_path") or ti.get("notebook_path")
        return "produce_file", {"path": c.rel(path), "tool": tool, "sha256": core.sha256_file(path) if path else None}, \
            f"{tool} {c.rel(path)}"
    return "tool", {"tool": tool}, tool


def on_post_tool_use(c: Ctx):
    tool = c.p.get("tool_name") or "?"
    ti = c.p.get("tool_input") or {}
    tr = c.p.get("tool_response")
    if tool == "Agent":
        tr = tr if isinstance(tr, dict) else {}
        c.emit("link", {"tool_use_id": c.p.get("tool_use_id"), "child": tr.get("agentId"),
                        "status": tr.get("status"), "child_type": tr.get("agentType") or ti.get("subagent_type"),
                        "model": tr.get("resolvedModel")})
        return None
    if tool == "AskUserQuestion":
        c.emit("hitl", {"questions": ti.get("questions"), "answers": tr if isinstance(tr, (dict, list, str)) else None},
               summary="asked the human")
        return None
    event, data, summary = _tool_summary(c, tool, ti)
    c.emit(event, data, summary=summary)
    path = data.get("path") if event == "produce_file" else None
    if path and c.agent == MAIN and path.startswith(f"{core.STATE_DIR}/plans/") and path.endswith(".md") \
            and not path.endswith("README.md") and not path.endswith("decisions.md") and not re.search(r"\.v\d+\.md$", path):
        from conductor import plans
        problems = plans.validate(c.root / path)
        if problems:
            return {"decision": "block", "reason": f"Conductor: plan {path} needs fixing — " + "; ".join(problems)
                    + ". Template: see the conductor:orchestrate skill, section 1."}
    return None


def _meta_tool_use_id(c: Ctx) -> str | None:
    """Claude Code writes subagents/agent-<id>.meta.json with the spawning toolUseId (undocumented)."""
    tp = c.p.get("transcript_path")
    if not tp:
        return None
    meta = Path(tp).with_suffix("") / "subagents" / f"agent-{c.p.get('agent_id')}.meta.json"
    try:
        return json.loads(meta.read_text()).get("toolUseId")
    except (OSError, ValueError):
        return None


def on_subagent_start(c: Ctx):
    tuid = _meta_tool_use_id(c) or c.tree.unbound_dispatch(c.p.get("agent_type"), c.p.get("session_id"))
    c.emit("agent_start", {"tool_use_id": tuid}, summary=f"started {c.p.get('agent_type')}")


def on_subagent_stop(c: Ctx):
    gcfg = c.cfg["gate"]
    a = c.tree.agents.get(c.agent, {})
    msg = c.p.get("last_assistant_message") or ""
    report = protocol.parse_report(msg)

    out_dir = c.state / "outputs"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{c.agent}.md"
    out_path.write_text(msg, encoding="utf-8")
    stop_data = {"output_path": c.rel(str(out_path)), "output_sha256": core.sha256_text(msg),
                 "report": {"status": report.get("status"), "summary": core.clip(report.get("summary") or report.get("body"), 600),
                            "files": report.get("files"), "needs": report.get("needs")} if report else None}

    tracked = a.get("task") and not a.get("exempt")
    if not gcfg["enabled"] or not tracked:
        c.emit("agent_stop", {**stop_data, "gate": "exempt"}, summary="finished (ungated)")
        return None

    reasons = []
    desc = c.tree.descendants(c.agent)
    running = {t.get("id") for t in (c.p.get("background_tasks") or [])
               if t.get("status") == "running" and t.get("id") in desc}
    running |= {d for d in desc if c.tree.agents[d]["state"] == "running"}
    if running:
        reasons.append(f"{len(running)} of your sub-agents are still running ({', '.join(sorted(running))}). "
                       "Wait for their results before reporting.")
    review_for = a.get("review_for")
    status = (report or {}).get("status")
    claims = _rel_claims(c, protocol.parse_claims((report or {}).get("claims")))
    verdicts = protocol.parse_verdicts((report or {}).get("verdicts")) if review_for else []
    states: dict = {}
    if gcfg["require_report"] and (not report or status not in protocol.REPORT_STATUSES):
        reasons.append("Missing or malformed report block. " + protocol.REPORT_HELP)
    elif review_for:
        reasons += gate.check_auditor(c.tree, c.agent, review_for, verdicts)
    elif report:
        if report.get("task") and report["task"] != a.get("task"):
            reasons.append(f'Your report says task="{report["task"]}" but you were dispatched for task "{a.get("task")}".')
        if status == "done":
            if gcfg["require_claims"] and not claims:
                reasons.append("A done report needs at least one claim. " + protocol.CLAIMS_HELP)
            if gcfg["check_evidence"]:
                reasons += gate.check_evidence(c.tree, c.agent, claims)
            if gcfg["require_verdicts"] and claims and a.get("review") != "skip":
                states, problems = gate.claim_states(c.tree, a["task"], c.agent, claims)
                if problems:
                    reasons += problems + ([protocol.REVIEW_HELP] if any("not been reviewed" in p or "changed" in p
                                                                          for p in problems) else [])
    stop_data["claims"] = [{**cl, "state": states.get(cl["id"], "asserted")} for cl in claims] if not review_for else None

    if reasons:
        blocks = c.tree.gate_blocks(c.agent)
        if blocks < gcfg["max_blocks"]:
            c.emit("gate_block", {"reasons": reasons, "attempt": blocks + 1}, summary=f"gate blocked (attempt {blocks + 1})")
            return {"decision": "block",
                    "reason": f"Conductor gate (attempt {blocks + 1}/{gcfg['max_blocks']}):\n- " + "\n- ".join(reasons)}
        verdict_note = "escalated"
    else:
        verdict_note = "passed"
    if review_for and verdicts:
        c.emit("verdicts", {"task": review_for, "verdicts": verdicts, "gate": verdict_note},
               summary=f"verdicts for {review_for}: " + ", ".join(f"{v['id']} {v['verdict']}" for v in verdicts))
    if verdict_note == "escalated":
        c.emit("agent_stop", {**stop_data, "gate": "escalated", "reasons": reasons},
               summary="finished — gate escalated (unverified)")
    else:
        unreviewed = a.get("review") == "skip" and not review_for
        c.emit("agent_stop", {**stop_data, "gate": "passed", **({"review": "skipped"} if unreviewed else {})},
               summary="finished — gate passed" + (" (review skipped)" if unreviewed else ""))
    return None


HANDLERS = {
    "SessionStart": on_session_start, "SessionEnd": on_session_end, "UserPromptSubmit": on_user_prompt_submit,
    "Stop": on_stop, "PreToolUse": on_pre_tool_use, "PostToolUse": on_post_tool_use,
    "SubagentStart": on_subagent_start, "SubagentStop": on_subagent_stop,
}


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return 0
    state = core.find_state_dir(core.project_dir(payload))
    if state is None:
        return 0  # Conductor not initialised in this project: stay silent
    try:
        cfg = core.load_config(state)
        handler = HANDLERS.get(payload.get("hook_event_name", ""))
        ctx = Ctx(payload, state, cfg)
        out = handler(ctx) if handler else None
        ctx.save_tree()
        if out:
            print(json.dumps(out))
        if cfg["render"]["enabled"]:
            render.trigger(state)
    except Exception:  # never break the session
        try:
            with core.locked(state / "state" / "hook-errors.log") as f:
                f.write(f"{core.utcnow()} {payload.get('hook_event_name')}\n{traceback.format_exc()}\n")
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
