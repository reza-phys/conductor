"""Rebuild the agent tree and task states by replaying events.jsonl.

The log is the single source of truth; everything here is derived and can be recomputed at any time.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
from typing import Any

MAIN = "main"


def norm_text(s: str | None) -> str:
    """Claim wording for comparison: ignores whitespace, markdown emphasis/backticks, case and a trailing full stop."""
    s = re.sub(r"[`*_]", "", s or "")
    return re.sub(r"\s+", " ", s).strip().rstrip(".").strip().lower()


def text_sha(s: str | None) -> str:
    return hashlib.sha256(norm_text(s).encode("utf-8")).hexdigest()[:16]


def task_key(plan: str | None, task: str | None) -> str | None:
    """Plan-qualified task key: ("P2@v1", "T3") -> "P2/T3". Without a plan the bare id is the key."""
    if not task:
        return None
    if "/" in task:  # already qualified ("P1/T2")
        return task
    pid = (plan or "").split("@")[0].strip()
    return f"{pid}/{task}" if pid else task


def _ts(s: str | None) -> _dt.datetime | None:
    if not s:
        return None
    try:
        return _dt.datetime.strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M:%S.%f").replace(tzinfo=_dt.timezone.utc)
    except ValueError:
        try:
            return _dt.datetime.strptime(s.replace("Z", ""), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            return None


class Tree:
    def __init__(self, events: list[dict] | None = None, keep_events: bool = True):
        """Replay `events`. keep_events=False drops the raw list (the hook's cached tree only needs derived state)."""
        self.keep_events = keep_events
        self.events: list[dict] = []
        self.dispatches: dict[str, dict] = {}  # tool_use_id -> dispatch event data
        self.agents: dict[str, dict[str, Any]] = {}
        self.bound: set[str] = set()  # tool_use_ids already bound to an agent
        self.run: dict[str, Any] = {}
        self.human_messages: list[dict] = []
        self.hitl: list[dict] = []
        self.live = False
        # provenance: what each agent actually touched, and the claims ledger per task
        self.obs: dict[str, dict[str, Any]] = {}
        self.ledger: dict[str, dict[str, Any]] = {}
        # sessions: id -> first/last event, run id, end, title (first human prompt)
        self.sessions: dict[str, dict[str, Any]] = {}
        # human-in-the-loop decisions by item id, and explicit resolutions per task key
        self.hitl_decisions: dict[str, dict[str, Any]] = {}
        self.resolutions: dict[str, dict[str, Any]] = {}
        self.apply(events or [])

    def apply(self, events: list[dict]) -> None:
        """Fold more events into the tree, in log order (used for incremental replay)."""
        for e in events:
            if self.keep_events:
                self.events.append(e)
            sid = e.get("session")
            if sid:
                s = self.sessions.setdefault(sid, {"id": sid, "first": e.get("ts"), "last": e.get("ts"),
                                                   "run": None, "ended": None, "title": None})
                s["last"] = e.get("ts")
            getattr(self, "_on_" + e.get("event", ""), self._on_tool)(e)

    # --- helpers -----------------------------------------------------------------
    def depth(self, agent_id: str | None) -> int:
        if not agent_id or agent_id == MAIN:
            return 0
        a = self.agents.get(agent_id)
        return a["depth"] if a and a.get("depth") is not None else 1

    def task_of(self, agent_id: str | None) -> str | None:
        a = self.agents.get(agent_id or "")
        return a.get("task") if a else None

    def children(self, agent_id: str) -> list[str]:
        return [k for k, a in self.agents.items() if a.get("parent") == agent_id]

    def descendants(self, agent_id: str) -> set[str]:
        out, stack = set(), [agent_id]
        while stack:
            for c in self.children(stack.pop()):
                if c not in out:
                    out.add(c)
                    stack.append(c)
        return out

    def unbound_dispatch(self, agent_type: str, session: str | None = None) -> str | None:
        """Oldest dispatch of this type not yet bound to a started agent (FIFO fallback)."""
        for tid, d in self.dispatches.items():
            if tid in self.bound or d.get("denied"):
                continue
            if d.get("subagent_type") == agent_type and (session is None or d.get("session") == session):
                return tid
        return None

    def unbound_count(self, agent_type: str, session: str | None = None) -> int:
        return sum(1 for tid, d in self.dispatches.items() if tid not in self.bound and not d.get("denied")
                   and d.get("subagent_type") == agent_type and (session is None or d.get("session") == session))

    def observed(self, agent_ids: set[str]) -> dict[str, Any]:
        """Union of observations (files read/written, commands, urls) of the given agents."""
        out: dict[str, Any] = {"files": set(), "cmds": [], "urls": set(), "search_paths": set(), "mcp": [], "files_ref": set()}
        for a in agent_ids:
            o = self.obs.get(a)
            if o:
                out["files"] |= o["files"]
                out["cmds"] += o["cmds"]
                out["urls"] |= o["urls"]
                out["search_paths"] |= o["search_paths"]
                out["mcp"] += o.get("mcp", [])
                out["files_ref"] |= o.get("files_ref", set())
        return out

    def work_subtree(self, agent_id: str) -> set[str]:
        """The agent plus its descendants, excluding auditors (their checks are not the worker's evidence)."""
        out = {agent_id}
        for d in self.descendants(agent_id):
            if not self.agents[d].get("review_for") and not any(
                    self.agents.get(x, {}).get("review_for") for x in self._ancestors(d, stop=agent_id)):
                out.add(d)
        return out

    def _ancestors(self, agent_id: str, stop: str) -> list[str]:
        chain, cur = [], self.agents.get(agent_id, {}).get("parent")
        while cur and cur != stop and cur != MAIN and cur in self.agents:
            chain.append(cur)
            cur = self.agents[cur].get("parent")
        return chain

    def chain(self, agent_id: str) -> list[str]:
        """agent -> parent -> … -> main."""
        out, cur = [], agent_id
        while cur and cur != MAIN and cur not in out:
            out.append(cur)
            cur = self.agents.get(cur, {}).get("parent")
        return out + [MAIN]

    def task_ledger(self, task: str) -> dict[str, Any]:
        return self.ledger.setdefault(task, {"submitted": {}, "verdicts": {}, "final": {}, "final_by": None, "reviews": {}})

    def gate_blocks(self, agent_id: str) -> int:
        """Blocks that count against gate.max_blocks: defects only, since the agent last started or was resumed."""
        return sum(1 for b in self.agents.get(agent_id, {}).get("blocks", []) if not b["transient"])

    def transient_blocks(self, agent_id: str) -> int:
        return sum(1 for b in self.agents.get(agent_id, {}).get("blocks", []) if b["transient"])

    # --- derived state: recomputed from the ledger, never frozen --------------------------------------------------
    def claims_of(self, key: str) -> tuple[dict, str | None, bool]:
        """(claims, worker, final?) of a task: its final claims, or, if the worker never delivered a report the hooks
        saw, the latest wording it submitted for review."""
        led = self.ledger.get(key) or {}
        if led.get("final"):
            return led["final"], led.get("final_by"), True
        sub = led.get("submitted") or {}
        by = next((c.get("by") for c in reversed(list(sub.values())) if c.get("by")), None)
        return sub, by, False

    def claim_state(self, key: str, cid: str) -> tuple[str, dict | None]:
        """Latest independent verdict on the claim's *current* wording -> (state, verdict).
        state: verified | refuted | unverified | unreviewed | asserted (review skipped)."""
        led = self.ledger.get(key) or {}
        claims, worker, _ = self.claims_of(key)
        c = claims.get(cid)
        if not c:
            return "unreviewed", None
        sha = c.get("text_sha") or text_sha(c.get("text"))
        vs = [v for v in (led.get("verdicts") or {}).get(cid, [])
              if v.get("by") and v["by"] != worker and v.get("text_sha") == sha]
        if vs:
            return vs[-1]["verdict"], vs[-1]
        return ("asserted" if led.get("review") == "skip" else "unreviewed"), None

    def task_resolution(self, key: str) -> dict | None:
        """How an escalated (or blocked) task was resolved, if it was: by a human decision, or by a later audit that
        verified every final claim. None means the task's own agent states stand."""
        if key in self.resolutions:
            return self.resolutions[key]
        led = self.ledger.get(key) or {}
        final = led.get("final") or {}
        if not final or led.get("review") == "skip":
            return None
        states = {cid: self.claim_state(key, cid) for cid in final}
        if all(st == "verified" for st, _ in states.values()):
            last = max((v for _, v in states.values() if v), key=lambda v: v.get("ts") or "")
            return {"decision": "verified", "by": "re-audit", "auditor": last.get("by"), "ts": last.get("ts")}
        return None

    def _agent(self, agent_id: str) -> dict:
        return self.agents.setdefault(agent_id, {
            "id": agent_id, "parent": None, "depth": None, "type": None, "task": None, "plan": None,
            "description": None, "state": "queued", "started": None, "ended": None,
            "last_action": None, "last_ts": None, "gate_reasons": [], "gate_result": None,
            "report": None, "files_read": set(), "files_written": [], "output_path": None,
            "review_for": None, "tool_use_id": None, "blocks": [],
        })

    def _bind(self, agent_id: str, tool_use_id: str | None) -> None:
        if not tool_use_id or tool_use_id not in self.dispatches:
            return
        d = self.dispatches[tool_use_id]
        a = self._agent(agent_id)
        self.bound.add(tool_use_id)
        plan = d.get("plan")
        review_key = task_key(plan, d.get("review_for")) if d.get("review_for") else None
        a.update({
            "tool_use_id": tool_use_id, "parent": d.get("parent") or MAIN, "depth": d.get("depth"),
            "task": task_key(plan, d.get("task")) or (f"review:{review_key}" if review_key else None),
            "task_id": d.get("task") or (f"review:{d.get('review_for')}" if d.get("review_for") else None),
            "plan": plan, "description": d.get("description"), "review_for": review_key,
            "type": a.get("type") or d.get("subagent_type"), "exempt": d.get("exempt", False),
            "review": d.get("review"), "session": a.get("session") or d.get("session"),
        })

    # --- event handlers ------------------------------------------------------------
    def _on_run_start(self, e: dict) -> None:
        self.run = {"session": e.get("session"), "started": e["ts"], "run": e.get("run")}
        self.live = True
        if e.get("session") in self.sessions:
            self.sessions[e["session"]].update(run=e.get("run"), ended=None)

    def _on_run_end(self, e: dict) -> None:
        if e.get("session") in (None, self.run.get("session")):
            self.live = False
            self.run["ended"] = e["ts"]
        if e.get("session") in self.sessions:
            self.sessions[e["session"]]["ended"] = e["ts"]

    def _on_human_message(self, e: dict) -> None:
        if not e.get("data", {}).get("notification"):
            self.human_messages.append(e)
            s = self.sessions.get(e.get("session") or "")
            if s is not None and not s["title"]:
                s["title"] = (e.get("data") or {}).get("text")

    def _on_dispatch(self, e: dict) -> None:
        d = dict(e.get("data", {}))
        d.update(session=e.get("session"), ts=e["ts"], parent=e.get("agent") or MAIN)
        self.dispatches[d["tool_use_id"]] = d

    def _on_dispatch_denied(self, e: dict) -> None:
        self._on_dispatch(e)
        self.dispatches[e["data"]["tool_use_id"]]["denied"] = True

    def _on_agent_resumed(self, e: dict) -> None:
        """A parent sent a stopped agent a new message: it runs again, with a fresh block budget."""
        a = self._agent((e.get("data") or {}).get("target") or e["agent"])
        a.update(state="running", ended=None, blocks=[], gate_result=None, last_ts=e["ts"],
                 last_action="resumed", resumed_at=e["ts"])

    def _on_hitl_decision(self, e: dict) -> None:
        d = e.get("data") or {}
        if d.get("id"):
            self.hitl_decisions[d["id"]] = {**d, "ts": e["ts"], "session": e.get("session")}

    def _on_review_resolved(self, e: dict) -> None:
        d = e.get("data") or {}
        if d.get("task"):
            self.resolutions[d["task"]] = {"decision": d.get("decision"), "by": d.get("by") or "human",
                                           "note": d.get("note"), "ts": e["ts"], "item": d.get("item")}

    def _on_agent_start(self, e: dict) -> None:
        a = self._agent(e["agent"])
        a.update(type=e.get("agent_type"), started=e["ts"], state="running", last_ts=e["ts"],
                 session=a.get("session") or e.get("session"))
        if not a.get("tool_use_id"):  # a link (PostToolUse) binding is exact; never override it with a FIFO guess
            self._bind(e["agent"], e.get("data", {}).get("tool_use_id"))

    def _on_link(self, e: dict) -> None:
        child = e["data"].get("child")
        if child:
            if child not in self.agents:  # link can precede start for background launches
                self._agent(child)
            self._bind(child, e["data"].get("tool_use_id"))
            if e["data"].get("model"):
                self.agents[child]["model"] = e["data"]["model"]
            a = self.agents[child]
            if e["data"].get("status") == "completed" and a["state"] == "running" and a.get("started"):
                # The parent already has the result but no stop was recorded for the child: close it, honestly labelled.
                a.update(state="unverified", ended=e["ts"], gate_result=a.get("gate_result") or "no stop recorded",
                         last_action="finished (no stop recorded)")

    def _on_handback(self, e: dict) -> None:
        a = self._agent(e["agent"])
        a["handed_back"] = e["ts"]
        a["handback"] = (e.get("data") or {}).get("message")
        a["last_action"] = "handed back"
        a["last_ts"] = e["ts"]

    def _on_gate_block(self, e: dict) -> None:
        a = self._agent(e["agent"])
        a["gate_reasons"].append("; ".join(e["data"].get("reasons", [])))
        a.setdefault("blocks", []).append({"transient": bool(e["data"].get("transient")), "ts": e["ts"]})
        a["gate_result"] = "blocked"
        a["last_action"] = "gate: blocked"
        a["last_ts"] = e["ts"]
        if a.get("handed_back") and e["data"].get("at") != "handback":
            # Logs from plugin 0.1.0: a stop-time block after the agent had already handed back never reached it.
            a.update(state="unverified", ended=e["ts"], gate_result="escalated",
                     last_action="finished (block after hand-back was not delivered)")

    def _internal_helper(self, e: dict) -> bool:
        """Claude Code-internal helpers (e.g. prompt suggestions) have an empty agent type and were never dispatched."""
        return e.get("agent_type") == "" and e.get("agent") not in self.agents

    def _on_agent_stop(self, e: dict) -> None:
        if self._internal_helper(e):
            return
        a = self._agent(e["agent"])
        d = e.get("data", {})
        a.update(ended=e["ts"], last_ts=e["ts"], output_path=d.get("output_path"), report=d.get("report"))
        if a.get("task") and not a.get("review_for") and d.get("claims") is not None:  # a["task"] is the plan-qualified key
            led = self.task_ledger(a["task"])
            led["final"] = {c["id"]: {**c, "text_sha": c.get("text_sha") or text_sha(c.get("text"))} for c in d["claims"]}
            led["final_by"] = e["agent"]
            led["final_status"] = d.get("gate")
            led["review"] = a.get("review")
            led["final_reasons"] = d.get("reasons") or []
            led["final_ts"] = e["ts"]
        gate = d.get("gate")  # passed | escalated | exempt
        a["gate_result"] = "passed (review skipped)" if gate == "passed" and d.get("review") == "skipped" else gate
        status = (d.get("report") or {}).get("status")
        if gate == "escalated":
            a["state"] = "unverified"
        elif status in ("blocked", "failed"):
            a["state"] = status
        else:
            a["state"] = "done"
        a["last_action"] = f"finished ({a['state']})"

    def _legacy_key(self, task: str, agent: str | None) -> str:
        """Logs before 0.3.0 recorded a worker's own review under the bare id ("T1"); qualify it with the worker's plan."""
        if task and "/" not in task and not task.startswith("review:") and task not in self.ledger:
            plan = (self.agents.get(agent or "") or {}).get("plan")
            if plan:
                return task_key(plan, task)
        return task

    def _on_claims_submitted(self, e: dict) -> None:
        d = e["data"]
        led = self.task_ledger(self._legacy_key(d["task"], e.get("agent")))
        review = led.setdefault("reviews", {}).setdefault(d.get("tool_use_id") or "-", {})
        for c in d.get("claims", []):
            sha = c.get("text_sha") or text_sha(c.get("text"))
            led["submitted"][c["id"]] = {**c, "text_sha": sha, "by": e.get("agent"), "ts": e["ts"], "review": d.get("tool_use_id")}
            review[c["id"]] = sha  # what *this* review was asked to check, so parallel/re-audits never mix wordings

    def _on_verdicts(self, e: dict) -> None:
        d = e["data"]
        auditor = self.agents.get(e.get("agent") or "", {})
        task = d["task"]
        if "/" not in task and "/" in str(auditor.get("review_for") or "") and auditor["review_for"].endswith("/" + task):
            task = auditor["review_for"]
        led = self.task_ledger(task)
        asked = led.get("reviews", {}).get(auditor.get("tool_use_id") or "", {})
        for v in d.get("verdicts", []):
            sha = asked.get(v["id"]) or (led["submitted"].get(v["id"]) or {}).get("text_sha")
            led["verdicts"].setdefault(v["id"], []).append({**v, "by": e.get("agent"), "ts": e["ts"], "text_sha": sha})

    def _on_hitl(self, e: dict) -> None:
        self.hitl.append(e)

    def _on_tool(self, e: dict) -> None:
        agent = e.get("agent")
        if not agent or agent == MAIN or self._internal_helper(e):
            return
        a = self._agent(agent)
        if e.get("event") == "tool" and (e.get("data") or {}).get("tool") == "SubagentHandback":
            a["handed_back"] = e["ts"]  # 0.1.0 logged hand-backs as plain tool events
        o = self.obs.setdefault(agent, {"files": set(), "cmds": [], "urls": set(), "search_paths": set(), "mcp": []})
        data = e.get("data") or {}
        if e.get("event") in ("observe_file", "produce_file") and data.get("path"):
            o["files"].add(data["path"])
        elif e.get("event") == "exec" and data.get("command"):
            o["cmds"].append(data["command"])
            o.setdefault("files_ref", set()).update(data.get("files") or [])  # project files the command named
            o["urls"].update(u.rstrip("/") for u in data.get("urls") or [])  # fetched with curl/wget/gh api
            for w in data.get("writes") or []:  # files the command created or changed (cp, mv, tar -C, redirects…)
                o["files"].add(w)
                if w not in a["files_written"]:
                    a["files_written"].append(w)
        elif e.get("event") == "observe_source":
            if data.get("url"):
                o["urls"].add(data["url"].rstrip("/"))
        elif e.get("event") == "observe_mcp" and data.get("tool"):
            o.setdefault("mcp", []).append(data["tool"])
        elif e.get("event") == "observe_search" and data.get("path"):
            o["search_paths"].add(data["path"])
        if e.get("event") == "observe_file":
            a["files_read"].add(e["data"].get("path"))
        elif e.get("event") == "produce_file":
            p = e["data"].get("path")
            if p and p not in a["files_written"]:
                a["files_written"].append(p)
        if e.get("summary"):
            a["last_action"] = e["summary"]
            a["last_ts"] = e["ts"]

    # --- views -----------------------------------------------------------------
    def agent_list(self) -> list[dict]:
        out = []
        now = _dt.datetime.now(_dt.timezone.utc)
        for a in sorted(self.agents.values(), key=lambda x: x.get("started") or "~"):
            start, end = _ts(a["started"]), _ts(a["ended"])
            dur = int(((end or now) - start).total_seconds()) if start else None
            out.append({
                "id": a["id"], "parent": a["parent"] or MAIN, "depth": a["depth"] or 1, "type": a["type"],
                "task": a.get("task_id") or a["task"], "task_key": a["task"], "plan": a.get("plan"),
                "session": a.get("session"), "description": a["description"], "state": a["state"],
                "started": a["started"], "ended": a["ended"], "duration_s": dur,
                "last_action": a["last_action"], "last_ts": a["last_ts"],
                "gate": {"blocks": len(a["gate_reasons"]), "result": a["gate_result"] or ("exempt" if a.get("exempt") else None),
                         "reasons": a["gate_reasons"]},
                "report": a["report"], "files_read": len(a["files_read"]), "files_written": a["files_written"],
                "output_path": a["output_path"], "model": a.get("model"),
            })
        return out
