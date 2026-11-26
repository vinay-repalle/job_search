"""Send the daily email digest built from docs/data/jobs.json.

Settings (see .env.example): SMTP_USER + SMTP_PASSWORD (Gmail App Password) + EMAIL_TO, optional EMAIL_BCC.

Usage:
    python scripts/notify.py                   # send today's digest
    python scripts/notify.py --preview         # print it instead of sending (HTML saved to a temp file)
    python scripts/notify.py --test            # marked as a test; goes ONLY to the maintainer (EMAIL_BCC, else SMTP_USER)
    python scripts/notify.py --failure "msg"   # short 'run failed' alert, to EMAIL_BCC only
"""
from __future__ import annotations

import argparse
import html
import os
import re
import smtplib
import ssl
import sys
import tempfile
from collections import Counter
from datetime import date
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

import requests

from common import CONFIG_DIR, JOBS_FILE, app_url, load_env, load_json

AUTOMATION_TITLE = re.compile(r"automation|sdet|selenium|playwright|cypress|appium", re.I)   # as in filters.py


def esc(text) -> str:
    return html.escape(str(text or ""), quote=True)


def plural(n: int, word: str, many: str | None = None) -> str:
    return f"{n} {word if n == 1 else (many or word + 's')}"


def run_day(run: dict) -> str:
    return date.fromisoformat(run["date"]).strftime("%a, %d %b") if run.get("date") else ""


def new_jobs(data: dict) -> list[dict]:
    new_ids = set((data.get("run") or {}).get("new_ids") or [])
    return sorted((j for j in data.get("jobs", []) if j["id"] in new_ids),
                  key=lambda j: j.get("score", 0), reverse=True)


def is_good_match(job: dict, my_skills: set[str]) -> bool:
    """Same rule as matchInfo() in docs/app.js, so the email and the app show the same ★ jobs."""
    mine = [s for s in job.get("skills") or [] if s in my_skills]
    title = job.get("title") or ""
    automation_only = bool(AUTOMATION_TITLE.search(title)) and not re.search("manual", title, re.I)
    exp_min = job.get("exp_min")
    exp_ok = exp_min is None or exp_min <= 3
    return (not job.get("shift") and exp_ok and not automation_only
            and (len(mine) >= 3 or (job.get("group") == "validation" and len(mine) >= 1)))


def meta_line(job: dict) -> str:
    mode = job["mode"] if job.get("mode") != "Not stated" else ""
    return " · ".join(x for x in (job.get("location"), mode, job.get("exp_text")) if x)


# ------------------------------------------------------------------ email
C = {"text": "#17162a", "muted": "#62617a", "line": "#e3e3ec", "bg": "#f5f5f8", "card": "#ffffff",
     "accent": "#4f46e5", "good": "#0f7b4f", "good_soft": "#e3f5ec", "warn": "#9a5b00", "warn_soft": "#fff1d6",
     "chip": "#f0f0f5"}
FONT = "system-ui,-apple-system,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"


def _tag(text: str, fg: str, bg: str) -> str:
    return (f'<span style="display:inline-block;font-size:12px;font-weight:600;line-height:18px;padding:1px 8px;'
            f'border-radius:999px;color:{fg};background:{bg};margin:0 4px 4px 0">{esc(text)}</span>')


def _job_card(j: dict, good: bool, labels: dict) -> str:
    url = esc(j.get("apply_url"))
    tags = [_tag("★ Good match", "#ffffff", C["good"])] if good else []
    tags.append(_tag("✓ Company site", C["good"], C["good_soft"]) if j.get("trust") == "company"
                else _tag(j.get("source", ""), C["muted"], C["chip"]))
    tags.append(_tag(labels.get(j.get("group"), j.get("group", "")), C["muted"], C["chip"]))
    if j.get("shift"):
        tags.append(_tag("⚠ Night / rotational shifts", C["warn"], C["warn_soft"]))
    return f"""
        <tr><td style="padding:0 0 12px">
          <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{C['card']};border:1px solid {C['line']};border-radius:12px">
            <tr><td style="padding:14px 16px">
              <div style="margin:0 0 4px">{''.join(tags)}</div>
              <a href="{url}" style="font-size:16px;line-height:1.3;font-weight:700;color:{C['text']};text-decoration:none">{esc(j.get('title'))}</a>
              <div style="font-size:14px;font-weight:600;color:{C['muted']};margin:3px 0 4px">{esc(j.get('company'))}</div>
              <div style="font-size:13.5px;color:{C['text']};margin:0 0 12px">📍 {esc(meta_line(j))}</div>
              <a href="{url}" style="display:inline-block;background:{C['accent']};color:#ffffff;text-decoration:none;font-weight:600;font-size:14px;padding:9px 20px;border-radius:8px">Apply ↗</a>
            </td></tr>
          </table>
        </td></tr>"""


def build_email(data: dict, top_n: int, test: bool) -> tuple[str, str, str] | None:
    """(subject, plain text, html) — or None when there's nothing new (no email on quiet days)."""
    run = data.get("run") or {}
    labels = data.get("groups") or {}
    my_skills = set(data.get("my_skills") or [])
    fresh = new_jobs(data)
    if not fresh and not test:
        return None
    fresh.sort(key=lambda j: is_good_match(j, my_skills), reverse=True)   # stable: score order kept inside
    good = sum(is_good_match(j, my_skills) for j in fresh)
    day = run_day(run)
    app = app_url()
    shown = fresh[:top_n]
    more = len(fresh) - len(shown)

    if fresh:
        headline = f"{plural(len(fresh), 'new QA job')} today"
        subject = headline + (f" · ★ {plural(good, 'good match', 'good matches')}" if good else "") + f" · {day}"
        counts = Counter(j["group"] for j in fresh)
        breakdown = " · ".join(f"{labels.get(g, g)} {c}" for g, c in counts.most_common())
        preheader = f"{shown[0]['title']} at {shown[0]['company']}" + (f" and {len(fresh) - 1} more" if len(fresh) > 1 else "")
    else:
        headline = "No new QA jobs today"
        subject = f"{headline} · {day}"
        breakdown = "The app still has the recent openings."
        preheader = breakdown
    if test:
        subject = f"[Test] {subject}"

    # plain-text version (shown by clients that don't display HTML)
    lines = [subject, ""]
    for i, j in enumerate(shown, 1):
        star = "★ " if is_good_match(j, my_skills) else ""
        shift = "  (night / rotational shifts)" if j.get("shift") else ""
        lines += [f"{i}. {star}{j['title']} — {j['company']}", f"   {meta_line(j)}{shift}", f"   Apply: {j['apply_url']}", ""]
    if more:
        lines.append(f"…and {more} more in the app.")
    lines += [f"Open the app to filter, save and track: {app}", "",
              "A real employer never asks you to pay a fee."]
    text = "\n".join(lines) + "\n"

    cards = "".join(_job_card(j, is_good_match(j, my_skills), labels) for j in shown)
    more_row = (f'<tr><td style="padding:0 0 4px;font-size:14px;color:{C["muted"]};text-align:center">'
                f'…and {plural(more, "more job")} in the app.</td></tr>') if more else ""
    good_line = (f'<span style="color:{C["good"]};font-weight:600">★ {plural(good, "good match", "good matches")} for your skills</span> · '
                 if good else "")
    html_body = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light"><title>{esc(subject)}</title></head>
<body style="margin:0;padding:0;background:{C['bg']};font-family:{FONT};color:{C['text']}">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0">{esc(preheader)}</div>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{C['bg']}">
    <tr><td align="center" style="padding:20px 12px">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:600px">
        <tr><td style="padding:0 0 14px">
          <div style="font-size:13px;color:{C['muted']}">QA Jobs · {esc(day)}{' · test' if test else ''}</div>
          <div style="font-size:22px;line-height:1.25;font-weight:700;margin:3px 0 5px">{esc(headline)}</div>
          <div style="font-size:14px;color:{C['muted']}">{good_line}{esc(breakdown)}</div>
        </td></tr>
        {cards}
        {more_row}
        <tr><td align="center" style="padding:10px 0 18px">
          <a href="{esc(app)}" style="display:inline-block;border:1px solid {C['accent']};color:{C['accent']};text-decoration:none;font-weight:600;font-size:14px;padding:10px 20px;border-radius:8px">Open the app to filter, save &amp; track</a>
        </td></tr>
        <tr><td style="font-size:12px;line-height:1.5;color:{C['muted']};text-align:center">
          Sent daily by your QA Jobs app when new openings are found.<br>A real employer never asks you to pay a fee.
        </td></tr>
      </table>
    </td></tr>
  </table>
</body></html>
"""
    return subject, text, html_body


def _addresses(value: str | None) -> list[str]:
    return [a.strip() for a in (value or "").split(",") if a.strip()]


def send_email(mail: tuple[str, str, str | None] | None, *, test: bool = False,
               maintainer_only: bool = False) -> bool | None:
    """True = sent (or nothing to send today), False = failed, None = email isn't set up."""
    user = (os.environ.get("SMTP_USER") or "").strip()
    password = (os.environ.get("SMTP_PASSWORD") or "").replace(" ", "").strip()
    to = _addresses(os.environ.get("EMAIL_TO"))
    bcc = _addresses(os.environ.get("EMAIL_BCC"))
    if not user or not password or not (to or bcc):
        print("Email not set up (SMTP_USER / SMTP_PASSWORD / EMAIL_TO) — skipped.")
        return None
    if maintainer_only:                       # failure alerts are for the maintainer only
        if not bcc:
            print("EMAIL_BCC not set — failure alert not emailed.")
            return None
        to, bcc = bcc, []
    elif test:                                # tests never reach the recipient's inbox
        to, bcc = (bcc or [user]), []
    if mail is None:
        print("No new jobs today — no email sent.")
        return True

    subject, text, html_body = mail
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr(("QA Jobs", user))
    msg["To"] = ", ".join(to)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=user.rsplit("@", 1)[-1])
    msg.set_content(text)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    host = os.environ.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(os.environ.get("SMTP_PORT") or 465)
    ctx = ssl.create_default_context()
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, context=ctx, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls(context=ctx)
        with server:
            server.login(user, password)
            server.send_message(msg, to_addrs=to + bcc)    # Bcc goes in the envelope only, never in the headers
    except smtplib.SMTPAuthenticationError:
        print("Gmail refused the login — check SMTP_USER, and that SMTP_PASSWORD is an App Password "
              "(myaccount.google.com/apppasswords), not the normal password.")
        return False
    except (smtplib.SMTPException, OSError) as e:
        print(f"Could not send the email ({e.__class__.__name__}).")
        return False
    who = "the maintainer only (test)" if test else plural(len(to) + len(bcc), "recipient")
    print(f"Email sent to {who}.")
    return True


# ------------------------------------------------------------------ main
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--preview", action="store_true", help="print the email instead of sending it")
    ap.add_argument("--test", action="store_true", help="mark as a test; goes only to the maintainer")
    ap.add_argument("--failure", metavar="TEXT", help="send a short failure alert instead of the digest")
    args = ap.parse_args()
    load_env()

    if args.failure is not None:
        reason = args.failure or "today’s run failed"
        mail = (f"⚠ QA Jobs: {reason}",
                f"QA Jobs: {reason}.\nCheck GitHub → Actions for details. The app may still show older jobs.\n", None)
    else:
        data = load_json(JOBS_FILE, None)
        if not data:
            print("docs/data/jobs.json not found — run scripts/fetch_jobs.py first.")
            return 1
        cfg = load_json(CONFIG_DIR / "search.json", {}) or {}
        mail = build_email(data, cfg.get("email_top_n", 10), args.test)

    if args.preview:
        if mail is None:
            print("(no email today — no new jobs)")
        else:
            print(f"Subject: {mail[0]}\n\n{mail[1]}")
            if mail[2]:
                out = Path(tempfile.gettempdir()) / "qa-jobs-email-preview.html"
                out.write_text(mail[2], encoding="utf-8")
                print(f"HTML version saved to {out} — open it in a browser.")
        return 0

    sent = send_email(mail, test=args.test, maintainer_only=args.failure is not None)
    return 0 if sent else 1      # "not set up" counts as a failure, so the run shows red


if __name__ == "__main__":
    sys.exit(main())
