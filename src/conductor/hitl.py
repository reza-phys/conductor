"""Human-in-the-loop items: one concise shape for escalations, worker questions and AskUserQuestion answers.

Every item says what is being asked (title, problem), what the options are (each with a copyable reply), and,
once decided, what was decided. Decisions are events in the log (`hitl_decision`, `review_resolved`), so an
item moves from the review queue to Decisions on the next render, everywhere at once.
"""
from __future__ import annotations

import json
import re
from typing import Any

from conductor import core
from conductor.tree import MAIN, Tree

PASTE_PREFIX = "[Conductor HITL "
_PASTE = re.compile(r"\[Conductor HITL (?P<id>[^\]\s]+)\][^\n]*?Decision:\s*(?P<decision>.+?)"
                    r"(?:\s+Note:\s*(?P<note>.+?))?\s*$", re.M)

# gate reason -> category label (order = display order)
CATEGORIES = [
    ("evidence not observed", re.compile(r"never (read or wrote|fetched|ran|called)|no checkable evidence|cites a review verdict")),
    ("claims reworded after review", re.compile(r"changed after its review")),
    ("claims not reviewed", re.compile(r"has not been reviewed")),
    ("refuted claims", re.compile(r"was refuted")),
    ("auditor skipped claims", re.compile(r"No verdict for")),
    ("sub-agents still running or verdicts pending", re.compile(r"still running|Verdicts pending")),
    ("malformed report", re.compile(r"Missing or malformed report")),
    ("no claims", re.compile(r"needs at least one claim")),
    ("wrong task id", re.compile(r"your report says task", re.I)),
    ("verified without checking", re.compile(r"without reading, running or fetching")),
]


def _short_key(key: str) -> str:
    return key.split("/")[-1] if key else key


def categorize(reasons: list[str]) -> list[dict]:
    out = []
    text = [r for reason in reasons for r in re.split(r"(?<=\.)\s*;\s*|\n-\s*", reason) if r.strip()]
    for label, rx in CATEGORIES:
        hits = [r for r in text if rx.search(r)]
        if not hits:
            continue
        claims = {c for r in hits for c in re.findall(r"\bC\d+\b", r)}
        out.append({"label": label, "count": len(claims) or len(hits)})
    return out


def tally(tree: Tree, key: str) -> tuple[str, dict[str, int]]:
    claims, _, final = tree.claims_of(key)
    counts: dict[str, int] = {}
    for cid in claims:
        st, _ = tree.claim_state(key, cid)
        counts[st] = counts.get(st, 0) + 1
    n = sum(counts.values())
    if not n:
        return "no claims recorded", counts
    order = ["verified", "refuted", "unverified", "unreviewed", "asserted"]
    parts = [f"{counts[s]}/{n} {s}" if i == 0 else f"{counts[s]} {s}" for i, s in enumerate(o for o in order if counts.get(o))]
    return ", ".join(parts) + ("" if final else " (as submitted; no final report)"), counts


def _paste(item_id: str, problem_line: str, label: str) -> str:
    return f"{PASTE_PREFIX}{item_id}] Problem: {problem_line}. Decision: {label}."


def _record(item: dict) -> str:
    who = item.get("decided_by") or "human"
    when = (item.get("decided_ts") or "")[11:16]
    return (f"{item['id']} · Problem: {item['title']} · Decided: {item.get('decision')}"
            + (f" · Note: {item['note']}" if item.get("note") else "") + f" · by {who}" + (f" {when}" if when else ""))


def review_item(tree: Tree, key: str, agent: dict) -> dict:
    short = _short_key(key)
    ta = tree.agents.get(agent["id"], {})
    reasons = (tree.ledger.get(key) or {}).get("final_reasons") or (ta.get("gate_reasons") or [])[-1:]
    cats = categorize(reasons)
    tl, counts = tally(tree, key)
    n_blocks = tree.gate_blocks(agent["id"])
    how = f"escalated after {n_blocks} gate block{'s' if n_blocks != 1 else ''}" if n_blocks else "ended unverified"
    title = core.clip(f"{short} {how} — accept, re-audit or redo?", 90)
    why = "; ".join(f"{c['label']} ({c['count']})" for c in cats) or "see details"
    problem = f"The gate stopped retrying {short}. Reasons: {why}. Latest audit: {tl}."
    line = f"{short} escalated ({why}; latest audit {tl})"
    item_id = f"R-{key}"
    if counts and counts.get("verified") == sum(counts.values()):
        suggested, why_s = "accept", "Every final claim has a matching 'verified' verdict from an independent auditor."
    elif counts.get("refuted"):
        suggested, why_s = "redo", "At least one final claim was refuted by an auditor."
    else:
        suggested, why_s = "reaudit", "Some final claims have no verdict on their current wording yet."
    options = [
        {"id": "accept", "label": f"Accept {short} as done", "consequence": f"Marks {short} done; claims keep their audit state."},
        {"id": "reaudit", "label": f"Re-audit {short}", "consequence": "The orchestrator sends a fresh auditor with the final claims."},
        {"id": "redo", "label": f"Redo {short}", "consequence": "A new worker redoes the task; the current output is set aside."},
    ]
    for o in options:
        o["recommended"] = o["id"] == suggested
        o["paste"] = _paste(item_id, line, o["label"])
    return {"id": item_id, "kind": "review", "state": "open", "title": title, "problem": problem,
            "context": {"task": key, "agent": agent["id"], "claims": tl, "since": agent.get("ended"),
                        "session": agent.get("session")},
            "categories": cats, "options": options, "suggested": suggested, "why_suggested": why_s,
            "details": "\n".join(reasons), "decision": None, "note": None, "decided_by": None, "decided_ts": None,
            "record": None}


def needs_item(agent: dict) -> dict:
    needs = ((agent.get("report") or {}).get("needs") or "").strip()
    item_id = f"N-{agent['id'][:6]}"
    who = f"{(agent.get('type') or 'agent').split(':')[-1]} {agent['id'][:6]}"
    task = agent.get("task_key") or agent.get("task")
    title = core.clip(needs if needs.endswith("?") else needs + "?", 90)
    line = core.clip(f"{who} on {_short_key(task or '')} asks: {needs}", 160)
    options = [
        {"id": "answer", "label": "Answer and resume", "recommended": True,
         "consequence": f"The orchestrator resumes {who} with your answer.",
         "paste": f"{PASTE_PREFIX}{item_id}] Problem: {line}. Decision: Answer and resume. Note: <your answer>"},
        {"id": "stop", "label": "Stop this task", "recommended": False,
         "consequence": "The worker is not resumed; the task stays blocked.",
         "paste": _paste(item_id, line, "Stop this task")},
    ]
    return {"id": item_id, "kind": "needs", "state": "open", "title": title,
            "problem": f"{who} stopped on {_short_key(task or '-')} and needs a decision: {core.clip(needs, 300)}",
            "context": {"task": task, "agent": agent["id"], "claims": None, "since": agent.get("ended"),
                        "session": agent.get("session")},
            "categories": [], "options": options, "suggested": "answer",
            "why_suggested": "The worker cannot continue without this answer.",
            "details": ((agent.get("report") or {}).get("summary") or ""), "decision": None, "note": None,
            "decided_by": None, "decided_ts": None, "record": None}


def _answers(ans: Any, questions: list[dict]) -> dict[str, str]:
    """AskUserQuestion response -> {question: chosen label(s) or free text}. Tolerates the shapes seen in practice."""
    if isinstance(ans, dict):
        inner = ans.get("answers", ans)
        if isinstance(inner, dict):
            return {str(k): (", ".join(v) if isinstance(v, list) else str(v)) for k, v in inner.items()
                    if k not in ("questions", "annotations")}
        ans = json.dumps(ans)
    if isinstance(ans, list):
        return {q.get("question", f"Q{i}"): str(a) for i, (q, a) in enumerate(zip(questions, ans))}
    if isinstance(ans, str):
        found = dict(re.findall(r'"([^"]+)"="([^"]*)"', ans))
        if found:
            return found
        return {questions[0].get("question", "Q"): ans} if questions else {}
    return {}


def decision_items(tree: Tree) -> list[dict]:
    out = []
    for i, e in enumerate(tree.hitl, 1):
        d = e.get("data") or {}
        qs = [q for q in (d.get("questions") or []) if isinstance(q, dict)]
        chosen = _answers(d.get("answers"), qs)
        parts, picks = [], []
        for q in qs:
            head = q.get("header")
            parts.append((f"{head}: " if head else "") + q.get("question", ""))
            picks.append(chosen.get(q.get("question", ""), "—"))
        title = core.clip(" · ".join(parts) or "question", 90)
        decision = "; ".join(picks) if picks else "; ".join(chosen.values()) or "—"
        item = {"id": f"H{i}", "kind": "decision", "state": "decided", "title": title,
                "problem": core.clip(" ".join(parts), 400),
                "context": {"task": None, "agent": MAIN, "claims": None, "since": e["ts"], "session": e.get("session")},
                "categories": [],
                "options": [{"id": f"o{j}", "label": o.get("label"), "consequence": o.get("description"),
                             "recommended": False, "paste": None} for q in qs for j, o in enumerate(q.get("options") or [])],
                "suggested": None, "why_suggested": None,
                "details": json.dumps(d.get("answers"), ensure_ascii=False, indent=1) if d.get("answers") is not None else "",
                "decision": decision, "note": None, "decided_by": "human", "decided_ts": e["ts"]}
        item["record"] = _record(item)
        out.append(item)
    return out


def open_and_decided(tree: Tree, agents: list[dict]) -> tuple[list[dict], list[dict]]:
    """(open items for the review queue, decided items) from the replayed tree."""
    items = []
    for a in agents:
        key = a.get("task_key")
        # an auditor's own escalation is part of its task's item, not a separate question
        if a["gate"]["result"] and str(a["gate"]["result"]).startswith("escalated") and key \
                and not str(key).startswith("review:") and not tree.agents.get(a["id"], {}).get("review_for"):
            items.append(review_item(tree, key, a))
        needs = ((a.get("report") or {}).get("needs") or "").strip()
        if a["state"] == "blocked" and needs and needs.lower() != "none":
            items.append(needs_item(a))
    seen, open_, decided = set(), [], []
    for it in items:
        if it["id"] in seen:
            continue
        seen.add(it["id"])
        dec = tree.hitl_decisions.get(it["id"])
        key = (it.get("context") or {}).get("task")
        res = tree.task_resolution(key) if it["kind"] == "review" and key else None
        if dec and not _decision_stands(tree, key, dec):
            dec = None
        if dec and res and res.get("item") != it["id"] and str(res.get("ts") or "") > str(dec.get("ts") or ""):
            dec = None  # a later resolution (e.g. the re-audit the human asked for) is the better record
        if dec:
            opt = next((o for o in it["options"] if o["id"] == dec.get("option")), None)
            it.update(state="decided", decision=(opt or {}).get("label") or dec.get("decision"), note=dec.get("note"),
                      decided_by=dec.get("by") or "human", decided_ts=dec.get("ts"))
        elif res:
            label = {"verified": "Verified by re-audit", "accept": f"Accepted {_short_key(key)} as done"}.get(
                res.get("decision"), str(res.get("decision")))
            it.update(state="decided", decision=label, note=res.get("note"), decided_by=res.get("by"),
                      decided_ts=res.get("ts"))
        if it["state"] == "decided":
            it["record"] = _record(it)
            decided.append(it)
        else:
            open_.append(it)
    return open_, decided


def _decision_stands(tree: Tree, key: str | None, dec: dict) -> bool:
    """A decision closes its item only until something newer reopens it: a later escalation of the same task (e.g.
    after a redo), or, for a re-audit, newer verdicts that did not verify every final claim."""
    led = tree.ledger.get(key) or {} if key else {}
    ts = str(dec.get("ts") or "")
    if str(led.get("final_ts") or "") > ts:
        return False
    if dec.get("option") == "reaudit":
        newer = [v for vs in (led.get("verdicts") or {}).values() for v in vs if str(v.get("ts") or "") > ts]
        if newer and not tree.task_resolution(key):
            return False
    return True


def decidable(tree: Tree, agents: list[dict]) -> list[dict]:
    """Items a human may (still) decide: the open ones, plus decided ones whose decision is still in progress
    (a requested re-audit or redo), so the human can change their mind."""
    open_, decided = open_and_decided(tree, agents)
    return open_ + [i for i in decided if (tree.hitl_decisions.get(i["id"]) or {}).get("option") in ("reaudit", "redo")
                    and i.get("decided_by") == "human"]


# --- deciding -----------------------------------------------------------------------------------------------------

def parse_paste(text: str) -> list[dict]:
    return [{"id": m.group("id"), "decision": m.group("decision").strip().rstrip("."),
             "note": (m.group("note") or "").strip() or None} for m in _PASTE.finditer(text or "")]


def match_option(item: dict, decision: str) -> str | None:
    d = (decision or "").lower()
    for o in item["options"]:
        if d.startswith((o.get("label") or "").lower()) or d == o["id"]:
            return o["id"]
    for oid, words in (("accept", ("accept",)), ("reaudit", ("re-audit", "reaudit")), ("redo", ("redo",)),
                       ("answer", ("answer",)), ("stop", ("stop",))):
        if any(w in d for w in words) and any(o["id"] == oid for o in item["options"]):
            return oid
    return None


def reaudit_envelope(tree: Tree, key: str) -> str | None:
    """A ready-to-send review envelope with the task's final claims, worded exactly as they were reported."""
    final, by, _ = tree.claims_of(key)
    if not final:
        return None
    worker = tree.agents.get(by or "", {})
    disp = tree.dispatches.get(worker.get("tool_use_id") or "", {})
    lines = []
    for cid, c in final.items():
        ev = "; ".join(f"{e['kind']}:{e['ref']}" + (f":{e['lines']}" if e.get("lines") else "") for e in c.get("evidence", []))
        lines.append(f"- {cid}: {c['text']}" + (f" | evidence: {ev}" if ev else ""))
    return (f'<conductor-review for="{key}">\n' + (f"criteria: {disp.get('criteria')}\n" if disp.get("criteria") else "")
            + "claims:\n" + "\n".join(lines) + "\n</conductor-review>")


def decide(tree: Tree, items: list[dict], item_id: str, option: str | None, decision: str, note: str | None,
           by: str = "human") -> tuple[list[dict], str]:
    """Events to append and a one-paragraph instruction for the orchestrator. Raises KeyError for unknown items."""
    item = next((i for i in items if i["id"] == item_id), None)
    if item is None:
        raise KeyError(item_id)
    option = option or match_option(item, decision)
    label = next((o["label"] for o in item["options"] if o["id"] == option), decision)
    events = [{"event": "hitl_decision", "summary": f"human decided {item_id}: {label}",
               "data": {"id": item_id, "option": option, "decision": label, "note": note, "by": by}}]
    key = (item.get("context") or {}).get("task")
    agent = (item.get("context") or {}).get("agent")
    if item["kind"] == "review" and option == "accept":
        events.append({"event": "review_resolved", "summary": f"{key} accepted as done",
                       "data": {"task": key, "decision": "accept", "by": by, "note": note, "item": item_id}})
        msg = f"Recorded: the human accepted {key} as done. Its claims keep their audit state; nothing to dispatch."
    elif item["kind"] == "review" and option == "reaudit":
        env = reaudit_envelope(tree, key)
        msg = (f"The human chose to re-audit {key}. Dispatch subagent_type \"conductor:auditor\" with this envelope, "
               f"unchanged:\n{env}" if env else f"The human chose to re-audit {key}, but no final claims are recorded.")
    elif item["kind"] == "review" and option == "redo":
        msg = (f"The human chose to redo {key}. Dispatch a new conductor:worker for it with the same task envelope "
               "(id, plan, criteria); treat the previous output as superseded.")
    elif item["kind"] == "needs" and option == "answer":
        msg = (f"The human answered {item_id}: {note or decision}. Resume agent {agent} with SendMessage, passing this "
               "answer verbatim.")
    elif item["kind"] == "needs" and option == "stop":
        msg = f"The human chose to stop the task behind {item_id}. Do not resume agent {agent}; report the task as blocked."
    else:
        msg = f"The human decided {item_id}: {label}" + (f" (note: {note})" if note else "") + ". Act on it."
    return events, f"[Conductor] {msg}"
