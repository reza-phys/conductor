---
description: Show who is doing what right now (agent tree, tasks, items needing you). No tokens spent by agents, nothing interrupted.
allowed-tools: Bash(python3:*)
---
!`python3 "${CLAUDE_PLUGIN_ROOT}/src/conductor/cli.py" status`

Relay the status above to the user as-is (it is already formatted). Do not take any other action.
