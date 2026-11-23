"""Send the daily Telegram digest built from docs/data/jobs.json.

Usage:
    python scripts/notify.py                 # send today's digest
    python scripts/notify.py --preview       # print it instead of sending
    python scripts/notify.py --test          # send, marked as a test
    python scripts/notify.py --failure "msg" # send a short 'run failed' alert
"""
from __future__ import annotations

import argparse
import html
import os
import sys
from collections import Counter
from datetime import date

import requests

from common import CONFIG_DIR, JOBS_FILE, app_url, load_env, load_json

TELEGRAM_LIMIT = 4000   # Telegram allows 4096 characters; keep a margin


def esc(text) -> str:
    return html.escape(str(text or ""), quote=True)


def build_digest(data: dict, top_n: int, test: bool) -> str:
    run = data.get("run") or {}
    labels = data.get("groups") or {}
    new_ids = set(run.get("new_ids") or [])
    fresh = sorted((j for j in data.get("jobs", []) if j["id"] in new_ids),
                   key=lambda j: j.get("score", 0), reverse=True)
    day = date.fromisoformat(run["date"]).strftime("%a, %d %b") if run.get("date") else ""
    head = f"🔎 <b>QA Job Alerts</b> · {day}" + ("  <i>(test run)</i>" if test else "")

    def render(n: int) -> str:
        lines = [head]
        if fresh:
            counts = Counter(j["group"] for j in fresh)
            lines.append(f"<b>{len(fresh)} new job{'s' if len(fresh) != 1 else ''}</b> · "
                         + " · ".join(f"{esc(labels.get(g, g))} {c}" for g, c in counts.most_common()))
            for i, j in enumerate(fresh[:n], 1):
                mode = j["mode"] if j["mode"] != "Not stated" else ""
                meta = " · ".join(x for x in (j["location"], mode, j.get("exp_text")) if x)
                badge = "✅ Company site" if j["trust"] == "company" else f"🔗 {esc(j['source'])}"
                shift = "  ⚠️ shifts" if j.get("shift") else ""
                lines.append(
                    f"\n{i}. <b>{esc(j['title'])}</b>\n"
                    f"🏢 {esc(j['company'])}\n"
                    f"📍 {esc(meta)}{shift}\n"
                    f"{badge} · <a href=\"{esc(j['apply_url'])}\">Apply</a>")
            if len(fresh) > n:
                lines.append(f"\n…and {len(fresh) - n} more in the app.")
        else:
            lines.append("No new matching jobs today. The app still has the recent openings.")
        lines.append(f"\n📱 <a href=\"{esc(app_url())}\">Open the app</a> to filter, save and track applications")
        notes = []
        if run.get("errors"):
            notes.append(f"{len(run['errors'])} source(s) didn't respond today")
        left = run.get("jsearch_calls_left")
        if isinstance(left, int) and left < 25:
            notes.append(f"JSearch free calls left this month: {left}")
        if notes:
            lines.append("<i>Note: " + esc("; ".join(notes)) + "</i>")
        return "\n".join(lines)

    n = top_n
    text = render(n)
    while len(text) > TELEGRAM_LIMIT and n > 1:
        n -= 1
        text = render(n)
    return text


def send(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set — nothing sent.")
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=30, json={
            "chat_id": chat_id, "text": text, "parse_mode": "HTML",
            "link_preview_options": {"is_disabled": True},
        })
        res = r.json()
    except (requests.RequestException, ValueError) as e:
        print(f"Could not reach Telegram ({e.__class__.__name__}).")   # never print the URL: it holds the token
        return False
    if res.get("ok"):
        print("Telegram message sent.")
        return True
    new_id = (res.get("parameters") or {}).get("migrate_to_chat_id")
    if new_id:
        print(f"The group was upgraded by Telegram. New chat id: {new_id} — update TELEGRAM_CHAT_ID.")
    else:
        print(f"Telegram refused the message: {res.get('description')}")
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preview", action="store_true", help="print the message instead of sending")
    ap.add_argument("--test", action="store_true", help="mark the message as a test")
    ap.add_argument("--failure", metavar="TEXT", help="send a short failure alert instead of the digest")
    args = ap.parse_args()
    load_env()

    if args.failure is not None:
        text = (f"⚠️ <b>QA Job Alerts</b>: {esc(args.failure or 'today’s run failed')}.\n"
                f"Check GitHub → Actions for details. The app still shows the previous jobs.")
    else:
        data = load_json(JOBS_FILE, None)
        if not data:
            print("docs/data/jobs.json not found — run scripts/fetch_jobs.py first.")
            return 1
        top_n = (load_json(CONFIG_DIR / "search.json", {}) or {}).get("telegram_top_n", 10)
        text = build_digest(data, top_n, args.test)

    if args.preview:
        print(text)
        return 0
    return 0 if send(text) else 1


if __name__ == "__main__":
    sys.exit(main())
