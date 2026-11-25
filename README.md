# QA Job Alerts

Every morning at **8:00 AM IST** this repo:

1. Collects fresh openings for **QA / Manual Testing**, **Application / Production Support** and **CSV / GxP Validation** roles.
2. Keeps only **Hyderabad** jobs (any work mode) and **remote jobs in India**, asking for **≤ 4 years** of experience.
3. Hides scam-looking posts (registration fees, "pay to apply", WhatsApp-only contact) and flags night/rotational-shift jobs.
4. Updates the website (installable phone app) and sends a **Telegram digest** with the top new jobs.

No LinkedIn/Naukri scraping, no AI key, no paid services.

## Where the jobs come from

| Source | Key needed | What it covers |
|---|---|---|
| **Company career pages** (Workday, Greenhouse, Lever, SmartRecruiters) | none | Novartis, Sanofi, Amgen, IQVIA, Parexel, Thermo Fisher, MSD, Evernorth, Invesco, Salesforce, BMS, Lilly, Medtronic, Zoetis, Veeva, Zenoti, HighRadius, ServiceNow, Bosch |
| **JSearch** (RapidAPI, free plan: 200 calls/month) | `RAPIDAPI_KEY` | Google Jobs: LinkedIn, Naukri, Indeed, Foundit, company sites… (5 searches/day ≈ 150/month) |

## Folder map

```
config/search.json          what counts as a match: role keywords, experience, scam & shift words, JSearch queries
config/companies.json       company career pages to check (set "enabled": false to skip one)
scripts/fetch_jobs.py       daily fetch → filter → de-duplicate → docs/data/jobs.json
scripts/notify.py           sends the Telegram digest
docs/                       the website (installable app), served by GitHub Pages
docs/data/jobs.json         the job list (updated automatically every day — don't edit by hand)
.github/workflows/daily-jobs.yml   the 8 AM schedule
.env                        your keys for local runs (git-ignored, never pushed)
```

## One-time setup (GitHub)

1. **Secrets:** repo → **Settings → Secrets and variables → Actions → New repository secret**. Add three:
   - `TELEGRAM_BOT_TOKEN`
   - `QA_JOB_SEARCH_TELEGRAM_CHAT_ID` (the group's id, a negative number — same name as in `.env`)
   - `RAPIDAPI_KEY`
2. **Website:** **Settings → Pages → Build and deployment → Source: “GitHub Actions”**.
3. **Push the code** (this publishes the site; it shows "waiting for the first daily run" until step 4).
4. **First run:** **Actions → Daily job alerts → Run workflow**, set *Look back* to `7`, click **Run workflow**. Takes ~5–10 minutes. You get a Telegram message and the site fills up at
   **https://vinay-repalle.github.io/job_search/**
5. From then on it runs by itself every day at 8 AM IST.

> Workday career sites (the pharma companies) block a network address for hours once it asks too often. So the script checks only **5 Workday companies per day in rotation** (all 14 every ~3 days), accepts their jobs up to 7 days old, waits 8+ seconds between calls, and **stops Workday for the day after 2 "busy" replies**. Skipped companies are listed in the run log and come round again in a few days. Most of their openings also reach us through JSearch/Google Jobs, which is the main source.

## Installing the app on her phone

Open the site link on the phone, then:
- **Android (Chrome):** tap **Install** on the banner, or menu **⋮ → Install app / Add to Home screen**.
- **iPhone (Safari):** **Share ⎋ → Add to Home Screen**.

In the app: the **Jobs** tab is grouped by the day each job was found — this morning's arrivals sit on top under **NEW · New today**, earlier days stay below. **Filters** (found today / 3 days / 7 days, role, remote/hybrid/office, experience, trusted sources only, hide shift jobs) open as a bottom sheet on the phone (tap outside, swipe down or press Back to close) and as a sidebar on a laptop. **Apply ↗** opens the original posting, **☆ Save**, **Mark applied**, and the **Applied** tab tracks status (Applied → Interview → Offer). Actions that lose data (**Remove** from Applied, un-marking an applied job, un-saving a job that has a note) need a second tap to confirm.

- **★ Good match** marks jobs that need 3+ of her skills (`my_skills`), or any CSV/validation role using at least one of them. The job must also ask ≤ 3 years, have no night shifts, and not be an automation-only title. Good matches come first within each day, and there's an **Only good matches** filter.
- **Share** (icon on each card) opens the phone's share sheet (WhatsApp etc.). On a laptop it copies the job and link instead.
- **Notes** on Saved / Applied jobs (recruiter name, interview date, which resume she sent). They save as she types.
- **Follow-up reminders:** an application still marked *Applied* with no reply after 7 days shows a nudge and puts a red dot on the Applied tab. **I followed up** restarts the 7-day clock.
- **My details** tab: answers job forms keep asking (experience, CTC, notice period, LinkedIn, About me…), each with a **Copy** button, plus her own custom questions.

Saved/Applied, notes and My details live on the phone only; use **Back up list** at the bottom now and then (the backup includes all of them).

> After changing `docs/index.html`, `app.js` or `styles.css`, bump `CACHE` in `docs/sw.js` (e.g. `qa-jobs-v2` → `qa-jobs-v3`) so installed phones switch to the new version on their next open.

## Changing what it searches

- **Experience limit:** `config/search.json` → `experience.max_min_years`.
- **Add/remove JSearch searches:** `config/search.json` → `jsearch.queries` (each one = 1 call per day; stay ≤ 6).
- **Role keywords / words to exclude:** `roles`, `title_exclude`, `non_it_title`.
- **Her skills (highlighted green, ranked higher):** `my_skills`.
- **Add a company:** `config/companies.json`. For a Workday site like `https://acme.wd5.myworkdayjobs.com/en-US/AcmeCareers` use `"tenant": "acme", "wd": "wd5", "site": "AcmeCareers"`.

Commit and push; the next run uses the new settings.

## Running it on your computer

```bash
pip install -r requirements.txt
python scripts/fetch_jobs.py --jsearch-limit 0 -v   # company pages only (saves JSearch calls), shows every kept/dropped job
python scripts/fetch_jobs.py                        # full run (uses 5 JSearch calls)
python scripts/notify.py --preview                  # print the Telegram message
python scripts/notify.py --test                     # send it, marked as a test
python -m http.server -d docs 8000                  # view the app at http://localhost:8000
```

Local runs read keys from `.env`.

## Troubleshooting

| Problem | Fix |
|---|---|
| No Telegram message | Actions tab → open the latest run → read the red step. Check the 3 secrets are spelled exactly. |
| `Telegram refused… group upgraded` | Telegram changed the group id; the log prints the new one — update `QA_JOB_SEARCH_TELEGRAM_CHAT_ID`. |
| `JSearch … HTTP 429` | Free monthly calls used up; resets monthly. Company pages keep working meanwhile. |
| A company shows ✗ in the log | Its career site was busy or changed. Others still run. If it fails for days, set `"enabled": false`. |
| Schedule stopped running | GitHub pauses schedules on repos with no activity for 60 days; Actions tab → enable the workflow again. |
