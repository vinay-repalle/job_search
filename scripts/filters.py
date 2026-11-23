"""Matching rules (driven by config/search.json) and the labels shown in the app."""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta

from sources import RawJob

# Experience phrases: "2-4 years", "2 to 4 yrs", "3+ years", "minimum 2 years", "2 years of experience"
_EXP_RANGE = re.compile(r"(\d{1,2})(?:\.\d)?\s*\+?\s*(?:-|–|—|to)\s*(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)
_EXP_PLUS = re.compile(r"(\d{1,2})\s*\+\s*(?:years?|yrs?)", re.I)
_EXP_MIN = re.compile(r"(?:minimum|min\.?|at\s*least|atleast)\s*(?:of\s*)?(\d{1,2})\s*\+?\s*(?:years?|yrs?)", re.I)
_EXP_SINGLE = re.compile(r"(\d{1,2})\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:[\w/&-]+\s+){0,3}?(?:experience|exp\b)", re.I)
_FRESHER = re.compile(r"\bfreshers?\b", re.I)

# Apply links that go straight to an employer's own careers system.
_COMPANY_HOSTS = re.compile(
    r"myworkdayjobs|greenhouse\.io|lever\.co|smartrecruiters|ashbyhq|icims|taleo|successfactors|"
    r"oraclecloud|workable|bamboohr|zohorecruit|//careers?\.|/careers?/", re.I)
_AUTOMATION_TITLE = re.compile(r"automation|sdet|selenium|playwright|cypress|appium", re.I)
_MULTI_LOCATION = re.compile(r"\d+\s+locations", re.I)


def _rx(pattern: str) -> re.Pattern:
    return re.compile(pattern, re.I)


def _norm(text: str, drop_words: str = "") -> str:
    text = text.lower()
    text = re.sub(r"\(.*?\)|\[.*?\]", " ", text)
    if drop_words:
        text = re.sub(rf"\b(?:{drop_words})\b", " ", text)
    text = re.sub(r"[^a-z0-9+#]+", " ", text)
    return " ".join(text.split())


_COMPANY_NOISE = "pvt|private|ltd|limited|inc|llc|llp|corp|corporation|co|india|technologies|technology|solutions|services|systems|group"
_TITLE_NOISE = "hyderabad|secunderabad|telangana|india|remote|hybrid|onsite|on site|wfh|urgent|hiring|immediate joiners?"


class Rules:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.roles = {}
        for key, role in cfg["roles"].items():
            self.roles[key] = {
                **role,
                "title_re": _rx(role["title"]),
                "strong_re": _rx(role["strong_title"]) if role.get("strong_title") else None,
            }
        self.title_exclude = _rx(cfg["title_exclude"])
        self.non_it = _rx(cfg["non_it_title"])
        self.software = _rx(cfg["software_signals"])
        self.it = _rx(cfg["it_signals"])
        self.local = _rx(cfg["locations"]["local"])
        self.india = _rx(cfg["locations"]["india"])
        wm = cfg["work_mode"]
        self.remote_short, self.remote_long = _rx(wm["remote_short"]), _rx(wm["remote_long"])
        self.hybrid, self.office = _rx(wm["hybrid"]), _rx(wm["office"])
        self.shift = _rx(cfg["shift_pattern"])
        self.scam = _rx(cfg["scam_pattern"])
        self.trusted = [b.lower() for b in cfg["trusted_boards"]]
        self.skills = {name: _rx(p) for name, p in cfg["skills"].items()}
        self.my_skills = set(cfg.get("my_skills", []))
        self.max_min_years = cfg["experience"]["max_min_years"]

    # ------------------------------------------------------------ role group
    def title_group(self, title: str) -> str | None:
        """Cheap title-only check. Returns 'validation' / 'support' / 'qa' or None."""
        if not title or self.title_exclude.search(title) or self.non_it.search(title):
            return None
        for key in ("validation", "support", "qa"):          # most specific first
            if key in self.roles and self.roles[key]["title_re"].search(title):
                return key
        return None

    @staticmethod
    def _distinct(rx: re.Pattern, text: str) -> int:
        return len({m.group(0).lower() for m in rx.finditer(text)})

    def is_it_job(self, group: str, job: RawJob) -> bool:
        """Separates software QA from pharma-plant QA, IT validation from process validation."""
        role = self.roles[group]
        text = f"{job.title}\n{job.description}"
        if group == "validation":
            return self._distinct(self.it, text) >= role.get("needs_it_signals", 2)
        if role.get("strong_re") is not None and role["strong_re"].search(job.title):
            return True
        return self._distinct(self.software, text) >= role.get("needs_signals", 1)

    # ------------------------------------------------------------ location / mode
    def location_maybe(self, job: RawJob) -> bool:
        """Before spending a detail call: could this job be in Hyderabad or remote-India?"""
        if job.detail is None or job.detail_done:
            return True
        loc = job.location or ""
        return bool(self.local.search(loc) or self.local.search(job.title) or _MULTI_LOCATION.search(loc)
                    or self.remote_short.search(f"{job.title} {loc}") or loc.strip().lower() in ("india", "remote"))

    def work_mode(self, job: RawJob) -> str:
        hint = (job.mode_hint or "").lower()
        if "hybrid" in hint or "flexible" in hint:
            return "Hybrid"
        if "remote" in hint:
            return "Remote"
        if "site" in hint or "office" in hint:
            return "Office"
        head = f"{job.title} {job.location}"
        if self.hybrid.search(head):
            return "Hybrid"
        if job.is_remote is True or self.remote_short.search(head):
            return "Remote"
        desc = job.description or ""
        if self.hybrid.search(desc):
            return "Hybrid"
        if self.remote_long.search(desc):
            return "Remote"
        if self.office.search(desc) or self.office.search(head):
            return "Office"
        return "Not stated"

    def location_tag(self, job: RawJob, mode: str) -> str | None:
        loc = job.location or ""
        if self.local.search(loc) or self.local.search(job.title):
            return "Hyderabad"
        in_india = bool(self.india.search(loc)) or (job.country or "").strip().lower() in ("in", "india")
        if mode == "Remote" and in_india:
            return "Remote (India)"
        return None

    # ------------------------------------------------------------ experience
    def experience(self, title: str, desc: str) -> dict:
        for text in (title or "", (desc or "")[:8000]):
            cands = []
            for m in _EXP_RANGE.finditer(text):
                lo, hi = int(m.group(1)), int(m.group(2))
                if lo <= hi <= 20:
                    cands.append((m.start(), lo, hi))
            for rx in (_EXP_PLUS, _EXP_MIN, _EXP_SINGLE):
                for m in rx.finditer(text):
                    n = int(m.group(1))
                    if n <= 15:
                        cands.append((m.start(), n, None))
            if cands:
                _, lo, hi = min(cands)          # first mention is usually the main requirement
                label = f"{lo}–{hi} yrs" if hi is not None else f"{lo}+ yrs"
                return {"min": lo, "max": hi, "text": label}
        if _FRESHER.search(title or "") or _FRESHER.search((desc or "")[:4000]):
            return {"min": 0, "max": 1, "text": "Fresher"}
        return {"min": None, "max": None, "text": ""}

    # ------------------------------------------------------------ safety
    def is_scam(self, job: RawJob) -> bool:
        return bool(self.scam.search(f"{job.title}\n{job.company}\n{job.description}"))

    def has_shifts(self, job: RawJob) -> bool:
        return bool(self.shift.search(f"{job.title}\n{job.description}"))

    # ------------------------------------------------------------ links / trust
    def _tier(self, opt: dict) -> tuple[int, int]:
        if opt.get("direct") or _COMPANY_HOSTS.search(opt.get("url") or ""):
            return (0, 0)
        name = (opt.get("name") or "").lower()
        for i, board in enumerate(self.trusted):
            if board in name:
                return (1, i)
        return (2, 0)

    def links(self, job: RawJob) -> tuple[list[dict], str, str]:
        """Sorted apply links (best first), source label, trust level."""
        opts = job.apply_options or [{"name": job.source, "url": job.url, "direct": job.source == "Company site"}]
        seen, ranked = set(), []
        for o in sorted(opts, key=self._tier):
            if o.get("url") and o["url"] not in seen:
                seen.add(o["url"])
                tier = self._tier(o)[0]
                ranked.append({"name": "Company site" if tier == 0 else o.get("name") or "Apply",
                               "url": o["url"], "tier": tier})
        if not ranked:
            ranked = [{"name": job.source, "url": job.url, "tier": 2}]
        best = ranked[0]
        trust = ("company", "trusted", "other")[best["tier"]]
        return ranked, best["name"], trust

    # ------------------------------------------------------------ output record
    def skills_found(self, job: RawJob) -> list[str]:
        text = f"{job.title}\n{job.description}"
        found = [name for name, rx in self.skills.items() if rx.search(text)]
        found.sort(key=lambda s: s not in self.my_skills)
        return found[:8]

    @staticmethod
    def job_id(company: str, title: str) -> str:
        key = f"{_norm(company, _COMPANY_NOISE)}|{_norm(title, _TITLE_NOISE)}"
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    def to_record(self, job: RawJob, group: str, exp: dict, mode: str, loc: str, now: datetime) -> dict:
        links, source, trust = self.links(job)
        skills = self.skills_found(job)
        shift = self.has_shifts(job)
        score = {"company": 3, "trusted": 2, "other": 0}[trust]
        score += {"Remote": 3, "Hybrid": 3, "Office": 1, "Not stated": 1}[mode]
        if exp["min"] is None:
            score += 1
        elif 1 <= exp["min"] <= 3:
            score += 2
        else:
            score += 1
        if group == "validation":
            score += 1
        if shift:
            score -= 2
        score += min(2.0, 0.5 * sum(1 for s in skills if s in self.my_skills))
        if job.posted_at and now - job.posted_at <= timedelta(days=1):
            score += 1
        if _AUTOMATION_TITLE.search(job.title):
            score -= 0.5

        desc = (job.description or "").strip()
        snippet = desc[:520].rsplit(" ", 1)[0] + "…" if len(desc) > 520 else desc
        title = re.sub(r"\s*\((?:copy|copy of)\)\s*$", "", job.title, flags=re.I).strip()
        return {
            "id": self.job_id(job.company, title),
            "title": title,
            "company": job.company,
            "location": loc,
            "location_raw": (job.location or "")[:120],
            "mode": mode,
            "group": group,
            "exp_min": exp["min"],
            "exp_max": exp["max"],
            "exp_text": exp["text"],
            "posted_at": job.posted_at.isoformat(timespec="seconds") if job.posted_at else None,
            "source": source,
            "trust": trust,
            "apply_url": links[0]["url"],
            "links": [{"name": l["name"], "url": l["url"]} for l in links[:5]],
            "shift": shift,
            "salary": job.salary or "",
            "skills": skills,
            "snippet": snippet,
            "score": round(score, 1),
        }
