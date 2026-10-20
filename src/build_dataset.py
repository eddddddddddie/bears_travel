"""Step 3: build the team-match travel dataset.

Inputs:   data/interim/rlp_results.csv  (scrape_rlp.py)
          data/interim/asb_lines.csv    (fetch_lines.py, optional)
          reference/teams.csv, venues.csv, base_overrides.csv
Outputs:  data/processed/team_matches.parquet and .csv  (two rows per match, one per team)
          data/processed/unmatched_teams.csv, unmatched_venues.csv  (fill gaps in reference/)
          data/processed/validation_report.txt

Travel is computed under two assumptions, and both are kept for Phase 2:
  return_home   the team flies home after every away match (normal NRL practice);
  venue_to_venue the team travels directly from its previous venue (multi-week trips, hubs).
"""
from __future__ import annotations

import sys
from datetime import datetime

import numpy as np
import pandas as pd

import config
import elo
from common import AliasResolver, clock_hour, haversine_km, local_to_utc, utc_offset_hours

FINALS_RE = r"(?i)final|week|semi|qualif|elimination|preliminary|grand|play[- ]?off"


# ---------------------------------------------------------------- loading and name resolution
def load_results():
    """Read rlp_results.csv, drop excluded rounds and games without scores.

    Returns (res, n_all, n_excluded, n_no_score). elo.py uses the same filter.
    """
    res = pd.read_csv(config.INTERIM_DIR / "rlp_results.csv", parse_dates=["date"], dtype={"kickoff_local": "string"})
    n_all = len(res)
    excluded = res["round_label"].fillna("").str.contains(config.EXCLUDE_ROUND_RE, regex=True)
    res = res[~excluded].dropna(subset=["home_score", "away_score"]).reset_index(drop=True)
    n_excluded = int(excluded.sum())
    return res, n_all, n_excluded, n_all - n_excluded - len(res)


def load_reference():
    teams = pd.read_csv(config.REF_DIR / "teams.csv")
    venues = pd.read_csv(config.REF_DIR / "venues.csv")
    overrides = pd.read_csv(config.REF_DIR / "base_overrides.csv", parse_dates=["start_date", "end_date"])
    return teams, venues, overrides


def resolve_names(res: pd.DataFrame, teams, venues):
    team_res, venue_res = AliasResolver(teams, "team_id"), AliasResolver(venues, "venue_id")
    unmatched_t, unmatched_v = [], []
    for side in ("home", "away"):
        ids = []
        for name, season in zip(res[f"{side}_team_raw"], res["season"]):
            row = team_res.resolve(name, int(season))
            ids.append(row["team_id"] if row else None)
            if row is None:
                unmatched_t.append((name, season))
        res[f"{side}_team_id"] = ids

    vcols = ["venue_id", "venue_name", "lat", "lon", "tz"]
    vals = []
    for name, season in zip(res["venue_raw"], res["season"]):
        row = venue_res.resolve(name, int(season))
        vals.append([row[c] for c in vcols] if row else [None] * len(vcols))
        if row is None:
            unmatched_v.append((name, season))
    res[["venue_id", "venue_name", "venue_lat", "venue_lon", "venue_tz"]] = pd.DataFrame(vals, index=res.index)

    summarise = lambda pairs, col: (pd.DataFrame(pairs, columns=[col, "season"])
                                    .groupby(col).agg(matches=("season", "size"),
                                                      first_season=("season", "min"),
                                                      last_season=("season", "max"))
                                    .sort_values("matches", ascending=False).reset_index())
    return res, summarise(unmatched_t, "team_raw"), summarise(unmatched_v, "venue_raw")


# ---------------------------------------------------------------- match level
def add_kickoff_utc(res: pd.DataFrame) -> pd.DataFrame:
    res["kickoff_imputed"] = res["kickoff_local"].isna()
    utc, offsets = [], []
    for d, ko, tz in zip(res["date"], res["kickoff_local"].fillna(config.DEFAULT_KICKOFF_LOCAL), res["venue_tz"]):
        if pd.isna(d) or pd.isna(tz):
            utc.append(pd.NaT)
            offsets.append(np.nan)
            continue
        h, m = map(int, ko.split(":"))
        u = local_to_utc(datetime(d.year, d.month, d.day, h, m), tz)
        utc.append(u)
        offsets.append(utc_offset_hours(tz, u))
    res["kickoff_utc"] = pd.to_datetime(utc, utc=True)
    res["venue_utc_offset_h"] = offsets
    return res


def to_long(res: pd.DataFrame) -> pd.DataFrame:
    keep = ["match_key", "season", "competition", "rlp_match_id", "round_label", "is_final", "date",
            "kickoff_local", "kickoff_imputed", "kickoff_utc", "venue_raw", "venue_id", "venue_name",
            "venue_lat", "venue_lon", "venue_tz", "venue_utc_offset_h", "crowd", "date_weekday_mismatch"]
    home = res[keep].assign(team_id=res["home_team_id"], opp_id=res["away_team_id"],
                            team_raw=res["home_team_raw"], is_designated_home=True,
                            points_for=res["home_score"], points_against=res["away_score"],
                            team_elo_pre=res["home_elo_pre"], opp_elo_pre=res["away_elo_pre"])
    away = res[keep].assign(team_id=res["away_team_id"], opp_id=res["home_team_id"],
                            team_raw=res["away_team_raw"], is_designated_home=False,
                            points_for=res["away_score"], points_against=res["home_score"],
                            team_elo_pre=res["away_elo_pre"], opp_elo_pre=res["home_elo_pre"])
    long = pd.concat([home, away], ignore_index=True)
    long["margin"] = long["points_for"] - long["points_against"]
    long["elo_expected_margin"] = elo.expected_margin(long["team_elo_pre"], long["opp_elo_pre"],
                                                     long["is_designated_home"])
    # Outcome for seasons without lines: points above the Elo expectation
    long["margin_vs_elo"] = long["margin"] - long["elo_expected_margin"]
    return long


# ---------------------------------------------------------------- team bases
def attach_bases(long: pd.DataFrame, teams: pd.DataFrame, overrides: pd.DataFrame) -> pd.DataFrame:
    base = teams.set_index("team_id")[["base_name", "lat", "lon", "tz"]]
    long = long.join(base.add_prefix("base_"), on="team_id")
    long = long.rename(columns={"base_base_name": "base_name"})
    long["base_override"] = False
    long["base_override_verified"] = pd.NA
    for ov in overrides.itertuples():
        mask = (long["team_id"] == ov.team_id) & (long["date"] >= ov.start_date) & (long["date"] <= ov.end_date)
        long.loc[mask, ["base_name", "base_lat", "base_lon", "base_tz"]] = [ov.base_name, ov.lat, ov.lon, ov.tz]
        long.loc[mask, "base_override"] = True
        long.loc[mask, "base_override_verified"] = bool(ov.verified)
    return long


# ---------------------------------------------------------------- travel features
def wrap_hours(h):
    """Fold a clock difference into [-12, 12): Brisbane to Las Vegas is +6 (east), not -18."""
    return (h + 12) % 24 - 12


def add_travel_features(long: pd.DataFrame) -> pd.DataFrame:
    long = long.sort_values(["team_id", "kickoff_utc", "match_key"]).reset_index(drop=True)
    long["km_from_base"] = haversine_km(long["base_lat"], long["base_lon"], long["venue_lat"], long["venue_lon"])
    long["at_base_venue"] = long["km_from_base"] <= config.AT_BASE_KM
    long["is_trip"] = long["km_from_base"] > config.AWAY_TRIP_KM

    # Clock comparisons are made at the kick-off instant, so daylight saving in each zone is applied
    def offset_at(tz_col):
        return [utc_offset_hours(tz, u) if isinstance(tz, str) and pd.notna(u) else np.nan
                for tz, u in zip(long[tz_col], long["kickoff_utc"])]
    long["base_utc_offset_h"] = offset_at("base_tz")
    long["tz_shift_from_base_h"] = wrap_hours(long["venue_utc_offset_h"] - long["base_utc_offset_h"])
    long["kickoff_base_clock_h"] = [clock_hour(tz, u) if isinstance(tz, str) and pd.notna(u) else np.nan
                                    for tz, u in zip(long["base_tz"], long["kickoff_utc"])]

    g = long.groupby("team_id", sort=False)
    long["prev_kickoff_utc"] = g["kickoff_utc"].shift()
    long["prev_season"] = g["season"].shift()
    for c in ("venue_id", "venue_lat", "venue_lon", "venue_tz"):
        long[f"prev_{c}"] = g[c].shift()
    long["season_opener"] = long["prev_season"].isna() | (long["prev_season"] != long["season"])
    rest = (long["kickoff_utc"] - long["prev_kickoff_utc"]).dt.total_seconds() / 86400
    long["days_rest"] = rest.where(~long["season_opener"]).round(2)
    long["km_from_prev_venue"] = haversine_km(long["prev_venue_lat"], long["prev_venue_lon"],
                                              long["venue_lat"], long["venue_lon"])
    long.loc[long["season_opener"], "km_from_prev_venue"] = long.loc[long["season_opener"], "km_from_base"]
    prev_off = [utc_offset_hours(tz, u) if isinstance(tz, str) and pd.notna(u) else np.nan
                for tz, u in zip(long["prev_venue_tz"], long["kickoff_utc"])]
    long["tz_shift_from_prev_venue_h"] = wrap_hours(long["venue_utc_offset_h"] - np.array(prev_off, dtype=float))
    long.loc[long["season_opener"], "tz_shift_from_prev_venue_h"] = long.loc[long["season_opener"], "tz_shift_from_base_h"]
    long["travel_direction"] = np.select(
        [long["tz_shift_from_base_h"] > 0, long["tz_shift_from_base_h"] < 0], ["east", "west"], "none")
    long.loc[long["tz_shift_from_base_h"].isna(), "travel_direction"] = pd.NA

    # Rolling travel load over the previous ROLLING_WINDOW_DAYS, including the trip to this match
    #   return_home: earlier matches count as a round trip (2 x km_from_base), this match as the outbound leg
    #   venue_to_venue: sum of km_from_prev_venue legs inside the window
    window = pd.Timedelta(days=config.ROLLING_WINDOW_DAYS)
    rh, v2v, trips = [], [], []
    for _, grp in long.groupby("team_id", sort=False):
        t = grp["kickoff_utc"].to_numpy()
        kb = grp["km_from_base"].fillna(0).to_numpy()
        kp = grp["km_from_prev_venue"].fillna(0).to_numpy()
        tr = grp["is_trip"].fillna(False).to_numpy(dtype=bool)
        for i in range(len(grp)):
            if pd.isna(t[i]):
                rh.append(np.nan); v2v.append(np.nan); trips.append(np.nan)
                continue
            lo = (t >= t[i] - window) & (t < t[i])
            rh.append(2 * kb[lo].sum() + kb[i])
            v2v.append(kp[lo].sum() + kp[i])
            trips.append(int(tr[lo].sum() + tr[i]))
    long["km_21d_return_home"] = rh
    long["km_21d_venue_to_venue"] = v2v
    long["trips_21d"] = trips
    return long


def add_opponent_features(long: pd.DataFrame) -> pd.DataFrame:
    cols = ["km_from_base", "tz_shift_from_base_h", "kickoff_base_clock_h", "days_rest",
            "km_21d_return_home", "trips_21d", "at_base_venue"]
    opp = long[["match_key", "team_id"] + cols].rename(columns={"team_id": "opp_id"})
    long = long.merge(opp.rename(columns={c: f"opp_{c}" for c in cols}), on=["match_key", "opp_id"], how="left")
    long["rest_diff_days"] = long["days_rest"] - long["opp_days_rest"]
    long["km_diff"] = long["km_from_base"] - long["opp_km_from_base"]
    long["abs_tz_shift_diff_h"] = long["tz_shift_from_base_h"].abs() - long["opp_tz_shift_from_base_h"].abs()
    return long


# ---------------------------------------------------------------- betting lines
def attach_lines(long: pd.DataFrame, teams: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    path = config.INTERIM_DIR / "asb_lines.csv"
    stats = {"asb_rows": 0, "asb_unmatched_names": [], "matched_matches": 0, "score_mismatches": 0}
    if not path.exists():
        long["team_line_close"] = np.nan
        return long, stats
    asb = pd.read_csv(path, parse_dates=["date"])
    stats["asb_rows"] = len(asb)
    res = AliasResolver(teams, "team_id")
    for side in ("home", "away"):
        ids = [(res.resolve(n, int(d.year)) or {}).get("team_id") for n, d in zip(asb[f"{side}_team_raw"], asb["date"])]
        asb[f"asb_{side}_id"] = ids
    bad = asb[asb["asb_home_id"].isna() | asb["asb_away_id"].isna()]
    stats["asb_unmatched_names"] = sorted(set(bad.loc[bad["asb_home_id"].isna(), "home_team_raw"])
                                          | set(bad.loc[bad["asb_away_id"].isna(), "away_team_raw"]))
    asb = asb.dropna(subset=["asb_home_id", "asb_away_id"])
    asb["pair"] = [("|".join(sorted(p))) for p in zip(asb["asb_home_id"], asb["asb_away_id"])]

    m = long[["match_key", "date", "team_id", "opp_id", "points_for", "points_against"]].dropna(subset=["team_id", "opp_id"]).copy()
    m["pair"] = ["|".join(sorted(p)) for p in zip(m["team_id"], m["opp_id"])]
    j = m.merge(asb, on="pair", suffixes=("", "_asb"))
    j["dd"] = (j["date"] - j["date_asb"]).abs().dt.days
    j = j[j["dd"] <= config.LINE_JOIN_MAX_DAYS].sort_values("dd").drop_duplicates(["match_key", "team_id"])
    team_is_asb_home = j["team_id"] == j["asb_home_id"]
    j["team_line_close"] = np.where(team_is_asb_home, j["home_line_close"], -j["home_line_close"])
    j["team_line_open"] = np.where(team_is_asb_home, j["home_line_open"], -j["home_line_open"])
    j["team_odds_close"] = np.where(team_is_asb_home, j["home_odds_close"], j["away_odds_close"])
    j["opp_odds_close"] = np.where(team_is_asb_home, j["away_odds_close"], j["home_odds_close"])
    asb_pf = np.where(team_is_asb_home, j["asb_home_score"], j["asb_away_score"])
    j["asb_score_mismatch"] = pd.notna(asb_pf) & (asb_pf != j["points_for"])
    stats["matched_matches"] = j["match_key"].nunique()
    stats["score_mismatches"] = int(j["asb_score_mismatch"].sum())

    cols = ["match_key", "team_id", "team_line_close", "team_line_open", "team_odds_close", "opp_odds_close", "asb_score_mismatch"]
    long = long.merge(j[cols], on=["match_key", "team_id"], how="left")
    # Phase 2 outcome: positive = team beat the market expectation by that many points
    long["margin_vs_line"] = long["margin"] + long["team_line_close"]
    inv_t, inv_o = 1 / long["team_odds_close"], 1 / long["opp_odds_close"]
    long["implied_win_prob"] = inv_t / (inv_t + inv_o)  # bookmaker margin removed proportionally
    return long, stats


# ---------------------------------------------------------------- main
def main() -> int:
    src = config.INTERIM_DIR / "rlp_results.csv"
    if not src.exists():
        print("Run src/scrape_rlp.py first.")
        return 1
    res, n_all, n_excluded, n_no_score = load_results()

    teams, venues, overrides = load_reference()
    res, unmatched_t, unmatched_v = resolve_names(res, teams, venues)
    res["match_key"] = (res["rlp_match_id"].astype("Int64").astype("string")
                        .fillna(res["season"].astype(str) + "_" + res.index.astype(str)))
    res["is_final"] = res["round_label"].fillna("").str.contains(FINALS_RE, regex=True) \
        & ~res["round_label"].fillna("").str.contains(r"(?i)^round\s*\d+$", regex=True)
    res = add_kickoff_utc(res)
    res = elo.add_elo(res)

    long = to_long(res)
    long = attach_bases(long, teams, overrides)
    long = add_travel_features(long)
    long = add_opponent_features(long)
    long, line_stats = attach_lines(long, teams)
    long["covid_season"] = long["season"].isin(config.COVID_SEASONS)

    config.PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    order = ["match_key", "season", "competition", "round_label", "is_final", "date", "kickoff_local",
             "kickoff_utc", "team_id", "opp_id", "is_designated_home", "points_for", "points_against",
             "margin", "team_line_close", "margin_vs_line", "implied_win_prob",
             "team_elo_pre", "opp_elo_pre", "elo_expected_margin", "margin_vs_elo",
             "venue_id", "venue_name", "base_name", "at_base_venue", "km_from_base", "km_from_prev_venue",
             "tz_shift_from_base_h", "tz_shift_from_prev_venue_h", "travel_direction", "kickoff_base_clock_h",
             "days_rest", "km_21d_return_home", "km_21d_venue_to_venue", "trips_21d"]
    long = long[order + [c for c in long.columns if c not in order]]
    long.to_parquet(config.PROCESSED_DIR / "team_matches.parquet", index=False)
    long.to_csv(config.PROCESSED_DIR / "team_matches.csv", index=False)
    unmatched_t.to_csv(config.PROCESSED_DIR / "unmatched_teams.csv", index=False)
    unmatched_v.to_csv(config.PROCESSED_DIR / "unmatched_venues.csv", index=False)

    report = validation_report(long, n_all, n_excluded, n_no_score, unmatched_t, unmatched_v, line_stats)
    (config.PROCESSED_DIR / "validation_report.txt").write_text(report, encoding="utf-8")
    print(report)
    return 0


def validation_report(long, n_all, n_excluded, n_no_score, unmatched_t, unmatched_v, line_stats) -> str:
    rows_per_match = long.groupby("match_key").size()
    lines = [
        "PHASE 1 VALIDATION REPORT",
        f"Scraped rows: {n_all}; excluded rounds: {n_excluded}; dropped without scores: {n_no_score}; matches kept: {long['match_key'].nunique()}",
        f"Matches without exactly two team rows: {int((rows_per_match != 2).sum())}",
        f"Unmatched team names: {len(unmatched_t)} (see unmatched_teams.csv)",
        f"Unmatched venue names: {len(unmatched_v)} covering {int(unmatched_v['matches'].sum()) if len(unmatched_v) else 0} matches (see unmatched_venues.csv)",
        f"Team rows with imputed kick-off time: {int(long['kickoff_imputed'].sum())}",
        f"Team rows with weekday mismatch between listed day and parsed date: {int(long['date_weekday_mismatch'].fillna(False).sum())}",
        f"Team rows using an unverified base override: {int((long['base_override'] & (long['base_override_verified'] == False)).sum())}",
        f"ASB rows: {line_stats['asb_rows']}; matches joined to a line: {line_stats['matched_matches']}; "
        f"score mismatches RLP vs ASB: {line_stats['score_mismatches']}",
        f"ASB team names not resolved: {line_stats['asb_unmatched_names'] or 'none'}",
        "",
        *elo.fit_summary(long),
        "",
        "Closing line coverage by season (share of team rows):",
        long.groupby("season")["team_line_close"].apply(lambda s: s.notna().mean()).round(2).to_string(),
        "",
        "Time zone shift from base, team rows by team (hours, count):",
        (long.dropna(subset=["tz_shift_from_base_h"])
             .assign(shift=lambda d: d["tz_shift_from_base_h"].round(1))
             .loc[lambda d: d["shift"] != 0]
             .groupby(["team_id", "shift"]).size().rename("rows").reset_index().to_string(index=False)),
        "",
        "Days rest summary (excluding season openers):",
        long["days_rest"].describe().round(2).to_string(),
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
