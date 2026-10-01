"""The Conductor mark ("score as agent graph"): geometry shared by the logo files and the diagrams.

A beamed group of notes on a staff. The lead note (ink) is the orchestrator; the beam ties it to three voices
in the diagrams' role colours (blue = orchestration, teal = agents doing work, gold = the audit gate).
`mark()` returns SVG elements in a 128x128 box; ink parts carry class="ink-s"/"ink-f" so callers choose the colour.
"""

INK_LIGHT, INK_DARK = "#082a54", "#e6edf3"
BLUE, TEAL, GOLD = "#2066a8", "#1f8a70", "#c08a17"

STAFF = (46, 58, 70, 82, 94)                      # five staff lines, 12 apart
NOTES = [(28, 88, None), (54, 76, BLUE), (78, 64, TEAL), (102, 82, GOLD)]  # (x, y) of each head; None = ink
STEM_X = 8.2                                       # stem sits on the right edge of each head
BEAM_TOP, BEAM_H = 20, 10


def mark(ox=0, oy=0):
    staff = "".join(f'<line class="ink-s" x1="{8+ox}" x2="{120+ox}" y1="{y+oy}" y2="{y+oy}" stroke-width="2" '
                    'opacity="0.32" stroke-linecap="round"/>' for y in STAFF)
    stems = "".join(f'<line class="ink-s" x1="{x+STEM_X+ox:.1f}" x2="{x+STEM_X+ox:.1f}" y1="{y-2+oy}" '
                    f'y2="{BEAM_TOP+2+oy}" stroke-width="3.6"/>' for x, y, _ in NOTES)
    x0, x1 = NOTES[0][0] + STEM_X - 1.8, NOTES[-1][0] + STEM_X + 1.8
    beam = f'<rect class="ink-f" x="{x0+ox:.1f}" y="{BEAM_TOP+oy}" width="{x1-x0:.1f}" height="{BEAM_H}" rx="1.5"/>'
    heads = "".join(f'<ellipse cx="{x+ox}" cy="{y+oy}" rx="10" ry="7" transform="rotate(-22 {x+ox} {y+oy})" '
                    + ('class="ink-f"/>' if c is None else f'fill="{c}"/>') for x, y, c in NOTES)
    return staff + stems + beam + heads


def mark_fixed(ink="#082a54"):
    """The mark with a fixed ink colour (for diagrams on a white card)."""
    return mark().replace('class="ink-s"', f'stroke="{ink}"').replace('class="ink-f"', f'fill="{ink}"')
