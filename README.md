# Under 50

Fills the **Under 50** tab of a Job Scout Google Sheet with IT jobs anywhere in Canada, internship to mid level, posted in the last 30 days, that LinkedIn shows with **fewer than 50 applicants**. A job is removed once it passes 100 applicants or 30 days.

Runs on GitHub Actions every 30 minutes, around the clock (`.github/workflows/under50.yml`). How a cycle works is at the top of [`under50.py`](under50.py); what counts as IT is in [`it_roles.py`](it_roles.py).

## Setup

1. Share the Google Sheet with the service account's email as an **Editor**.
2. Add two repository secrets (Settings → Secrets and variables → Actions):
   - `UNDER50_SHEET_ID`: the long id in the sheet's URL
   - `GOOGLE_CREDENTIALS`: the service account's JSON key, pasted whole
3. Actions tab → **Under 50** → **Run workflow**. It keeps itself going after that.

To stop it: Actions tab → **Under 50** → **…** → **Disable workflow**.

## Columns

`level` is LinkedIn's seniority label. `window` is *Last week* (0–7 days old) or *Last month* (8–30). `applicants` shows *under 25* when LinkedIn only says "Be among the first 25 applicants". `status` and `notes` are yours: rows with either filled in are never removed.

## Run it locally

```
pip install -r requirements.txt
UNDER50_SHEET_ID=... GOOGLE_CREDENTIALS_FILE=credentials.json python under50.py "help desk"
```

Search terms on the command line run a quick test cycle (last 24 hours) without touching the saved state.
