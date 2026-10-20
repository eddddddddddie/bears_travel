"""Step 2: download AusSportsBetting (ASB) NRL results and odds, keep closing lines.

The ASB workbook has had a note row above the header in some versions, and its column
names have changed over time, so the header row and the line columns are located by name.

Usage:  python src/fetch_lines.py            # uses cached data/raw/asb_nrl.xlsx if present
        python src/fetch_lines.py --refresh
"""
from __future__ import annotations

import argparse
import sys

import pandas as pd
import requests

import config


def download(refresh: bool = False):
    path = config.RAW_DIR / "asb_nrl.xlsx"
    if path.exists() and not refresh:
        return path
    resp = requests.get(config.ASB_URL, headers=config.HTTP_HEADERS, timeout=60)
    resp.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(resp.content)
    return path


def _find_col(cols, *words, exclude=()):
    for c in cols:
        lc = str(c).lower()
        if all(w in lc for w in words) and not any(x in lc for x in exclude):
            return c
    return None


def read_asb(path) -> pd.DataFrame:
    probe = pd.read_excel(path, header=None, nrows=10)
    header_row = next(i for i, row in probe.iterrows()
                      if any(str(v).strip().lower() == "date" for v in row.values))
    raw = pd.read_excel(path, header=header_row)
    cols = raw.columns
    pick = {
        "date": _find_col(cols, "date"),
        "home_team_raw": _find_col(cols, "home team"),
        "away_team_raw": _find_col(cols, "away team"),
        "asb_home_score": _find_col(cols, "home score"),
        "asb_away_score": _find_col(cols, "away score"),
        "home_line_close": _find_col(cols, "home line", "close", exclude=("odds",)),
        "home_line_open": _find_col(cols, "home line", "open", exclude=("odds",)),
        "home_odds_close": _find_col(cols, "home odds", "close"),
        "away_odds_close": _find_col(cols, "away odds", "close"),
        "home_odds": _find_col(cols, "home odds", exclude=("close", "open", "min", "max")),
        "away_odds": _find_col(cols, "away odds", exclude=("close", "open", "min", "max")),
    }
    missing = [k for k in ("date", "home_team_raw", "away_team_raw", "home_line_close") if pick[k] is None]
    if missing:
        raise KeyError(f"ASB columns not found for {missing}. Columns present: {list(cols)}")
    out = pd.DataFrame({k: raw[v] if v is not None else pd.NA for k, v in pick.items()})
    out["date"] = pd.to_datetime(out["date"], dayfirst=True, errors="coerce").dt.normalize()
    out = out.dropna(subset=["date", "home_team_raw", "away_team_raw"])
    # Closing head-to-head odds fall back to the generic odds column for early seasons
    out["home_odds_close"] = out["home_odds_close"].fillna(out["home_odds"])
    out["away_odds_close"] = out["away_odds_close"].fillna(out["away_odds"])
    return out.drop(columns=["home_odds", "away_odds"])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args(argv)
    lines = read_asb(download(args.refresh))
    config.INTERIM_DIR.mkdir(parents=True, exist_ok=True)
    lines.to_csv(config.INTERIM_DIR / "asb_lines.csv", index=False)
    by_year = lines.groupby(lines["date"].dt.year)["home_line_close"].apply(lambda s: s.notna().mean())
    print(f"Wrote {len(lines)} rows to data/interim/asb_lines.csv")
    print("Share of matches with a closing line, by year:")
    print(by_year.round(2).to_string())
    return 0


if __name__ == "__main__":
    sys.exit(main())
