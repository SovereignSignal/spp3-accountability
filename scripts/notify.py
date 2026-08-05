"""notify.py — Telegram notification helper for SPP3 harness scripts.

Reads TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID from ~/.claude/secrets/spp3-telegram.env.
If credentials are missing or sending fails, silently no-ops so the harness
scripts continue running. Notifications are nice-to-have, not load-bearing.

Usage:
    from notify import send, row_url
    send(f"[PREQUAL] {applicant} — {status}\\nFlags: {flags}\\n{row_url(row_id)}")
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SECRETS_PATH = Path.home() / ".claude" / "secrets" / "spp3-telegram.env"


def _load():
    if not SECRETS_PATH.exists():
        return None, None
    token = None
    chat_id = None
    for line in SECRETS_PATH.read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or not line:
            continue
        if line.startswith("TELEGRAM_BOT_TOKEN="):
            token = line.split("=", 1)[1].strip()
        elif line.startswith("TELEGRAM_CHAT_ID="):
            chat_id = line.split("=", 1)[1].strip()
    return token, chat_id


def send(message, parse_mode="HTML", disable_preview=True):
    """Send a Telegram message. Returns True on success, False on failure (silent)."""
    token, chat_id = _load()
    if not token or not chat_id:
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        data = urllib.parse.urlencode({
            "chat_id": chat_id,
            "text": message,
            "parse_mode": parse_mode,
            "disable_web_page_preview": "true" if disable_preview else "false",
        }).encode()
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as r:
            body = json.loads(r.read())
            return bool(body.get("ok"))
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        print(f"  notify failed: {e}", file=sys.stderr)
        return False


def row_url(row_id):
    """Notion URL for a Pipeline DB row."""
    bare = row_id.replace("-", "")
    return f"https://www.notion.so/{bare}"


def escape_html(text):
    """Minimal HTML escape for Telegram parse_mode=HTML."""
    return (text.replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;"))
