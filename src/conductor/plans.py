"""Plan files: .conductor/plans/P<n>-<slug>.md (current) and P<n>-<slug>.v<k>.md (immutable history).

Format — flat front-matter plus two markdown tables, readable by humans and diffable:

    ---
    id: P1
    version: 2
    status: active            # draft | active | done | abandoned
    goal: one sentence
    intent: what the run is trying to achieve right now
    kill_test: stop if …
    ---
    ## Phases
    | id  | name | note |
    ## Tasks
    | id | title | phase | agent | depends | criteria |

Plans hold intent only. Task *state* is derived from events, never written here.
"""
from __future__ import annotations

import re
from pathlib import Path

_VERSIONED = re.compile(r"\.v\d+\.md$")


def _front_matter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m.group(1).splitlines():
        kv = re.match(r"^\s*([A-Za-z_][\w-]*)\s*:\s*(.*?)\s*(?:#.*)?$", line)
        if kv:
            meta[kv.group(1)] = kv.group(2).strip().strip('"')
    return meta, m.group(2)


def _tables(body: str) -> dict[str, list[dict]]:
    """Map lower-cased '## Heading' -> rows of the first markdown table under it."""
    out: dict[str, list[dict]] = {}
    for sec in re.split(r"^##\s+", body, flags=re.M)[1:]:
        heading, _, rest = sec.partition("\n")
        rows = [ln.strip() for ln in rest.splitlines() if ln.strip().startswith("|")]
        if len(rows) < 2:
            continue
        cells = lambda ln: [c.strip() for c in ln.strip().strip("|").split("|")]  # noqa: E731
        header = [h.lower() for h in cells(rows[0])]
        out[heading.strip().lower()] = [dict(zip(header, cells(r))) for r in rows[2:]]
    return out


def _get(row: dict, *keys: str) -> str | None:
    for k in keys:
        if row.get(k):
            return row[k]
    return None


def validate(path: Path) -> list[str]:
    """Problems with a plan file, phrased as instructions to the orchestrator."""
    meta, body = _front_matter(path.read_text(encoding="utf-8"))
    t = _tables(body)
    problems = []
    for k in ("id", "version", "status", "goal"):
        if not meta.get(k):
            problems.append(f"front-matter is missing `{k}:`")
    if "tasks" not in t:
        problems.append("no `## Tasks` table (columns: id | title | phase | agent | depends | criteria)")
    else:
        header = set(t["tasks"][0]) if t["tasks"] else set()
        if t["tasks"] and not header & {"criteria", "acceptance", "acceptance criteria", "done when"}:
            problems.append("the Tasks table needs a `criteria` column with checkable acceptance criteria per task")
        if header & {"status", "state"}:
            problems.append("remove the status column — task state is derived from events, plans hold intent only")
        for r in t["tasks"]:
            if not _get(r, "id", "task id"):
                problems.append("every task row needs an id (T1, T2, …)")
                break
    return problems


def _split(v: str | None) -> list[str]:
    return [x.strip() for x in re.split(r"[,\s]+", v or "") if x.strip() and x.strip() != "-"]


def load_plans(state: Path) -> list[dict]:
    d = state / "plans"
    if not d.is_dir():
        return []
    plans = []
    for f in sorted(d.glob("*.md")):
        if _VERSIONED.search(f.name):
            continue
        meta, body = _front_matter(f.read_text(encoding="utf-8"))
        if not meta.get("id"):
            continue
        t = _tables(body)
        phases = [{"id": _get(r, "id", "phase"), "name": _get(r, "name", "description", "title") or _get(r, "id", "phase"),
                   "note": _get(r, "note", "notes") or ""}
                  for r in t.get("phases", []) if _get(r, "id", "phase")]
        tasks = [{"id": _get(r, "id", "task id"), "title": _get(r, "title", "task", "description", "name"),
                  "phase": _get(r, "phase"), "agent_type": _get(r, "agent", "agent type"),
                  "depends_on": _split(_get(r, "depends", "depends on", "depends_on", "after")),
                  "criteria": _get(r, "criteria", "acceptance", "acceptance criteria", "done when") or ""}
                 for r in t.get("tasks", []) if _get(r, "id", "task id")]
        plans.append({**meta, "file": str(f.relative_to(state.parent)), "phases": phases, "tasks": tasks})
    return plans


def active_plan(plans: list[dict]) -> dict | None:
    active = [p for p in plans if p.get("status", "active") == "active"]
    return (active or plans or [None])[-1]
