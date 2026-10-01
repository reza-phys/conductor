---
description: Initialise Conductor in this project (creates .conductor/ with config, standards and plans folder).
allowed-tools: Bash(python3:*)
---
!`python3 "${CLAUDE_PLUGIN_ROOT}/src/conductor/cli.py" init "$CLAUDE_PROJECT_DIR"`

Tell the user what was created, and that `/conductor:serve` (or `python3 <plugin>/src/conductor/cli.py serve`) opens the live dashboard.
