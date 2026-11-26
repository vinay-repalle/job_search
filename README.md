# QA Job Alerts

Once a day (scheduled for 8:00 AM IST, though GitHub often starts it hours late) this repo:

1. Collects openings for **QA / Manual Testing**, **Application / Production Support** and **CSV / GxP Validation** roles.
2. Keeps **Hyderabad** and **remote-in-India** jobs asking for **4 years of experience or less**.
3. Hides scam-looking posts and flags night / rotational shifts.
4. Updates the website (an installable phone app) and emails the top new jobs.

Sources: company career pages (no key) and JSearch / Google Jobs (free RapidAPI plan, 200 calls a month).

## Files

| Path | Purpose |
|---|---|
| `config/search.json` | Roles, experience limit, skills, search queries |
| `config/companies.json` | Company career pages to check |
| `scripts/fetch_jobs.py` | Fetch, filter and save to `docs/data/jobs.json` |
| `scripts/notify.py` | Send the email |
| `docs/` | The website, served by GitHub Pages |
| `.github/workflows/daily-jobs.yml` | Schedule: search → website → email |

## Setup

1. **Settings → Secrets and variables → Actions**, add:
   - `RAPIDAPI_KEY`
   - `SMTP_USER`: the sending Gmail address
   - `SMTP_PASSWORD`: its Gmail **App Password**
   - `EMAIL_TO`: who receives the jobs
   - `EMAIL_BCC` (optional): hidden copy, and the only address that gets failure alerts
2. **Settings → Pages → Source:** GitHub Actions.
3. **Actions → Daily job alerts → Run workflow**, with *Look back* set to `7` the first time.

The site is at `https://<github-username>.github.io/<repo-name>/`. No email is sent on days without new jobs.

**Install on a phone:** open the site, then choose *Install app* (Android) or *Share → Add to Home Screen* (iPhone). Saved jobs, notes and details stay on the phone, so use **Back up list** now and then.

## Run locally

Copy `.env.example` to `.env` and fill it in.

```bash
pip install -r requirements.txt
python scripts/fetch_jobs.py            # uses 5 JSearch calls (--jsearch-limit 0 uses none)
python scripts/notify.py --preview      # show the email without sending
python scripts/notify.py --test         # send only to EMAIL_BCC (or SMTP_USER)
python -m http.server -d docs 8000      # view the app at http://localhost:8000
```

After changing `docs/index.html`, `app.js` or `styles.css`, bump `CACHE` in `docs/sw.js` so installed apps update.

## Troubleshooting

| Problem | Fix |
|---|---|
| No email | Open the latest run in the Actions tab and read the red step. Check the secret names. |
| `Gmail refused the login` | Use an App Password (needs 2-Step Verification), not the normal password. |
| Email lands in Spam | Mark it *Not spam* and add the sender to contacts. |
| `JSearch … HTTP 429` | Monthly free calls are used up. Company pages still work. |
| A company shows ✗ | Its site was busy or changed. If it keeps failing, set `"enabled": false`. |
| Schedule stopped | GitHub pauses it after 60 days without activity. Re-enable it in the Actions tab. |

Workday sites block frequent requests, so only 5 of those companies are checked per day, in rotation.
