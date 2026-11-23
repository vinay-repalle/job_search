"""Job sources. Every fetch_* function returns a list of RawJob.

Sources that need a second call for the full description (Workday, SmartRecruiters)
set `detail`; the pipeline only calls it for jobs that already pass the cheap checks.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable

import requests

from common import html_to_text, parse_iso


@dataclass
class RawJob:
    title: str
    company: str
    location: str
    url: str
    source: str                      # "Company site" or the job board's name
    posted_at: datetime | None
    description: str = ""
    is_remote: bool | None = None
    mode_hint: str = ""              # explicit work-mode field from the source, if any
    country: str = ""
    salary: str = ""
    apply_options: list = field(default_factory=list)   # [{"name", "url", "direct"}]
    detail: Callable[[], None] | None = None
    detail_done: bool = False
    max_age_days: int | None = None  # per-source override (Workday companies are checked every few days)

    def load_detail(self) -> None:
        if self.detail and not self.detail_done:
            self.detail_done = True
            self.detail()


class SourceError(Exception):
    pass


# --------------------------------------------------------------------------- Workday

_POSTED_ON = re.compile(r"posted\s+(today|yesterday|(\d+)(\+)?\s+days?\s+ago)", re.I)


def workday_posted(text: str, now: datetime) -> datetime | None:
    m = _POSTED_ON.search(text or "")
    if not m:
        return None
    word = m.group(1).lower()
    if word == "today":
        days = 0
    elif word == "yesterday":
        days = 1
    else:
        days = int(m.group(2)) + (1 if m.group(3) else 0)
    return now - timedelta(days=days)


class WorkdayClient:
    """Workday blocks fast callers (HTTP 429) across all its customer sites, so calls are
    paced, retried a couple of times, and the whole Workday step has a time budget."""

    MAX_PAUSE = 20.0

    def __init__(self, session: requests.Session, pause: float = 8.0, budget_seconds: float = 600,
                 cooldown: float = 45.0, max_details: int = 25):
        self.s = session
        self.pause = pause
        self.cooldown = cooldown
        self.details_left = max_details
        self.deadline = time.monotonic() + budget_seconds

    def out_of_time(self) -> bool:
        return time.monotonic() > self.deadline

    def call(self, method: str, url: str, attempts: int = 2, **kw):
        last = None
        for attempt in range(attempts):
            if self.out_of_time():
                raise SourceError("skipped (Workday time budget used up)")
            retry_left = attempt < attempts - 1
            try:
                r = self.s.request(method, url, timeout=30, **kw)
            except requests.RequestException as e:
                last = f"{e.__class__.__name__} (busy)"
                if retry_left:
                    time.sleep(self.cooldown)
                continue
            time.sleep(self.pause)
            if r.status_code == 429 or r.status_code >= 500:
                last = f"HTTP {r.status_code} (busy)"
                # Being told "too fast": go slower for the rest of the run, then wait it out once.
                self.pause = min(self.pause * 1.5, self.MAX_PAUSE)
                if retry_left:
                    time.sleep(self.cooldown)
                continue
            if not r.ok:
                raise SourceError(f"HTTP {r.status_code}")
            try:
                return r.json()
            except ValueError:
                last = "non-JSON reply (busy)"
                if retry_left:
                    time.sleep(self.cooldown)
        raise SourceError(last or "failed")


def fetch_workday(co: dict, queries: list[str], pages: int, client: WorkdayClient, now: datetime,
                  max_age_days: int | None = None) -> list[RawJob]:
    tenant, wd, site, name = co["tenant"], co["wd"], co["site"], co["name"]
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}"
    jobs, seen, ok_calls = [], set(), 0
    last_error = None

    for q in queries:
        for page in range(pages):
            try:
                data = client.call("POST", f"{api}/jobs",
                                   json={"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": q},
                                   headers={"Content-Type": "application/json"})
                ok_calls += 1
            except SourceError as e:
                last_error = e
                break
            posts = data.get("jobPostings") or []
            for p in posts:
                path = p.get("externalPath")
                if not path or path in seen:
                    continue
                seen.add(path)
                job = RawJob(
                    title=p.get("title", "").strip(),
                    company=name,
                    location=p.get("locationsText", ""),
                    url=f"{base}/{site}{path}",
                    source="Company site",
                    posted_at=workday_posted(p.get("postedOn", ""), now),
                    mode_hint=p.get("remoteType", "") or "",
                    max_age_days=max_age_days,
                )
                job.detail = _workday_detail(job, client, f"{api}{path}")
                jobs.append(job)
            if len(posts) < 20:
                break

    if ok_calls == 0 and last_error:
        raise SourceError(str(last_error))
    return jobs


def _workday_detail(job: RawJob, client: WorkdayClient, url: str) -> Callable[[], None]:
    def load() -> None:
        # Detail pages: capped in number, a little extra time each, no retry if Workday is busy.
        if client.details_left <= 0:
            return
        client.details_left -= 1
        client.deadline = max(client.deadline, time.monotonic() + 40)
        try:
            info = (client.call("GET", url, attempts=1) or {}).get("jobPostingInfo") or {}
        except SourceError:
            return
        job.description = html_to_text(info.get("jobDescription"))
        locs = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
        country = (info.get("country") or {}).get("descriptor", "")
        job.location = ", ".join(x for x in dict.fromkeys(locs) if x) or job.location
        job.country = country
        job.mode_hint = info.get("remoteType") or job.mode_hint
        if info.get("startDate"):
            job.posted_at = parse_iso(info["startDate"] + "T00:00:00+05:30") or job.posted_at
        if info.get("externalUrl"):
            job.url = info["externalUrl"]
    return load


# --------------------------------------------------------------------------- Greenhouse

def fetch_greenhouse(co: dict, session: requests.Session) -> list[RawJob]:
    r = session.get(f"https://boards-api.greenhouse.io/v1/boards/{co['slug']}/jobs",
                    params={"content": "true"}, timeout=40)
    if not r.ok:
        raise SourceError(f"HTTP {r.status_code}")
    out = []
    for j in r.json().get("jobs", []):
        out.append(RawJob(
            title=j.get("title", "").strip(),
            company=co["name"],
            location=(j.get("location") or {}).get("name", ""),
            url=j.get("absolute_url", ""),
            source="Company site",
            posted_at=parse_iso(j.get("first_published") or j.get("updated_at")),
            description=html_to_text(j.get("content")),
        ))
    return out


# --------------------------------------------------------------------------- Lever

def fetch_lever(co: dict, session: requests.Session) -> list[RawJob]:
    r = session.get(f"https://api.lever.co/v0/postings/{co['slug']}", params={"mode": "json"}, timeout=40)
    if not r.ok:
        raise SourceError(f"HTTP {r.status_code}")
    out = []
    for j in r.json():
        cats = j.get("categories") or {}
        locs = [cats.get("location") or ""] + list(cats.get("allLocations") or [])
        parts = [j.get("descriptionPlain") or ""]
        for block in j.get("lists") or []:
            parts.append(f"{block.get('text', '')}\n{html_to_text(block.get('content'))}")
        parts.append(j.get("additionalPlain") or "")
        created = j.get("createdAt")
        out.append(RawJob(
            title=j.get("text", "").strip(),
            company=co["name"],
            location=", ".join(x for x in dict.fromkeys(locs) if x),
            url=j.get("hostedUrl", ""),
            source="Company site",
            posted_at=datetime.fromtimestamp(created / 1000, tz=timezone.utc) if created else None,
            description="\n".join(p for p in parts if p).strip(),
            mode_hint=j.get("workplaceType") or "",
            country=j.get("country") or "",
        ))
    return out


# --------------------------------------------------------------------------- SmartRecruiters

def fetch_smartrecruiters(co: dict, queries: list[str], session: requests.Session) -> list[RawJob]:
    cid = co["slug"]
    out, seen, ok_calls = [], set(), 0
    for q in queries:
        r = session.get(f"https://api.smartrecruiters.com/v1/companies/{cid}/postings",
                        params={"country": "in", "q": q, "limit": 100}, timeout=40)
        if not r.ok:
            continue
        ok_calls += 1
        for p in r.json().get("content", []):
            if p.get("id") in seen:
                continue
            seen.add(p.get("id"))
            loc = p.get("location") or {}
            mode = "remote" if loc.get("remote") else ("hybrid" if loc.get("hybrid") else "")
            job = RawJob(
                title=p.get("name", "").strip(),
                company=co["name"],
                location=loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x),
                url=f"https://jobs.smartrecruiters.com/{cid}/{p.get('id')}",
                source="Company site",
                posted_at=parse_iso(p.get("releasedDate")),
                mode_hint=mode,
                country=loc.get("country") or "",
            )
            job.detail = _sr_detail(job, session, p.get("ref"))
            out.append(job)
    if ok_calls == 0:
        raise SourceError("no successful calls")
    return out


def _sr_detail(job: RawJob, session: requests.Session, ref: str | None) -> Callable[[], None]:
    def load() -> None:
        if not ref:
            return
        try:
            r = session.get(ref, timeout=30)
            d = r.json() if r.ok else {}
        except (requests.RequestException, ValueError):
            return
        sections = ((d.get("jobAd") or {}).get("sections") or {})
        job.description = "\n".join(html_to_text((sections.get(k) or {}).get("text"))
                                    for k in ("jobDescription", "qualifications", "additionalInformation"))
        if d.get("applyUrl"):
            job.apply_options = [{"name": "Company site", "url": d["applyUrl"], "direct": True}]
    return load


# --------------------------------------------------------------------------- JSearch (Google Jobs)

JSEARCH_URL = "https://jsearch.p.rapidapi.com/search-v2"


def fetch_jsearch(queries: list[dict], date_posted: str, api_key: str, session: requests.Session):
    """Returns (jobs, remaining_quota, errors)."""
    headers = {"X-RapidAPI-Key": api_key, "X-RapidAPI-Host": "jsearch.p.rapidapi.com"}
    jobs, errors, remaining = [], [], None
    for qc in queries:
        params = {"query": qc["query"], "num_pages": "1", "country": "in",
                  "date_posted": qc.get("date_posted", date_posted), "language": "en"}
        if qc.get("remote"):
            params["work_from_home"] = "true"
        try:
            r = session.get(JSEARCH_URL, params=params, headers=headers, timeout=60)
        except requests.RequestException as e:
            errors.append(f"JSearch '{qc['query']}': {e.__class__.__name__}")
            continue
        remaining = r.headers.get("X-RateLimit-Requests-Remaining", remaining)
        if not r.ok:
            msg = r.text[:120].replace("\n", " ")
            errors.append(f"JSearch '{qc['query']}': HTTP {r.status_code} {msg}")
            if r.status_code in (401, 403, 429):
                break   # bad key or monthly quota used up — stop wasting calls
            continue
        data = r.json().get("data") or {}
        items = data.get("jobs", []) if isinstance(data, dict) else data
        for j in items:
            options = [{"name": o.get("publisher") or "Apply", "url": o.get("apply_link"), "direct": bool(o.get("is_direct"))}
                       for o in (j.get("apply_options") or []) if o.get("apply_link")]
            if not options and j.get("job_apply_link"):
                options = [{"name": j.get("job_publisher") or "Apply", "url": j["job_apply_link"],
                            "direct": bool(j.get("job_apply_is_direct"))}]
            city_state = ", ".join(x for x in (j.get("job_city"), j.get("job_state")) if x)
            jobs.append(RawJob(
                title=(j.get("job_title") or "").strip(),
                company=(j.get("employer_name") or "").strip(),
                location=j.get("job_location") or city_state,
                url=j.get("job_apply_link") or "",
                source=j.get("job_publisher") or "Job board",
                posted_at=parse_iso(j.get("job_posted_at_datetime_utc")),
                description=j.get("job_description") or "",
                is_remote=j.get("job_is_remote"),
                country=j.get("job_country") or "",
                salary=j.get("job_salary_string") or "",
                apply_options=options,
            ))
    return jobs, (int(remaining) if str(remaining or "").isdigit() else None), errors
