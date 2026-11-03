# Handoff

A Claude Code plugin for continuing work across sessions and accounts.

You hit a usage limit mid-task. Or you need to switch machines. Or you're just
ending the day with something half-finished. `/cutoff` writes down everything a
*cold* session would need (no shared conversation, no shared memory), and
`/pickup` reads it back in on the other side.

## Install

In an interactive `claude` session:

```
/plugin marketplace add CEaussadawut/handoff-plugin
/plugin install handoff@aun-plugins
```

Requires `python3` (3.7 or later) on your `PATH`. Git is optional, but most of
the drift checks need it.

## Commands

| Command | What it does |
| --- | --- |
| `/cutoff [reason]` | Stops work and writes a handoff capturing decisions, dead ends, state, and the next action. |
| `/pickup [name]` | Resumes the newest open handoff (or the one you name), after checking it against the working tree. |
| `/handoffs` | Lists this project's handoffs, newest first, with status and next action. |

## What happens without you asking

- **A new session points at open handoffs.** When a session starts (or after
  `/clear`) in a project with an open handoff from the last 14 days, you'll see
  `Open handoff: "…" — run /pickup to resume it`, and Claude is told the same.
- **Hitting a usage limit auto-saves the session.** Once you're out of usage,
  `/cutoff` can't run. So when a turn fails on a rate or billing limit, the
  plugin saves a snapshot on its own: your recent requests, Claude's last
  message, the task list, edited files, recent errors, and the git state.
  Secrets that look like tokens or keys are redacted. `/pickup` on the other
  account treats it as a starting point and rebuilds the rest from the
  transcript and the tree.

To turn both off, set `HANDOFF_HOOKS=0`, for example in `~/.claude/settings.json`:

```json
{ "env": { "HANDOFF_HOOKS": "0" } }
```

## Typical use

Session A, about to run out:

```
/cutoff hit weekly limit
```

Session B, different account, same repo:

```
/pickup
```

The handoff is the source of truth for *what was decided*; the working tree is
the source of truth for *what actually exists*. `/pickup` reconciles the two
before continuing. It also lists the commits made since the handoff and flags
files that changed, or a branch or worktree that doesn't match.

## Handoff files

Handoffs live in `.handoff/` at the root of the main checkout, so every
worktree of a repository shares one folder. Each file is plain markdown with a
small metadata header:

```markdown
---
handoff: 2
status: open
created: 2026-11-03T22:45+07:00
from: you@example.com
session: 3f2b9c1e-…
branch: fix/tray
commit: 9f2c1d0
---
# Handoff — Fix tray crash on resume
## Goal …
## Where things stand …
## Next action …
## Dead ends: do not retry …
```

A handoff's `status` moves from `open` to `picked-up` to `done`. When a picked-up
session runs `/cutoff` again, the new file links back (`continues:`) and the
old one becomes `superseded`, so the folder reads as a history. Auto-saved
snapshots have the status `auto`. Files written by version 0.1 have no header
and still work.

Uncommitted work stays on the machine where it was written. If you're
switching machines, commit and push first; `/cutoff` warns you when this
applies. Add `.handoff/` to `.gitignore` unless you want handoffs committed.

## Development

```
python3 -m unittest discover -s tests -v
claude plugin validate --strict .
claude plugin validate --strict ./handoff
```

`handoff/evals/` holds behavioral eval cases: a cutoff mid-debugging, a pickup
whose handoff no longer matches the tree, and a pickup after an auto-save.
Each case builds its own throwaway repository. Running them makes real, billed
model calls (Claude Code 2.1.269 or later):

```
claude plugin eval ./handoff --allow-tools Write,Edit,Bash --scaffold
```

## License

MIT
