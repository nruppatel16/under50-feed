"""Under 50 — IT jobs anywhere in Canada with fewer than 50 applicants.

Fills the "Under 50" tab of the Job Scout sheet: every IT role (see
it_roles.py), internship to mid level, posted in the last 30 days, in any
province, territory, town or remote-in-Canada, that LinkedIn shows with fewer
than 50 applicants. Indeed isn't a source: it doesn't show applicant counts.

One run of this script is one cycle (the GitHub workflow runs it every 30
minutes, see .github/workflows/under50.yml):
  • First ever cycle (or after more than a week without one): the full
    search — every IT search term in Canada and in each province and
    territory, posted in the last 30 days.
  • Every other cycle: every IT search term in Canada, posted since the last
    cycle (plus two hours of overlap).
  • Every 4 hours: re-check the jobs already listed; a job is removed once it
    is over 30 days old or has more than 100 applicants (rows with a status
    or notes filled in are kept).
New jobs are read one by one from LinkedIn's public job page for their
applicant count and seniority level, and added if under 50 applicants and
not senior (by title, or LinkedIn's Director / Executive levels).

LinkedIn's public search ignores its job-function and experience-level
filters, so both are applied here instead.

Settings (environment): UNDER50_SHEET_ID, and GOOGLE_CREDENTIALS (the
service account's JSON key) or GOOGLE_CREDENTIALS_FILE (path to it).
`python under50.py "help desk"` runs a quick cycle for just those terms
without touching the saved state.
"""
import json
import os
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from it_roles import SEARCH_TERMS, is_it_role

TAB = "Under 50"
MAX_APPLICANTS = 50       # add jobs with fewer than this
REMOVE_APPLICANTS = 100   # remove a listed job once it has more than this
MAX_AGE_DAYS = 30
WEEK_DAYS = 7
REFRESH_HOURS = 4
FULL_AFTER_HOURS = WEEK_DAYS * 24   # a gap longer than this redoes the full search

DATA = Path(__file__).parent / "data"
STATE_PATH = DATA / "state.json"      # when the last full search / search / re-check finished
RULED_OUT_PATH = DATA / "ruled_out.json"  # LinkedIn ids with 50+ applicants or senior; never change

COLS = ["id", "company", "role", "level", "location", "province", "posted_date", "days_ago",
        "window", "applicants", "apply_url", "status", "notes", "checked_at", "discovered_at"]
USER_COLS = ("status", "notes")  # never delete a row someone has written in

PLACES = [
    "Canada", "Ontario, Canada", "Quebec, Canada", "British Columbia, Canada", "Alberta, Canada",
    "Manitoba, Canada", "Saskatchewan, Canada", "Nova Scotia, Canada", "New Brunswick, Canada",
    "Newfoundland and Labrador, Canada", "Prince Edward Island, Canada",
    "Northwest Territories, Canada", "Yukon, Canada", "Nunavut, Canada",
]
# Pages of 10 per search. LinkedIn stops at about 1000 results, and the
# province searches pick up what the Canada-wide one runs out before.
FULL_PAGES = {"Canada": 30}
FULL_PAGES_DEFAULT = 10
RECENT_PAGES = 10

PROVINCES = {
    "ON": "Ontario", "QC": "Quebec", "BC": "British Columbia", "AB": "Alberta", "MB": "Manitoba",
    "SK": "Saskatchewan", "NS": "Nova Scotia", "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
    "PE": "Prince Edward Island", "NT": "Northwest Territories", "YT": "Yukon", "NU": "Nunavut",
}
_PROV_RE = re.compile(r"\b(" + "|".join(list(PROVINCES) + [re.escape(v) for v in PROVINCES.values()]) + r")\b")
# Canada or a remote label (LinkedIn's Canada searches occasionally return US-only jobs)
LOC_OK_RE = re.compile(r"\b(canada|remote|" + "|".join(PROVINCES) + "|"
                       + "|".join(re.escape(v) for v in PROVINCES.values()) + r")\b", re.I)

# Senior titles, English and French. "Manager" is senior except in
# individual-contributor titles like Customer Success Manager.
SENIOR_RE = re.compile(
    r"\b(senior|s[ée]nior(?:\.?e)?|sr\.?|staff|principal(?:e)?|lead|manager|director|directeur|directrice|"
    r"gestionnaire|chef|vp|vice[\s-]*pr[ée]sident(?:e)?|head\s*of|chief|cto|cio|ciso|cpo|cmo|cfo|coo|ceo|"
    r"svp|evp|c-suite|president|founder|partner|superintendent|architect|architecte)\b", re.I)
IC_MANAGER_RE = re.compile(r"\b(customer success|client success|technical account|account) manager\b|\bcsm\b", re.I)
SENIOR_LEVELS = {"Director", "Executive"}  # LinkedIn's own levels; "Mid-Senior level" stays

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
JOB_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{}"
_APPLICANTS_RE = re.compile(r'num-applicants__caption[^>]*>\s*([^<]+?)\s*<', re.S)
_LEVEL_RE = re.compile(r'Seniority level\s*</h3>\s*<span[^>]*>\s*([^<]+?)\s*<', re.S)
_LI_ID_RE = re.compile(r"/jobs/view/(?:[^/?]*-)?(\d+)")
TORONTO = timezone(timedelta(hours=-4))


def log(*a):
    print(datetime.now(TORONTO).strftime("%H:%M:%S"), *a, flush=True)


# ── Filters ───────────────────────────────────────────────────────────────

def is_senior(title):
    t = IC_MANAGER_RE.sub("", title or "")
    return bool(SENIOR_RE.search(t))


def wanted_title(title):
    return is_it_role(title) and not is_senior(title)


def province_of(location):
    m = _PROV_RE.search(location or "")
    if m:
        return PROVINCES.get(m.group(1), m.group(1))
    return "Remote / Canada-wide" if re.search(r"remote|canada", location or "", re.I) else ""


def parse_applicants(caption):
    """'47 applicants' -> 47, 'Be among the first 25 applicants' -> 0 (under 25,
    exact number not shown), 'Over 200 applicants' -> 200."""
    c = (caption or "").lower()
    if "among the first" in c:
        return 0
    m = re.search(r"(\d[\d,]*)", c)
    return int(m.group(1).replace(",", "")) if m else None


def applicants_label(n):
    return "under 25" if n == 0 else str(n)


def today():
    return datetime.now(TORONTO).date()


def days_ago(posted):
    try:
        return max(0, (today() - date.fromisoformat(posted[:10])).days)
    except (TypeError, ValueError):
        return None


def window(days):
    return "Last week" if days <= WEEK_DAYS else "Last month"


# ── LinkedIn ──────────────────────────────────────────────────────────────

def _get(url, params=None):
    """GET with Chrome's fingerprint; backs off on 429. None if it never answered."""
    from curl_cffi import requests as cffi
    for attempt in range(4):
        try:
            r = cffi.get(url, params=params, impersonate="chrome", timeout=20)
        except Exception:
            r = None
        if r is not None and r.status_code != 429:
            return r
        time.sleep(5 * (attempt + 1) + random.random() * 5)
    return None


def search(keywords, place, seconds, max_pages):
    """LinkedIn's public job search, newest first: one dict per job card."""
    from bs4 import BeautifulSoup
    out, seen, start = [], set(), 0
    for _ in range(max_pages):
        r = _get(SEARCH_URL, {"keywords": keywords, "location": place, "f_TPR": f"r{seconds}",
                              "sortBy": "DD", "start": start})
        if r is None or r.status_code != 200:
            break
        cards = BeautifulSoup(r.text, "html.parser").select("div.base-search-card")
        before = len(seen)
        start += len(cards)
        for c in cards:
            m = re.search(r"jobPosting:(\d+)", c.get("data-entity-urn", ""))
            title = c.select_one(".base-search-card__title")
            if not m or not title or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            company = c.select_one(".base-search-card__subtitle")
            loc = c.select_one(".job-search-card__location")
            when = c.find("time")
            out.append({"id": m.group(1), "role": title.get_text(strip=True),
                        "company": company.get_text(strip=True) if company else "",
                        "location": loc.get_text(strip=True) if loc else "Canada",
                        "posted_date": (when.get("datetime") or "")[:10] if when else ""})
        if len(cards) < 10 or len(seen) == before:  # last page
            break
        time.sleep(1 + random.random())
    return out


def job_page(job_id):
    """(applicant count, seniority level) from the public job page; the count is
    None if the page didn't show one (closed, or never answered)."""
    r = _get(JOB_URL.format(job_id))
    if r is None or r.status_code != 200:
        return None, ""
    m, lv = _APPLICANTS_RE.search(r.text), _LEVEL_RE.search(r.text)
    return (parse_applicants(m.group(1)) if m else None), (lv.group(1) if lv else "")


def search_all(terms, places, seconds, pages_for):
    """terms x places, three searches at a time. Wanted (IT, not senior,
    Canadian) jobs keyed by LinkedIn id."""
    found, lock, done = {}, threading.Lock(), [0]
    pairs = [(t, p) for p in places for t in terms]
    log(f"{len(pairs)} searches ({len(terms)} terms x {len(places)} places, last {seconds // 3600}h)")

    def one(pair):
        term, place = pair
        try:
            jobs = search(term, place, seconds, pages_for(place))
        except Exception as e:
            log(f"  [{term} / {place}] {type(e).__name__}: {e}")
            jobs = []
        with lock:
            for j in jobs:
                if j["id"] not in found and wanted_title(j["role"]) and LOC_OK_RE.search(j["location"]):
                    found[j["id"]] = j
            done[0] += 1
            if done[0] % 100 == 0:
                log(f"  {done[0]}/{len(pairs)} searches, {len(found)} jobs so far")

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(one, pairs))
    return found


def job_pages(ids):
    out, lock = {}, threading.Lock()

    def one(jid):
        res = job_page(jid)
        time.sleep(0.5 + random.random())
        with lock:
            out[jid] = res
            if len(out) % 200 == 0:
                log(f"  {len(out)}/{len(ids)} job pages read")

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(one, ids))
    return out


# ── Sheet ─────────────────────────────────────────────────────────────────

def open_sheet():
    import gspread
    from google.oauth2.service_account import Credentials
    sheet_id = os.environ.get("UNDER50_SHEET_ID", "")
    if not sheet_id:
        sys.exit("UNDER50_SHEET_ID isn't set (the Job Scout sheet's id).")
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    raw = os.environ.get("GOOGLE_CREDENTIALS", "")
    if raw:
        creds = Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
    else:
        creds = Credentials.from_service_account_file(
            os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json"), scopes=scopes)
    return gspread.authorize(creds).open_by_key(sheet_id)


class Tab:
    def __init__(self, spreadsheet):
        self.spreadsheet = spreadsheet
        if TAB in [ws.title for ws in spreadsheet.worksheets()]:
            self.ws = spreadsheet.worksheet(TAB)
        else:
            self.ws = spreadsheet.add_worksheet(title=TAB, rows=1000, cols=len(COLS))
            self.ws.append_row(COLS)
            self.ws.freeze(rows=1)
            log(f"Created tab: {TAB}")

    def rows(self):
        values = self.ws.get_all_values()
        return [dict(zip(COLS, v + [""] * (len(COLS) - len(v))), _row=i)
                for i, v in enumerate(values[1:], start=2)]  # _row: 1-based sheet row

    def update(self, rows):
        if rows:
            end = chr(ord("A") + len(COLS) - 1)
            self.ws.batch_update([{"range": f"A{r['_row']}:{end}{r['_row']}", "values": [[r[c] for c in COLS]]}
                                  for r in rows], value_input_option="USER_ENTERED")

    def delete(self, row_numbers):
        if row_numbers:
            self.spreadsheet.batch_update({"requests": [
                {"deleteDimension": {"range": {"sheetId": self.ws.id, "dimension": "ROWS",
                                               "startIndex": n - 1, "endIndex": n}}}
                for n in sorted(row_numbers, reverse=True)]})

    def append(self, jobs):
        ids = [int(r["id"]) for r in self.rows() if str(r["id"]).isdigit()]
        first = max(ids, default=0) + 1
        rows = [[first + i] + [j.get(c, "") for c in COLS[1:]] for i, j in enumerate(jobs)]
        for start in range(0, len(rows), 100):
            self.ws.append_rows(rows[start:start + 100], value_input_option="USER_ENTERED")


# ── Cycle ─────────────────────────────────────────────────────────────────

def load(path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def hours_since(iso):
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(iso)).total_seconds() / 3600
    except (TypeError, ValueError):
        return None


def recheck(tab, now_iso):
    """Refresh listed jobs; delete ones past 30 days or 100 applicants. Returns
    the LinkedIn ids still listed."""
    rows = tab.rows()
    ids = {r["_row"]: (_LI_ID_RE.search(r["apply_url"]) or [None, None])[1] for r in rows}
    log(f"Re-checking {len(rows)} listed jobs")
    pages = job_pages([i for i in ids.values() if i])
    keep, doomed = [], []
    for r in rows:
        n = pages.get(ids[r["_row"]], (None, ""))[0]
        days = days_ago(r["posted_date"])
        days = days if days is not None else int(r["days_ago"] or 0)
        too_old_or_full = days > MAX_AGE_DAYS or (n is not None and n > REMOVE_APPLICANTS)
        if too_old_or_full and not any(r[c].strip() for c in USER_COLS):
            doomed.append(r["_row"])
            continue
        r["days_ago"], r["window"] = days, window(days)
        if n is not None:
            r["applicants"], r["checked_at"] = applicants_label(n), now_iso
        keep.append(r)
    tab.update(keep)
    tab.delete(doomed)
    log(f"Removed {len(doomed)} (over 30 days or {REMOVE_APPLICANTS}+ applicants), kept {len(keep)}")
    return {ids[r["_row"]] for r in keep if ids[r["_row"]]}


def cycle(terms=None):
    test = terms is not None
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    now_iso = datetime.now(TORONTO).strftime("%Y-%m-%d %H:%M")
    state = {} if test else load(STATE_PATH, {})
    ruled_out = set(load(RULED_OUT_PATH, []))
    tab = Tab(open_sheet())

    since = hours_since(state.get("searched_at"))
    full = not test and (not state.get("full_at") or since is None or since > FULL_AFTER_HOURS)
    last_refresh = hours_since(state.get("refreshed_at"))
    refresh = full or test or last_refresh is None or last_refresh >= REFRESH_HOURS
    listed = recheck(tab, now_iso) if refresh else \
        {m.group(1) for r in tab.rows() if (m := _LI_ID_RE.search(r["apply_url"]))}

    if full:
        log("Full search: all of Canada and each province, last 30 days")
        found = search_all(SEARCH_TERMS, PLACES, MAX_AGE_DAYS * 86400,
                           lambda p: FULL_PAGES.get(p, FULL_PAGES_DEFAULT))
    else:
        hours = 24 if test else min(MAX_AGE_DAYS * 24, int(since) + 2)
        found = search_all(terms or SEARCH_TERMS, ["Canada"], hours * 3600, lambda p: RECENT_PAGES)

    new = [i for i in found if i not in listed and i not in ruled_out]
    log(f"{len(found)} IT jobs found, {len(new)} new — reading their job pages")
    pages = job_pages(new)

    jobs, unknown, senior = [], 0, 0
    for jid in new:
        n, level = pages[jid]
        if n is None:
            unknown += 1
            continue
        if n >= MAX_APPLICANTS or level in SENIOR_LEVELS:
            ruled_out.add(jid)  # neither ever changes back
            senior += level in SENIOR_LEVELS
            continue
        j = found[jid]
        days = days_ago(j["posted_date"])
        if days is None or days > MAX_AGE_DAYS:
            continue
        jobs.append({**j, "level": level, "province": province_of(j["location"]), "days_ago": days,
                     "window": window(days), "applicants": applicants_label(n),
                     "apply_url": f"https://www.linkedin.com/jobs/view/{jid}",
                     "checked_at": now_iso, "discovered_at": now_iso})
    jobs.sort(key=lambda j: j["days_ago"])
    tab.append(jobs)
    week = sum(j["window"] == "Last week" for j in jobs)
    log(f"Added {len(jobs)} ({week} last week, {len(jobs) - week} last month); "
        f"{senior} Director/Executive left out; {unknown} without an applicant count")

    DATA.mkdir(exist_ok=True)
    RULED_OUT_PATH.write_text(json.dumps(sorted(ruled_out)))
    if not test:
        state["searched_at"] = started
        if full:
            state["full_at"] = started
        if refresh:
            state["refreshed_at"] = started
        STATE_PATH.write_text(json.dumps(state, indent=1))


if __name__ == "__main__":
    cycle(sys.argv[1:] or None)
