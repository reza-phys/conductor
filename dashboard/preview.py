#!/usr/bin/env python3
"""Render dashboard/template.html with sample-status.json -> dashboard/preview.html, then self-check.

Stdlib only, Python 3.9+. Exits non-zero (and writes nothing) if the output fails validation.
Usage: python3 dashboard/preview.py [status.json] [out.html]
"""
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from conductor.page import check, render  # noqa: E402


def main(argv):
    src = Path(argv[1]) if len(argv) > 1 else HERE / "sample-status.json"
    out = Path(argv[2]) if len(argv) > 2 else HERE / "preview.html"
    template = (HERE / "template.html").read_text(encoding="utf-8")
    data = json.loads(src.read_text(encoding="utf-8"))
    for key in ("schema", "program", "updated"):
        if key not in data:
            print("FAIL: status JSON missing required key %r" % key, file=sys.stderr)
            return 1
    try:
        html, payload = render(template, data)
    except ValueError as e:
        print("FAIL: %s" % e, file=sys.stderr)
        return 1
    errors = check(html, payload)
    if errors:
        for e in errors:
            print("FAIL: %s" % e, file=sys.stderr)
        return 1
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(out)
    print("ok: wrote %s (%d bytes)" % (out, len(html.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
