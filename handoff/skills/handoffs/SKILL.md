---
name: handoffs
description: List this project's handoff files, newest first, with who wrote each one, its status (open, auto-saved, picked up, done, superseded), and its next action. Use when the user runs /handoffs, or asks which handoffs exist, what's pending from another session or account, or what a previous session left unfinished.
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py *)
---

# Handoffs in this project

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py list --project "${CLAUDE_PROJECT_DIR}"`

Show that table as it is. It's already sorted and summarized from each file, so
you don't need to open the files. Under it, add one line: `/pickup` resumes the
newest open handoff, and `/pickup <name>` resumes a specific one. If there are
no handoffs, say so and mention that `/cutoff` creates one.

What the statuses mean, if the user asks:

- **open**: written by /cutoff, not resumed yet
- **auto-saved**: captured automatically when a session stopped on a usage limit
- **picked-up**: a session resumed it and may still be working on it
- **done**: finished, with the outcome recorded at the bottom of the file
- **superseded**: continued in a later handoff
- **stale**: an auto-save whose session carried on after the limit reset
