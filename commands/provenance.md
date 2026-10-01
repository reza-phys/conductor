---
description: Show the lineage of a claim (T1/C2), task (T1), agent id, or file — who produced it, from what evidence, and who verified it.
argument-hint: <claim | task | agent | file>
allowed-tools: Bash(python3:*)
---
!`python3 "${CLAUDE_PLUGIN_ROOT}/src/conductor/cli.py" provenance "$ARGUMENTS"`

Relay the lineage above to the user as-is. Do not take any other action.
