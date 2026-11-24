"""Download the published 2027 NRL draw from nrl.com and write reference/nrl_draw_2027.csv.

The draw page (https://www.nrl.com/draw/) is drawn by script from a JSON feed, one request per round:
    https://www.nrl.com/draw/data?competition=111&season=2027&round=N
Each fixture gives the home and away nicknames, the venue and its city, and the kick-off instant in UTC.
Raw responses are cached in data/raw/nrl_draw/ (not committed). The CSV is committed, since every later
step (src/published_2027.py) reads it, and it is the record of the draw the site was built from.

The NRL first published the 2027 draw on 20 November 2026. Rerun with --refresh when it changes
(kick-off times for later rounds are often moved during the season).

    python src/fetch_draw.py [--refresh]
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import pandas as pd
import requests

import config

DRAW_URL = "https://www.nrl.com/draw/data"
COMPETITION = 111   # NRL Telstra Premiership
SEASON = 2027
ROUNDS = 27
OUT = config.REF_DIR / f"nrl_draw_{SEASON}.csv"
COLUMNS = ["round", "kickoff_utc", "home", "away", "venue", "venue_city", "match_centre_url"]


def parse_round(payload: dict, round_no: int) -> list[dict]:
    """Matches in one round of the feed; byes and other entries are skipped."""
    rows = []
    for f in payload.get("fixtures", []):
        if f.get("type") != "Match":
            continue
        rows.append({"round": round_no, "kickoff_utc": f["clock"]["kickOffTimeLong"],
                     "home": f["homeTeam"]["nickName"], "away": f["awayTeam"]["nickName"],
                     "venue": f["venue"], "venue_city": f["venueCity"],
                     "match_centre_url": "https://www.nrl.com" + f["matchCentreUrl"]})
    return rows


def fetch_round(round_no: int, refresh: bool) -> dict:
    cache = config.RAW_DIR / "nrl_draw" / f"{SEASON}_round_{round_no:02d}.json"
    if cache.exists() and not refresh:
        return json.loads(cache.read_text())
    res = requests.get(DRAW_URL, params={"competition": COMPETITION, "season": SEASON, "round": round_no},
                       headers=config.HTTP_HEADERS, timeout=30)
    res.raise_for_status()
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(res.text)
    time.sleep(config.REQUEST_PAUSE_S)
    return res.json()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--refresh", action="store_true", help="download again even when cached")
    args = ap.parse_args()
    rows = []
    for r in range(1, ROUNDS + 1):
        rows.extend(parse_round(fetch_round(r, args.refresh), r))
    draw = pd.DataFrame(rows, columns=COLUMNS)
    per_club = pd.concat([draw["home"], draw["away"]]).value_counts()
    print(f"{len(draw)} matches in {draw['round'].nunique()} rounds; games per club: "
          f"{per_club.min()} to {per_club.max()}")
    draw.to_csv(OUT, index=False)
    print(f"Wrote {OUT.relative_to(config.ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
