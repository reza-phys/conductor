"""Stop-gate checks: is this agent's output fit to go to its parent?

Pure functions over the replayed Tree, so they are unit-testable without Claude Code.
Each returns a list of human-readable problems; an empty list means "pass".
"""
from __future__ import annotations

import re
from typing import Any

from conductor import protocol
from conductor.tree import Tree

CHECKABLE = ("file", "cmd", "url", "mcp", "claim", "hitl")


def _norm_cmd(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().strip("`")).strip()


def _norm_path(p: str) -> str:
    p = p.strip().strip("`")
    return p[2:] if p.startswith("./") else p


def evidence_observed(tree: Tree, ev: dict, seen: dict[str, Any]) -> bool:
    kind, ref = ev["kind"], ev.get("ref", "")
    if kind == "file":
        ref = _norm_path(ref)
        return any(f == ref or f.endswith("/" + ref) or ref.endswith("/" + f) for f in seen["files"] if f) \
            or ref in seen["search_paths"]
    if kind == "cmd":
        want = _norm_cmd(ref)
        return len(want) >= 3 and any(want in _norm_cmd(c) or (len(_norm_cmd(c)) >= 8 and _norm_cmd(c) in want)
                                      for c in seen["cmds"])
    if kind == "url":
        want = ref.rstrip("/")
        return any(u == want or u.startswith(want) or want.startswith(u) for u in seen["urls"])
    if kind == "mcp":
        want = ref.split()[0] if ref.split() else ""
        want = want[5:] if want.startswith("mcp__") else want
        return bool(want) and any(t[5:] == want or t[5:].endswith("__" + want) for t in seen.get("mcp", []))
    if kind == "claim":
        task, _, cid = ref.partition("/")
        led = tree.ledger.get(task, {})
        vs = led.get("verdicts", {}).get(cid, [])
        return bool(vs) and vs[-1]["verdict"] == "verified"
    return kind == "hitl"  # human answers are logged by the orchestrator, not by the agent


def check_evidence(tree: Tree, agent_id: str, claims: list[dict]) -> list[str]:
    """Anti-fabrication: cited evidence must appear in this agent's (or its workers') own observations."""
    seen = tree.observed(tree.work_subtree(agent_id))
    problems = []
    for c in claims:
        checkable = [e for e in c["evidence"] if e["kind"] in CHECKABLE]
        if not checkable:
            problems.append(f"{c['id']} has no checkable evidence (use file:, cmd:, url:, claim: or hitl:).")
            continue
        for e in checkable:
            if e["kind"] == "claim" and e["ref"].startswith("review:"):
                problems.append(f"{c['id']} cites a review verdict as evidence; cite the files, commands or sources themselves.")
                continue
            if not evidence_observed(tree, e, seen):
                if e["kind"] == "claim":
                    problems.append(f"{c['id']} cites claim:{e['ref']}, which is not a verified claim.")
                else:
                    what = {"file": "never read or wrote", "cmd": "never ran", "url": "never fetched",
                            "mcp": "never called"}[e["kind"]]
                    problems.append(f"{c['id']} cites {e['kind']}:{e['ref']} but you {what} it.")
    return problems


def claim_states(tree: Tree, task: str, worker: str, claims: list[dict]) -> tuple[dict[str, str], list[str]]:
    """Latest independent verdict per final claim -> (states, problems)."""
    led = tree.ledger.get(task, {"submitted": {}, "verdicts": {}})
    states, problems = {}, []
    for c in claims:
        sub = led["submitted"].get(c["id"])
        vs = [v for v in led["verdicts"].get(c["id"], []) if v.get("by") and v["by"] != worker]
        if not sub or not vs:
            states[c["id"]] = "unreviewed"
            problems.append(f"{c['id']} has not been reviewed by an auditor.")
            continue
        if sub.get("text", "").strip() != c["text"].strip():
            states[c["id"]] = "unreviewed"
            problems.append(f"{c['id']} changed after its review; send the new wording to an auditor.")
            continue
        v = vs[-1]
        states[c["id"]] = v["verdict"]
        if v["verdict"] == "refuted":
            problems.append(f"{c['id']} was refuted by the auditor ({v.get('evidence') or 'no detail'}). "
                            "Fix it and get it re-reviewed, or withdraw it from your report.")
    return states, problems


def check_auditor(tree: Tree, agent_id: str, task: str, verdicts: list[dict]) -> list[str]:
    led = tree.ledger.get(task, {"submitted": {}})
    asked = set(led["submitted"])
    got = {v["id"] for v in verdicts}
    problems = []
    missing = sorted(asked - got)
    if missing:
        problems.append("No verdict for " + ", ".join(missing) + ". " + protocol.VERDICTS_HELP)
    if any(v["verdict"] == "verified" for v in verdicts):
        seen = tree.observed({agent_id})
        if not (seen["files"] or seen["cmds"] or seen["urls"] or seen["search_paths"] or seen["mcp"]):
            problems.append("You marked claims verified without reading, running or fetching anything. "
                            "Check each claim against its source first.")
    return problems
