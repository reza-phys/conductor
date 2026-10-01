# Generates docs/logo.svg (the mark) and docs/logo-wordmark.svg (mark + name).
# "Score as agent graph": a beamed group of notes on a staff. The lead note (ink) is the orchestrator; the beam
# ties it to three voices in the diagrams' role colours (blue = orchestration, teal = agents doing work,
# gold = the audit gate). Ink follows the viewer's theme (prefers-color-scheme), so it reads on light and dark.
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

DOCS = pathlib.Path(__file__).resolve().parent.parent
from logomark import BLUE, GOLD, INK_DARK, INK_LIGHT, TEAL, mark  # noqa: F401

STYLE = (f'<style>.ink-s{{stroke:{INK_LIGHT}}}.ink-f{{fill:{INK_LIGHT}}}'
         f'@media (prefers-color-scheme: dark){{.ink-s{{stroke:{INK_DARK}}}.ink-f{{fill:{INK_DARK}}}}}</style>')
FONT = "Inter, 'IBM Plex Sans', -apple-system, 'Segoe UI', system-ui, sans-serif"

logo = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" role="img" aria-label="Conductor logo">'
        f'<title>Conductor</title>{STYLE}{mark()}</svg>')
wordmark = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 440 128" role="img" aria-label="Conductor">'
            f'<title>Conductor</title>{STYLE}{mark()}'
            f'<text class="ink-f" x="138" y="82" font-family="{FONT}" font-size="52" font-weight="650" '
            f'letter-spacing="-1">Conductor</text></svg>')
# Fixed-colour versions for raster export (PNG icon, GitHub social preview): no theme switching
FIXED = f'<style>.ink-s{{stroke:{INK_LIGHT}}}.ink-f{{fill:{INK_LIGHT}}}</style>'
icon_tile = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">{FIXED}'
             f'<rect width="128" height="128" rx="28" fill="#ffffff"/>'
             f'<g transform="translate(10 12) scale(0.84)">{mark()}</g></svg>')
social = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1280 640">{FIXED}'
          f'<rect width="1280" height="640" fill="#f3f6fa"/>'
          f'<g transform="translate(84 200) scale(1.9)">{mark()}</g>'
          f'<text class="ink-f" x="364" y="318" font-family="{FONT}" font-size="104" font-weight="650" letter-spacing="-2">Conductor</text>'
          f'<text x="368" y="392" font-family="{FONT}" font-size="34" fill="#3d4f66">Multi-agent orchestration harness for Claude Code</text>'
          f'<text x="368" y="440" font-family="{FONT}" font-size="34" fill="#3d4f66">Only verified claims flow up · hooks record everything</text></svg>')

(DOCS / "logo.svg").write_text(logo)
(DOCS / "logo-wordmark.svg").write_text(wordmark)
RASTER = pathlib.Path(__file__).resolve().parent / "_raster"  # sources rendered to PNG with macOS qlmanage, see below
RASTER.mkdir(exist_ok=True)
(RASTER / "icon.svg").write_text(icon_tile)
(RASTER / "social-preview.svg").write_text(social)
print("wrote docs/logo.svg, docs/logo-wordmark.svg (+ raster sources in docs/diagram/_raster/)")
# PNGs (macOS):  qlmanage -t -s 512 -o /tmp docs/diagram/_raster/icon.svg && mv /tmp/icon.svg.png docs/logo.png
#                qlmanage -t -s 1280 -o /tmp docs/diagram/_raster/social-preview.svg && sips --cropToHeightWidth 640 1280 /tmp/social-preview.svg.png --out docs/social-preview.png
