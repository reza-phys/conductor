"""Lineage queries over the event log: `conductor provenance <claim|task|agent|file>`.

Vocabulary follows W3C PROV: agents (human, orchestrator, sub-agents), activities (tasks,
tool calls) and entities (file versions, sources, claims, verdicts, plan versions).
"""
from __future__ import annotations

from conductor import core, gate
from conductor.tree import MAIN, Tree


def _label(tree: Tree, agent: str | None) -> str:
    if not agent or agent == MAIN:
        return "orchestrator"
    a = tree.agents.get(agent, {})
    return f"{(a.get('type') or 'agent').split(':')[-1]}·{agent[:6]}"


def _chain(tree: Tree, agent: str) -> str:
    parts = [_label(tree, a) for a in tree.chain(agent)]
    first = next((m["data"]["text"] for m in tree.human_messages), None)
    if first:
        parts.append(f'human: "{core.clip(first, 70)}"')
    return " → ".join(parts)


def _dispatch_of(tree: Tree, agent: str) -> dict:
    tuid = tree.agents.get(agent, {}).get("tool_use_id")
    return tree.dispatches.get(tuid, {}) if tuid else {}


def _evidence_trace(tree: Tree, ev: dict, workers: set[str]) -> str:
    kind, ref = ev["kind"], ev.get("ref", "")
    for e in tree.events:
        if e.get("agent") not in workers:
            continue
        d = e.get("data") or {}
        hit = False
        if kind == "file" and e["event"] in ("observe_file", "produce_file") and d.get("path"):
            hit = gate.evidence_observed(tree, ev, {"files": {d["path"]}, "cmds": [], "urls": set(), "search_paths": set()})
        elif kind == "cmd" and e["event"] == "exec":
            hit = gate.evidence_observed(tree, ev, {"files": set(), "cmds": [d.get("command", "")], "urls": set(), "search_paths": set()})
        elif kind == "mcp" and e["event"] == "observe_mcp" and d.get("tool"):
            hit = gate.evidence_observed(tree, ev, {"files": set(), "cmds": [], "urls": set(), "search_paths": set(), "mcp": [d["tool"]]})
        elif kind == "url" and e["event"] == "observe_source" and d.get("url"):
            hit = gate.evidence_observed(tree, ev, {"files": set(), "cmds": [], "urls": {d["url"].rstrip("/")}, "search_paths": set()})
        if hit:
            verb = {"observe_file": "read", "produce_file": "written", "exec": "ran", "observe_source": "fetched",
                    "observe_mcp": "called"}[e["event"]]
            sha = f" sha256 {d['sha256'][:12]}…" if d.get("sha256") else ""
            return f"{verb} by {_label(tree, e['agent'])} at {e['ts']}{sha}"
    if kind == "claim":
        return "verified claim" if gate.evidence_observed(tree, ev, {}) else "NOT a verified claim"
    if kind == "hitl":
        return "human answer (see decisions)"
    return "NOT OBSERVED"


def _task_keys(tree: Tree, ref: str) -> list[str]:
    """'T1' or 'P2/T1' -> matching plan-qualified task keys (newest plan last)."""
    keys = {a.get("task") for a in tree.agents.values() if a.get("task")} | set(tree.ledger)
    keys = {k for k in keys if not str(k).startswith("review:")}
    return sorted(k for k in keys if k == ref or k.endswith("/" + ref))


def _state(tree: Tree, task: str, cid: str) -> str:
    """Current state of a final claim: the latest independent verdict on its exact wording (same as the dashboard)."""
    if cid not in ((tree.ledger.get(task) or {}).get("final") or {}):
        return "submitted"
    return tree.claim_state(task, cid)[0]


def claim(tree: Tree, ref: str) -> list[str]:
    hit = gate.resolve_claim(tree, ref)
    if not hit:
        return [f"No claim {ref}."]
    task, cid = hit
    led = tree.ledger.get(task)
    c = (led or {}).get("final", {}).get(cid) or (led or {}).get("submitted", {}).get(cid)
    if not c:
        return [f"No claim {ref}."]
    by = led.get("final_by") or c.get("by")
    a = tree.agents.get(by or "", {})
    disp = _dispatch_of(tree, by) if by else {}
    out = [f'Claim {ref}: "{c["text"]}"  [{_state(tree, task, cid)}]',
           f"  asserted by: {_label(tree, by)} · task {task} · plan {a.get('plan') or '-'} · finished {a.get('ended') or '-'}",
           f"  chain:       {_chain(tree, by) if by else '-'}"]
    if disp.get("criteria"):
        out.append(f"  criteria:    {core.clip(disp['criteria'], 200)}")
    out.append("  evidence:")
    workers = tree.work_subtree(by) if by else set()
    for ev in c.get("evidence", []):
        lines = f":{ev['lines']}" if ev.get("lines") else ""
        out.append(f"    {ev['kind']}:{ev['ref']}{lines} — {_evidence_trace(tree, ev, workers)}")
    vs = led.get("verdicts", {}).get(cid, [])
    out.append("  verdicts:" if vs else "  verdicts:    none")
    for v in vs:
        stale = "" if v.get("text_sha") in (None, c.get("text_sha")) else " (earlier wording)"
        out.append(f"    {v['verdict']} by {_label(tree, v.get('by'))} at {v['ts']}{stale}: {v.get('evidence') or ''}")
    if a.get("output_path"):
        out.append(f"  report:      {a['output_path']}")
    return out


def task(tree: Tree, ref: str) -> list[str]:
    keys = _task_keys(tree, ref)
    if len(keys) > 1:
        return [f"{ref} is ambiguous; it exists in several plans: " + ", ".join(keys) + ". Ask for one of these."]
    tid = keys[0] if keys else ref
    agents = [a for a in tree.agents.values() if a.get("task") == tid or a.get("review_for") == tid]
    if not agents:
        return [f"No task {ref}."]
    out = [f"Task {tid}"]
    for a in agents:
        d = _dispatch_of(tree, a["id"])
        role = "review" if a.get("review_for") else "work"
        out.append(f"  {role}: {_label(tree, a['id'])} [{a['state']}] dispatched by {_label(tree, a.get('parent'))} "
                   f"at {d.get('ts', '-')} · gate {a.get('gate_result') or '-'}")
    res = tree.task_resolution(tid)
    if res:
        out.append(f"  resolved: {res.get('decision')} by {res.get('by')} at {res.get('ts') or '-'}"
                   + (f" ({res['note']})" if res.get("note") else ""))
        if d.get("criteria") and role == "work":
            out.append(f"    criteria: {core.clip(d['criteria'], 200)}")
    for cid in sorted((tree.ledger.get(tid) or {}).get("final", {})):
        c = tree.ledger[tid]["final"][cid]
        out.append(f"  claim {tid}/{cid} [{_state(tree, tid, cid)}]: {core.clip(c['text'], 100)}")
    return out


def agent(tree: Tree, aid: str) -> list[str]:
    matches = [k for k in tree.agents if k.startswith(aid)]
    if len(matches) != 1:
        return [f"{'No' if not matches else 'Ambiguous'} agent {aid}."]
    a = tree.agents[matches[0]]
    o = tree.observed({a["id"]})
    out = [f"Agent {_label(tree, a['id'])} ({a['id']}) [{a['state']}] task {a.get('task') or '-'}",
           f"  chain:   {_chain(tree, a['id'])}",
           f"  read:    {', '.join(sorted(a['files_read'])) or '-'}",
           f"  wrote:   {', '.join(a['files_written']) or '-'}",
           f"  ran:     {'; '.join(core.clip(x, 60) for x in o['cmds']) or '-'}",
           f"  gate:    {a.get('gate_result') or '-'}" + (f" after {len(a['gate_reasons'])} block(s)" if a["gate_reasons"] else "")]
    for r in a["gate_reasons"]:
        out.append(f"    blocked: {core.clip(r, 160)}")
    if a.get("output_path"):
        out.append(f"  report:  {a['output_path']}")
    return out


def file(tree: Tree, path: str) -> list[str]:
    path = path[2:] if path.startswith("./") else path
    out = [f"File {path}"]
    for e in tree.events:
        d = e.get("data") or {}
        if e["event"] in ("produce_file", "observe_file") and d.get("path") == path:
            verb = "written" if e["event"] == "produce_file" else "read"
            sha = f" sha256 {d['sha256'][:12]}…" if d.get("sha256") else ""
            out.append(f"  {verb} by {_label(tree, e.get('agent'))} [task {e.get('task') or '-'}] at {e['ts']}{sha}")
    for t, led in tree.ledger.items():
        for cid, c in (led.get("final") or {}).items():
            if any(ev["kind"] == "file" and ev["ref"].lstrip("./") == path for ev in c.get("evidence", [])):
                out.append(f"  cited by claim {t}/{cid} [{_state(tree, t, cid)}]: {core.clip(c['text'], 80)}")
    return out if len(out) > 1 else [f"No recorded activity for {path}."]


def query(tree: Tree, ref: str) -> list[str]:
    if "/" in ref and ref.rsplit("/", 1)[1][:1] == "C" and gate.resolve_claim(tree, ref):
        return claim(tree, ref)
    if _task_keys(tree, ref):
        return task(tree, ref)
    if any(k.startswith(ref) for k in tree.agents) and len(ref) >= 4:
        return agent(tree, ref)
    return file(tree, ref)
