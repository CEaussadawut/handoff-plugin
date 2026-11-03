#!/usr/bin/env python3
"""Build the invoice-tool repository that the handoff eval cases run in.

    python3 build.py <scenario> <target-dir>

Scenarios:
  cutoff                mid-session: a verified fix, an unverified change, a
                        token in .env, nothing committed
  drift                 a handoff from 3h ago whose "verified" retry change is
                        gone from the tree, plus one commit made since
  autosave              the session died on a usage limit; only the plugin's
                        auto-saved snapshot and the transcript exist
  autosave-no-snapshot  the same, as the 0.1 plugin would have left it: no
                        snapshot at all
"""
import datetime as dt
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parents[1] / "scripts" / "handoff.py"
TOKEN = "tok_9f8e7d6c5b4a39281706f5e4d3c2b1a0"
SESSION = "5f0c2d9e-8a71-4b3e-9c62-0d4e7a1b3c58"

PARSE_BUGGY = '''"""Parse invoice CSV lines: SKU,QTY,UNIT_PRICE[,DISCOUNT]."""


def parse_line(line):
    sku, qty, price, *rest = [p.strip() for p in line.split(",")]
    return sku, int(qty), float(price), parse_discount(rest[0] if rest else "0")


def parse_discount(text):
    text = text.strip()
    if text.endswith("%"):
        return float(text[:-1]) / 10
    return float(text)


def line_total(qty, price, discount):
    # Round per line with plain round(): the legacy billing system does the same.
    return round(qty * price * (1 - discount), 2)


def invoice_total(lines):
    return round(sum(line_total(q, p, d) for _, q, p, d in map(parse_line, lines)), 2)
'''
PARSE_FIXED = PARSE_BUGGY.replace("float(text[:-1]) / 10", "float(text[:-1]) / 100")

CLIENT = '''import json
import urllib.request

API_URL = "https://staging.billing.example.com/v1/totals"


def post_total(invoice_id, total, token):
    req = urllib.request.Request(
        API_URL,
        data=json.dumps({"invoice": invoice_id, "total": total}).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)
'''
CLIENT_RETRY = '''import json
import time
import urllib.error
import urllib.request

API_URL = "https://staging.billing.example.com/v1/totals"
ATTEMPTS = 3
BACKOFF = 0.5


def post_total(invoice_id, total, token):
    req = urllib.request.Request(
        API_URL,
        data=json.dumps({"invoice": invoice_id, "total": total}).encode(),
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as exc:
            if exc.code < 500 or attempt == ATTEMPTS - 1:
                raise
        except urllib.error.URLError:
            if attempt == ATTEMPTS - 1:
                raise
        time.sleep(BACKOFF * 2 ** attempt)
'''
TEST_PARSE = '''from invoice.parse import invoice_total, parse_discount


def test_percent_discount():
    assert parse_discount("15%") == 0.15


def test_flat_discount():
    assert parse_discount("0.1") == 0.1


def test_invoice_total_with_discount():
    assert invoice_total(["WIDGET,3,10.00,15%"]) == 25.5


def test_invoice_total_without_discount():
    assert invoice_total(["GADGET,2,4.25"]) == 8.5


def test_rounding_matches_legacy():
    # Legacy billing rounds each line with float round(); keep it that way.
    assert invoice_total(["BOLT,3,3.335"]) == 10.0
'''
TEST_CLIENT = '''from unittest import mock
import urllib.error

import pytest

from invoice import client


def _http_error(code):
    return urllib.error.HTTPError(client.API_URL, code, "err", {}, None)


@mock.patch("invoice.client.time.sleep")
@mock.patch("urllib.request.urlopen")
def test_retries_on_503(urlopen, _sleep):
    ok = mock.MagicMock()
    ok.__enter__.return_value.read.return_value = b'{"ok": true}'
    urlopen.side_effect = [_http_error(503), ok]
    assert client.post_total("INV-1", 25.5, "t") == {"ok": True}


@mock.patch("invoice.client.time.sleep")
@mock.patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down"))
def test_gives_up_after_three_attempts(urlopen, _sleep):
    with pytest.raises(urllib.error.URLError):
        client.post_total("INV-1", 25.5, "t")
    assert urlopen.call_count == 3


@mock.patch("urllib.request.urlopen")
def test_no_retry_on_400(urlopen):
    urlopen.side_effect = _http_error(400)
    with pytest.raises(urllib.error.HTTPError):
        client.post_total("INV-1", 25.5, "t")
    assert urlopen.call_count == 1
'''
README = "# invoice-tool\n\nTotals invoice CSV files and posts them to the billing API.\n"


def sh(cwd, *args, when=None):
    env = dict(os.environ, GIT_AUTHOR_NAME="Dev", GIT_AUTHOR_EMAIL="dev@example.com",
               GIT_COMMITTER_NAME="Dev", GIT_COMMITTER_EMAIL="dev@example.com")
    if when:
        env["GIT_AUTHOR_DATE"] = env["GIT_COMMITTER_DATE"] = when.isoformat()
    subprocess.run(["git", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"] + list(args),
                   cwd=cwd, env=env, check=True, capture_output=True)


def put(root, rel, text, age_minutes=None):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    if age_minutes is not None:
        t = (dt.datetime.now() - dt.timedelta(minutes=age_minutes)).timestamp()
        os.utime(p, (t, t))
    return p


def base_repo(root):
    now = dt.datetime.now().astimezone()
    root.mkdir(parents=True, exist_ok=True)
    sh(root, "init", "-q", "-b", "main")
    put(root, "README.md", README)
    put(root, ".gitignore", ".env\n__pycache__/\n.pytest_cache/\n.handoff/\n.claude-home/\n")
    put(root, "invoice/__init__.py", "")
    put(root, "invoice/parse.py", PARSE_BUGGY)
    put(root, "invoice/client.py", CLIENT)
    put(root, "tests/test_parse.py", TEST_PARSE)
    sh(root, "add", ".")
    sh(root, "commit", "-q", "-m", "Initial invoice tool", when=now - dt.timedelta(days=3))
    sh(root, "checkout", "-q", "-b", "fix/discount-and-retry")
    return now


def scenario_cutoff(root):
    base_repo(root)
    put(root, "invoice/parse.py", PARSE_FIXED)
    put(root, "invoice/client.py", CLIENT_RETRY)
    put(root, "tests/test_client.py", TEST_CLIENT)
    put(root, ".env", "INVOICE_API_TOKEN=%s\n" % TOKEN)


DRIFT_HANDOFF = """---
handoff: 2
status: open
created: {created}
from: ce.aun@example.com
session: 0b9d6a52-3c1e-4f7a-a8d2-6e5f4c3b2a19
dir: {root}
branch: fix/discount-and-retry
commit: {commit}
---
# Handoff — Fix percent discounts and add API retries

**Reason:** hit weekly limit

## Goal
Invoice totals were wrong (even negative) for lines with a percentage discount,
e.g. `WIDGET,3,10.00,15%`. Fix the parsing, then make `post_total` survive the
staging API's intermittent 503s.

## Where things stand

**Done and verified**: ran it in this session and saw it work
- Percent discounts divide by 100, not 10 (`invoice/parse.py`): `python -m pytest tests/test_parse.py -q` gave `5 passed`. Committed.
- Retry with exponential backoff in `invoice/client.py` (3 attempts, 0.5s base, retries URLError and 5xx): `python -m pytest tests/test_client.py -q` gave `3 passed`.

**Done but NOT verified**
- none

**Not started**
- README note on the retry behaviour

## Next action
Ask the user the 4xx question below, then open a PR from `fix/discount-and-retry`.

## Files in play
| File | State |
|---|---|
| `invoice/parse.py` | fixed and committed |
| `invoice/client.py` | retry added, not committed yet |
| `tests/test_client.py` | new, not committed yet |

## Decisions already made
- Kept float math with per-line `round(..., 2)`, because the legacy billing system rounds that way and `test_rounding_matches_legacy` encodes it on purpose.

## Dead ends: do not retry
- Rewriting the math with `decimal.Decimal` and `quantize(Decimal("0.01"), ROUND_HALF_UP)`. It broke the legacy test: `AssertionError: assert Decimal('10.01') == 10.0`.

## How to verify
```sh
python -m pytest -q
```

## Open questions for the user
- Should 4xx responses other than 429 ever be retried? The code currently assumes no.
"""


def scenario_drift(root):
    now = base_repo(root)
    put(root, "invoice/parse.py", PARSE_FIXED)
    sh(root, "commit", "-qam", "Fix percent discounts", when=now - dt.timedelta(hours=4))
    commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=root, capture_output=True,
                            text=True, check=True).stdout.strip()
    created = (now - dt.timedelta(hours=3)).isoformat(timespec="minutes")
    put(root, ".handoff/%s-discounts-and-retry.md" % (now - dt.timedelta(hours=3)).strftime("%Y-%m-%d-%H%M"),
        DRIFT_HANDOFF.format(created=created, root=root, commit=commit))
    # After the handoff, the uncommitted retry work was lost and someone committed docs.
    put(root, "README.md", README + "\nDiscounts may be written as `15%` or `0.15`.\n")
    sh(root, "commit", "-qam", "docs: note discount formats in README", when=now - dt.timedelta(hours=1))
    put(root, "invoice/parse.py", PARSE_FIXED, age_minutes=240)
    put(root, "invoice/client.py", CLIENT, age_minutes=60)


def transcript_entries(root):
    def user(text, **kw):
        e = {"type": "user", "sessionId": SESSION, "message": {"role": "user", "content": text}}
        e.update(kw)
        return e

    def assistant(*blocks):
        return {"type": "assistant", "sessionId": SESSION, "message": {"role": "assistant", "content": list(blocks)}}

    def tool(tid, name, **inp):
        return {"type": "tool_use", "id": tid, "name": name, "input": inp}

    def result(tid, text, error=False):
        return user([{"type": "tool_result", "tool_use_id": tid, "is_error": error, "content": text}])

    p = lambda rel: str(root / rel)
    return [
        {"type": "custom-title", "sessionId": SESSION, "customTitle": "Invoice discounts and API retries"},
        user("Invoice totals are wrong when a line has a percentage discount: `WIDGET,3,10.00,15%` should "
             "total 25.50 but we get negative numbers. Fix it, then add retries to the API client because "
             "staging keeps returning 503s."),
        assistant({"type": "text", "text": "Looking at invoice/parse.py first."}, tool("t1", "Read", file_path=p("invoice/parse.py"))),
        result("t1", PARSE_BUGGY),
        assistant({"type": "text", "text": "parse_discount divides percentages by 10. I'll move the math to Decimal so rounding is exact."},
                  tool("t2", "Edit", file_path=p("invoice/parse.py"), old_string="x", new_string="y")),
        result("t2", "ok"),
        assistant(tool("t3", "Bash", command="python -m pytest tests/test_parse.py -q")),
        result("t3", "FAILED tests/test_parse.py::test_rounding_matches_legacy - AssertionError: assert Decimal('10.01') == 10.0\n1 failed, 4 passed", error=True),
        user("No Decimal please. Legacy billing rounds each line with plain round(), that test is on purpose."),
        assistant({"type": "text", "text": "Understood: back to floats, and only the /100 fix."},
                  tool("t4", "Edit", file_path=p("invoice/parse.py"), old_string="x", new_string="y")),
        result("t4", "ok"),
        assistant(tool("t5", "Bash", command="python -m pytest tests/test_parse.py -q")),
        result("t5", "5 passed in 0.04s"),
        user("great. here's the staging token: %s, put it in .env, don't commit it" % TOKEN),
        assistant(tool("t6", "Write", file_path=p(".env"), content="INVOICE_API_TOKEN=..."),
                  tool("t7", "TodoWrite", todos=[
                      {"content": "Fix percent discount parsing", "status": "completed"},
                      {"content": "Add retry with backoff to post_total", "status": "in_progress"},
                      {"content": "Write tests for the retry behaviour", "status": "pending"}])),
        result("t6", "ok"),
        user("now add the retries to post_total"),
        assistant({"type": "text", "text": "Adding 3 attempts with exponential backoff, retrying URLError and 5xx."},
                  tool("t8", "Edit", file_path=p("invoice/client.py"), old_string="x", new_string="y")),
        result("t8", "ok"),
        assistant({"type": "text", "text": "Retry loop is in. Next I'll write tests/test_client.py with a mocked urlopen, "
                   "then run it. One question first: should 4xx responses other than 429 ever be retried? I've assumed no."}),
    ]


def scenario_autosave(root, snapshot=True):
    now = base_repo(root)
    put(root, "invoice/parse.py", PARSE_FIXED)
    put(root, "invoice/client.py", CLIENT_RETRY)
    put(root, ".env", "INVOICE_API_TOKEN=%s\n" % TOKEN)
    home = root / ".claude-home"
    tdir = home / ".claude" / "projects" / "-invoice-tool"
    tdir.mkdir(parents=True, exist_ok=True)
    transcript = tdir / ("%s.jsonl" % SESSION)
    transcript.write_text("\n".join(json.dumps(e) for e in transcript_entries(root)) + "\n")
    (home / ".claude.json").write_text(json.dumps({"oauthAccount": {"emailAddress": "ce.aun@example.com"}}))
    if snapshot:
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_ACCOUNT_UUID"}
        env.update(HOME=str(home), CLAUDE_CONFIG_DIR=str(home / ".claude"), CLAUDE_PROJECT_DIR=str(root))
        payload = {"session_id": SESSION, "transcript_path": str(transcript), "cwd": str(root),
                   "hook_event_name": "StopFailure", "error": "rate_limit",
                   "last_assistant_message": "API Error: Rate limit reached"}
        subprocess.run([sys.executable, str(SCRIPT), "hook-stop-failure"], input=json.dumps(payload),
                       text=True, env=env, check=True)
        # Backdate it a little, as if the limit was hit 40 minutes ago.
        for snap in (root / ".handoff").glob("*-auto-*.md"):
            then = (now - dt.timedelta(minutes=40)).isoformat(timespec="minutes")
            text = snap.read_text().replace("created: %s" % now.isoformat(timespec="minutes"), "created: " + then)
            import re
            text = re.sub(r"^(created|updated): .*$", lambda m: "%s: %s" % (m.group(1), then), text, flags=re.M)
            snap.write_text(text)
    t = (now - dt.timedelta(minutes=41)).timestamp()
    os.utime(transcript, (t, t))


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    scenario, root = sys.argv[1], Path(sys.argv[2]).resolve()
    if (root / ".git").exists():
        sys.exit("%s already holds a repository" % root)
    {
        "cutoff": scenario_cutoff,
        "drift": scenario_drift,
        "autosave": scenario_autosave,
        "autosave-no-snapshot": lambda r: scenario_autosave(r, snapshot=False),
    }[scenario](root)
    print("built %s scenario in %s" % (scenario, root))


if __name__ == "__main__":
    main()
