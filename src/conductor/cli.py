#!/usr/bin/env python3
"""conductor — init | status | render | serve

    conductor init [DIR]          create .conductor/ in DIR (default: current project)
    conductor status [--json]     who is doing what, right now (no tokens, no interruption)
    conductor render              rebuild status.json + status.html once
    conductor serve [--port N]    live dashboard on http://127.0.0.1:N
    conductor provenance REF      lineage of a claim (T1/C2, P2/T1/C2), task (T1, P2/T1), agent id, or file path
    conductor artifact [enable | url <URL>]   build the status page for a claude.ai Artifact (opt-in)
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conductor import core, render  # noqa: E402

TEMPLATES = Path(__file__).resolve().parents[2] / "templates" / core.STATE_DIR
ICON = {"running": "▶", "done": "✓", "blocked": "■", "failed": "✗", "unverified": "?", "queued": "·", "todo": "·"}


def _state(args) -> Path:
    if getattr(args, "state", None):
        return Path(args.state)
    s = core.find_state_dir(core.project_dir())
    if s is None:
        sys.exit(f"No {core.STATE_DIR}/ found here or above. Run: conductor init")
    return s


def cmd_init(args) -> int:
    root = Path(args.dir or core.project_dir()).resolve()
    dest = root / core.STATE_DIR
    created = []
    for src in TEMPLATES.rglob("*"):
        rel = src.relative_to(TEMPLATES)
        target = dest / rel
        if src.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, target)
            created.append(str(target.relative_to(root)))
    (dest / "events.jsonl").touch()
    print(f"Conductor initialised in {dest}")
    for c in created:
        print("  +", c)
    return 0


def _fmt_dur(s) -> str:
    if s is None:
        return ""
    return f"{s}s" if s < 90 else f"{s // 60}m{s % 60:02d}s"


def cmd_status(args) -> int:
    st = render.build_status(_state(args))
    if args.json:
        print(json.dumps(st, indent=1, ensure_ascii=False))
        return 0
    s = st["summary"]
    print(f"Conductor · {st['program']} · {st['subtitle']} · {'live' if st['live'] else 'ended'}")
    if st.get("intent"):
        print(f"Intent: {st['intent']}")
    if st["phases"]:
        print("Phases: " + "  ".join(f"{ICON.get(p['state'], '·')} {p['id']} {p['name']}" for p in st["phases"]))
    print(f"Agents: {s['agents_running']} running · {s['agents_done']} done · {s['agents_total']} total"
          + (f" · {s['unverified']} unverified" if s["unverified"] else ""))

    agents = st["agents"]
    kids: dict[str, list[dict]] = {}
    for a in agents:
        kids.setdefault(a["parent"], []).append(a)

    def walk(parent: str, prefix: str) -> None:
        items = kids.get(parent, [])
        if args.active:
            items = [a for a in items if a["id"] in active]
        for i, a in enumerate(items):
            last = i == len(items) - 1
            gate = a["gate"]["result"]
            gate_s = f" · gate {gate}" + (f" ×{a['gate']['blocks']}" if a["gate"]["blocks"] else "") if gate else ""
            now = f" — {a['last_action']}" if a["state"] == "running" and a["last_action"] else ""
            print(f"{prefix}{'└─' if last else '├─'} {ICON.get(a['state'], '·')} {(a['type'] or '?').split(':')[-1]}"
                  f"·{a['id'][:6]} [{a['task'] or '-'}] {core.clip(a['description'], 40)} {_fmt_dur(a['duration_s'])}{gate_s}{now}")
            walk(a["id"], prefix + ("   " if last else "│  "))

    active = set()
    for a in agents:  # running agents and their ancestors
        if a["state"] == "running":
            node = a
            while node:
                active.add(node["id"])
                node = next((x for x in agents if x["id"] == node["parent"]), None)
    print("orchestrator")
    walk("main", "")
    open_items = [d for d in st["decisions"] if d["state"] == "open"]
    if open_items or st["review_queue"]:
        print("Needs you:")
        for d in open_items:
            print(f"  • [{d.get('task') or '-'}] {core.clip(d['q'], 120)}")
        for r in st["review_queue"]:
            print(f"  • review [{r['task']}] {core.clip(r['reason'], 120)}")
    if st["log"]:
        print("Recent:")
        for e in st["log"][: args.recent]:
            print(f"  {e['ts'][11:19]} {e['agent']}: {core.clip(e['text'], 100)}")
    return 0


def cmd_provenance(args) -> int:
    from conductor import provenance
    from conductor.tree import Tree
    tree = Tree(core.read_events(_state(args)))
    print("\n".join(provenance.query(tree, args.ref)))
    return 0


ARTIFACT_UPLOADS = ("plans and task criteria, agent names and states, claims with their evidence references, "
                    "gate results, file paths the agents touched, and one-line log entries (commands too, unless "
                    "artifact.include_commands is false). Prompts and human messages are left out unless "
                    "artifact.include_prompts is true.")


def cmd_artifact(args) -> int:
    """Build the page for a claude.ai Artifact and tell Claude how to publish it (opt-in, one URL per project)."""
    state = _state(args)
    cfg_path = state / "config.json"
    saved = state / "state" / "artifact.json"
    words = args.words or []
    if words[:1] == ["enable"]:
        cfg = json.loads(cfg_path.read_text()) if cfg_path.exists() else {}
        cfg.setdefault("artifact", {})["enabled"] = True
        cfg_path.write_text(json.dumps(cfg, indent=2) + "\n")
        print("Artifact publishing enabled for this project. Run /conductor:artifact to publish.")
        return 0
    if words[:1] == ["url"] and len(words) > 1:
        saved.parent.mkdir(exist_ok=True)
        saved.write_text(json.dumps({"url": words[1], "saved": core.utcnow()}) + "\n")
        print(f"Saved. Later refreshes publish to {words[1]}.")
        return 0
    if not core.load_config(state)["artifact"]["enabled"]:
        print("Artifact publishing is OFF for this project (it is opt-in).\n"
              f"Publishing uploads to claude.ai (private by default): {ARTIFACT_UPLOADS}\n"
              "Ask the human whether to enable it. Only if they agree, run: /conductor:artifact enable")
        return 0
    st = render.build_artifact(state)
    from conductor import page
    html, embedded = page.render(page.TEMPLATE.read_text(encoding="utf-8"), st)
    errs = page.check(html, embedded)
    if errs:
        sys.exit("status page failed its self-check: " + ", ".join(errs))
    out = state / "status-artifact.html"
    out.write_text(html, encoding="utf-8")
    url = (json.loads(saved.read_text()).get("url") if saved.exists() else None)
    print(f"Built {out} ({len(html) // 1024} KB, {len(st['sessions'])} sessions).")
    if url:
        print(f"Publish it with the Artifact tool: file_path={out}, url={url} (updates the existing page in place).")
    else:
        print(f"Publish it with the Artifact tool: file_path={out} (first publish; icon: chart). Then save the URL it "
              "returns with: /conductor:artifact url <URL>")
    return 0


def cmd_render(args) -> int:
    state = _state(args)
    if args.loop:
        render.render_loop(state)
        return 0
    st = render.write_outputs(state)
    print(f"rendered {state / 'status.html'} ({len(st['agents'])} agents, {len(st['log'])} log entries)")
    return 0


def cmd_serve(args) -> int:
    from conductor import serve
    state = _state(args)
    render.write_outputs(state)
    serve.run(state, args.port)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="conductor", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init"); p.add_argument("dir", nargs="?"); p.set_defaults(fn=cmd_init)
    p = sub.add_parser("status"); p.add_argument("--state", help=argparse.SUPPRESS); p.add_argument("--json", action="store_true")
    p.add_argument("--active", action="store_true", help="only running agents (and their ancestors)"); p.add_argument("--recent", type=int, default=6)
    p.set_defaults(fn=cmd_status)
    p = sub.add_parser("provenance", help="lineage of a claim (T1/C2), task (T1), agent id, or file path")
    p.add_argument("ref"); p.add_argument("--state", help=argparse.SUPPRESS); p.set_defaults(fn=cmd_provenance)
    p = sub.add_parser("artifact", help="build the claude.ai Artifact page (opt-in): artifact [enable | url <URL>]")
    p.add_argument("words", nargs="*"); p.add_argument("--state", help=argparse.SUPPRESS); p.set_defaults(fn=cmd_artifact)
    p = sub.add_parser("render"); p.add_argument("--loop", action="store_true"); p.add_argument("--state"); p.set_defaults(fn=cmd_render)
    p = sub.add_parser("serve"); p.add_argument("--state", help=argparse.SUPPRESS); p.add_argument("--port", type=int, default=8765); p.set_defaults(fn=cmd_serve)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
