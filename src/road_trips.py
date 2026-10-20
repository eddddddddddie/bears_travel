"""Two-game road trips: how much travel the Perth Bears could save by staying on the road.

Rule: no more than two away games in a row. A season's away travel is then a set of trips,
each either one game (base -> A -> base) or two games on consecutive rounds, staying away
between them (base -> A -> B -> base). The trip plan with the least total km is a
maximum-weight matching on the saving from pairing two away games:
    saving(A, B) = d(base, A) + d(base, B) - d(A, B)
which networkx solves exactly. (Longer trips would make this a vehicle routing problem; with a
two-game cap it is not needed.)

The 2027 draw is not published, so every possible set of AWAY_GAMES away opponents is evaluated
(C(17, 12) = 6,188 sets for the Bears), each with its best trip plan. Venues are taken as each
opponent's base, as in the travel model's projection. Distances are great-circle km between bases.

The travel model found no measurable effect of travel in the previous 21 days on results, so the
savings are reported in km and flights, not in points of margin.

Outputs (data/processed/travel_model/ and site/data/):
    road_trips_summary      average over all away sets, plus the median, least-travel and most-travel sets
    road_trips_schedule     the trip plan for the median, least-travel and most-travel sets
    road_trips_pair_savings km saved by pairing each two away opponents in one trip
"""
from __future__ import annotations

import sys
from itertools import combinations

import networkx as nx
import numpy as np
import pandas as pd

import config
from common import haversine_km
from travel_model import AWAY_GAMES, OUT_DIR_NAME, SITE_DATA_DIR

TEAM = "perth_bears"
MAX_AWAY_IN_A_ROW = 2   # the matching formulation below relies on this being 2


def distance_matrix(teams: pd.DataFrame, clubs: list[str]) -> pd.DataFrame:
    t = teams.set_index("team_id").loc[clubs]
    lat, lon = t["lat"].to_numpy(), t["lon"].to_numpy()
    d = haversine_km(lat[:, None], lon[:, None], lat[None, :], lon[None, :])
    return pd.DataFrame(d, index=clubs, columns=clubs)


def best_trips(base: str, away: list[str], d: pd.DataFrame) -> tuple[float, list[tuple[str, ...]]]:
    """Least-km trip plan for one set of away games: (total km, trips as tuples of opponent ids)."""
    g = nx.Graph()
    g.add_nodes_from(away)
    for a, b in combinations(away, 2):
        saving = d.at[base, a] + d.at[base, b] - d.at[a, b]
        if saving > 0:
            g.add_edge(a, b, weight=saving)
    paired = nx.max_weight_matching(g)
    trips = [tuple(sorted(p)) for p in paired]
    in_pairs = {t for p in trips for t in p}
    trips += [(a,) for a in away if a not in in_pairs]
    return sum(trip_km(base, trip, d) for trip in trips), trips


def trip_km(base: str, trip: tuple[str, ...], d: pd.DataFrame) -> float:
    stops = [base, *trip, base]
    return float(sum(d.at[a, b] for a, b in zip(stops, stops[1:])))


def flights(base: str, trips: list[tuple[str, ...]], d: pd.DataFrame) -> int:
    """Legs longer than AWAY_TRIP_KM; shorter legs (two Sydney grounds) count as a bus trip."""
    n = 0
    for trip in trips:
        stops = [base, *trip, base]
        n += sum(d.at[a, b] > config.AWAY_TRIP_KM for a, b in zip(stops, stops[1:]))
    return n


def evaluate_all(base: str, opponents: list[str], d: pd.DataFrame, n_away: int) -> pd.DataFrame:
    rows = []
    for away in combinations(opponents, n_away):
        km, trips = best_trips(base, list(away), d)
        rows.append({"away": away, "trips": trips, "km_road_trips": km,
                     "km_return_home": float(2 * d.loc[base, list(away)].sum()),
                     "flights_road_trips": flights(base, trips, d),
                     "flights_return_home": flights(base, [(a,) for a in away], d)})
    s = pd.DataFrame(rows)
    s["km_saved"] = s["km_return_home"] - s["km_road_trips"]
    return s


def summarise(sets: pd.DataFrame, names: pd.Series) -> tuple[pd.DataFrame, pd.DataFrame]:
    order = sets.sort_values("km_road_trips", kind="stable").reset_index(drop=True)
    picks = {"median": order.iloc[len(order) // 2], "least_travel": order.iloc[0], "most_travel": order.iloc[-1]}
    labels = {"average": "Average over every possible set of away opponents",
              "median": "Median set of away opponents", "least_travel": "Away set with the least travel",
              "most_travel": "Away set with the most travel"}
    cols = ["km_return_home", "km_road_trips", "km_saved", "flights_return_home", "flights_road_trips"]
    summary = [{"scenario": "average", "label": labels["average"], "away_sets": len(sets),
                **{c: float(sets[c].mean()) for c in cols}, "away_opponents": ""}]
    for key, r in picks.items():
        summary.append({"scenario": key, "label": labels[key], "away_sets": 1, **{c: float(r[c]) for c in cols},
                        "away_opponents": ", ".join(sorted(names[a] for a in r["away"]))})
    summary = pd.DataFrame(summary)
    summary["pct_saved"] = summary["km_saved"] / summary["km_return_home"]
    summary["two_game_trips"] = [np.nan] + [sum(len(t) == 2 for t in picks[k]["trips"]) for k in picks]
    return summary, pd.DataFrame(picks).T


def schedule(picks: pd.DataFrame, base: str, d: pd.DataFrame, names: pd.Series) -> pd.DataFrame:
    rows = []
    for scenario, r in picks.iterrows():
        trips = sorted(r["trips"], key=lambda t: -trip_km(base, t, d))
        for i, trip in enumerate(trips, 1):
            km = trip_km(base, trip, d)
            alone = float(sum(2 * d.at[base, a] for a in trip))
            rows.append({"scenario": scenario, "trip": i, "games": len(trip),
                         "route": " to ".join([names[base], *(names[a] for a in trip), names[base]]),
                         "stop1": names[trip[0]], "stop2": names[trip[1]] if len(trip) == 2 else "",
                         "km": km, "km_if_return_home": alone, "km_saved": alone - km})
    return pd.DataFrame(rows)


def pair_savings(base: str, opponents: list[str], d: pd.DataFrame, names: pd.Series) -> pd.DataFrame:
    rows = [{"club_a": names[a], "club_b": names[b], "km_between": d.at[a, b],
             "km_saved": d.at[base, a] + d.at[base, b] - d.at[a, b]} for a, b in combinations(opponents, 2)]
    return pd.DataFrame(rows).sort_values("km_saved", ascending=False).reset_index(drop=True)


def main() -> int:
    src = config.PROCESSED_DIR / "team_matches.parquet"
    if not src.exists():
        print("Run src/build_dataset.py first.")
        return 1
    seasons = pd.read_parquet(src, columns=["season", "team_id"])
    teams = pd.read_csv(config.REF_DIR / "teams.csv")
    opponents = sorted(set(seasons.loc[seasons["season"] == seasons["season"].max(), "team_id"]))
    clubs = [TEAM] + opponents
    d = distance_matrix(teams, clubs)
    names = teams.set_index("team_id")["display_name"]
    names = names.str.replace(r"^Perth Bears$", "Perth", regex=True)   # the base, in routes

    sets = evaluate_all(TEAM, opponents, d, AWAY_GAMES)
    summary, picks = summarise(sets, names)
    tables = {"road_trips_summary": summary, "road_trips_schedule": schedule(picks, TEAM, d, names),
              "road_trips_pair_savings": pair_savings(TEAM, opponents, d, names)}
    out = config.PROCESSED_DIR / OUT_DIR_NAME
    out.mkdir(parents=True, exist_ok=True)
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name, table in tables.items():
        table.to_csv(out / f"{name}.csv", index=False, float_format="%.5g")
        table.to_json(SITE_DATA_DIR / f"{name}.json", orient="records", double_precision=4)

    print(f"{len(sets)} away sets of {AWAY_GAMES} from {len(opponents)} opponents; at most "
          f"{MAX_AWAY_IN_A_ROW} away games in a row")
    print(summary.drop(columns=["label"]).round(3).to_string(index=False))
    print()
    print(tables["road_trips_schedule"].round(0).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
