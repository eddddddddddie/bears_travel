"""Elo-style team ratings on a points scale, used as the expectation for seasons without lines.

A rating is points better than the league average (0). Before each match:
    expected margin (home side) = home rating - away rating + ELO_HOME_ADV
and after it both ratings move by ELO_K x (actual margin - expected margin), in opposite
directions, so ratings stay centred within a season. This is the squared-error form of a
margin-of-victory Elo; the ratings read directly in points rather than on a 1500 scale.

Between seasons each rating keeps ELO_SEASON_CARRY of its value (the rest regresses to the
mean, compounded for each season a club sat out) and ratings are re-centred on the clubs
active that season. Clubs entering the competition:
    1995 clubs            start at 0; 1995 is a burn-in season and is left out of the fit
    expansion clubs       start at ELO_NEW_TEAM (Adelaide, Hunter, Melbourne, Titans, Dolphins)
    merged or successor   start at the mean of their parents' carried ratings (ELO_PARENTS)
    returning clubs       keep their last rating, regressed once per season missed (Souths 2002)

build_dataset.py applies the parameters stored in config.py. Refit them with
    python src/elo.py              # fit on ELO_FIT_SEASONS, print values to copy into config.py
which minimises squared error of the pre-match expected margin (each prediction uses only
earlier results, so the error is out of sample) and checks the fit against 2013+ closing lines.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

import config


def run_elo(res: pd.DataFrame, k: float, home_adv: float, carry: float, new_team: float,
            parents: dict | None = None) -> pd.DataFrame:
    """One chronological pass. Returns home_elo_pre and away_elo_pre aligned to res.index.

    res needs season, date, home_team_id, away_team_id, home_score, away_score. Rows with an
    unresolved team get no rating and do not update anyone.
    """
    parents = config.ELO_PARENTS if parents is None else parents
    order = res.sort_values(["date", "rlp_match_id"] if "rlp_match_id" in res else ["date"], kind="stable").index
    first_season = int(res["season"].min())
    active_by_season = {}
    for s, h, a in zip(res["season"], res["home_team_id"], res["away_team_id"]):
        active_by_season.setdefault(int(s), set()).update(t for t in (h, a) if isinstance(t, str))

    rating: dict[str, float] = {}
    last_played: dict[str, int] = {}
    home_pre = pd.Series(np.nan, index=res.index)
    away_pre = pd.Series(np.nan, index=res.index)
    season = None

    def start_season(s):
        start = {}
        for t in sorted(active_by_season.get(s, ())):
            ps = [p for p in parents.get(t, ()) if p in rating and last_played[p] > last_played.get(t, -1)]
            if ps:
                start[t] = float(np.mean([rating[p] * carry ** (s - last_played[p]) for p in ps]))
            elif t in rating:
                start[t] = rating[t] * carry ** (s - last_played[t])
            else:
                start[t] = 0.0 if s == first_season else new_team
        shift = np.mean(list(start.values())) if start else 0.0
        for t, r in start.items():
            rating[t] = r - shift
            last_played[t] = s

    for i in order:
        s = int(res.at[i, "season"])
        if s != season:
            start_season(s)
            season = s
        h, a = res.at[i, "home_team_id"], res.at[i, "away_team_id"]
        if not (isinstance(h, str) and isinstance(a, str)):
            continue
        home_pre[i], away_pre[i] = rating[h], rating[a]
        err = (res.at[i, "home_score"] - res.at[i, "away_score"]) - (rating[h] - rating[a] + home_adv)
        rating[h] += k * err
        rating[a] -= k * err
    return pd.DataFrame({"home_elo_pre": home_pre, "away_elo_pre": away_pre})


def add_elo(res: pd.DataFrame) -> pd.DataFrame:
    """Attach pre-match ratings using the parameters in config.py."""
    elo = run_elo(res, config.ELO_K, config.ELO_HOME_ADV, config.ELO_SEASON_CARRY, config.ELO_NEW_TEAM)
    return res.join(elo)


def expected_margin(team_elo, opp_elo, is_home) -> pd.Series:
    """Expected margin from the team's side: positive means the team is expected to win."""
    return team_elo - opp_elo + np.where(is_home, config.ELO_HOME_ADV, -config.ELO_HOME_ADV)


# ---------------------------------------------------------------- fitting
def _errors(res, params):
    elo = run_elo(res, *params)
    return (res["home_score"] - res["away_score"]) - (elo["home_elo_pre"] - elo["away_elo_pre"] + params[1])


def fit(res: pd.DataFrame, seasons=None) -> dict:
    """Fit K, home advantage, season carry and expansion offset by least squares on `seasons`."""
    from scipy.optimize import minimize

    lo, hi = seasons or config.ELO_FIT_SEASONS
    sub = res[res["season"] <= hi]
    in_fit = sub["season"].between(lo, hi)

    def loss(p):
        k, ha, carry, new = p
        if not (0 < k < 1 and 0 <= carry <= 1):
            return 1e9
        e = _errors(sub, (k, ha, carry, new))[in_fit]
        return float(np.nanmean(e ** 2))

    best = minimize(loss, x0=[0.08, 4.0, 0.6, -4.0], method="Nelder-Mead",
                    options={"xatol": 1e-4, "fatol": 1e-4, "maxiter": 2000})
    k, ha, carry, new = best.x
    return {"ELO_K": round(k, 4), "ELO_HOME_ADV": round(ha, 2),
            "ELO_SEASON_CARRY": round(carry, 3), "ELO_NEW_TEAM": round(new, 2),
            "rmse": round(best.fun ** 0.5, 3)}


def fit_summary(long: pd.DataFrame) -> list[str]:
    """Report lines: MAE of elo_expected_margin by period and agreement with closing lines."""
    lo, hi = config.ELO_FIT_SEASONS
    rows = long[long["is_designated_home"] & long["elo_expected_margin"].notna()]
    err = (rows["margin"] - rows["elo_expected_margin"]).abs()
    out = [f"Elo parameters: K {config.ELO_K}, home advantage {config.ELO_HOME_ADV} points, "
           f"season carry {config.ELO_SEASON_CARRY}, expansion start {config.ELO_NEW_TEAM} points; "
           f"fitted on {lo} to {hi}"]
    for label, mask in [(f"burn-in {rows['season'].min()} to {lo - 1}", rows["season"] < lo),
                        (f"fit {lo} to {hi}", rows["season"].between(lo, hi)),
                        (f"holdout {hi + 1} on", rows["season"] > hi)]:
        if mask.any():
            out.append(f"  MAE of elo_expected_margin, {label}: {err[mask].mean():.2f} points over {int(mask.sum())} matches; "
                       f"mean margin_vs_elo for the designated home side {rows.loc[mask, 'margin_vs_elo'].mean():+.2f}")
    lined = rows[(rows["season"] > hi) & rows["team_line_close"].notna()]
    if len(lined) > 2:
        corr = np.corrcoef(lined["elo_expected_margin"], -lined["team_line_close"])[0, 1]
        out.append(f"  Matches after {hi} with a closing line: {len(lined)}; "
                   f"correlation of elo_expected_margin with -team_line_close: {corr:.3f}")
        out.append(f"  On those matches: MAE Elo {(lined['margin'] - lined['elo_expected_margin']).abs().mean():.2f}, "
                   f"MAE closing line {(lined['margin'] + lined['team_line_close']).abs().mean():.2f}, "
                   f"mean Elo minus line expectation {(lined['elo_expected_margin'] + lined['team_line_close']).mean():.2f}")
    return out


def main() -> int:
    import build_dataset

    res, *_ = build_dataset.load_results()
    teams, venues, _ = build_dataset.load_reference()
    res, _, _ = build_dataset.resolve_names(res, teams, venues)
    params = fit(res)
    print(f"Fitted on {config.ELO_FIT_SEASONS[0]} to {config.ELO_FIT_SEASONS[1]} "
          f"(RMSE {params.pop('rmse')} points). Copy into src/config.py:")
    for name, value in params.items():
        print(f"{name} = {value}")
    print(f"Currently in config.py: K {config.ELO_K}, home {config.ELO_HOME_ADV}, "
          f"carry {config.ELO_SEASON_CARRY}, new team {config.ELO_NEW_TEAM}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
