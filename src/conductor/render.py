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
from conductor.tree import MAIN, Tree



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


def build_status(state: Path) -> dict:
    cfg = core.load_config(state)
    events = core.read_events(state)
    tree = Tree(events)
    agents = tree.agent_list()
    plans = plans_mod.load_plans(state)
    plan = plans_mod.active_plan(plans)

    # Tasks: planned ones first, then ad-hoc dispatches that carried a task id.
    by_task: dict[str, list[dict]] = {}
    for a in agents:
        if a["task"] and not str(a["task"]).startswith("review:"):
            by_task.setdefault(a["task"], []).append(a)
    tasks, seen = [], set()
    planned = plan["tasks"] if plan else []
    done_ids = {t for t, ags in by_task.items() if ags and ags[-1]["state"] == "done"}
    for t in planned:
        ags = by_task.get(t["id"], [])
        deps_done = all(d in done_ids for d in t["depends_on"])
        tasks.append({**t, "state": _task_state([a["state"] for a in ags], deps_done),
                      "agents": [a["id"] for a in ags], "in_plan": True})
        seen.add(t["id"])
    for tid, ags in by_task.items():
        if tid not in seen:
            tasks.append({"id": tid, "title": ags[0]["description"], "phase": None, "state": _task_state([a["state"] for a in ags], True),
                          "agent_type": ags[0]["type"], "agents": [a["id"] for a in ags], "in_plan": False})

    phases = []
    for ph in (plan["phases"] if plan else []):
        pts = [t for t in tasks if t.get("phase") == ph["id"]]
        phases.append({**ph, "state": _phase_state([t["state"] for t in pts]), "tasks": [t["id"] for t in pts]})

    decisions, review_queue = [], []
    for i, e in enumerate(tree.hitl, 1):
        qs = (e.get("data") or {}).get("questions") or []
        q = "; ".join(x.get("question", "") for x in qs if isinstance(x, dict)) or "question"
        ans = (e.get("data") or {}).get("answers")
        decisions.append({"id": f"H{i}", "q": q, "state": "decided", "asked_by": "main", "ts": e["ts"],
                          "resolution": core.clip(json.dumps(ans) if not isinstance(ans, str) else ans, 400)})
    for a in agents:
        needs = ((a.get("report") or {}).get("needs") or "").strip()
        if a["state"] == "blocked" and needs and needs.lower() != "none":
            decisions.insert(0, {"id": f"N-{a['id'][:6]}", "q": needs, "state": "open", "asked_by": a["id"],
                                 "task": a["task"], "ts": a["ended"]})
        if a["gate"]["result"] == "escalated":
            review_queue.append({"task": a["task"], "agent": a["id"], "state": "open",
                                 "reason": "gate escalated: " + "; ".join(a["gate"]["reasons"][-1:])})

    findings = []
    for task, led in tree.ledger.items():
        for cid, c in (led.get("final") or {}).items():
            st = c.get("state") or "asserted"
            if st in ("unreviewed", "asserted") and led.get("final_status") == "escalated":
                st = "unverified"
            vs = led["verdicts"].get(cid, [])
            v = vs[-1] if vs else None
            note = f"by {_agent_label(tree, led.get('final_by'))}"
            if v:
                note += f" · {v['verdict']} by {_agent_label(tree, v.get('by'))}: {v.get('evidence') or ''}"
            findings.append({"id": f"{task}/{cid}", "text": c["text"], "state": st, "task": task, "note": note,
                             "evidence": "; ".join(f"{e['kind']}:{e['ref']}" + (f":{e['lines']}" if e.get("lines") else "")
                                                   for e in c.get("evidence", []))})

    log = []
    for e in reversed(events):
        if not e.get("summary"):
            continue
        log.append({"ts": e["ts"], "kind": e.get("event"), "agent": _agent_label(tree, e.get("agent")), "text": e["summary"]})
        if len(log) >= cfg["render"]["log_limit"]:
            break

    first_ask = next((m["data"]["text"] for m in reversed(tree.human_messages)
                      if tree.run.get("started") and m["ts"] >= tree.run["started"]), None) if tree.human_messages else None
    running = [a for a in agents if a["state"] == "running"]
    open_human = [d for d in decisions if d["state"] == "open"] + review_queue
    status = {
        "schema": 1,
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
        "empty_states": {"decisions": "Nothing needs you right now.", "agents": "No agents dispatched yet."},
        "footer": f"Generated from {core.STATE_DIR}/events.jsonl ({len(events)} events)",
    }
    return status


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
