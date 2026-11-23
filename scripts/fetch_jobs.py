"""Daily job fetch: collect → filter → de-duplicate → merge with history → docs/data/jobs.json

Usage (local):
    python scripts/fetch_jobs.py                     # full run (uses 5 JSearch calls)
    python scripts/fetch_jobs.py --jsearch-limit 0   # company pages only, saves JSearch quota
    python scripts/fetch_jobs.py --only lever,greenhouse -v --dry-run
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import date, timedelta

from common import CONFIG_DIR, JOBS_FILE, load_env, load_json, make_session, now_utc, save_json, today_ist
from filters import Rules
from sources import (WorkdayClient, fetch_greenhouse, fetch_jsearch, fetch_lever,
                     fetch_smartrecruiters, fetch_workday)

TRUST_RANK = {"company": 0, "trusted": 1, "other": 2}


def log(msg: str) -> None:
    print(msg, flush=True)


def jsearch_window(search: dict, max_age: int) -> str:
    """Normal daily run → config value ('today'); a longer look-back (e.g. first run) → wider window."""
    if max_age == search["max_age_days"]:
        return search["jsearch"]["date_posted"]
    return "today" if max_age <= 1 else "3days" if max_age <= 3 else "week" if max_age <= 7 else "month"


def collect(companies: dict, search: dict, only: set, jsearch_limit: int | None, now, jsearch_date: str):
    raw, errors, per_source = [], [], {}
    session = make_session()

    enabled = [co for co in companies["companies"]
               if co.get("enabled", True) and (not only or co["ats"] in only)]
    # Fast sources first. Workday sites block busy callers for hours, so only a few Workday
    # companies are checked per day, in rotation (all of them every few days).
    others = [co for co in enabled if co["ats"] != "workday"]
    workday = [co for co in enabled if co["ats"] == "workday"]
    per_day = companies.get("workday_companies_per_day", 5)
    if workday:
        start = (now.toordinal() * per_day) % len(workday)
        rotated = workday[start:] + workday[:start]
        workday = rotated[:per_day]
        log(f"  Workday companies today: {', '.join(co['name'] for co in workday)}")
    wd_client = WorkdayClient(make_session(),
                              pause=companies.get("workday_pause_seconds", 8),
                              budget_seconds=companies.get("workday_budget_seconds", 600),
                              cooldown=companies.get("workday_cooldown_seconds", 45),
                              max_details=companies.get("workday_max_detail_pages", 25))

    skipped = []
    busy_streak = 0
    for co in others + workday:
        if co["ats"] == "workday" and (wd_client.out_of_time() or busy_streak >= 2):
            skipped.append(co["name"])
            why = "Workday is busy today" if busy_streak >= 2 else "Workday time budget used up"
            log(f"  – {co['name']:<22} skipped ({why}; tried again in a few days)")
            continue
        try:
            if co["ats"] == "workday":
                got = fetch_workday(co, companies["workday_queries"], companies.get("workday_pages", 1), wd_client,
                                    now, companies.get("workday_max_age_days", 7))
                busy_streak = 0
            elif co["ats"] == "greenhouse":
                got = fetch_greenhouse(co, session)
            elif co["ats"] == "lever":
                got = fetch_lever(co, session)
            elif co["ats"] == "smartrecruiters":
                got = fetch_smartrecruiters(co, companies.get("smartrecruiters_queries", ["test"]), session)
            else:
                raise ValueError(f"unknown ats '{co['ats']}'")
            per_source[co["name"]] = len(got)
            raw.extend(got)
            log(f"  ✓ {co['name']:<22} {len(got):>4} postings")
        except Exception as e:                      # one broken site must not stop the run
            if co["ats"] == "workday" and ("busy" in str(e) or "budget" in str(e)):
                skipped.append(co["name"])          # normal for Workday; retried on its next turn
                busy_streak += 1
                log(f"  – {co['name']:<22} busy, skipped today")
            else:
                errors.append(f"{co['name']}: {e}")
                log(f"  ✗ {co['name']:<22} {e}")

    remaining = None
    if not only or "jsearch" in only:
        key = os.environ.get("RAPIDAPI_KEY")
        queries = search["jsearch"]["queries"]
        if jsearch_limit is not None:
            queries = queries[:jsearch_limit]
        if not key:
            errors.append("JSearch: RAPIDAPI_KEY not set")
        elif queries:
            got, remaining, errs = fetch_jsearch(queries, jsearch_date, key, session)
            errors.extend(errs)
            per_source["JSearch"] = len(got)
            raw.extend(got)
            log(f"  ✓ {'JSearch (Google Jobs)':<22} {len(got):>4} postings   (calls left this month: {remaining})")
            for e in errs:
                log(f"  ✗ {e}")
    return raw, errors, skipped, per_source, remaining


def filter_jobs(raw, rules: Rules, max_age: int, now, verbose: bool):
    kept, dropped = [], Counter()

    def too_old(job) -> bool:
        if not job.posted_at:
            return False
        days = max(max_age, job.max_age_days or 0)
        return job.posted_at < now - timedelta(days=days, hours=12)   # 12h grace for day rounding

    def drop(reason, job):
        dropped[reason] += 1
        if verbose and reason not in ("title",):
            log(f"    - [{reason}] {job.title} | {job.company} | {job.location}")

    for job in raw:
        group = rules.title_group(job.title)
        if not group:
            drop("title", job)
            continue
        if too_old(job):
            drop("old", job)
            continue
        if not rules.location_maybe(job):
            drop("location", job)
            continue
        job.load_detail()                           # extra call only for promising jobs
        if too_old(job):
            drop("old", job)
            continue
        if rules.is_scam(job):
            drop("scam", job)
            continue
        if not rules.is_it_job(group, job):
            drop("not_software", job)
            continue
        exp = rules.experience(job.title, job.description)
        if exp["min"] is not None and exp["min"] > rules.max_min_years:
            drop("experience", job)
            continue
        mode = rules.work_mode(job)
        loc = rules.location_tag(job, mode)
        if not loc:
            drop("location", job)
            continue
        rec = rules.to_record(job, group, exp, mode, loc, now)
        kept.append(rec)
        if verbose:
            log(f"    + {rec['title']} | {rec['company']} | {loc} · {mode} · {exp['text'] or 'exp n/a'} | {rec['source']}")
    return kept, dropped


def dedupe(records: list[dict]) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    for rec in records:
        prev = by_id.get(rec["id"])
        if prev is None:
            by_id[rec["id"]] = rec
            continue
        best, other = (rec, prev) if TRUST_RANK[rec["trust"]] < TRUST_RANK[prev["trust"]] else (prev, rec)
        urls = {l["url"] for l in best["links"]}
        best["links"] = (best["links"] + [l for l in other["links"] if l["url"] not in urls])[:5]
        best["score"] = max(best["score"], other["score"])
        by_id[rec["id"]] = best
    return by_id


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-age-days", type=int, help="override max_age_days from config/search.json")
    ap.add_argument("--only", help="comma list of sources: workday,greenhouse,lever,smartrecruiters,jsearch")
    ap.add_argument("--jsearch-limit", type=int, help="use only the first N JSearch queries (0 = skip; saves quota)")
    ap.add_argument("--dry-run", action="store_true", help="do not write docs/data/jobs.json")
    ap.add_argument("-v", "--verbose", action="store_true", help="show every kept / dropped job")
    args = ap.parse_args()

    load_env()
    search = load_json(CONFIG_DIR / "search.json", None)
    companies = load_json(CONFIG_DIR / "companies.json", None)
    if not search or not companies:
        log("config/search.json or config/companies.json is missing or not valid JSON")
        return 2
    rules = Rules(search)
    only = {s.strip() for s in args.only.split(",")} if args.only else set()
    max_age = args.max_age_days or int(os.environ.get("MAX_AGE_DAYS") or 0) or search["max_age_days"]
    now = now_utc()

    log(f"Fetching jobs (posted in the last {max_age} days)…")
    raw, errors, skipped, per_source, remaining = collect(companies, search, only, args.jsearch_limit, now,
                                                          jsearch_window(search, max_age))
    if not raw and (errors or skipped):
        log("Every source failed — keeping yesterday's data.")
        return 1

    log(f"\nFiltering {len(raw)} postings…")
    kept, dropped = filter_jobs(raw, rules, max_age, now, args.verbose)
    found = dedupe(kept)

    previous = load_json(JOBS_FILE, {})
    history = {j["id"]: j for j in previous.get("jobs", [])}
    today = today_ist()
    new_ids = []
    for jid, rec in found.items():
        old = history.get(jid)
        rec["first_seen"] = old["first_seen"] if old else today
        rec["last_seen"] = today
        if not old:
            new_ids.append(jid)
        history[jid] = rec

    keep_from = (date.fromisoformat(today) - timedelta(days=search.get("retention_days", 30))).isoformat()
    jobs = [j for j in history.values() if j["first_seen"] >= keep_from]
    jobs.sort(key=lambda j: (j["first_seen"], j["score"]), reverse=True)

    out = {
        "updated_at": now.isoformat(timespec="seconds"),
        "run": {
            "date": today,
            "new_ids": new_ids,
            "matched": len(found),
            "scanned": len(raw),
            "dropped": dict(dropped),
            "errors": errors,
            "skipped": skipped,
            "sources": per_source,
            "jsearch_calls_left": remaining,
            "max_age_days": max_age,
        },
        "groups": {k: v["label"] for k, v in search["roles"].items()},
        "my_skills": search.get("my_skills", []),
        "jobs": jobs,
    }

    log(f"\nMatched {len(found)} jobs ({len(new_ids)} new today) · {len(jobs)} kept in the app")
    log("Dropped: " + ", ".join(f"{k} {v}" for k, v in dropped.most_common()))
    if errors:
        log(f"Sources with problems: {len(errors)}")
    if args.dry_run:
        log("(dry run — jobs.json not written)")
    else:
        save_json(JOBS_FILE, out)
        log(f"Saved → {JOBS_FILE.relative_to(JOBS_FILE.parents[2])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
