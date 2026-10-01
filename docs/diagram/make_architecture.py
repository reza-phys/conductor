# Generates docs/architecture.svg. Palette: editorial-two-tier (light fill / deep border / navy label).
import base64, pathlib
HERE = pathlib.Path(__file__).resolve().parent
OUT = HERE.parent / "architecture.svg"
# The human: the author's cut-out site photo (176 px, transparent PNG), embedded so the SVG stays self-contained
PORTRAIT = base64.b64encode((HERE / "portrait.png").read_bytes()).decode()
from svgkit import ROLE, Canvas
W, H = 1010, 750
c = Canvas(W, H)
text, box, cyl, edge, o = c.text, c.box, c.cyl, c.edge, c.o
NAVY = "#082a54"

text(32, 36, "Conductor: work flows down, only verified claims flow up", 20, 650, "start")
c.logo(W - 24 - 46, 14, 46)  # the Conductor mark, top right
text(32, 62, "Hooks record every action, so the live dashboard and the provenance trail never depend on an agent remembering to report.", 13, 400, "start", "#3d4f66")

# Hook boundary
o.append('<rect x="270" y="160" width="470" height="500" rx="12" fill="#f3f6fa" stroke="#082a54" stroke-width="0.6" stroke-dasharray="4 4"/>')
text(286, 646, "Hooks: log every action, enforce every rule", 12, 500, "start", "#3d4f66", italic=True)

# Human: cut-out photo, no frame. Image box 64x64; its bottom edge (shoulders) is at y=138.
HX, HTOP, HS = 410, 74, 64
o.append(f'<image href="data:image/png;base64,{PORTRAIT}" x="{HX-HS//2}" y="{HTOP}" width="{HS}" height="{HS}" '
         f'role="img" aria-label="You, the human"><title>You (the human)</title></image>')
# Both arrows: 6 px clear of where they leave, 8 px clear of where they point
edge("M395 144 V192"); text(387, 147, "task, answers", 12, 400, "end")
edge("M425 194 V146"); text(433, 147, "questions", 12, 400, "start")

# Hierarchy
box(330, 200, 160, 56, "focal", "Orchestrator", "plan · dispatch · ask")
# Row 2: one group, two agent kinds — both dispatched the same way, both behind the same audit gate
o.append(f'<rect x="290" y="382" width="260" height="72" rx="10" fill="none" stroke="{ROLE["action"][1]}" stroke-width="0.75"/>')
box(298, 390, 118, 56, "action", "Workers", "make changes")
box(424, 390, 118, 56, "action", "Analysts", "read-only")
box(330, 580, 160, 56, "action", "Sub-agents", "narrower tasks")
edge("M385 256 V372"); text(377, 319, "dispatch", 12, 400, "end")
edge("M385 454 V570"); text(377, 513, "dispatch", 12, 400, "end")

# Audit gates on every upward edge
for gy, child_x, child_cy, parent_bottom in [(300, 550, 418, 256), (500, 490, 608, 454)]:
    box(570, gy, 140, 48, "gate", "Audit gate", "verifies claims", rx=6)
    edge(f"M{child_x} {child_cy} H640 V{gy+58}", "control"); text(child_x + 8, child_cy + 11, "report + claims", 12, 400, "start")
    # turn 18 px above the gate so the last leg (into the parent) is long enough to carry its arrowhead
    edge(f"M640 {gy} V{gy-18} H460 V{parent_bottom+10}", "verified"); text(470, gy - 8, "verified only", 12, 600, "start")

# Left: context the orchestrator reads, plans it writes
cyl(40, 120, 180, 56, "Project context", "files · CLAUDE.md · DB")
box(40, 204, 180, 48, "ext", "Web + MCP sources", "search · docs · your RAG")
cyl(40, 300, 180, 56, "Plans folder", ".conductor/plans")
edge("M220 164 H290 V212 H320", "data")
edge("M220 228 H320", "data")
edge("M330 244 H250 V328 H230", "data"); text(256, 316, "writes", 12, 400, "start")

# Tools on the user's machine: used by every agent below the orchestrator (calls observed + limited by hooks)
box(40, 392, 180, 52, "ext", "Tools on your machine", "shell · files · git · MCP")
edge("M280 418 H230", "data", two_way=True)
edge("M320 608 H130 V454", "data", two_way=True); text(138, 520, "call · result", 12, 400, "start")

# Right: one append-only log; provenance and the dashboard are both derived from it
cyl(790, 300, 170, 56, "Event log", "events.jsonl · claims")
box(790, 440, 170, 56, "focal", "Provenance", "lineage · git trailers")
box(790, 580, 170, 56, "focal", "Live dashboard", "you watch here")
edge("M740 328 H780", "data")
edge("M875 356 V430", "data")
edge("M960 328 H990 V608 H970", "data")  # outer lane, 20 px final leg into the dashboard

# Legend
ly = 690
x = 40
for kind, label in [("control", "task / dispatch / report"), ("verified", "verified result"), ("data", "data and events")]:
    x = c.legend_line(x, ly, kind, label)
x = 40
for r, l in [("focal", "orchestration and view"), ("action", "agents doing work"), ("gate", "quality gate"),
             ("ext", "external tools & sources"), ("neutral", "state on disk")]:
    x = c.legend_swatch(x, ly + 28, r, l)

DESC = ("You (shown as a photo) give a task to the orchestrator, which reads project context and web or MCP sources, writes plans, "
        "and dispatches workers (which make changes) and analysts (read-only), which can dispatch sub-agents; all of them use the "
        "tools on your machine (shell, files, git, MCP). Every report travelling upward passes an audit gate that verifies its claims; "
        "only verified results reach the parent. Hooks observe every action inside the agent tree and enforce its rules; they write one "
        "append-only event log, from which the provenance view and the live dashboard are derived.")
open(OUT, "w").write(c.svg("Conductor architecture", DESC)); print("wrote", OUT)
