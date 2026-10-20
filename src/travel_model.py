"""Phase 2: what travel costs a team, whether the betting market prices it, and the Perth Bears outlook.

Input:   data/processed/team_matches.parquet (build_dataset.py)
Outputs: data/processed/travel_model/*.csv and model_report.txt (tables sized for a dashboard),
         and site/data/*.json, the copies the static site at site/index.html reads

Unit of analysis: one row per match, seen from the designated home side, with every travel feature
entered as team minus opponent. Each season gets its own intercept, which is the home advantage left
over when both sides are local, equally rested and equally rated (crowd, ground familiarity, referees).
The travel terms then measure the part of home advantage that comes from the visitors' journey.

Models (OLS, heteroskedasticity-robust HC1 standard errors):
  bands      margin ~ season intercepts + elo diff + distance band diffs + rest diff + prior 21-day load diff
  linear     as bands, with km per 1,000 and east and west time-zone hours in place of the bands.
             A sensitivity check only: almost every time-zone crossing is a New Zealand or Perth trip, so
             time zone and distance cannot be separated, and the hour terms come out positive (they offset
             a straight line that overstates the cost of 2,000 to 3,000 km trips). The projection uses bands.
  market     on 2013+ matches with a closing line, the same two designs fitted to
             -team_line_close (what the market prices), margin (what happened, same matches) and
             margin_vs_line (what the market missed; no Elo term because the line is the expectation)

Sample: 1996 to 2026 (1995 is the Elo burn-in season), excluding the 2020 and 2021 seasons
(temporary bases are unverified) and matches over EXCLUDE_KM from either base (Las Vegas).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import statsmodels.api as sm

from scipy.stats import norm

import config
from common import haversine_km, utc_offset_hours

OUT_DIR_NAME = "travel_model"
# Tables the static site (site/index.html) reads, written as JSON to site/data/ on every run
SITE_DATA_DIR = config.ROOT / "site" / "data"
SITE_TABLES = ("travel_effects", "home_advantage_by_season", "projection_2027_clubs", "team_season_travel",
               "perth_reds_1995_1997")
FIRST_SEASON = 1996
EXCLUDE_KM = 6000
BANDS = [(150, 1000), (1000, 2000), (2000, 3000), (3000, EXCLUDE_KM)]   # baseline: within 150 km of base
BAND_LABELS = ["150 to 1,000 km", "1,000 to 2,000 km", "2,000 to 3,000 km", "3,000 km and over"]
REST_CAP = (4, 14)               # days; rest beyond two weeks adds nothing, season openers set to 7
BEARS_SEASON = 2027
BEARS_SEASON_DATES = ("2027-03-05", "2027-09-05")  # regular season window used to average time-zone shifts
AWAY_GAMES = 12                  # 24-round regular season, half away


# ---------------------------------------------------------------- features
def side_features(km, tz, rest, load21) -> pd.DataFrame:
    """Travel features for one side of a match, from that side's columns."""
    km = pd.Series(km, dtype=float).reset_index(drop=True)
    tz = pd.Series(tz, dtype=float).reset_index(drop=True)
    f = pd.DataFrame(index=km.index)
    for (lo, hi), name in zip(BANDS, band_terms()):
        f[name] = ((km > lo) & (km <= hi)).astype(float)
    f["km_1000"] = km / 1000
    f["tz_east_h"] = tz.clip(lower=0)
    f["tz_west_h"] = (-tz).clip(lower=0)
    f["rest_days"] = pd.Series(rest, dtype=float).reset_index(drop=True).fillna(7).clip(*REST_CAP)
    f["prior_load_1000km"] = (pd.Series(load21, dtype=float).reset_index(drop=True) - km).clip(lower=0) / 1000
    return f


def band_terms() -> list[str]:
    return [f"band_{lo}_{hi}" for lo, hi in BANDS]


def match_frame(long: pd.DataFrame) -> pd.DataFrame:
    """One row per match from the designated home side, travel features as home minus away."""
    h = long[long["is_designated_home"]].reset_index(drop=True)
    own = side_features(h["km_from_base"], h["tz_shift_from_base_h"], h["days_rest"], h["km_21d_return_home"])
    opp = side_features(h["opp_km_from_base"], h["opp_tz_shift_from_base_h"], h["opp_days_rest"],
                        h["opp_km_21d_return_home"])
    m = h[["match_key", "season", "date", "team_id", "opp_id", "is_final", "margin", "team_line_close",
           "margin_vs_line", "km_from_base", "opp_km_from_base", "covid_season"]].copy()
    m["elo_diff"] = h["team_elo_pre"] - h["opp_elo_pre"]
    m["line_expected_margin"] = -h["team_line_close"]
    for c in own.columns:
        m[c] = own[c] - opp[c]
    return m


def sample(m: pd.DataFrame) -> pd.DataFrame:
    keep = (m["season"] >= FIRST_SEASON) & ~m["covid_season"] \
        & (m[["km_from_base", "opp_km_from_base"]].max(axis=1) < EXCLUDE_KM) \
        & m[["km_from_base", "opp_km_from_base", "elo_diff"]].notna().all(axis=1)
    return m[keep].reset_index(drop=True)


# ---------------------------------------------------------------- models
TRAVEL_TERMS = {"bands": band_terms(), "linear": ["km_1000", "tz_east_h", "tz_west_h"]}
COMMON_TERMS = ["rest_days", "prior_load_1000km"]


def fit_ols(m: pd.DataFrame, outcome: str, design: str, elo: bool = True):
    seasons = pd.get_dummies(m["season"], prefix="home_adv", dtype=float)
    X = pd.concat([seasons, m[(["elo_diff"] if elo else []) + TRAVEL_TERMS[design] + COMMON_TERMS]], axis=1)
    return sm.OLS(m[outcome].astype(float), X).fit(cov_type="HC1")


def tidy(fit, model: str, design: str, outcome: str, sample_label: str) -> pd.DataFrame:
    ci = fit.conf_int()
    return pd.DataFrame({
        "model": model, "design": design, "outcome": outcome, "sample": sample_label,
        "term": fit.params.index, "estimate": fit.params.values, "std_error": fit.bse.values,
        "ci_low": ci[0].values, "ci_high": ci[1].values, "p_value": fit.pvalues.values,
        "n_matches": int(fit.nobs), "r2": fit.rsquared,
    })


def run_models(m: pd.DataFrame) -> pd.DataFrame:
    s = sample(m)
    lined = s[(s["season"] >= 2013) & s["team_line_close"].notna()]
    span = f"{s['season'].min()} to {s['season'].max()} excl. 2020 and 2021"
    lspan = f"{lined['season'].min()} to {lined['season'].max()} with closing line, excl. 2020 and 2021"
    out = []
    for design in ("bands", "linear"):
        out.append(tidy(fit_ols(s, "margin", design), "travel_cost", design, "margin", span))
        out.append(tidy(fit_ols(lined, "line_expected_margin", design), "market_priced", design,
                        "-team_line_close", lspan))
        out.append(tidy(fit_ols(lined, "margin", design), "travel_cost_line_era", design, "margin", lspan))
        out.append(tidy(fit_ols(lined, "margin_vs_line", design, elo=False), "market_missed", design,
                        "margin_vs_line", lspan))
    return pd.concat(out, ignore_index=True)


# ---------------------------------------------------------------- dashboard tables
TERM_LABELS = dict(zip(band_terms(), BAND_LABELS)) | {
    "km_1000": "per 1,000 km", "tz_east_h": "per hour east", "tz_west_h": "per hour west",
    "rest_days": "per extra day of rest", "prior_load_1000km": "per 1,000 km travelled in the previous 21 days",
    "elo_diff": "per point of Elo difference"}


def travel_effects(coefs: pd.DataFrame) -> pd.DataFrame:
    """Travel terms only, labelled, in points of margin for the team doing the travelling."""
    t = coefs[~coefs["term"].str.startswith("home_adv")].copy()
    t["label"] = t["term"].map(TERM_LABELS)
    return t


def home_advantage(coefs: pd.DataFrame, long: pd.DataFrame) -> pd.DataFrame:
    """Season intercepts from the travel cost model next to the raw home margin and the Elo constant."""
    c = coefs[(coefs["model"] == "travel_cost") & (coefs["design"] == "bands")
              & coefs["term"].str.startswith("home_adv")].copy()
    c["season"] = c["term"].str.replace("home_adv_", "").astype(int)
    raw = (long[long["is_designated_home"]].groupby("season")
           .agg(raw_home_margin=("margin", "mean"), matches=("margin", "size")).reset_index())
    out = raw.merge(c[["season", "estimate", "ci_low", "ci_high"]].rename(
        columns={"estimate": "home_ground_adv", "ci_low": "home_ground_ci_low", "ci_high": "home_ground_ci_high"}),
        on="season", how="left")
    out["elo_home_adv"] = config.ELO_HOME_ADV
    return out


def team_season_travel(long: pd.DataFrame) -> pd.DataFrame:
    g = long[long["season"] >= 1995].groupby(["season", "team_id"])
    out = g.agg(matches=("match_key", "size"), trips=("is_trip", "sum"),
                km_return_home=("km_from_base", lambda s: 2 * s.sum()),
                mean_trip_km=("km_from_base", lambda s: s[s > config.AWAY_TRIP_KM].mean()),
                tz_hours_crossed=("tz_shift_from_base_h", lambda s: 2 * s.abs().sum()),
                margin=("margin", "mean"), margin_vs_elo=("margin_vs_elo", "mean")).reset_index()
    out["projected"] = False
    return out


def pair_offsets(tz_from: str, tz_to: str, start: str, end: str) -> float:
    """Mean time-zone shift (to minus from) at 9:00 UTC on each week of the season window."""
    t = datetime.fromisoformat(start).replace(hour=9, tzinfo=timezone.utc)
    stop = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)
    shifts = []
    while t <= stop:
        shifts.append(utc_offset_hours(tz_to, t) - utc_offset_hours(tz_from, t))
        t += timedelta(days=7)
    return float(np.mean(shifts))


def bears_projection(coefs: pd.DataFrame, teams: pd.DataFrame, clubs: list[str],
                     sigma: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Each club's travel cost against every other club in an 18-team 2027 competition (bands design).

    Away games are approximated as trips to the opponent's base, so the draw is balanced and each
    club's season total is zero by construction: what a club loses on the road it gains when the
    same opponents travel to it. The useful outputs are the per-game costs and the home-away swing.
    sigma (residual SD of margin) converts points to win probability for an evenly matched game.
    Returns (pairs, clubs), in points of margin for that club.
    """
    t = teams.set_index("team_id")
    bands = (coefs[(coefs["model"] == "travel_cost") & (coefs["design"] == "bands")]
             .set_index("term")["estimate"])
    rows = []
    for a in clubs:
        for b in clubs:
            if a == b:
                continue
            km = float(haversine_km(t.at[a, "lat"], t.at[a, "lon"], t.at[b, "lat"], t.at[b, "lon"]))
            f = side_features([km], [0.0], [7.0], [km]).iloc[0]
            cost = float(sum(bands[c] * f[c] for c in band_terms()))
            rows.append({"team_id": a, "opp_id": b, "km": km,
                         "tz_shift_h": pair_offsets(t.at[a, "tz"], t.at[b, "tz"], *BEARS_SEASON_DATES),
                         "band": next((lab for (lo, hi), lab in zip(BANDS, BAND_LABELS) if lo < km <= hi),
                                      "within 150 km"),
                         # a travels to b; b is at its base, so only a's band enters
                         "away_cost": cost, "home_gain": -cost})
    pairs = pd.DataFrame(rows)
    club = pairs.groupby("team_id").agg(
        mean_away_km=("km", "mean"), mean_abs_tz_h=("tz_shift_h", lambda s: s.abs().mean()),
        trips_over_3000km=("km", lambda s: int((s > 3000).sum())),
        away_cost_per_game=("away_cost", "mean"), home_gain_per_game=("home_gain", "mean")).reset_index()
    club["home_away_swing"] = club["home_gain_per_game"] - club["away_cost_per_game"]
    club["away_win_prob_change"] = norm.cdf(club["away_cost_per_game"] / sigma) - 0.5
    club["season_km_return_home"] = AWAY_GAMES * 2 * club["mean_away_km"]
    club["season"] = BEARS_SEASON
    return pairs, club.sort_values("mean_away_km", ascending=False).reset_index(drop=True)


def perth_reds_case(long: pd.DataFrame) -> pd.DataFrame:
    """Western Reds 1995 to 1997: actual results against Elo, home and on the road, by trip length."""
    r = long[long["team_id"] == "perth_reds"].copy()
    r["setting"] = np.where(r["km_from_base"] <= config.AWAY_TRIP_KM, "home (Perth)",
                            np.where(r["opp_km_from_base"] <= config.AWAY_TRIP_KM, "away", "neutral"))
    return (r.groupby(["season", "setting"])
             .agg(matches=("margin", "size"), mean_margin=("margin", "mean"),
                  mean_margin_vs_elo=("margin_vs_elo", "mean"), mean_km=("km_from_base", "mean"),
                  mean_tz_shift_h=("tz_shift_from_base_h", "mean"))
             .reset_index())


# ---------------------------------------------------------------- report
def fmt(coefs, model, design, term):
    r = coefs[(coefs["model"] == model) & (coefs["design"] == design) & (coefs["term"] == term)]
    if r.empty:
        return "n/a"
    r = r.iloc[0]
    return f"{r['estimate']:+.2f} ({r['ci_low']:+.2f} to {r['ci_high']:+.2f})"


def report(coefs, home, clubs, reds) -> str:
    n = coefs.groupby("model")["n_matches"].first()
    lines = ["PHASE 2 TRAVEL MODEL REPORT",
             "Points of margin for the travelling team, 95% intervals (HC1). Negative = travel costs the team.",
             f"Matches: travel cost {n['travel_cost']}; line era {n['market_priced']}", ""]
    terms = band_terms() + TRAVEL_TERMS["linear"] + COMMON_TERMS + ["elo_diff"]
    hdr = f"{'term':<50}{'actual (all seasons)':>26}{'actual (line era)':>26}{'priced by market':>26}{'missed by market':>26}"
    lines.append(hdr)
    for term in terms:
        design = "bands" if term in band_terms() or term in COMMON_TERMS + ["elo_diff"] else "linear"
        vals = [fmt(coefs, mdl, design, term) for mdl in
                ("travel_cost", "travel_cost_line_era", "market_priced", "market_missed")]
        lines.append(f"{TERM_LABELS[term]:<50}" + "".join(f"{v:>26}" for v in vals))
    lines += ["", "Common terms (rest, prior load, Elo) are from the bands design; the linear design gives similar values.",
              "The linear design is a sensitivity check: time zone and distance cannot be separated in this data.",
              "", "Home advantage by season: raw designated-home margin and the home-ground part left after travel, rest and Elo:",
              home[["season", "matches", "raw_home_margin", "home_ground_adv"]].round(2).to_string(index=False),
              "", f"Projected {BEARS_SEASON} travel effect by club (balanced draw, away games at the opponent's base):",
              clubs[["team_id", "mean_away_km", "mean_abs_tz_h", "trips_over_3000km", "away_cost_per_game",
                     "home_away_swing", "away_win_prob_change"]].round(3).to_string(index=False),
              "", "Western Reds 1995 to 1997 against Elo:", reds.round(2).to_string(index=False)]
    return "\n".join(lines)


def main() -> int:
    src = config.PROCESSED_DIR / "team_matches.parquet"
    if not src.exists():
        print("Run src/build_dataset.py first.")
        return 1
    long = pd.read_parquet(src)
    out = config.PROCESSED_DIR / OUT_DIR_NAME
    out.mkdir(parents=True, exist_ok=True)

    m = match_frame(long)
    coefs = run_models(m)
    effects = travel_effects(coefs)
    home = home_advantage(coefs, long)
    teams = pd.read_csv(config.REF_DIR / "teams.csv")
    clubs_2027 = sorted(set(long.loc[long["season"] == long["season"].max(), "team_id"]) | {"perth_bears"})
    sigma = float(np.sqrt(fit_ols(sample(m), "margin", "bands").mse_resid))
    pairs, clubs = bears_projection(coefs, teams, clubs_2027, sigma)
    reds = perth_reds_case(long)

    # Projected 2027 rows sit after the history so a dashboard can rank the Bears against past seasons
    trips = pairs.assign(trip=pairs["km"] > config.AWAY_TRIP_KM).groupby("team_id")["trip"].mean()
    projected = clubs.assign(matches=2 * AWAY_GAMES, trips=lambda c: (AWAY_GAMES * c["team_id"].map(trips)).round(),
                             km_return_home=clubs["season_km_return_home"], mean_trip_km=clubs["mean_away_km"],
                             tz_hours_crossed=2 * AWAY_GAMES * clubs["mean_abs_tz_h"], projected=True)
    travel = pd.concat([team_season_travel(long), projected[["season", "team_id", "matches", "trips", "km_return_home",
                                                             "mean_trip_km", "tz_hours_crossed", "projected"]]],
                       ignore_index=True)

    tables = {"coefficients": coefs, "travel_effects": effects, "home_advantage_by_season": home,
              "team_season_travel": travel, "projection_2027_pairs": pairs, "projection_2027_clubs": clubs,
              "perth_reds_1995_1997": reds}
    names = teams.set_index("team_id")["display_name"]
    for name, table in tables.items():
        for col in ("team_id", "opp_id"):
            if col in table:
                table.insert(table.columns.get_loc(col) + 1, col.replace("_id", "") + "_name", table[col].map(names))
        table.to_csv(out / f"{name}.csv", index=False, float_format="%.5g")
        if name in SITE_TABLES:
            SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
            table.to_json(SITE_DATA_DIR / f"{name}.json", orient="records", double_precision=4)
    text = report(coefs, home, clubs, reds)
    (out / "model_report.txt").write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
