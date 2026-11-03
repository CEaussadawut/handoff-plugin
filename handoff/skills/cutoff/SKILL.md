---
name: cutoff
description: Stop work and write a handoff file so another Claude Code session — usually on a different account, sometimes on another machine — can continue exactly where this one left off. Use when the user runs /cutoff, or says "cut off", "hand this off", "write a handoff", "I'm out of usage", "I'm about to hit my limit", "save this for my other account", or "I need to switch accounts". Not for ordinary end-of-task summaries, commit messages, or PR descriptions.
argument-hint: "[reason, e.g. 'hit weekly limit']"
allowed-tools: Bash(python3 ${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py *)
---

# Cut off this session into a handoff

Write one markdown file that lets a **cold** session pick this work up: one with
no access to this conversation and no memory of what was said, quite possibly on
another account or another machine. Assume the reader is competent but knows
nothing about what just happened here.

Reason given by the user (may be empty): **$ARGUMENTS**

## Work fast: the budget may be nearly gone

People usually run this right before a usage limit, so the handoff has to land
before the session does. Everything you need is already in this conversation and
in the facts below. Don't explore the codebase, re-run tests, or open files to
double-check before writing. Write the file in one command, then report. Where
you're unsure, say so in the file. An honest "unverified" costs the next session
far less than a handoff that never got written.

## Facts, gathered just now

!`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py facts --project "${CLAUDE_PROJECT_DIR}" --session "${CLAUDE_SESSION_ID}"`

The account label identifies the account this session runs on, which is what
the next session needs to tell accounts apart. An email elsewhere in your
context identifies the *person*, and one person may own several accounts, so
don't substitute it. Pass `--from` only when the user has said which account
this is.

If the facts list earlier handoffs this session continues, this session picked
up older work. Write the new handoff so it stands on its own: carry forward the
decisions and dead ends that still matter. The next session should never need
to open the older file. The helper links the files and marks the old one
superseded.

## Write it

Pipe the document to the helper. It chooses the file name and stamps the
metadata (time, account, session, branch, commit, which handoff this one
continues), so the body only needs the content:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py write --slug <2-4-word-slug> --session "${CLAUDE_SESSION_ID}" --project "${CLAUDE_PROJECT_DIR}" <<'HANDOFF_EOF'
# Handoff — <short task title>
...the template below, filled in...
HANDOFF_EOF
```

Keep the quoted `'HANDOFF_EOF'`: it keeps backticks and `$` in the document
literal. The slug names the task (`fix-tray-crash`, `timestamp-camera-ui`).

## Template

````markdown
# Handoff — <short task title>

**Reason:** <the reason above, or "manual switch">

## Goal
<2–4 sentences: what we are ultimately trying to achieve, and why. Enough that
someone who has never seen this project understands the point.>

## Where things stand

**Done and verified**: ran it in this session and saw it work
- <item>: how it was verified

**Done but NOT verified**: written, never executed
- <item>: what would confirm it

**Not started**
- <item>

## Next action
<One imperative line first. /handoffs and the next session's banner show it.
Then whatever detail it needs: the command to run, the file and line to edit,
the question to answer. Not "continue the refactor". Say which file and what
change.>

## Files in play
| File | State |
|---|---|
| `path/from/repo/root` | what changed, or what still needs changing |

## Decisions already made
<Each with its reason: "Used X, not Y, because Z." This stops the next session
re-litigating settled choices.>

## Dead ends: do not retry
<What was tried and did NOT work, with the symptom. If nothing has failed yet,
write "none yet".>

## How to verify
```sh
<exact commands, and the output that means success>
```

## Open questions for the user
<What the next session must ask before proceeding, or "none".>

## Carry over verbatim
<Exact error text, API response shapes, config snippets, IDs: anything that must
not be paraphrased. Omit the section if empty.>
````

## Rules for writing it

- **Specific over complete.** Real paths, real commands, real error strings. A
  short, precise handoff beats a long, vague one. Aim for something readable in
  two minutes, usually 40–150 lines.
- **Never claim something works unless you ran it in this session and saw it
  pass.** That's what the verified / not-verified split is for. A false "done"
  costs the next session more than an honest "untested".
- **The dead-ends section is the point.** Everything else can be re-derived from
  the code. What was already tried and failed can't be.
- **Write what the next session needs in order to act**, not a summary of the
  conversation. Skip the back-and-forth.
- **No secrets.** The file may be read on another account or committed by
  accident. Never include tokens, keys, passwords, or credential file contents.
  Refer to them by name and location only.
- **If this conversation was compacted,** details from before the summary may be
  gone from your context. The facts give the transcript path. To recover one
  exact string, `grep` the transcript for it. Don't read the whole file.
- Match the user's language for prose if they've been writing in one other than
  English. Keep code, paths, and commands as they are.

## Then tell the user

Use the helper's output to fill this in:

```
Handoff written: <path>

Next action: <one line>

To continue on another account, open this project there and run /pickup.
New sessions here will also point at the handoff on their own.
```

If the helper said some work exists only on this machine, add one line: commit
and push it before switching machines, or the other side won't see it. If it
said `.handoff/` isn't git-ignored, mention that once.

Then add: **"I can't close this session from here. Quit or /clear when you're
ready."** A skill runs inside the session and can't end it. Then stop. Don't
start new work.
