---
description: Publish the Conductor status (sessions, tasks, agents, claims) as a claude.ai Artifact at one fixed URL per project. Opt-in.
argument-hint: "[enable | url <URL>]"
allowed-tools: Bash(python3:*)
---
!`python3 "${CLAUDE_PLUGIN_ROOT}/src/conductor/cli.py" artifact $ARGUMENTS`

Follow the output above exactly:
- If it says publishing is OFF, ask the human (AskUserQuestion) whether to enable it, showing what would be uploaded. Run `/conductor:artifact enable` only if they agree.
- If it built a page, publish that file with the Artifact tool (pass the given `url` when there is one, so the same page updates in place). After a first publish, save the returned URL with `/conductor:artifact url <URL>`.
- Then give the human the link in one line.
