---
name: cutoff
description: Stop work here and write a handoff file so a different Claude Code session — usually on another account — can continue exactly where this one left off. Use when the user runs /cutoff, or says "cut off", "hand this off", "write a handoff", "I'm out of usage", or "I need to switch accounts".
argument-hint: "[reason, e.g. 'hit weekly limit']"
allowed-tools: Read, Write, Bash, Glob, Grep
---

# Cut off this session into a handoff

Write one markdown file that lets a **cold** session pick this work up — no access
to this conversation, no memory of what was said. Assume the reader is competent
but knows nothing about what just happened here.

Reason given by the user (may be empty): **$ARGUMENTS**

## Facts to stamp into the header

- Account: !`python3 -c "import json,os;print((json.load(open(os.path.expanduser('~/.claude.json'))).get('oauthAccount') or {}).get('emailAddress','unknown'))" 2>/dev/null || echo unknown`
- Time: !`date '+%Y-%m-%d %H:%M %Z'`
- Directory: !`pwd`
- Branch: !`git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '(not a git repo)'`
- Uncommitted: !`git status --short 2>/dev/null | head -30 || true`
- Changed vs HEAD: !`git diff --stat HEAD 2>/dev/null | tail -25 || true`

## Steps

1. **Pick the path.** `${CLAUDE_PROJECT_DIR}/.handoff/YYYY-MM-DD-HHMM-<slug>.md`,
   where `<slug>` is 2–4 words describing the task (`fix-tray-crash`,
   `timestamp-camera-ui`). Create `.handoff/` if missing.

2. **Write the document** using the template below.

3. **Report back**: the path, a one-line summary of the next action, and the
   pickup instruction. Then stop — do not start new work.

## Template

```markdown
# Handoff — <short task title>

**From** <account> · **At** <time> · **Dir** <directory> · **Branch** <branch>
**Reason** <reason, or "manual switch">

## Goal
<2–4 sentences. What are we ultimately trying to achieve, and why. Enough that
someone who has never seen this project understands the point.>

## Where things stand

**Done and verified** — I ran it and saw it work
- <item> — how it was verified

**Done but NOT verified** — written, never executed
- <item> — what would confirm it

**Not started**
- <item>

## Next action
<The single most specific next step. A command to run, a file and line to edit,
a question to answer. Not "continue the refactor" — say which file and what change.>

## Files in play
| File | State |
|---|---|
| `path/to/file` | what changed, or what still needs changing |

## Decisions already made
<Each with the reason. This is what stops the next session re-litigating settled
choices. "Used X not Y because Z.">

## Dead ends — do not retry
<What was tried that did NOT work, and the symptom. This is the most valuable
section: it is the part the next session cannot rediscover cheaply. If nothing
failed yet, write "none yet".>

## How to verify
```sh
<exact commands to run, with expected output>
```

## Open questions for the user
<Things the next session must ask before proceeding. If none, write "none".>

## Carry over verbatim
<Exact error text, API response shapes, config snippets, IDs — anything that
must not be paraphrased. Omit the section if empty.>
```

## Rules for writing it

- **Specific over complete.** Real paths, real commands, real error strings. A
  short precise handoff beats a long vague one.
- **Never claim something works if you did not run it.** That is what the
  verified / not-verified split is for. A false "done" costs the next session
  more than an honest "untested".
- **The dead-ends section is the point.** Everything else can be re-derived from
  the code. What was already tried and failed cannot.
- **Do not summarize the conversation.** Write what the next session needs in
  order to *act*. Skip the back-and-forth.
- **No secrets.** Never write tokens, keys, or credential file contents. Refer to
  them by name and location only.
- **Keep it under ~300 lines.** If it is longer, the next session will skim it.
- Match the user's language for prose if they have been writing in one other
  than English; keep code, paths, and commands as-is.

## Then tell the user

Print exactly this shape, filled in:

```
Handoff written: <path>

Next action: <one line>

On the other account, from this same directory:
  /pickup
or paste the file contents into a new session.
```

Then add: **"I can't close this session from here — quit or /clear when you're
ready."** A slash command runs inside the session; it cannot terminate it.
