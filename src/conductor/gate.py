"""Stop-gate checks: is this agent's output fit to go to its parent?

Pure functions over the replayed Tree, so they are unit-testable without Claude Code.
Each returns a list of human-readable problems; an empty list means "pass".
"""
from __future__ import annotations

import difflib
import re
from typing import Any

from conductor import protocol
from conductor.tree import Tree, task_key, text_sha

CHECKABLE = ("file", "cmd", "url", "mcp", "claim", "hitl")


def _norm_cmd(s: str) -> str:
    return re.sub(r"\s+", " ", s.strip().strip("`")).strip()


def _norm_path(p: str) -> str:
    p = p.strip().strip("`")
    return p[2:] if p.startswith("./") else p


def _independently_verified(tree: Tree, agent_id: str, claim: dict) -> bool:
    """An auditor other than this agent verified this exact wording: anti-fabrication is then already covered."""
    a = tree.agents.get(agent_id, {})
    led = tree.ledger.get(a.get("task") or "", {})
    sha = text_sha(claim.get("text"))
    return any(v["verdict"] == "verified" and v.get("text_sha") == sha and v.get("by") != agent_id
               for v in (led.get("verdicts") or {}).get(claim["id"], []))


def _nearest(ref: str, candidates) -> str | None:
    hit = difflib.get_close_matches(ref, [c for c in candidates if c], n=1, cutoff=0.6)
    return hit[0] if hit else None


def _match_file(ref: str, files) -> bool:
    return any(f == ref or f.endswith("/" + ref) or ref.endswith("/" + f) for f in files if f)


def resolve_claim(tree: Tree, ref: str, plan: str | None = None) -> tuple[str, str] | None:
    """'T2/C1', 'P2/T2/C1' or 'T2.1/C1' -> (ledger task key, claim id), qualifying with `plan` when needed."""
    task, _, cid = ref.rpartition("/")
    if not task or not cid:
        return None
    for key in (task, task_key(plan, task)):
        if key and key in tree.ledger:
            return key, cid
    hits = [k for k in tree.ledger if k.endswith("/" + task)]
    return (hits[-1], cid) if hits else None


def evidence_observed(tree: Tree, ev: dict, seen: dict[str, Any], plan: str | None = None) -> bool:
    kind, ref = ev["kind"], ev.get("ref", "")
    if kind == "file":
        ref = _norm_path(ref)
        return _match_file(ref, seen["files"]) or _match_file(ref, seen.get("files_ref", ())) \
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
        hit = resolve_claim(tree, ref, plan)
        vs = tree.ledger[hit[0]].get("verdicts", {}).get(hit[1], []) if hit else []
        return bool(vs) and vs[-1]["verdict"] == "verified"
    return kind == "hitl"  # human answers are logged by the orchestrator, not by the agent


def check_evidence(tree: Tree, agent_id: str, claims: list[dict]) -> list[str]:
    """Anti-fabrication: cited evidence must appear in this agent's (or its workers') own observations."""
    seen = tree.observed(tree.work_subtree(agent_id))
    plan = tree.agents.get(agent_id, {}).get("plan")
    problems = []
    for c in claims:
        checkable = [e for e in c["evidence"] if e["kind"] in CHECKABLE]
        if not checkable:
            problems.append(f"{c['id']} has no checkable evidence (use file:, cmd:, url:, mcp:, claim: or hitl:).")
            continue
        if _independently_verified(tree, agent_id, c):
            continue
        for e in checkable:
            if e["kind"] == "claim" and e["ref"].startswith("review:"):
                problems.append(f"{c['id']} cites a review verdict as evidence; cite the files, commands or sources themselves.")
                continue
            if not evidence_observed(tree, e, seen, plan):
                if e["kind"] == "claim":
                    problems.append(f"{c['id']} cites claim:{e['ref']}, which is not a verified claim.")
                else:
                    what = {"file": "never read or wrote", "cmd": "never ran", "url": "never fetched",
                            "mcp": "never called"}[e["kind"]]
                    pool = {"file": seen["files"] | seen.get("files_ref", set()), "url": seen["urls"],
                            "cmd": seen["cmds"], "mcp": seen.get("mcp", [])}[e["kind"]]
                    near = _nearest(e["ref"], pool)
                    hint = f" (did you mean {e['kind']}:{near}, which you did?)" if near else ""
                    problems.append(f"{c['id']} cites {e['kind']}:{e['ref']} but you {what} it{hint}.")
    return problems


def claim_states(tree: Tree, task: str, worker: str, claims: list[dict]) -> tuple[dict[str, str], list[str]]:
    """Latest independent verdict per final claim -> (states, problems)."""
    led = tree.ledger.get(task, {"submitted": {}, "verdicts": {}})
    states, problems = {}, []
    for c in claims:
        sha = text_sha(c["text"])
        indep = [v for v in led["verdicts"].get(c["id"], []) if v.get("by") and v["by"] != worker]
        vs = [v for v in indep if v.get("text_sha") == sha]  # verdicts on this exact wording
        if not vs:
            states[c["id"]] = "unreviewed"
            problems.append(f"{c['id']} changed after its review; send the new wording to an auditor." if indep
                            else f"{c['id']} has not been reviewed by an auditor.")
            continue
        v = vs[-1]
        states[c["id"]] = v["verdict"]
        if v["verdict"] == "refuted":
            problems.append(f"{c['id']} was refuted by the auditor ({v.get('evidence') or 'no detail'}). "
                            "Fix it and get it re-reviewed, or withdraw it from your report.")
    return states, problems


def check_auditor(tree: Tree, agent_id: str, task: str, verdicts: list[dict]) -> list[str]:
    """The auditor must give a verdict for every claim *in its own review* (a re-audit may cover only some)."""
    led = tree.ledger.get(task, {"submitted": {}})
    mine = tree.agents.get(agent_id, {}).get("tool_use_id")
    asked = {cid for cid, s in led["submitted"].items() if mine and s.get("review") == mine} or set(led["submitted"])
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
