#!/usr/bin/env python3
"""handoff.py - the deterministic half of the handoff plugin.

The skills (/cutoff, /pickup, /handoffs) decide what a handoff should *say*.
This script does the parts that have to be exact: where handoff files live,
their metadata, the listing, how far the repository has moved since a handoff
was written, and the two hooks (a SessionStart hint and a StopFailure
snapshot).

Standard library only, Python 3.8+. Every command fails soft: a skill's `!`
injection aborts the whole skill on a non-zero exit, and a broken hook would
show up in every session, so problems are reported as text instead of raised.
"""

import argparse
import collections
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

FORMAT = 2
DIRNAME = ".handoff"
ACTIONABLE = ("open", "auto")
HINT_MAX_AGE_DAYS = 14
# A snapshot whose transcript kept growing this long after it was written
# belongs to a session that carried on (the limit reset), so it is stale.
AUTO_STALE_SECONDS = 600
EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
META_ORDER = (
    "handoff", "status", "created", "updated", "from", "session", "transcript",
    "dir", "branch", "commit", "continues", "error", "picked_up_at",
    "picked_up_by", "picked_up_session", "done_at", "continued_in",
)

SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(-----END [A-Z ]*PRIVATE KEY-----|\Z)", re.S),
    re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_\-]{16,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\b(?:tok|key|pk|sk|rk|api|secret|token)_(?:live_|test_)?[A-Za-z0-9]{16,}\b"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/\-]{16,}=*"),
    re.compile(r"(?i)\b(password|passwd|secret|token|api[_-]?key)(\s*[:=]\s*)(\"[^\"]+\"|'[^']+'|[^\s,;]+)"),
]


# --------------------------------------------------------------------------
# small helpers

def now():
    return dt.datetime.now().astimezone()


def iso(t):
    return t.isoformat(timespec="minutes")


def parse_time(text):
    """Best effort: ISO 8601, or the v1 header's 'YYYY-MM-DD HH:MM TZ'."""
    if not text:
        return None
    text = str(text).strip()
    try:
        t = dt.datetime.fromisoformat(text)
        return t if t.tzinfo else t.astimezone()
    except ValueError:
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})", text)
    if m:
        return dt.datetime(*map(int, m.groups())).astimezone()
    return None


def time_from_name(name):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})", name)
    if not m:
        return None
    try:
        return dt.datetime(*map(int, m.groups())).astimezone()
    except ValueError:
        return None


def ago(t):
    if not t:
        return "unknown time"
    s = (now() - t).total_seconds()
    if s < 90:
        return "just now"
    if s < 5400:
        return "%dm ago" % round(s / 60)
    if s < 129600:
        return "%dh ago" % round(s / 3600)
    return "%dd ago" % round(s / 86400)


def short(text, n=110):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def redact(text):
    text = str(text or "")
    for pat in SECRET_PATTERNS:
        if pat.groups >= 3:
            text = pat.sub(lambda m: m.group(1) + m.group(2) + "[redacted]", text)
        else:
            text = pat.sub("[redacted]", text)
    return text


def quote_block(text, limit=600):
    text = redact(text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return "\n".join("> " + line if line else ">" for line in text.splitlines()) or "> (empty)"


def git(cwd, *args):
    try:
        out = subprocess.run(
            ["git", "-C", str(cwd)] + list(args),
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.rstrip("\n") if out.returncode == 0 else None


def hooks_disabled():
    return os.environ.get("HANDOFF_HOOKS", "").strip().lower() in ("0", "off", "false", "no")


# --------------------------------------------------------------------------
# where things live

def git_roots(start):
    """(worktree_root, main_checkout_root) for `start`, without running git.

    Handoffs go in the main checkout so that every worktree of a repository,
    and every session in any of them, sees the same folder.
    """
    p = Path(start).resolve()
    for d in [p] + list(p.parents):
        dotgit = d / ".git"
        if dotgit.is_dir():
            return d, d
        if dotgit.is_file():
            try:
                line = dotgit.read_text(errors="replace").strip()
            except OSError:
                return d, d
            if not line.startswith("gitdir:"):
                return d, d
            gitdir = Path(line[len("gitdir:"):].strip())
            if not gitdir.is_absolute():
                gitdir = (d / gitdir).resolve()
            commondir = gitdir / "commondir"
            if commondir.is_file():
                try:
                    common = (gitdir / commondir.read_text().strip()).resolve()
                except OSError:
                    return d, d
                if common.name == ".git":
                    return d, common.parent
            return d, d  # submodule or unusual layout: keep it local
    return None, None


def handoff_dir(project=None, cwd=None):
    base = project or os.environ.get("CLAUDE_PROJECT_DIR") or cwd or os.getcwd()
    _, main = git_roots(base)
    return Path(main or base) / DIRNAME


def config_dir():
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude").expanduser()


def find_transcript(session):
    if not session or not re.match(r"^[0-9A-Za-z_\-]+$", session):
        return None
    projects = config_dir() / "projects"
    try:
        hits = sorted(projects.glob("*/%s.jsonl" % session))
    except OSError:
        return None
    return hits[0] if hits else None


def account():
    """A label for the account this session runs on.

    ~/.claude.json (or $CLAUDE_CONFIG_DIR/.claude.json) describes the account
    the CLI last logged in with. Desktop sessions say which account they run on
    through CLAUDE_CODE_ACCOUNT_UUID, and with several profiles side by side the
    config file can belong to a different one, so it is only trusted when the
    ids agree. Otherwise the label is the profile name (when the install path
    shows one) and the account id.
    """
    env_uuid = os.environ.get("CLAUDE_CODE_ACCOUNT_UUID", "").strip()
    candidates = []
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        candidates.append(Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser() / ".claude.json")
    candidates.append(Path("~/.claude.json").expanduser())
    for cfg in candidates:
        try:
            oa = json.loads(cfg.read_text()).get("oauthAccount") or {}
        except (OSError, ValueError, AttributeError):
            continue
        if not oa:
            continue
        if env_uuid and oa.get("accountUuid") != env_uuid:
            continue
        who = oa.get("emailAddress") or oa.get("displayName")
        if who:
            return who
    if env_uuid:
        m = re.search(r"[/\\][^/\\]*profiles[/\\]([^/\\]+)[/\\]", os.environ.get("CLAUDE_CODE_EXECPATH", ""))
        return "%s (account %s)" % (m.group(1), env_uuid[:8]) if m else "account %s" % env_uuid[:8]
    return "unknown"


# --------------------------------------------------------------------------
# reading and writing handoff files

V1_HEADER = re.compile(
    r"\*\*From\*\*\s*(?P<from>.*?)\s*·\s*\*\*At\*\*\s*(?P<at>.*?)\s*·\s*"
    r"\*\*Dir\*\*\s*(?P<dir>.*?)\s*·\s*\*\*Branch\*\*\s*(?P<branch>.*)"
)


def split_frontmatter(text):
    if not text.startswith("---\n"):
        return {}, text, False
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text, False
    meta = collections.OrderedDict()
    for line in text[4:end].splitlines():
        m = re.match(r"^([A-Za-z_][A-Za-z0-9_\-]*):\s?(.*)$", line)
        if m:
            value = m.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] == '"':
                try:
                    value = json.loads(value)
                except ValueError:
                    value = value[1:-1]
            elif len(value) >= 2 and value[0] == value[-1] == "'":
                value = value[1:-1]
            meta[m.group(1)] = value
    return meta, text[end + 5:], True


def join_frontmatter(meta, body):
    keys = [k for k in META_ORDER if meta.get(k) not in (None, "")]
    keys += [k for k in meta if k not in META_ORDER and meta.get(k) not in (None, "")]
    lines = ["---"]
    for k in keys:
        v = str(meta[k]).replace("\n", " ").strip()
        if v[:1] in "\"'{[&*!|>%@`#" or ": " in v or v.endswith(":"):
            v = json.dumps(v, ensure_ascii=False)
        lines.append("%s: %s" % (k, v))
    lines.append("---")
    return "\n".join(lines) + "\n" + body.lstrip("\n")


def section(body, *names):
    """Text of the first `## <name>` section (case-insensitive prefix match)."""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"^##\s+(.*)$", line)
        if m and any(m.group(1).strip().lower().startswith(n) for n in names):
            out = []
            for nxt in lines[i + 1:]:
                if re.match(r"^##?\s", nxt):
                    break
                out.append(nxt)
            return "\n".join(out).strip()
    return ""


def first_line(text):
    in_code = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("```"):
            in_code = not in_code
            continue
        if not s or in_code:
            continue
        s = re.sub(r"^([-*>]|\d+\.)\s+", "", s)
        return s.strip()
    return ""


def read_handoff(path):
    path = Path(path)
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return None
    meta, body, has_fm = split_frontmatter(text)
    h = {"path": path, "name": path.name, "meta": meta, "body": body, "text": text}
    h["format"] = 2 if has_fm else 1
    m = re.search(r"^#\s+(?:Handoff\s*[—–-]\s*)?(.+)$", body, re.M)
    h["title"] = m.group(1).strip() if m else path.stem
    h["has_outcome"] = bool(re.search(r"^##\s+Outcome\b", body, re.M | re.I))
    v1 = V1_HEADER.search(body) if not has_fm else None
    h["from"] = meta.get("from") or (v1.group("from").strip() if v1 else "")
    h["branch"] = meta.get("branch") or (v1.group("branch").strip() if v1 else "")
    h["dir"] = meta.get("dir") or (v1.group("dir").strip() if v1 else "")
    h["commit"] = meta.get("commit", "")
    h["session"] = meta.get("session", "")
    h["created"] = (
        parse_time(meta.get("created"))
        or (parse_time(v1.group("at")) if v1 else None)
        or time_from_name(path.name)
    )
    if not h["created"]:
        try:
            h["created"] = dt.datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        except OSError:
            h["created"] = None
    status = meta.get("status", "").strip().lower()
    if not status:
        status = "done" if h["has_outcome"] else "open"
    h["status"] = status
    h["next"] = first_line(section(body, "next action", "next step"))
    h["stale"] = False
    if status == "auto":
        transcript = Path(meta["transcript"]) if meta.get("transcript") else None
        written = parse_time(meta.get("updated")) or h["created"]
        try:
            if transcript and written and transcript.is_file():
                moved = transcript.stat().st_mtime - written.timestamp()
                h["stale"] = moved > AUTO_STALE_SECONDS
        except OSError:
            pass
    return h


def scan(hdir):
    if not hdir.is_dir():
        return []
    items = [read_handoff(p) for p in hdir.glob("*.md")]
    items = [h for h in items if h]
    epoch = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)
    items.sort(key=lambda h: (h["created"] or epoch, h["name"]), reverse=True)
    return items


def actionable(h):
    return h["status"] in ACTIONABLE and not h["stale"]


def status_label(h):
    if h["status"] == "auto":
        return "auto (stale: that session carried on)" if h["stale"] else "auto-saved"
    return h["status"]


def save(h):
    h["path"].write_text(join_frontmatter(h["meta"], h["body"]))


def ensure_meta(h):
    """Give a v1 file a v2 frontmatter block, keeping what its header said."""
    if h["format"] == 2:
        return
    meta = collections.OrderedDict()
    meta["handoff"] = FORMAT
    meta["status"] = h["status"]
    if h["created"]:
        meta["created"] = iso(h["created"])
    for key in ("from", "dir", "branch"):
        if h[key]:
            meta[key] = h[key]
    h["meta"] = meta
    h["format"] = 2


def append_outcome(h, note):
    stamp = now().strftime("%Y-%m-%d %H:%M")
    if h["has_outcome"]:
        h["body"] = h["body"].rstrip() + "\n\n**%s** — %s\n" % (stamp, note.strip())
    else:
        h["body"] = h["body"].rstrip() + "\n\n## Outcome\n\n**%s** — %s\n" % (stamp, note.strip())
        h["has_outcome"] = True


def resolve_ref(hdir, ref):
    """A path, a file name, or a unique fragment of one."""
    ref = (ref or "").strip().strip("\"'")
    if not ref:
        return None, None
    p = Path(ref).expanduser()
    for cand in (p, hdir / p.name, hdir / (p.name + ".md")):
        if cand.is_file():
            return cand, None
    matches = sorted(x for x in hdir.glob("*.md") if ref.lower() in x.name.lower())
    if len(matches) == 1:
        return matches[0], None
    if matches:
        return None, "%r matches %d handoffs: %s" % (ref, len(matches), ", ".join(m.name for m in matches))
    return None, "no handoff matches %r in %s" % (ref, hdir)


# --------------------------------------------------------------------------
# repository facts

def repo_facts(cwd):
    root, main = git_roots(cwd)
    f = {"cwd": str(Path(cwd).resolve()), "root": str(root) if root else "", "main": str(main) if main else ""}
    if not root:
        return f
    f["branch"] = git(cwd, "rev-parse", "--abbrev-ref", "HEAD") or ""
    f["commit"] = git(cwd, "rev-parse", "--short", "HEAD") or ""
    f["subject"] = git(cwd, "log", "-1", "--format=%s") or ""
    upstream = git(cwd, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    f["upstream"] = upstream or ""
    if upstream:
        counts = git(cwd, "rev-list", "--left-right", "--count", "HEAD...@{u}")
        if counts and len(counts.split()) == 2:
            f["ahead"], f["behind"] = (int(x) for x in counts.split())
    status = git(cwd, "status", "--porcelain=v1", "--untracked-files=all") or ""
    lines = [l for l in status.splitlines() if l and not re.search(r"(^|/)\.handoff/", l[3:])]
    f["dirty"] = lines
    f["diffstat"] = (git(cwd, "diff", "--shortstat", "HEAD") or "").strip()
    return f


def is_ignored(hdir):
    """Whether git would ignore a file in the handoff folder (None outside git)."""
    if not git_roots(hdir.parent)[0]:
        return None
    return git(hdir.parent, "check-ignore", "-q", "--no-index", DIRNAME + "/x.md") is not None


def branch_line(f):
    if not f.get("root"):
        return "not a git repository"
    s = "%s @ %s" % (f.get("branch") or "?", f.get("commit") or "?")
    if f.get("subject"):
        s += " — %s" % short(f["subject"], 70)
    if f.get("upstream"):
        s += " · upstream %s (ahead %s, behind %s)" % (f["upstream"], f.get("ahead", "?"), f.get("behind", "?"))
    elif f.get("branch") and f.get("branch") != "HEAD":
        s += " · no upstream: this branch has never been pushed"
    return s


def dirty_block(f, limit=30):
    lines = f.get("dirty") or []
    if not lines:
        return "  (clean)"
    out = ["    " + l for l in lines[:limit]]
    if len(lines) > limit:
        out.append("    … and %d more" % (len(lines) - limit))
    return "\n".join(out)


# --------------------------------------------------------------------------
# transcripts

def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict) and b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif isinstance(b, str):
                parts.append(b)
        return "\n".join(parts)
    return ""


def clean_prompt(text):
    text = re.sub(r"<system-reminder>.*?</system-reminder>", "", text, flags=re.S)
    name = re.search(r"<command-name>\s*/?(.*?)\s*</command-name>", text, re.S)
    if name:
        args = re.search(r"<command-args>(.*?)</command-args>", text, re.S)
        return ("/%s %s" % (name.group(1), args.group(1).strip() if args else "")).strip()
    text = re.sub(r"</?(command-message|local-command-stdout|local-command-stderr)>", "", text)
    return text.strip()


def _tool_summary(name, inp):
    inp = inp if isinstance(inp, dict) else {}
    if name == "Bash":
        return "`%s`" % short(inp.get("command", "").splitlines()[0] if inp.get("command") else "", 100)
    for key in ("file_path", "notebook_path", "path", "pattern", "url"):
        if inp.get(key):
            return "%s `%s`" % (name, short(inp[key], 100))
    return name


def transcript_digest(path, last=6):
    d = {
        "title": "", "first": "", "recent": collections.deque(maxlen=last),
        "assistant": collections.deque(maxlen=2), "edited": collections.OrderedDict(),
        "todos": [], "errors": collections.deque(maxlen=5), "prompts": 0, "tool_calls": 0,
        "read_handoffs": [],
    }
    tools = {}
    try:
        fh = open(path, errors="replace")
    except OSError:
        return None
    with fh:
        for line in fh:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            kind = e.get("type")
            if kind == "custom-title" and e.get("customTitle"):
                d["title"] = e["customTitle"]
                continue
            if e.get("isSidechain") or kind not in ("user", "assistant"):
                continue
            msg = e.get("message") or {}
            content = msg.get("content")
            if kind == "user":
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                            summary = tools.get(b.get("tool_use_id"), "tool call")
                            err = first_line(_text_of(b.get("content")) or str(b.get("content") or ""))
                            d["errors"].append((summary, short(redact(err), 160)))
                    if all(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                        continue
                if e.get("isMeta"):
                    continue
                text = clean_prompt(_text_of(content))
                if not text:
                    continue
                d["prompts"] += 1
                if not d["first"]:
                    d["first"] = text
                d["recent"].append(text)
            else:
                if not isinstance(content, list):
                    continue
                for b in content:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and b.get("text", "").strip():
                        d["assistant"].append(b["text"])
                    elif b.get("type") == "tool_use":
                        d["tool_calls"] += 1
                        name, inp = b.get("name", "?"), b.get("input")
                        inp = inp if isinstance(inp, dict) else {}
                        tools[b.get("id")] = _tool_summary(name, inp)
                        target = inp.get("file_path") or inp.get("notebook_path")
                        if name in EDIT_TOOLS and target:
                            d["edited"][target] = d["edited"].get(target, 0) + 1
                            d["edited"].move_to_end(target)
                        if name == "TodoWrite" and isinstance(inp.get("todos"), list):
                            d["todos"] = inp["todos"]
                        if name == "Read" and target and "/%s/" % DIRNAME in target.replace("\\", "/"):
                            d["read_handoffs"].append(target)
    return d


def render_digest(d, transcript=None, base=None):
    def rel(path):
        try:
            return str(Path(path).resolve().relative_to(Path(base).resolve())) if base else path
        except (ValueError, OSError):
            return path

    out = []
    if d["first"]:
        out += ["## The session's first request", "", quote_block(d["first"]), ""]
    recent = [p for p in d["recent"] if p != d["first"]]
    if recent:
        out += ["## Latest messages from the user", ""]
        for p in recent:
            out += [quote_block(p, 400), ""]
    if d["assistant"]:
        out += ["## Claude's last words before it stopped", "", quote_block(d["assistant"][-1], 900), ""]
    if d["todos"]:
        out += ["## Task list at the time", ""]
        mark = {"completed": "x", "in_progress": "~"}
        for t in d["todos"][:20]:
            if isinstance(t, dict):
                out.append("- [%s] %s" % (mark.get(t.get("status"), " "), short(redact(t.get("content", "")), 140)))
        out.append("")
    if d["edited"]:
        out += ["## Files edited in the session (most recent last)", ""]
        for p, n in list(d["edited"].items())[-25:]:
            out.append("- `%s`%s" % (rel(p), " (×%d)" % n if n > 1 else ""))
        out.append("")
    if d["errors"]:
        out += ["## Recent tool errors (possible dead ends)", ""]
        for summary, err in d["errors"]:
            out.append("- %s → %s" % (redact(summary), err or "(no message)"))
        out.append("")
    out.append("_%d user messages, %d tool calls%s._" % (
        d["prompts"], d["tool_calls"], (" · transcript: `%s`" % transcript) if transcript else ""))
    return "\n".join(out)


# --------------------------------------------------------------------------
# commands

def cmd_facts(a):
    hdir = handoff_dir(a.project)
    f = repo_facts(os.getcwd())
    t = now()
    transcript = find_transcript(a.session)
    print("- Account: %s" % account())
    print("- Time: %s (%s)" % (t.strftime("%Y-%m-%d %H:%M %Z").strip(), iso(t)))
    print("- Session: %s · transcript: %s" % (a.session or "unknown", transcript or "not found"))
    print("- Working directory: %s" % f["cwd"])
    if f.get("root") and f["root"] != f.get("main"):
        print("- Worktree: %s (main checkout: %s)" % (f["root"], f["main"]))
    print("- Branch: %s" % branch_line(f))
    print("- Uncommitted changes:\n%s" % dirty_block(f))
    if f.get("diffstat"):
        print("- Diff vs HEAD: %s" % f["diffstat"])
    print("- Handoff folder: %s%s" % (hdir, " (not git-ignored)" if is_ignored(hdir) is False else ""))
    chain = predecessors(hdir, a.session)
    if chain:
        print("- Earlier handoffs this session continues: %s" % ", ".join(
            "%s (%s)" % (h["name"], status_label(h)) for h in chain))


def predecessors(hdir, session):
    """Handoffs this session picked up, plus its own auto-snapshot."""
    if not session:
        return []
    out = []
    for h in scan(hdir):
        m = h["meta"]
        if m.get("picked_up_session") == session and h["status"] == "picked-up":
            out.append(h)
        elif h["status"] == "auto" and h["session"] == session:
            out.append(h)
    out.sort(key=lambda h: h["status"] != "picked-up")
    return out


def slugify(text):
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:48].strip("-") or "handoff"


def cmd_write(a):
    body = sys.stdin.read()
    meta_in, body, had_fm = split_frontmatter(body)
    if not body.strip():
        print("Nothing written: the handoff body on stdin was empty.")
        return
    hdir = handoff_dir(a.project)
    try:
        hdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print("Could not create %s: %s" % (hdir, exc))
        return
    t = now()
    f = repo_facts(os.getcwd())
    stem = "%s-%s" % (t.strftime("%Y-%m-%d-%H%M"), slugify(a.slug))
    path, n = hdir / (stem + ".md"), 2
    while path.exists():
        path, n = hdir / ("%s-%d.md" % (stem, n)), n + 1
    chain = predecessors(hdir, a.session)
    meta = collections.OrderedDict()
    meta["handoff"] = FORMAT
    meta["status"] = "open"
    meta["created"] = iso(t)
    meta["from"] = a.from_ or meta_in.get("from") or account()
    meta["session"] = a.session or ""
    transcript = find_transcript(a.session)
    meta["transcript"] = str(transcript) if transcript else ""
    meta["dir"] = f.get("root") or f["cwd"]
    meta["branch"] = f.get("branch", "")
    meta["commit"] = f.get("commit", "")
    meta["continues"] = chain[0]["name"] if chain else ""
    try:
        path.write_text(join_frontmatter(meta, body.strip() + "\n"))
    except OSError as exc:
        print("Could not write %s: %s" % (path, exc))
        return
    for h in chain:
        ensure_meta(h)
        h["meta"]["status"] = "superseded"
        h["meta"]["continued_in"] = path.name
        append_outcome(h, "Not finished here; continued in `%s`." % path.name)
        try:
            save(h)
        except OSError:
            pass
    h = read_handoff(path)
    print("Handoff written: %s" % path)
    print("Next action: %s" % (short(h["next"], 200) if h and h["next"] else "(no '## Next action' section found)"))
    if chain:
        print("Continues: %s (now marked superseded)" % ", ".join(c["name"] for c in chain))
    local_only = []
    if f.get("dirty"):
        local_only.append("%d uncommitted file(s)" % len(f["dirty"]))
    if f.get("root") and not f.get("upstream") and f.get("branch") not in ("", "HEAD"):
        local_only.append("branch %s has no upstream" % f["branch"])
    elif f.get("ahead"):
        local_only.append("%d unpushed commit(s)" % f["ahead"])
    if local_only:
        print("Only on this machine: %s." % "; ".join(local_only))
    if is_ignored(hdir) is False:
        print("Note: %s/ is not git-ignored in this repository." % DIRNAME)
    if had_fm:
        print("Note: the frontmatter you supplied was replaced with generated metadata.")


def cmd_list(a):
    hdir = handoff_dir(a.project)
    items = scan(hdir)
    if a.json:
        print(json.dumps([{
            "file": str(h["path"]), "title": h["title"], "status": h["status"], "stale": h["stale"],
            "created": iso(h["created"]) if h["created"] else None, "from": h["from"],
            "next": h["next"], "branch": h["branch"],
        } for h in items], indent=1, ensure_ascii=False))
        return
    if not items:
        print("(none — no handoffs in %s yet; /cutoff creates one)" % hdir)
        return
    print("Handoffs in %s, newest first:\n" % hdir)
    print("| When | From | Task | Status | Next action | Name |")
    print("|---|---|---|---|---|---|")
    for h in items[: a.limit]:
        when = "%s (%s)" % (h["created"].strftime("%b %d %H:%M"), ago(h["created"])) if h["created"] else "?"
        who = (h["from"] or "?").split("@")[0]
        name = re.sub(r"^\d{4}-\d{2}-\d{2}-\d{4}-", "", h["path"].stem)
        cells = [when, who, short(h["title"], 60), status_label(h), short(h["next"], 80) or "—", "`%s`" % name]
        print("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    if len(items) > a.limit:
        print("\n… %d older not shown." % (len(items) - a.limit))


def pick_default(items):
    for h in items:
        if actionable(h):
            return h, None
    for h in items:
        if h["status"] == "picked-up":
            return h, "No open handoff; the newest one already picked up is shown."
    return None, None


def cmd_resolve(a):
    hdir = handoff_dir(a.project)
    items = scan(hdir)
    if a.ref:
        path, err = resolve_ref(hdir, a.ref)
        if err:
            print(err)
            return
        h, note = read_handoff(path), None
    else:
        h, note = pick_default(items)
    if not h:
        if items:
            print("No open handoff in %s. Every handoff there is done or superseded:" % hdir)
            for x in items[:8]:
                print("- %s — %s (%s, %s)" % (x["name"], short(x["title"], 60), status_label(x), ago(x["created"])))
        else:
            print("No handoffs in %s — nothing has been cut off here yet." % hdir)
        return
    f = repo_facts(os.getcwd())
    m = h["meta"]
    print("## Handoff to resume\n")
    if note:
        print("_%s_\n" % note)
    print("- File: %s" % h["path"])
    print("- Status: %s · written %s by %s" % (status_label(h), ago(h["created"]), h["from"] or "unknown"))
    if h["status"] == "auto":
        print("- Auto-saved when that session stopped on `%s`. Nobody curated it: decisions, dead ends and"
              " verification status were not recorded." % (m.get("error") or "an API error"))
    if h["status"] == "picked-up":
        print("- Already picked up %s by %s (session %s). That session may have changed the tree since." % (
            ago(parse_time(m.get("picked_up_at"))), m.get("picked_up_by") or "?", m.get("picked_up_session") or "?"))
    if h["status"] in ("done", "superseded"):
        print("- This handoff is %s%s." % (h["status"], (" → continued in %s" % m["continued_in"]) if m.get("continued_in") else ""))
    if h["session"]:
        transcript = Path(m["transcript"]) if m.get("transcript") else find_transcript(h["session"])
        if transcript and transcript.is_file():
            size = transcript.stat().st_size / 1e6
            print("- Its session's transcript is on this machine: %s (%.1f MB)" % (transcript, size))
        else:
            print("- Its session's transcript is not on this machine.")
    if a.session and a.session == h["session"]:
        print("- Written by this same session (it was cleared or resumed since).")
    rec = "%s @ %s in %s" % (h["branch"] or "?", h["commit"] or "?", h["dir"] or "?")
    print("- Recorded: %s" % rec)
    print("- Now: %s in %s" % (branch_line(f), f.get("root") or f["cwd"]))
    if h["dir"] and f.get("root") and Path(h["dir"]).resolve() != Path(f["root"]).resolve():
        print("- Different directory than the handoff's: the work may live in another worktree or clone.")
    if h["commit"] and f.get("root"):
        if git(f["cwd"], "cat-file", "-e", h["commit"] + "^{commit}") is None:
            print("- The recorded commit %s is not in this repository (different clone, or not fetched)." % h["commit"])
        else:
            log = git(f["cwd"], "log", "--oneline", "-n", "15", "%s..HEAD" % h["commit"]) or ""
            print("- Commits since the handoff: %s" % ("none" if not log else ""))
            for line in log.splitlines():
                print("    " + line)
    print("- Uncommitted now:\n%s" % dirty_block(f))
    files = re.findall(r"^\|\s*`([^`]+)`", section(h["body"], "files in play"), re.M)
    if files:
        base = Path(f.get("root") or h["dir"] or f["cwd"])
        states = []
        for rel in files[:20]:
            p = Path(rel) if os.path.isabs(rel) else base / rel
            if not p.exists():
                states.append("`%s` missing" % rel)
            elif h["created"] and p.stat().st_mtime > h["created"].timestamp() + 60:
                states.append("`%s` touched since" % rel)
            else:
                states.append("`%s` untouched since" % rel)
        print("- Files in play: " + "; ".join(states))
    others = [x for x in items if actionable(x) and x["path"] != h["path"]]
    if others:
        print("- Other open handoffs: " + "; ".join("%s (%s)" % (x["name"], ago(x["created"])) for x in others[:5]))
    print("\n## Handoff content\n")
    print(h["text"].rstrip())


def cmd_mark(a):
    hdir = handoff_dir(a.project)
    path, err = resolve_ref(hdir, a.ref)
    if err or not path:
        print(err or "no handoff given")
        return
    h = read_handoff(path)
    ensure_meta(h)
    m = h["meta"]
    m["status"] = a.status
    t = iso(now())
    if a.status == "picked-up":
        m["picked_up_at"] = t
        m["picked_up_by"] = a.by or account()
        m["picked_up_session"] = a.session or ""
    elif a.status == "done":
        m["done_at"] = t
    elif a.status == "superseded" and a.continued_in:
        m["continued_in"] = a.continued_in
    note = sys.stdin.read() if a.note == "-" else a.note
    if note and note.strip():
        append_outcome(h, note)
    elif a.status == "done" and not h["has_outcome"]:
        append_outcome(h, "Finished (no summary given).")
    try:
        save(h)
    except OSError as exc:
        print("Could not update %s: %s" % (path, exc))
        return
    print("%s → %s" % (path.name, a.status))


def cmd_digest(a):
    d = transcript_digest(a.transcript, last=a.last)
    if d is None:
        print("Could not read transcript %s" % a.transcript)
        return
    if d["title"]:
        print("Session title: %s\n" % redact(d["title"]))
    root, _ = git_roots(os.getcwd())
    print(render_digest(d, a.transcript, base=root))


def read_hook_input():
    try:
        data = json.loads(sys.stdin.read() or "{}")
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def cmd_hook_session_start(a):
    if hooks_disabled():
        return
    data = read_hook_input()
    hdir = handoff_dir(cwd=data.get("cwd"))
    if not hdir.is_dir():
        return
    cutoff = now() - dt.timedelta(days=HINT_MAX_AGE_DAYS)
    fresh = [h for h in scan(hdir) if actionable(h) and h["created"] and h["created"] > cutoff]
    if not fresh:
        return
    h = fresh[0]
    kind = "an auto-saved snapshot (its session stopped on a usage limit)" if h["status"] == "auto" else "an open handoff"
    rel = "%s/%s" % (DIRNAME, h["name"])
    context = "This project has %s: %s, \"%s\", written %s by %s on branch %s." % (
        kind, rel, short(h["title"], 80), ago(h["created"]), h["from"] or "an unknown account", h["branch"] or "?")
    if len(fresh) > 1:
        context += " %d more open handoffs are listed by /handoffs." % (len(fresh) - 1)
    context += " The /pickup skill resumes it after checking it against the working tree."
    message = "Open handoff: \"%s\" (%s, from %s). Run /pickup to resume it." % (
        short(h["title"], 60), ago(h["created"]), (h["from"] or "?").split("@")[0])
    print(json.dumps({
        "systemMessage": message,
        "hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context},
    }))


def cmd_hook_stop_failure(a):
    if hooks_disabled():
        return
    data = read_hook_input()
    session = data.get("session_id") or ""
    cwd = data.get("cwd") or os.getcwd()
    hdir = handoff_dir(cwd=cwd)
    transcript = data.get("transcript_path") or str(find_transcript(session) or "")
    d = transcript_digest(transcript) if transcript else None
    if not d or (d["prompts"] < 2 and not d["edited"] and d["tool_calls"] < 3):
        return  # nothing worth handing off yet
    existing = None
    for h in scan(hdir):
        if h["session"] != session or not session:
            continue
        if h["status"] == "auto":
            existing = h
        elif h["created"] and (now() - h["created"]).total_seconds() < 1800:
            return  # this session wrote a real handoff moments ago; it says more than a snapshot
    try:
        hdir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    t = now()
    f = repo_facts(cwd)
    if existing:
        path, created = existing["path"], existing["meta"].get("created") or iso(t)
    else:
        path = hdir / ("%s-auto-%s.md" % (t.strftime("%Y-%m-%d-%H%M"), (session or "session")[:8]))
        created = iso(t)
    continues = ""
    for read_path in reversed(d["read_handoffs"]):
        if Path(read_path).name != path.name and (hdir / Path(read_path).name).is_file():
            continues = Path(read_path).name
            break
    error = data.get("error") or "error"
    detail = short(data.get("last_assistant_message") or data.get("error_details") or "", 120)
    meta = collections.OrderedDict()
    meta["handoff"] = FORMAT
    meta["status"] = "auto"
    meta["created"] = created
    meta["updated"] = iso(t)
    meta["from"] = account()
    meta["session"] = session
    meta["transcript"] = transcript or ""
    meta["dir"] = f.get("root") or f["cwd"]
    meta["branch"] = f.get("branch", "")
    meta["commit"] = f.get("commit", "")
    meta["continues"] = continues
    meta["error"] = "%s — %s" % (error, detail) if detail else error
    title = redact(d["title"] or short(d["first"], 70) or "untitled session")
    asks = [p for p in d["recent"] if not p.startswith("/")] or list(d["recent"]) or [d["first"]]
    last_ask = short(redact(asks[-1]), 160)
    body = [
        "# Handoff — %s (auto-saved)" % title,
        "",
        "Written by the handoff plugin, not by Claude: the session stopped on `%s` before anyone could"
        " run /cutoff. This is a mechanical snapshot of the session's last state. Nothing below says what"
        " was verified, what was decided, or what already failed — reconstruct that from the transcript"
        " and the working tree before trusting it." % error,
        "",
        "## Next action",
        "",
        "Re-establish the state, then continue the user's last request: %s" % last_ask,
        "",
        render_digest(d, transcript, base=f.get("root") or f["cwd"]),
        "",
        "## Repository at the time",
        "",
        "- Directory: `%s`" % (f.get("root") or f["cwd"]),
        "- Branch: %s" % branch_line(f),
        "- Uncommitted changes:",
        "",
        "```",
        "\n".join(f.get("dirty") or ["(clean)"]),
        "```",
    ]
    try:
        path.write_text(join_frontmatter(meta, "\n".join(body) + "\n"))
    except OSError:
        return


def main(argv=None):
    p = argparse.ArgumentParser(prog="handoff.py", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd")

    s = sub.add_parser("facts", help="facts for /cutoff to stamp into a handoff")
    s.add_argument("--project")
    s.add_argument("--session")
    s.set_defaults(fn=cmd_facts)

    s = sub.add_parser("write", help="write a handoff; body on stdin, metadata generated")
    s.add_argument("--slug", required=True)
    s.add_argument("--session")
    s.add_argument("--from", dest="from_")
    s.add_argument("--project")
    s.set_defaults(fn=cmd_write)

    s = sub.add_parser("list", help="table of handoffs, newest first")
    s.add_argument("--project")
    s.add_argument("--limit", type=int, default=15)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("resolve", help="pick a handoff for /pickup and check it against the tree")
    s.add_argument("ref", nargs="?")
    s.add_argument("--project")
    s.add_argument("--session")
    s.set_defaults(fn=cmd_resolve)

    s = sub.add_parser("mark", help="set a handoff's status; an outcome note may come on stdin")
    s.add_argument("ref")
    s.add_argument("status", choices=["open", "picked-up", "done", "superseded"])
    s.add_argument("--project")
    s.add_argument("--session")
    s.add_argument("--by")
    s.add_argument("--note", help="outcome note to append; '-' reads it from stdin")
    s.add_argument("--continued-in")
    s.set_defaults(fn=cmd_mark)

    s = sub.add_parser("digest", help="compact, redacted digest of a session transcript")
    s.add_argument("transcript")
    s.add_argument("--last", type=int, default=8)
    s.set_defaults(fn=cmd_digest)

    s = sub.add_parser("hook-session-start")
    s.set_defaults(fn=cmd_hook_session_start)
    s = sub.add_parser("hook-stop-failure")
    s.set_defaults(fn=cmd_hook_stop_failure)

    a = p.parse_args(argv)
    if not getattr(a, "fn", None):
        p.print_help()
        return 0
    try:
        a.fn(a)
    except Exception as exc:  # fail soft, see module docstring
        if a.cmd.startswith("hook-"):
            return 0
        print("handoff.py %s failed: %s: %s" % (a.cmd, type(exc).__name__, exc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
