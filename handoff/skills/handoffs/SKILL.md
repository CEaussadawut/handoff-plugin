---
name: handoffs
description: List the handoff files in this project, newest first, with who wrote each one and what its next action was. Use when the user runs /handoffs or asks which handoffs exist.
allowed-tools: Read, Bash, Glob
---

# Handoffs in this project

Files, newest first: !`ls -1 "${CLAUDE_PROJECT_DIR}/.handoff/"*.md 2>/dev/null | sort -r | grep . || echo "(none — nothing has been cut off yet)"`

Read each file's header and its **Next action** line, then print a table, newest first:

| When | From | Task | Next action | Done? |
|---|---|---|---|---|

Set **Done?** from whether the file has an `## Outcome` section: `yes` if it does,
`open` if it does not.

Keep it to one line per handoff. If there are none, say so and mention that
`/cutoff` creates one.
