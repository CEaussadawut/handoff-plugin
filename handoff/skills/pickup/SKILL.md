---
name: pickup
description: Resume work from a handoff file written by /cutoff, usually on a different account. Use when the user runs /pickup, or says "continue from the handoff", "pick up where the other session left off", or "resume the handoff".
argument-hint: "[path to a specific handoff file]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Pick up a handoff

Continue work that a previous session cut off. That session had context you do
not — treat its handoff as the source of truth for *what was decided*, and the
working tree as the source of truth for *what actually exists*.

Requested file (may be empty — then use the newest): **$ARGUMENTS**

## Current state

- Handoffs, newest first: !`ls -1 "${CLAUDE_PROJECT_DIR}/.handoff/"*.md 2>/dev/null | sort -r | head -10 | grep . || echo "(none — nothing has been cut off yet)"`
- Now: !`date '+%Y-%m-%d %H:%M %Z'`
- This account: !`python3 -c "import json,os;print((json.load(open(os.path.expanduser('~/.claude.json'))).get('oauthAccount') or {}).get('emailAddress','unknown'))" 2>/dev/null || echo unknown`
- Branch: !`git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '(not a git repo)'`
- Uncommitted now: !`git status --short 2>/dev/null | head -30 || true`

## Steps

1. **Read the handoff.** Use `$ARGUMENTS` if given, otherwise the first file
   listed above. That list is sorted by filename, not modification time, on
   purpose — step 5 appends to these files, so mtime order would surface an
   already-finished handoff as the newest one. If a handoff already has an
   `## Outcome` section it was completed; say so and ask before redoing it.
   If there are no handoffs at all, say so and stop — do not guess at the task.

2. **Check it against reality before trusting it.** Read the files it names.
   Confirm the "done and verified" claims are actually present in the tree, and
   treat everything under "done but NOT verified" as unproven until you run it.
   If the working tree has moved on since the handoff was written — different
   branch, files changed, commits added — say so plainly rather than assuming.

3. **Report back before changing anything:**

   ```
   Picked up: <filename>  (from <account>, <time ago>)

   Goal:        <one line>
   Next action: <one line>

   Checked:     <what you confirmed in the tree>
   Stale:       <anything that no longer matches, or "nothing">
   Questions:   <open questions from the handoff, or "none">
   ```

4. **Then continue the work.** Start with the "Next action" from the handoff.
   Respect the "Decisions already made" — do not re-litigate a settled choice
   unless it is now demonstrably wrong, and say so if it is. Do not retry
   anything under "Dead ends".

5. **If the handoff has open questions, ask them first** and wait. Do not pick an
   answer on the previous session's behalf.

## Rules

- **The handoff can be wrong.** It was written by a session that may have run out
  of room mid-thought. Where the file and the code disagree, the code wins —
  flag the discrepancy rather than silently following either one.
- **Do not re-plan from scratch.** The point of a handoff is to skip that. If the
  plan genuinely does not survive contact with the tree, say why in one or two
  sentences, then propose the adjustment.
- **Leave a trail.** When the task finishes, append a short `## Outcome` section
  to the handoff file — what got done, and what is still open. That way the next
  `/cutoff` has something to build on and the folder reads as a history.
