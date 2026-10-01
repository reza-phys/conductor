---
description: Open the live Conductor dashboard (local server on 127.0.0.1:8765, updates on every event).
allowed-tools: Bash(python3:*), Bash(nohup:*)
---
!`(nohup python3 "${CLAUDE_PLUGIN_ROOT}/src/conductor/cli.py" serve --port 8765 >/dev/null 2>&1 &) ; sleep 0.5; echo "Dashboard: http://127.0.0.1:8765"`

Give the user the dashboard link above. If the port was already in use, the dashboard is most likely already running at that address.
