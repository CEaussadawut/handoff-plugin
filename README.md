# Handoff

A Claude Code plugin for continuing work across sessions and accounts.

You hit a usage limit mid-task. Or you need to switch machines. Or you're just
ending the day with something half-finished. `/cutoff` writes down everything a
*cold* session would need — no shared conversation, no shared memory — and
`/pickup` reads it back in on the other side.

## Install

In an interactive `claude` session:

```
/plugin marketplace add CEaussadawut/handoff-plugin
/plugin install handoff@aun-plugins
```

## Commands

| Command | What it does |
| --- | --- |
| `/cutoff [reason]` | Stops work and writes a handoff file capturing decisions, state, and the next action. |
| `/pickup [path]` | Resumes from a handoff file — most recent one by default. |
| `/handoffs` | Lists this project's handoff files, newest first, with each one's next action. |

Handoff files live in `.handoff/` inside the project.

## Typical use

Session A, out of usage:

```
/cutoff hit weekly limit
```

Session B, different account, same repo:

```
/pickup
```

The handoff is the source of truth for *what was decided*; the working tree is
the source of truth for *what actually exists*. `/pickup` reconciles the two
before continuing.

## License

MIT
