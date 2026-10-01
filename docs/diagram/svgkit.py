"""Shared drawing helpers for the docs diagrams (house palette: light fill, deep border, navy label)."""
import html

NAVY = "#082a54"
MUTED = "#3d4f66"
ROLE = {  # fill, border
    "focal": ("#9dc5e8", "#2066a8"), "action": ("#59a89c", "#1f8a70"),
    "gate": ("#f0c571", "#c08a17"), "ext": ("#cba3ce", "#a559aa"), "neutral": ("#e4e4e4", NAVY),
}
FONT = "Inter, 'IBM Plex Sans', -apple-system, 'Segoe UI', system-ui, sans-serif"


class Canvas:
    def __init__(self, w, h):
        self.w, self.h, self.o = w, h, [f'<rect x="0" y="0" width="{w}" height="{h}" rx="14" fill="#ffffff"/>']

    def raw(self, s):
        self.o.append(s)

    def text(self, x, y, s, size=12, weight=400, anchor="middle", fill=NAVY, italic=False, mono=False):
        st = ' font-style="italic"' if italic else ""
        ff = ' font-family="ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"' if mono else ""
        self.o.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" text-anchor="{anchor}" '
                      f'dominant-baseline="central" fill="{fill}"{st}{ff}>{html.escape(s, quote=False)}</text>')

    def labels(self, cx, cy, title, sub=None, mono=False):
        if sub:
            self.text(cx, cy - 9, title, 14, 600, mono=mono)
            self.text(cx, cy + 10, sub, 12)
        else:
            self.text(cx, cy, title, 14, 600, mono=mono)

    def box(self, x, y, w, h, role, title, sub=None, rx=8, mono=False):
        f, b = ROLE[role]
        self.o.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{f}" stroke="{b}" stroke-width="1"/>')
        self.labels(x + w / 2, y + h / 2, title, sub, mono)

    def cyl(self, x, y, w, h, title, sub=None, role="neutral", mono=False):
        f, b = ROLE[role]
        r = 7
        self.o.append(f'<path d="M{x} {y+r} V{y+h-r} A{w/2} {r} 0 0 0 {x+w} {y+h-r} V{y+r}" fill="{f}" stroke="{b}" stroke-width="1"/>')
        self.o.append(f'<ellipse cx="{x+w/2}" cy="{y+r}" rx="{w/2}" ry="{r}" fill="{f}" stroke="{b}" stroke-width="1"/>')
        self.labels(x + w / 2, y + h / 2 + 4, title, sub, mono)

    def edge(self, d, kind="control", two_way=False):
        """Arrow; two-way edges get a head at both ends."""
        sw, dash = {"control": (1, ""), "verified": (1.75, ""), "data": (1, ' stroke-dasharray="5 4"')}[kind]
        start = ' marker-start="url(#arrow)"' if two_way else ""
        self.o.append(f'<path d="{d}" fill="none" stroke="{NAVY}" stroke-width="{sw}"{dash}{start} marker-end="url(#arrow)"/>')

    def legend_line(self, x, y, kind, label):
        sw, dash = {"control": (1, ""), "verified": (1.75, ""), "data": (1, ' stroke-dasharray="5 4"')}[kind]
        self.o.append(f'<path d="M{x} {y} H{x+28}" fill="none" stroke="{NAVY}" stroke-width="{sw}"{dash} marker-end="url(#arrow)"/>')
        self.text(x + 38, y, label, 12, 400, "start")
        return x + 38 + round(len(label) * 6.6) + 32

    def legend_swatch(self, x, y, role, label):
        f, b = ROLE[role]
        self.o.append(f'<rect x="{x}" y="{y-7}" width="14" height="14" rx="3" fill="{f}" stroke="{b}"/>')
        self.text(x + 22, y, label, 12, 400, "start")
        return x + 22 + round(len(label) * 6.6) + 32

    def logo(self, x, y, size=44):
        """The Conductor mark at (x, y), `size` px square, fixed ink for the white card."""
        from logomark import mark_fixed
        self.o.append(f'<svg x="{x}" y="{y}" width="{size}" height="{size}" viewBox="0 0 128 128" '
                      f'aria-label="Conductor logo" role="img">{mark_fixed()}</svg>')

    def svg(self, title, desc):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="100%" viewBox="0 0 {self.w} {self.h}" role="img" '
                f'font-family="{html.escape(FONT)}">'
                f'<title>{html.escape(title)}</title><desc>{html.escape(desc)}</desc>'
                '<defs><marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                f'<path d="M2 1L8 5L2 9" fill="none" stroke="{NAVY}" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>'
                '</marker></defs>' + "\n".join(self.o) + "</svg>")
