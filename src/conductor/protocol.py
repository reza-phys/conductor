"""The three tagged blocks agents exchange: task envelope, review envelope, and report.

    <conductor-task id="T3" plan="P1@v2" parent="T1">
    criteria: …
    </conductor-task>

    <conductor-review for="T3"> … </conductor-review>

    <conductor-report task="T3" status="done|blocked|failed">
    summary …
    </conductor-report>
"""
from __future__ import annotations

import re

_ATTR = re.compile(r"""([a-zA-Z_][\w-]*)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
REPORT_STATUSES = ("done", "blocked", "failed")


def _block(tag: str, text: str, last: bool = False) -> tuple[dict, str] | None:
    """(attributes, body) of the first <tag …>…</tag> — or the last one, for reports, which come at the end.

    Envelopes use the FIRST block: a prompt may quote further envelopes meant for sub-dispatches."""
    if not text:
        return None
    matches = list(re.finditer(rf"<{tag}\b([^>]*)>(.*?)(?:</{tag}>|\Z)", text, re.S))
    if not matches:
        return None
    m = matches[-1] if last else matches[0]
    return {k: a or b for k, a, b in _ATTR.findall(m.group(1))}, m.group(2).strip()


def _fields(body: str) -> dict:
    """`key: value` lines inside a block body (multi-line values continue until the next key)."""
    out: dict[str, str] = {}
    key = None
    for line in body.splitlines():
        m = re.match(r"^\s*([a-z][\w-]*)\s*:\s*(.*)$", line)
        if m:
            key = m.group(1)
            out[key] = m.group(2).strip()
        elif key and line.strip():
            out[key] += "\n" + line.strip()
    return out


def parse_task(prompt: str) -> dict | None:
    b = _block("conductor-task", prompt)
    if not b or not b[0].get("id"):
        return None
    attrs, body = b
    return {**attrs, **{k: v for k, v in _fields(body).items() if k not in attrs}}


def parse_review(prompt: str) -> dict | None:
    b = _block("conductor-review", prompt)
    if not b or not b[0].get("for"):
        return None
    return {**b[0], **_fields(b[1])}


def parse_report(message: str) -> dict | None:
    b = _block("conductor-report", message, last=True)
    if not b:
        return None
    attrs, body = b
    return {"task": attrs.get("task"), "status": attrs.get("status"), "body": body, **_fields(body)}


ENVELOPE_HELP = (
    'Start the Agent prompt with a task envelope, e.g.\n'
    '<conductor-task id="T3" plan="P1@v1" parent="T1">\n'
    'criteria: what must be true for this task to count as done\n'
    '</conductor-task>\n'
    'Auditor dispatches use <conductor-review for="T3"> … </conductor-review> instead.'
)

REPORT_HELP = (
    'End your final message with a report block:\n'
    '<conductor-report task="<task id>" status="done|blocked|failed">\n'
    'summary: what you did and found, in a few lines\n'
    'files: paths you created or changed (or "none")\n'
    'needs: anything the parent or the human must decide (or "none")\n'
    '</conductor-report>'
)


# --- claims and verdicts ------------------------------------------------------------------
#
#   claims:
#   - C1: calc.py defines two functions | evidence: file:src/calc.py:1-6
#   - C2: the test suite passes | evidence: cmd:python3 -m src.test_calc
#
#   verdicts:
#   - C1: verified | read src/calc.py: add (l.1), sub (l.5)
#   - C2: refuted | exit code 1: AssertionError

VERDICTS = ("verified", "unverified", "refuted")
EVIDENCE_KINDS = ("file", "cmd", "url", "mcp", "claim", "hitl")
REVIEW_SKIP = ("skip", "none", "no", "off")
_ITEM = re.compile(r"^\s*(?:[-*]\s*)?(C\d+)\s*[:.)]\s*(.*)$")


def _items(block: str | None) -> list[tuple[str, str]]:
    return [(m.group(1), m.group(2).strip()) for m in map(_ITEM.match, (block or "").splitlines()) if m]


_ANNOT_DASH = re.compile(r"\s+(?:—|–|--)\s+.*$")                       # "… — verified", "… -- see notes"
_ANNOT_PAREN = re.compile(r"\s+\((?:[^()]*)\)\s*$")                       # "… (verified)" at the very end
_ANNOT_WORD = re.compile(r"[\s,;:|-]+(?:verified|refuted|unverified|ok|checked|confirmed)[.!]?\s*$", re.I)
_FILE_REF = re.compile(r"^(?P<path>`[^`]+`|\S+?)(?::(?P<lines>\d+(?:-\d+)?(?:\s*,\s*\d+(?:-\d+)?)*))?(?=\s|$)")


def _strip_annotation(ref: str, kind: str) -> str:
    """Drop harmless notes agents append to a reference ("— verified", "(ok)"). Commands keep their own
    parentheses; for them only a spaced dash or a bare trailing verdict word is cut."""
    ref = _ANNOT_DASH.sub("", ref.strip())
    if kind != "cmd":
        ref = _ANNOT_PAREN.sub("", ref)
    ref = _ANNOT_WORD.sub("", ref).strip()
    return ref


def parse_evidence(s: str) -> list[dict]:
    out = []
    # Split on ";" only where the next item starts with an evidence kind: commands may contain ";" themselves.
    kinds = "|".join(EVIDENCE_KINDS)
    for part in re.split(rf"\s*;\s*(?=(?:{kinds})\s*:)", s.strip(), flags=re.I):
        if not part:
            continue
        kind, sep, ref = part.partition(":")
        kind = kind.strip().lower()
        if sep and kind in EVIDENCE_KINDS:
            raw = ref.strip()
            ref = _strip_annotation(raw, kind)
            if kind == "file":
                m = _FILE_REF.match(ref)  # path, path:12, path:3-9, path:303,350 — anything after whitespace is a note
                ev = {"kind": kind, "ref": m.group("path").strip("`") if m else ref.strip("`")}
                if m and m.group("lines"):
                    ev["lines"] = re.sub(r"\s+", "", m.group("lines"))
            elif kind in ("url", "mcp", "claim", "hitl"):
                ev = {"kind": kind, "ref": (ref.strip("`").split() or [""])[0].strip("`<>")}
            else:  # cmd: the whole command, minus wrapping backticks
                ev = {"kind": kind, "ref": ref.strip("`").strip()}
            if raw.strip("`") != ev["ref"] and kind != "cmd":
                ev["raw"] = raw
            out.append(ev)
        else:
            out.append({"kind": "note", "ref": part})
    return out


def parse_claims(block: str | None) -> list[dict]:
    claims = []
    for cid, rest in _items(block):
        parts = re.split(r"\|\s*evidence\s*:\s*", rest, maxsplit=1)
        text = parts[0].strip().rstrip("|").strip()
        claims.append({"id": cid, "text": text, "evidence": parse_evidence(parts[1]) if len(parts) > 1 else []})
    return claims


def parse_verdicts(block: str | None) -> list[dict]:
    out = []
    for cid, rest in _items(block):
        m = re.match(r"^(verified|unverified|refuted)\b\s*(?:[|—–-]+\s*)?(.*)$", rest, re.I)
        if m:
            out.append({"id": cid, "verdict": m.group(1).lower(), "evidence": m.group(2).strip()})
    return out


CLAIMS_HELP = (
    'List your claims inside the report, one per line, each with evidence you actually produced:\n'
    'claims:\n'
    '- C1: <claim> | evidence: file:<path>[:<lines>]\n'
    '- C2: <claim> | evidence: cmd:<exact command you ran>\n'
    '- C3: <claim> | evidence: mcp:<server>__<tool> (an MCP call you made)\n'
    '(evidence kinds: file, cmd, url, mcp, claim:<task>/<Cn>, hitl:<id>; separate several with ";")'
)

REVIEW_HELP = (
    'Before reporting status="done", dispatch subagent_type "conductor:auditor" with:\n'
    '<conductor-review for="<your task id>">\n'
    'criteria: <your task criteria>\n'
    'claims:\n'
    '- C1: <claim> | evidence: …\n'
    '</conductor-review>\n'
    'Then fix or withdraw anything refuted, and report the final claims.'
)

VERDICTS_HELP = (
    'End with a report whose `verdicts:` field has one line per claim you were given:\n'
    'verdicts:\n'
    '- C1: verified | <what you checked and saw>\n'
    '- C2: refuted | <what contradicts it>\n'
    '(verified | unverified | refuted)'
)


ORCHESTRATOR_BRIEF = """\
[Conductor is active in this project — .conductor/ exists. You are the orchestrator.]
- You plan and delegate; you do not edit project files (a hook refuses it). Write plans/decisions only under .conductor/.
- Plan first, in .conductor/plans/P<n>-<slug>.md, exactly this shape (a hook checks it):
  ---\n  id: P1\n  version: 1\n  status: active\n  goal: …\n  intent: what the run is doing now\n  kill_test: when to stop\n  ---
  ## Phases  — table: | id | name | note |
  ## Tasks   — table: | id | title | phase | agent | depends | criteria |   (no status column; criteria must be checkable)
  Revising: keep the old version as P<n>-<slug>.v<k>.md, bump version, log why in .conductor/plans/decisions.md.
- Dispatch with subagent_type "conductor:worker" (or "conductor:analyst" for read-only research). Every prompt starts with
  <conductor-task id="T3" plan="P1@v1">\ncriteria: …\n</conductor-task>  — dispatches without it are refused.
  Independent tasks: one message, several Agent calls, run_in_background true; you stay free to talk to the human.
  Low-stakes mechanical tasks may carry review="skip" in the envelope (claims then show as asserted, not verified).
- Workers can only finish once their claims are verified by an auditor; results arrive with verified claims (ids like T3/C1). Cite those ids when you report to the human, and label anything unverified as such.
- A worker returning status="blocked" with a `needs:` question → ask the human with AskUserQuestion, log the decision in .conductor/plans/decisions.md, then resume the worker with SendMessage or re-dispatch.
- Ask the human before: irreversible or outward actions (push, publish, send, delete), scope changes, budget overruns, conflicting results, or anything escalated as unverified.
- An escalated task can be re-audited (`<conductor-review for="P1/T2">`, final claims verbatim) instead of redone. A pasted "[Conductor HITL <id>] … Decision: …" line is the human's decision; the hook tells you the next step.
- The human can run /conductor:status anytime; the live dashboard is /conductor:serve. Full playbook: the conductor:orchestrate skill.
"""

ARTIFACT_BRIEF = """\
- Conductor's status Artifact is enabled for this project. After a task finishes (and when the human asks), run
  /conductor:artifact: it rebuilds the page and tells you how to publish it to the project's fixed Artifact URL.
"""
