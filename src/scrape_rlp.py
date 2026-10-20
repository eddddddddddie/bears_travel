"""Step 1: scrape season results from Rugby League Project (RLP).

Parsing follows the column layout used by nrlR::fetch_results_rugbyproject (MIT licence),
with three changes:
  * any season slug can be fetched, so the 1995 to 1997 Western Reds seasons are included
    (nrlR rejects seasons before 1998);
  * the round label is read from the round header rows. nrlR increments its round counter
    whenever a new month name appears, which gives month numbers rather than rounds;
  * the listed weekday is checked against the parsed date and mismatches are flagged
    rather than shifted.

Pages are cached in data/raw/rlp/ so re-runs do not hit the site again.

Usage:  python src/scrape_rlp.py            # fetch missing pages, parse all
        python src/scrape_rlp.py --refresh  # re-download every page
"""
from __future__ import annotations

import argparse
import re
import sys
import time
from datetime import datetime

import pandas as pd
import requests
from bs4 import BeautifulSoup

import config

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
ROUND_RE = re.compile(r"\b(round|final|finals|week|qualif|elimination|semi|preliminary|grand|play[- ]?off)\b", re.I)
TIME_RE = re.compile(r"(?:(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\w*\s*)?(\d{1,2})[:.](\d{2})\s*([ap]\.?m\.?)?", re.I)
MATCH_ID_RE = re.compile(r"/matches/(\d+)")

# Default positions of the 11-cell results row (as in nrlR). Overridden if a header row is found.
DEFAULT_COLS = {"date": 1, "time": 2, "home": 3, "home_score": 4, "away": 5,
                "away_score": 6, "referee": 7, "venue": 8, "crowd": 9}
HEADER_KEYS = {"date": "date", "time": "time", "home": "home", "away": "away",
               "referee": "referee", "venue": "venue", "crowd": "crowd"}


def fetch_page(slug: str, refresh: bool = False) -> str:
    cache = config.RAW_DIR / "rlp" / f"{slug}.html"
    if cache.exists() and not refresh:
        return cache.read_text(encoding="utf-8")
    url = f"{config.RLP_BASE_URL}/seasons/{slug}/results.html"
    resp = requests.get(url, headers=config.HTTP_HEADERS, timeout=30)
    resp.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(resp.text, encoding="utf-8")
    time.sleep(config.REQUEST_PAUSE_S)
    return resp.text


def _to_int(text: str):
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def _header_map(cells: list[str]) -> dict | None:
    """Map column names from a header row such as 'Date | Time | Home | ... | Venue | Crowd'."""
    lower = [c.strip().lower() for c in cells]
    if not {"home", "away", "venue"} <= set(lower):
        return None
    cols = {key: lower.index(word) for key, word in HEADER_KEYS.items() if word in lower}
    # Scores sit in the cell after each team name when the header leaves them unlabelled
    cols.setdefault("home_score", cols["home"] + 1)
    cols.setdefault("away_score", cols["away"] + 1)
    return cols


def parse_results_html(html: str, season: int, competition: str, slug: str) -> pd.DataFrame:
    soup = BeautifulSoup(html, "lxml")
    cols = dict(DEFAULT_COLS)
    round_label, month, records = None, None, []

    for tr in soup.select("table tr"):
        cells = tr.find_all(["td", "th"])
        texts = [c.get_text(" ", strip=True) for c in cells]
        hdr = _header_map(texts)
        if hdr:
            cols = hdr
            continue
        if len(cells) < max(cols.values()) + 1:
            joined = " ".join(t for t in texts if t)
            # Themed rounds run long ("Round 15 - Beanies for Brain Cancer Round"); bye rows name teams, not rounds
            if joined and ROUND_RE.search(joined) and len(joined) < 60 and not joined.lower().startswith("bye"):
                round_label = joined
            continue

        # Some seasons (Super League 1997) omit the leading competition cell, giving 10-cell rows
        row_cols = cols
        if cols == DEFAULT_COLS and len(cells) == max(DEFAULT_COLS.values()) + 1:
            row_cols = {k: v - 1 for k, v in cols.items()}
        get = lambda key: texts[row_cols[key]] if key in row_cols and row_cols[key] < len(texts) else ""
        date_raw = get("date")
        m = re.match(r"([A-Za-z]{3})\w*\s+(\d{1,2})", date_raw)
        if m:
            month, day = m.group(1)[:3].title(), int(m.group(2))
        elif date_raw.isdigit() and month:
            day = int(date_raw)
        else:
            day = None
        try:
            match_date = datetime.strptime(f"{day} {month} {season}", "%d %b %Y").date() if day else None
        except ValueError:
            match_date = None

        time_raw = get("time")
        tm = TIME_RE.search(time_raw)
        weekday_listed = tm.group(1).title() if tm and tm.group(1) else (time_raw[:3].title() if time_raw[:3].title() in WEEKDAYS else None)
        kickoff = None
        if tm:
            hour, minute, ampm = int(tm.group(2)), int(tm.group(3)), (tm.group(4) or "").lower().replace(".", "")
            if ampm == "pm" and hour != 12:
                hour += 12
            elif ampm == "am" and hour == 12:
                hour = 0
            kickoff = f"{hour:02d}:{minute:02d}"

        link = tr.find("a", href=MATCH_ID_RE)
        records.append({
            "season": season,
            "competition": competition,
            "rlp_slug": slug,
            "rlp_match_id": int(MATCH_ID_RE.search(link["href"]).group(1)) if link else None,
            "round_label": round_label,
            "date": match_date,
            "weekday_listed": weekday_listed,
            "kickoff_local": kickoff,
            "home_team_raw": get("home"),
            "home_score": _to_int(get("home_score")),
            "away_team_raw": get("away"),
            "away_score": _to_int(get("away_score")),
            "referee": get("referee"),
            "venue_raw": get("venue"),
            "crowd": _to_int(get("crowd")),
        })

    df = pd.DataFrame.from_records(records)
    if df.empty:
        return df
    df["date"] = pd.to_datetime(df["date"])
    actual = df["date"].dt.strftime("%a")
    df["date_weekday_mismatch"] = df["weekday_listed"].notna() & df["date"].notna() & (actual != df["weekday_listed"])
    return df


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true", help="re-download pages already cached")
    args = ap.parse_args(argv)

    frames, failed = [], []
    for season, comp, slug in config.SEASON_PAGES:
        try:
            html = fetch_page(slug, refresh=args.refresh)
        except requests.RequestException as exc:
            failed.append((slug, str(exc)))
            print(f"  FAILED {slug}: {exc}")
            continue
        df = parse_results_html(html, season, comp, slug)
        print(f"  {slug}: {len(df)} matches, {df['round_label'].nunique() if len(df) else 0} round labels")
        if df.empty:
            failed.append((slug, "page loaded but no result rows parsed"))
        frames.append(df)

    if not frames:
        print("No pages parsed.")
        return 1
    out = pd.concat(frames, ignore_index=True)
    config.INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    out.to_csv(config.INTERIM_DIR / "rlp_results.csv", index=False)
    print(f"Wrote {len(out)} rows to data/interim/rlp_results.csv")
    print(f"Rows with weekday mismatch: {int(out['date_weekday_mismatch'].sum())}; "
          f"missing kick-off time: {int(out['kickoff_local'].isna().sum())}")
    if failed:
        print("Check these slugs on rugbyleagueproject.org and correct config.SEASON_PAGES:")
        for slug, why in failed:
            print(f"  {slug}: {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
