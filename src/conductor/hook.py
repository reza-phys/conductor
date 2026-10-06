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
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conductor import cache, core, gate, protocol, render  # noqa: E402
from conductor.tree import MAIN, Tree, task_key  # noqa: E402

WRITE_TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}
HANDBACK = "SubagentHandback"  # desktop-app tool a sub-agent may end its run with; its `message` carries the report
NOTIFICATION_PREFIXES = ("<task-notification>", "<agent-message")


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
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": _brief(c)}}


def _brief(c: Ctx) -> str:
    brief = protocol.ORCHESTRATOR_BRIEF
    if c.cfg["artifact"]["enabled"]:
        brief += protocol.ARTIFACT_BRIEF
    return brief


def on_session_end(c: Ctx):
    c.emit("run_end", {"reason": c.p.get("reason")})


def on_user_prompt_submit(c: Ctx):
    text = c.p.get("prompt") or ""
    note = text.lstrip().startswith(NOTIFICATION_PREFIXES)  # task notifications and sub-agent hand-backs, not the human
    c.emit("human_message", {"text": core.clip(text, 4000), "notification": note},
           summary=None if note else "human: " + text)
    sid = c.p.get("session_id")
    if sid and not (c.tree.sessions.get(sid) or {}).get("run"):
        # SessionStart ran before .conductor/ existed (e.g. /conductor:init mid-session): start the run and brief now
        c.emit("run_start", {"source": "late_init"}, run=f"R-{core.utcnow()[:10].replace('-', '')}-{sid[:6]}")
        if c.cfg["orchestrator"]["inject_protocol"]:
            return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": _brief(c)}}
    return None


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
    if tool == HANDBACK:
        return _pre_handback(c, ti)
    if tool == "Bash":
        return _guard_bash(c, ti) or _pre_bash(c, ti)
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
                "Conductor-Task": a.get("task_id") or a.get("task"), "Conductor-Plan": a.get("plan")}
    flags = " ".join(f"--trailer {shlex.quote(f'{k}: {v}')}" for k, v in trailers.items() if v)
    new = _GIT_COMMIT.sub(lambda m: f"{m.group(0)} {flags}", cmd, count=1)
    c.emit("commit_stamped", {"trailers": {k: v for k, v in trailers.items() if v}})
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "updatedInput": {**ti, "command": new}}}


_REDIRECT = re.compile(r"""(?<![<0-9&])(?:[0-9]?>>?|&>>?)\s*(?!&)("[^"]+"|'[^']+'|[^\s;|&<>()]+)""")
_WRITE_CMD = re.compile(r"""(?:^|[;&|(]\s*|\s)(?:sudo\s+)?(tee(?:\s+-a)?|cp|mv|rm|rmdir|touch|mkdir|ln|install|truncate|dd)\s+([^;&|]*)""")
_INPLACE = re.compile(r"""(?:^|[;&|(]\s*|\s)(?:sed|perl|gsed)\s+[^;&|]*?-[a-zA-Z]*i[^;&|]*""")
_SAFE_TARGETS = ("/dev/null", "/dev/stdout", "/dev/stderr", "/tmp/", "/private/tmp/", "/var/folders/")


def _bash_write_targets(cmd: str) -> list[str]:
    """Paths an orchestrator Bash command visibly writes (redirects, tee, cp/mv/rm/touch/…; sed -i => '?')."""
    targets = [m.strip("'\"") for m in _REDIRECT.findall(cmd)]
    for verb, rest in _WRITE_CMD.findall(cmd):
        words = [w for w in shlex.split(rest, posix=True) if not w.startswith("-")] if rest.strip() else []
        if verb.startswith("tee") or verb in ("touch", "mkdir", "rm", "rmdir", "truncate"):
            targets += words
        elif words:
            targets.append(words[-1])  # cp/mv/ln/install/dd: the destination
    if _INPLACE.search(cmd):
        targets.append("?")  # in-place edit of files we cannot reliably name
    return targets


def _guard_bash(c: Ctx, ti: dict):
    """The orchestrator's write scope also covers obvious Bash writes (best effort, not a sandbox)."""
    if c.agent != MAIN or c.cfg["orchestrator"]["allow_project_edits"]:
        return None
    cmd = ti.get("command") or ""
    try:
        targets = _bash_write_targets(cmd)
    except ValueError:  # unbalanced quotes: let it through rather than guess
        return None
    state = str(c.state.resolve())
    bad = []
    for t in targets:
        if t == "?":
            bad.append("in-place edit (sed/perl -i)")
            continue
        if t.startswith(_SAFE_TARGETS):
            continue
        full = Path(t) if os.path.isabs(t) else (c.root / t)
        try:
            resolved = str(full.resolve())
        except OSError:
            resolved = str(full)
        if not resolved.startswith(state):
            bad.append(c.rel(resolved) or t)
    if not bad:
        return None
    c.emit("write_denied", {"tool": "Bash", "targets": bad, "command": core.clip(cmd, 300)},
           summary=f"orchestrator Bash write refused: {', '.join(bad)[:80]}")
    return _deny(f"Conductor: the orchestrator does not change project files, including through Bash "
                 f"({', '.join(bad)}). Dispatch a worker, or write only under {core.STATE_DIR}/.")


def _gate_eval(c: Ctx, a: dict, msg: str, background_tasks=None) -> dict:
    """Run the audit gate on a report. Returns reasons (empty = pass) plus the parsed pieces."""
    gcfg = c.cfg["gate"]
    report = protocol.parse_report(msg)
    reasons: list[str] = []
    desc = c.tree.descendants(c.agent)
    running = {t.get("id") for t in (background_tasks or [])
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
        mine = a.get("task_id") or a.get("task")
        if report.get("task") and report["task"] not in (mine, a.get("task")):
            reasons.append(f'Your report says task="{report["task"]}" but you were dispatched for task "{mine}".')
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
    return {"reasons": reasons, "report": report, "claims": claims, "verdicts": verdicts, "states": states}


def _gated(c: Ctx, a: dict) -> bool:
    return bool(c.cfg["gate"]["enabled"] and a.get("task") and not a.get("exempt"))


def _pre_handback(c: Ctx, ti: dict):
    """Gate at hand-back time: the agent is still running, so a refusal reaches it and it can fix the report."""
    a = c.tree.agents.get(c.agent, {})
    if c.agent == MAIN or not _gated(c, a):
        return None
    ev = _gate_eval(c, a, ti.get("message") or "")
    blocks = c.tree.gate_blocks(c.agent)
    if not ev["reasons"] or blocks >= c.cfg["gate"]["max_blocks"]:
        return None  # pass, or out of attempts: SubagentStop records the outcome (escalated if still failing)
    c.emit("gate_block", {"reasons": ev["reasons"], "attempt": blocks + 1, "at": "handback"},
           summary=f"gate blocked hand-back (attempt {blocks + 1})")
    return _deny(f"Conductor gate (attempt {blocks + 1}/{c.cfg['gate']['max_blocks']}): fix this, then hand back again:\n- "
                 + "\n- ".join(ev["reasons"]))


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
    caller_plan = c.tree.agents.get(c.agent, {}).get("plan")
    if task:
        plan = task.get("plan") or caller_plan
        parent = task.get("parent")
        data.update(task=task.get("id"), plan=plan, parent_task=task_key(plan, parent) if parent else parent_task,
                    criteria=core.clip(task.get("criteria"), 2000), review="skip" if skip_review else "required")
    if c.cfg["ledger"]["store_prompts"]:
        data["prompt"] = core.clip(prompt, c.cfg["ledger"]["prompt_clip"])
    review_claims = _rel_claims(c, protocol.parse_claims(review.get("claims"))) if review else []
    if review:
        data.update(review_for=review.get("for"), parent_task=parent_task, plan=caller_plan)

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
        c.emit("claims_submitted", {"task": task_key(caller_plan, review.get("for")), "claims": review_claims,
                                    "tool_use_id": c.p.get("tool_use_id")},
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
        return "exec", {"command": core.clip(cmd, 1000), "description": ti.get("description"), "files": _named_files(c, cmd)}, \
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


def _named_files(c: Ctx, cmd: str, limit: int = 50) -> list[str]:
    """Existing project files a shell command names (cat, sed -n, head, a script…): evidence that it looked at them.
    Weaker than a Read, which records the file's hash; documented as such."""
    try:
        words = shlex.split(cmd, posix=True)
    except ValueError:
        words = cmd.split()
    cwd = Path(c.p.get("cwd") or c.root)
    out = []
    for w in words:
        if w.startswith("-") or len(w) > 300 or any(ch in w for ch in "*?$`|;&<>"):
            continue
        p = Path(w) if os.path.isabs(w) else cwd / w
        try:
            if p.is_file():
                r = c.rel(str(p))
                if r and not os.path.isabs(r) and r not in out:
                    out.append(r)
        except OSError:
            continue
        if len(out) >= limit:
            break
    return out


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
    if tool == HANDBACK:
        msg = ti.get("message") or ""
        c.emit("handback", {"message": core.clip(msg, 20000), "sha256": core.sha256_text(msg)}, summary="handed back")
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
    tuid = _meta_tool_use_id(c) or c.tree.agents.get(c.agent, {}).get("tool_use_id")
    if not tuid and c.tree.unbound_count(c.p.get("agent_type"), c.p.get("session_id")) > 1:
        # Several same-type dispatches are pending: a FIFO guess could pick the wrong task. meta.json (with the exact
        # spawning tool-use id) appears ~60-70 ms after SubagentStart, so wait for it briefly.
        for _ in range(8):
            time.sleep(0.05)
            tuid = _meta_tool_use_id(c)
            if tuid:
                break
    tuid = tuid or c.tree.unbound_dispatch(c.p.get("agent_type"), c.p.get("session_id"))
    c.emit("agent_start", {"tool_use_id": tuid}, summary=f"started {c.p.get('agent_type')}")


def _handback_message(c: Ctx) -> str | None:
    """The report an agent handed back: from the logged hand-back, else from its transcript (last SubagentHandback call)."""
    a = c.tree.agents.get(c.agent, {})
    if a.get("handback"):
        return a["handback"]
    paths = [Path(p) for p in [c.p.get("agent_transcript_path")] if p]
    if c.p.get("transcript_path"):
        paths.append(Path(c.p["transcript_path"]).with_suffix("") / "subagents" / f"agent-{c.agent}.jsonl")
    for p in paths:
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            content = (r.get("message") or {}).get("content") if r.get("type") == "assistant" else None
            for b in content if isinstance(content, list) else []:
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == HANDBACK:
                    return (b.get("input") or {}).get("message")
    return None


def on_subagent_stop(c: Ctx):
    gcfg = c.cfg["gate"]
    a = c.tree.agents.get(c.agent, {})
    msg = c.p.get("last_assistant_message") or ""
    handback = None if protocol.parse_report(msg) else _handback_message(c)
    msg = handback or msg
    report = protocol.parse_report(msg)

    out_dir = c.state / "outputs"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{c.agent}.md"
    out_path.write_text(msg, encoding="utf-8")
    stop_data = {"output_path": c.rel(str(out_path)), "output_sha256": core.sha256_text(msg),
                 "report": {"status": report.get("status"), "summary": core.clip(report.get("summary") or report.get("body"), 600),
                            "files": report.get("files"), "needs": report.get("needs")} if report else None}

    if handback:
        stop_data["ended_by"] = "handback"
    if not _gated(c, a):
        c.emit("agent_stop", {**stop_data, "gate": "exempt"}, summary="finished (ungated)")
        return None

    ev = _gate_eval(c, a, msg, c.p.get("background_tasks"))
    reasons, claims, verdicts, states = ev["reasons"], ev["claims"], ev["verdicts"], ev["states"]
    review_for = a.get("review_for")
    stop_data["claims"] = [{**cl, "state": states.get(cl["id"], "asserted")} for cl in claims] if not review_for else None

    if reasons:
        blocks = c.tree.gate_blocks(c.agent)
        # After a hand-back the agent has ended: a block here would never reach it (the gate ran at hand-back time).
        if blocks < gcfg["max_blocks"] and not handback:
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
        if payload.get("agent_id") and payload.get("agent_type") == "" and payload["agent_id"] not in ctx.tree.agents:
            ctx.save_tree()
            return 0  # Claude Code-internal helper (e.g. prompt suggestions): never dispatched, not part of the run
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
