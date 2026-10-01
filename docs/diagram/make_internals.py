# Generates docs/internals.svg: what happens to one Claude Code event inside the harness.
# Main path, left to right on one row: Claude Code -> hook.py -> events.jsonl -> render.py -> you.
# Above it: the re-render trigger. Below it: the modules hook.py calls, and the log read back by replay.
# Palette: editorial-two-tier (light fill / deep border / navy label), shared with architecture.svg.
import base64, pathlib

from svgkit import MUTED, Canvas

HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "internals.svg"
PORTRAIT = base64.b64encode((HERE / "portrait.png").read_bytes()).decode()  # same "you" as architecture.svg
W, H = 1100, 610
c = Canvas(W, H)
text, box, cyl, edge, o = c.text, c.box, c.cyl, c.edge, c.o

text(32, 36, "Inside Conductor: only the hook writes the log; every view replays it", 20, 650, "start")
c.logo(W - 24 - 46, 14, 46)  # the Conductor mark, top right
text(32, 62, "Each Claude Code event starts one short hook process: it replies with a decision, appends to events.jsonl "
             "and triggers a re-render.", 13, 400, "start", MUTED)

# Column headings
for x, label in [(32, "CLAUDE CODE"), (296, "HOOK PROCESS · ONE PER EVENT, ~50 MS"), (616, "ON DISK · .conductor/"),
                 (806, "READERS"), (1018, "YOU")]:
    text(x, 96, label, 12, 600, "start", MUTED)

CY = 254  # main row centre line

# 1. Claude Code hands every event to hook.py and gets a decision back
# 0. Your input enters here: messages (UserPromptSubmit) and answers to questions (AskUserQuestion)
o.append(f'<image href="data:image/png;base64,{PORTRAIT}" x="72" y="118" width="56" height="56" '
         f'role="img" aria-label="You, the human"><title>You (the human)</title></image>')
edge("M100 180 V212"); text(110, 196, "prompts · answers", 12, 400, "start")
box(32, 222, 136, 64, "ext", "Claude Code", "session + agents")
# arrows stop short of the dashed hook-process frame (x=296) so their heads stay visible
edge(f"M174 {CY-12} H286"); text(232, CY - 24, "event (stdin)", 12, 400)
edge(f"M290 {CY+12} H178"); text(232, CY + 24, "decision (stdout)", 12, 400)

text(32, 316, "Events in (hooks.json)", 12, 600, "start")
for i, line in enumerate(["SessionStart · SessionEnd", "UserPromptSubmit · Stop", "PreToolUse · PostToolUse",
                          "SubagentStart · SubagentStop"]):
    text(32, 338 + 19 * i, line, 12, 400, "start", mono=True)
text(32, 426, "Decisions back", 12, 600, "start")
for i, line in enumerate(["deny a dispatch or an edit", "rewrite input: foreground, trailers",
                          "block + reason: stop gate, bad plan", "add context: orchestrator brief"]):
    text(32, 448 + 19 * i, line, 12, 400, "start")

# 2. The hook process: hook.py on the main row, the modules it calls beneath it
o.append('<rect x="296" y="206" width="268" height="308" rx="12" fill="#f3f6fa" stroke="#082a54" '
         'stroke-width="0.6" stroke-dasharray="4 4"/>')
box(312, 222, 236, 64, "action", "hook.py", "route event · apply rules · reply", mono=True)
for col, row, name, sub in [(0, 0, "protocol.py", "parse blocks"), (1, 0, "gate.py", "check a stop"),
                            (0, 1, "core.py", "locked log I/O"), (1, 1, "tree.py", "replay (cached)"),
                            (1, 2, "plans.py", "validate a plan")]:
    box(312 + 124 * col, 316 + 66 * row, 112, 50, "action", name, sub, mono=True)
text(312, 473, "modules it calls", 12, 500, "start", MUTED, italic=True)

# 3. State on disk: one append-only log (only hook.py writes it), plus the plans the orchestrator writes
cyl(616, 222, 130, 64, "events.jsonl", "append-only", mono=True)
cyl(616, 146, 130, 56, "plans/", "+ config.json", mono=True)
edge(f"M554 {CY} H606", "data"); text(590, CY - 12, "append", 12, 400)
edge(f"M610 {CY+20} H580 V407 H558", "data"); text(588, 342, "replay", 12, 400, "start")  # loop-back: hook reads its own log

# 4. Readers: rebuild everything from the log, never write it
box(806, 222, 140, 64, "focal", "render.py", "status.json + .html", mono=True)
box(806, 379, 140, 56, "focal", "cli.py", "status · provenance", mono=True)
edge(f"M752 {CY} H796", "data")
edge("M752 174 H856 V212", "data")
edge(f"M752 {CY+20} H770 V407 H796", "data"); text(778, 342, "replay", 12, 400, "start")
# hook.py starts the renderer in a detached process; bursts of events coalesce into one render
edge("M500 216 V132 H906 V212"); text(703, 120, "trigger re-render · detached, debounced", 12, 400)

# 5. You: the live page in the browser, or the CLI in a terminal
o.append(f'<image href="data:image/png;base64,{PORTRAIT}" x="1018" y="{CY-28}" width="56" height="56" '
         f'role="img" aria-label="You, the human"><title>You (the human)</title></image>')
edge(f"M952 {CY} H1008", "data"); text(980, CY - 12, "live page", 12, 400); text(980, CY + 13, "serve.py", 12, 400, mono=True)
edge("M952 407 H1046 V292", "data"); text(996, 419, "terminal", 12, 400)

# Legend (two rows, as in architecture.svg)
ly = 556
x = 32
for kind, label in [("control", "event · decision · trigger"), ("data", "reads and writes")]:
    x = c.legend_line(x, ly, kind, label)
x = 32
for role, label in [("ext", "Claude Code"), ("action", "hook process"), ("neutral", "state on disk"),
                    ("focal", "readers (never write the log)")]:
    x = c.legend_swatch(x, ly + 26, role, label)

DESC = ("Flow of one Claude Code event through Conductor. "
        "0. You type to the Claude Code session: your messages arrive as UserPromptSubmit events and your answers to the "
        "orchestrator's questions as PostToolUse(AskUserQuestion); both are logged, and they are the root of every provenance chain. "
        "1. Claude Code sends each hook event (SessionStart, SessionEnd, UserPromptSubmit, Stop, PreToolUse, PostToolUse, "
        "SubagentStart, SubagentStop, as routed by hooks.json) as JSON on stdin to hook.py, which runs as one short process "
        "per event (about 50 ms). "
        "2. hook.py routes the event and applies the rules using protocol.py (parse task, review and report blocks), "
        "tree.py (replay the event log into the agent tree and claims ledger), gate.py (stop checks), plans.py (validate a "
        "plan the orchestrator just wrote) and core.py (config and locked log I/O). "
        "3. It answers on stdout with a decision: deny a dispatch or an edit, rewrite the tool input (force foreground, add "
        "git trailers), block with a reason (stop gate failed, or plan invalid), or add context (the orchestrator brief). "
        "4. It appends the event to .conductor/events.jsonl; hook.py is the only writer of that log. "
        "5. It triggers a re-render in a detached, debounced process: render.py replays the log, reads plans/ and writes "
        "status.json and a self-checked status.html, which serve.py shows to you as a live page. "
        "6. cli.py answers status and provenance queries in the terminal by replaying the same log.")
open(OUT, "w").write(c.svg("Conductor internals", DESC))
print("wrote", OUT)
