"""Turn status data into the self-contained dashboard page, with a structural self-check.

Shared by the live renderer and dashboard/preview.py. Stdlib only, Python 3.9+.
"""
import json
import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[2] / "dashboard" / "template.html"
TOKEN = "/*__STATUS_JSON__*/null"


def embed_json(data):
    """JSON safe to place inside a <script> element."""
    s = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # "</" can never close the script; "<!--" can never enter the script-data escape state.
    # Both sequences can only occur inside JSON strings, where "\/" and "!" are valid escapes.
    return s.replace("</", "<\\/").replace("<!--", "<\\u0021--")


def render(template, data):
    if template.count(TOKEN) != 1:
        raise ValueError("template must contain the token %r exactly once (found %d)" % (TOKEN, template.count(TOKEN)))
    payload = embed_json(data)
    return template.replace(TOKEN, payload), payload


def check(html, payload):
    errors = []
    # Structural checks run on the page minus the data payload, so data text can't fake/break tag counts.
    shell = html.replace(payload, "", 1)
    low = shell.lower()

    titles = re.findall(r"<title\b", low)
    body_at = low.find("<body")
    if len(titles) != 1:
        errors.append("expected exactly one <title>, found %d" % len(titles))
    elif body_at < 0 or low.find("<title") > body_at:
        errors.append("<title> must appear before <body>")

    if TOKEN in html or "__STATUS_JSON__" in html:
        errors.append("status token not replaced")
    # window.__STATUS__ is the intended global; anything else starting with __STATUS is a template leak.
    if "__STATUS" in html.replace("window.__STATUS__", ""):
        errors.append("leftover __STATUS placeholder")
    if re.search(r"\{\{|\}\}|/\*__", shell):
        errors.append("template marker leaked ({{ }} or /*__)")

    for tag in ("script", "style", "details"):
        opened = len(re.findall(r"<%s\b" % tag, low))
        closed = len(re.findall(r"</%s\s*>" % tag, low))
        if opened != closed:
            errors.append("unbalanced <%s>: %d open vs %d close" % (tag, opened, closed))

    if "</" in payload or "<!--" in payload:
        errors.append("payload not script-safe")
    try:
        json.loads(payload)
    except ValueError as e:
        errors.append("payload is not valid JSON: %s" % e)
    return errors
