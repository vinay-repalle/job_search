"""Shared helpers: .env loading, paths, dates, HTTP session, HTML cleanup."""
from __future__ import annotations

import html
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
JOBS_FILE = ROOT / "docs" / "data" / "jobs.json"
IST = timezone(timedelta(hours=5, minutes=30))


def load_env(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from .env (local runs). Real env vars (GitHub Secrets) win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in ('"', "'"):
            value = value.strip('"').strip("'")
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()   # drop "  # comment" at the end of a line
        if value:
            os.environ.setdefault(key.strip(), value)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today_ist() -> str:
    return datetime.now(IST).date().isoformat()


def parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (compatible; qa-job-alerts/1.0)",
        "Accept": "application/json",
    })
    return s


_BLOCK_TAGS = re.compile(r"(?i)<\s*(?:br|/p|/li|/div|/h\d|li|/tr)\b[^>]*>")
_ANY_TAG = re.compile(r"<[^>]+>")


def html_to_text(raw: str | None) -> str:
    """Turn job-description HTML (sometimes double-escaped) into plain text."""
    if not raw:
        return ""
    text = html.unescape(raw)
    text = _BLOCK_TAGS.sub("\n", text)
    text = _ANY_TAG.sub(" ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t ​]+", " ", text)
    text = re.sub(r"\s*\n\s*", "\n", text)
    return text.strip()


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data, pretty: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    if pretty:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    tmp.replace(path)


def app_url() -> str:
    """Public URL of the website (GitHub Pages)."""
    if os.environ.get("APP_URL"):
        return os.environ["APP_URL"].rstrip("/") + "/"
    repo = os.environ.get("GITHUB_REPOSITORY", "vinay-repalle/job_search")
    owner, name = repo.split("/", 1)
    return f"https://{owner.lower()}.github.io/{name}/"
