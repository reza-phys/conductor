"""Reporter core: events + plans -> status.json -> status.html (self-validated).

Deterministic and model-free, so the dashboard is live by construction: every hook event
triggers a debounced re-render in a detached process.
"""
from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from conductor import core, page, plans as plans_mod
from conductor.tree import MAIN, Tree, task_key



def _agent_label(tree: Tree, agent: str | None) -> str:
    if not agent or agent == MAIN:
        return "orchestrator"
    a = tree.agents.get(agent, {})
    return f"{(a.get('type') or 'agent').split(':')[-1]}·{agent[:6]}"


def _task_state(agent_states: list[str], deps_done: bool) -> str:
    if not agent_states:
        return "queued" if deps_done else "todo"
    if "running" in agent_states:
        return "running"
    return agent_states[-1]


def _phase_state(states: list[str]) -> str:
    if not states:
        return "todo"
    if all(s == "done" for s in states):
        return "done"
    if "running" in states:
        return "running"
    if any(s in ("blocked", "failed", "unverified") for s in states):
        return "blocked"
    if any(s == "done" for s in states):
        return "running"
    return "todo"


def _short_ids(keys) -> dict[str, str]:
    """Plan-qualified task key -> what to show: the bare id when no other plan uses it, else the full key."""
    keys = [k for k in keys if k]
    bare = {}
    for k in keys:
        bare.setdefault(k.split("/")[-1], set()).add(k)
    return {k: (k.split("/")[-1] if len(bare[k.split("/")[-1]]) == 1 else k) for k in keys}


def _ev_text(e: dict) -> str:
    return "; ".join(f"{x['kind']}:{x['ref']}" + (f":{x['lines']}" if x.get("lines") else "") for x in e)


def build_status(state: Path) -> dict:
    cfg = core.load_config(state)
    events = core.read_events(state)
    tree = Tree(events)
    agents = tree.agent_list()
    plans = plans_mod.load_plans(state)
    plan = plans_mod.active_plan(plans)
    agents_by_id = {a["id"]: a for a in agents}

    # --- tasks of the active plan (project overview), keyed by plan-qualified key -------------------------------
    by_key: dict[str, list[dict]] = {}
    for a in agents:
        k = a.get("task_key")
        if k and not str(k).startswith("review:"):
            by_key.setdefault(k, []).append(a)
    short = _short_ids(list(by_key) + list(tree.ledger) +
                       [task_key(p["id"], t["id"]) for p in plans for t in p["tasks"]])
    tasks, seen = [], set()
    planned = plan["tasks"] if plan else []
    done_keys = {k for k, ags in by_key.items() if ags and ags[-1]["state"] == "done"}
    for t in planned:
        k = task_key(plan["id"], t["id"])
        ags = by_key.get(k, [])
        deps_done = all(task_key(plan["id"], d) in done_keys for d in t["depends_on"])
        tasks.append({**t, "key": k, "plan": f"{plan['id']}@v{plan.get('version', 1)}",
                      "state": _task_state([a["state"] for a in ags], deps_done),
                      "agents": [a["id"] for a in ags], "in_plan": True})
        seen.add(k)
    for k, ags in by_key.items():
        if k not in seen:
            tasks.append({"id": short.get(k, k), "key": k, "plan": ags[0].get("plan"), "title": ags[0]["description"],
                          "phase": None, "state": _task_state([a["state"] for a in ags], True),
                          "agent_type": ags[0]["type"], "agents": [a["id"] for a in ags], "in_plan": False})

    phases = []
    for ph in (plan["phases"] if plan else []):
        pts = [t for t in tasks if t.get("in_plan") and t.get("phase") == ph["id"]]
        phases.append({**ph, "state": _phase_state([t["state"] for t in pts]), "tasks": [t["id"] for t in pts]})

    # --- human in the loop ------------------------------------------------------------------------------------------
    decisions, review_queue = [], []
    for i, e in enumerate(tree.hitl, 1):
        qs = (e.get("data") or {}).get("questions") or []
        q = "; ".join(x.get("question", "") for x in qs if isinstance(x, dict)) or "question"
        ans = (e.get("data") or {}).get("answers")
        decisions.append({"id": f"H{i}", "q": q, "state": "decided", "asked_by": "main", "ts": e["ts"],
                          "session": e.get("session"),
                          "resolution": core.clip(json.dumps(ans) if not isinstance(ans, str) else ans, 400)})
    for a in agents:
        needs = ((a.get("report") or {}).get("needs") or "").strip()
        if a["state"] == "blocked" and needs and needs.lower() != "none":
            decisions.insert(0, {"id": f"N-{a['id'][:6]}", "q": needs, "state": "open", "asked_by": a["id"],
                                 "task": a["task"], "task_key": a.get("task_key"), "session": a.get("session"),
                                 "ts": a["ended"]})
        if a["gate"]["result"] == "escalated":
            review_queue.append({"task": a["task"], "task_key": a.get("task_key"), "agent": a["id"], "state": "open",
                                 "session": a.get("session"),
                                 "reason": "gate escalated: " + "; ".join(a["gate"]["reasons"][-1:])})

    # --- claims ------------------------------------------------------------------------------------------------------
    findings = []
    for key, led in tree.ledger.items():
        for cid, c in (led.get("final") or {}).items():
            st = c.get("state") or "asserted"
            if st in ("unreviewed", "asserted") and led.get("final_status") == "escalated":
                st = "unverified"
            vs = led["verdicts"].get(cid, [])
            v = vs[-1] if vs else None
            note = f"by {_agent_label(tree, led.get('final_by'))}"
            if v:
                note += f" · {v['verdict']} by {_agent_label(tree, v.get('by'))}: {v.get('evidence') or ''}"
            by = agents_by_id.get(led.get("final_by") or "", {})
            findings.append({"id": f"{short.get(key, key)}/{cid}", "key": f"{key}/{cid}", "text": c["text"], "state": st,
                             "task": short.get(key, key), "task_key": key, "session": by.get("session"),
                             "note": note, "evidence": _ev_text(c.get("evidence", []))})

    log = []
    for e in reversed(events):
        if not e.get("summary"):
            continue
        log.append({"ts": e["ts"], "kind": e.get("event"), "agent": _agent_label(tree, e.get("agent")), "text": e["summary"]})
        if len(log) >= cfg["render"]["log_limit"]:
            break

    sessions = _sessions(cfg, tree, events, agents, plans, findings, decisions, review_queue, short)
    first_ask = next((m["data"]["text"] for m in reversed(tree.human_messages)
                      if tree.run.get("started") and m["ts"] >= tree.run["started"]), None) if tree.human_messages else None
    running = [a for a in agents if a["state"] == "running"]
    open_human = [d for d in decisions if d["state"] == "open"] + review_queue
    plan_sessions: dict[str, list[str]] = {}
    for d in tree.dispatches.values():
        pid = (d.get("plan") or "").split("@")[0]
        if pid and d.get("session") and d["session"] not in plan_sessions.setdefault(pid, []):
            plan_sessions[pid].append(d["session"])
    status = {
        "schema": 2,
        "mode": "local",
        "program": cfg["program"],
        "subtitle": f"Conductor run {tree.run.get('run') or '—'}" + (f" · plan {plan['id']}@v{plan.get('version', 1)}" if plan else ""),
        "updated": core.utcnow(),
        "live": tree.live,
        "intent": (plan or {}).get("intent") or (plan or {}).get("goal"),
        "orchestrator_task": first_ask or (plan or {}).get("goal"),
        "kill_test": {"state": "open", "text": plan["kill_test"]} if plan and plan.get("kill_test") else None,
        "summary": {
            "phases_done": sum(p["state"] == "done" for p in phases), "phases_total": len(phases),
            "agents_running": len(running), "agents_done": sum(a["state"] == "done" for a in agents),
            "agents_total": len(agents), "open_human": len(open_human),
            "unverified": sum(a["state"] == "unverified" for a in agents),
            "claims_verified": sum(f["state"] == "verified" for f in findings), "claims_total": len(findings),
        },
        "phases": phases, "tasks": tasks, "agents": agents,
        "findings": findings,
        "decisions": decisions, "review_queue": review_queue, "results": [],
        "log": log, "log_visible": cfg["render"]["log_visible"],
        "plans": [{"id": p["id"], "version": p.get("version"), "status": p.get("status"), "goal": p.get("goal"),
                   "file": p.get("file"), "sessions": plan_sessions.get(p["id"], [])} for p in reversed(plans)],
        "sessions": sessions,
        "empty_states": {"decisions": "Nothing needs you right now.", "agents": "No agents dispatched yet."},
        "footer": f"Generated from {core.STATE_DIR}/events.jsonl ({len(events)} events)",
    }
    return status


def _sessions(cfg, tree: Tree, events, agents, plans, findings, decisions, review_queue, short) -> list[dict]:
    """Per-session view model: the tasks dispatched in each session, and everything else as 'other activity'."""
    lim = cfg["render"]["task_log_limit"]
    plan_titles = {task_key(p["id"], t["id"]): t.get("title") for p in plans for t in p["tasks"]}
    by_session: dict[str, list[dict]] = {}
    for e in events:
        if e.get("session"):
            by_session.setdefault(e["session"], []).append(e)
    out = []
    order = sorted(tree.sessions.values(), key=lambda s: s.get("first") or "", reverse=True)[: cfg["render"]["max_sessions"]]
    for s in order:
        sid = s["id"]
        evs = by_session.get(sid, [])
        s_agents = [a for a in agents if a.get("session") == sid]
        humans = [e for e in evs if e["event"] == "human_message" and not (e.get("data") or {}).get("notification")]
        task_rows: dict[str, dict] = {}
        for d in sorted((d for d in tree.dispatches.values() if d.get("session") == sid and d.get("task")
                         and not d.get("denied")), key=lambda d: d.get("ts") or ""):
            key = task_key(d.get("plan"), d["task"])
            if key in task_rows:
                continue
            before = [h for h in humans if h["ts"] <= (d.get("ts") or "")]
            task_rows[key] = {
                "key": key, "id": short.get(key, d["task"]), "plan": d.get("plan"),
                "title": plan_titles.get(key) or d.get("description") or d["task"],
                "parent_task": d.get("parent_task"), "review": d.get("review"),
                "criteria": d.get("criteria"), "description": d.get("description"), "prompt": d.get("prompt"),
                "asked_after": {"ts": before[-1]["ts"], "text": core.clip((before[-1].get("data") or {}).get("text"), 600)}
                if before else None,
                "dispatched": d.get("ts"),
            }
        claimed_agents = set()
        for key, row in task_rows.items():
            mine = [a for a in s_agents if a.get("task_key") in (key, f"review:{key}")]
            ids = {a["id"] for a in mine}
            claimed_agents |= ids
            workers = [a for a in mine if not str(a.get("task_key") or "").startswith("review:")]
            row["agents"] = [a["id"] for a in mine]
            row["state"] = _task_state([a["state"] for a in workers], True) if workers else "queued"
            row["claims"] = [f for f in findings if f.get("task_key") == key]
            row["gates"] = [{"ts": e["ts"], "agent": e.get("agent"), "attempt": (e.get("data") or {}).get("attempt"),
                             "reasons": (e.get("data") or {}).get("reasons", [])}
                            for e in evs if e["event"] == "gate_block" and e.get("agent") in ids]
            files = []
            for a in mine:
                files += [f for f in a.get("files_written") or [] if f not in files]
            row["files_written"] = files
            row["log"] = [_log_row(tree, e) for e in evs if e.get("summary") and
                          (e.get("agent") in ids or e.get("task") in (key, f"review:{key}"))][-lim:]
        other_log = [_log_row(tree, e) for e in evs if e.get("summary") and e.get("agent") not in claimed_agents
                     and not (e["event"] in ("dispatch", "claims_submitted") and e.get("task"))][-lim:]
        open_items = [d for d in decisions if d["state"] == "open" and d.get("session") == sid] + \
                     [r for r in review_queue if r.get("session") == sid]
        live = bool(tree.live and tree.run.get("session") == sid and not s.get("ended"))
        out.append({
            "id": sid, "short": sid[:6], "run": s.get("run"), "started": s.get("first"),
            "ended": s.get("ended") or (None if live else s.get("last")), "live": live,
            "title": core.clip(s.get("title") or (f"Session {sid[:6]}"), 90),
            "counts": {"tasks": len(task_rows), "agents_running": sum(a["state"] == "running" for a in s_agents),
                       "open_human": len(open_items)},
            "tasks": list(task_rows.values()),
            "other": {"agents": [a["id"] for a in s_agents if a["id"] not in claimed_agents], "log": other_log},
        })
    return out


def _log_row(tree: Tree, e: dict) -> dict:
    return {"ts": e["ts"], "kind": e.get("event"), "agent": _agent_label(tree, e.get("agent")),
            "agent_id": e.get("agent"), "text": e["summary"]}


def build_artifact(state: Path) -> dict:
    """The status for a claude.ai Artifact: never polls, and drops what the project did not opt in to upload."""
    acfg = core.load_config(state)["artifact"]
    st = build_status(state)
    st["mode"] = "artifact"
    if not acfg["include_prompts"]:
        st["orchestrator_task"] = None
        for s in st["sessions"]:
            s["title"] = f"Session {s['short']}" + (f" · {s['run']}" if s.get("run") else "")
            for t in s["tasks"]:
                t["prompt"] = None
                t["asked_after"] = None
        for rows in [st["log"]] + [s["other"]["log"] for s in st["sessions"]] + \
                [t["log"] for s in st["sessions"] for t in s["tasks"]]:
            for r in rows:
                if r.get("kind") == "human_message":
                    r["text"] = "human: (message not uploaded)"
    if not acfg["include_commands"]:
        for rows in [st["log"]] + [s["other"]["log"] for s in st["sessions"]] + \
                [t["log"] for s in st["sessions"] for t in s["tasks"]]:
            for r in rows:
                if r.get("kind") == "exec":
                    r["text"] = "Bash: (command not uploaded)"
    if acfg["redact_paths"]:
        root = str(state.parent.resolve())
        st = json.loads(json.dumps(st).replace(root, "<project>"))
    st["footer"] = "Published snapshot of the Conductor ledger · updated " + st["updated"]
    return st


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def write_outputs(state: Path) -> dict:
    status = build_status(state)
    payload = json.dumps(status, ensure_ascii=False, indent=1)
    _atomic_write(state / "status.json", payload)
    if page.TEMPLATE.exists():
        html, embedded = page.render(page.TEMPLATE.read_text(encoding="utf-8"), status)
        errs = page.check(html, embedded)
        if errs:
            raise RuntimeError("status.html failed self-check: " + ", ".join(errs))
        _atomic_write(state / "status.html", html)
    return status


# --- debounced background rendering -----------------------------------------------------

def trigger(state: Path) -> None:
    sdir = state / "state"
    sdir.mkdir(exist_ok=True)
    (sdir / "render.dirty").touch()
    subprocess.Popen([sys.executable, str(Path(__file__).with_name("cli.py")), "render", "--loop", "--state", str(state)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True)


def render_loop(state: Path) -> None:
    """Render while the dirty flag is set; only one renderer runs at a time."""
    sdir = state / "state"
    dirty, lock = sdir / "render.dirty", sdir / "render.lock"
    while dirty.exists():
        with lock.open("a") as lf:
            try:
                fcntl.flock(lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return  # another renderer holds the lock and will pick up the dirty flag
            try:
                while dirty.exists():
                    time.sleep(0.15)  # coalesce bursts of events
                    dirty.unlink(missing_ok=True)
                    try:
                        write_outputs(state)
                    except Exception as e:  # keep the last good page
                        with core.locked(sdir / "hook-errors.log") as f:
                            f.write(f"{core.utcnow()} render: {e!r}\n")
            finally:
                fcntl.flock(lf, fcntl.LOCK_UN)
        # loop re-checks: an event may have landed between the last render and unlock
