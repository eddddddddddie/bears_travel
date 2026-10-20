"""A 2027 fixture the Perth Bears could put to the NRL to reduce their travel time.

Rules: no more than FIXTURE_MAX_AWAY_IN_A_ROW away games in a row, at least FIXTURE_MIN_NIGHTS_BETWEEN
nights between games, and games only on FIXTURE_GAME_DAYS (Thursday to Sunday).

Assumptions (settings in config.py; the 2027 draw is not published):
  - FIXTURE_ROUNDS rounds from the week of FIXTURE_SEASON_START: 24 games and 3 byes in the State of
    Origin window. No Magic Round or overseas round.
  - 24 games: every other club once and FIXTURE_REPEAT_OPPONENTS clubs twice (once home, once away),
    so 12 home games in Perth and 12 away games at 12 different opponents' bases.
  - Travel time is door to door: FLIGHT_GROUND_H per journey plus air time at FLIGHT_KMH_EAST or
    FLIGHT_KMH_WEST; legs under AWAY_TRIP_KM go by road. Legs to and from Perth follow the Qantas Group
    route flying on that day (flights.py: Townsville connects in Brisbane, Auckland is served on three
    days each way, Newcastle on three days with Sydney and road on the others).
  - The team travels the day before an away game and returns the day after. On a two-game trip it
    stays in the east and moves to the second city the day after the first game. So it leaves Perth on
    a Saturday and returns on a Friday (or on the Monday after a one-game trip).

Steps:
  1. Away opponents and trips. For every set of 12 away opponents from the 17 that includes the clubs
     in FIXTURE_AWAY_REQUIRED (the Warriors, so the season has a trip to New Zealand), the least-time
     trip plan is a maximum-weight matching on the travel time saved by pairing two away games (each
     pair flown in its quicker direction). The set with the least total travel time is proposed.
  2. Repeat opponents. The clubs met twice are chosen from the away set so that the average end-2026
     rating of the Bears' 24 opponents is as close as possible to the average of all 17. This does not
     change travel.
  3. Order. Round 1 is at home, then an away trip, two home games, and so on
     (H, [A A H H] x 5, A A, H), with byes after games FIXTURE_BYE_AFTER_GAME. The order of trips and of
     home opponents gives the longest possible gap between the two meetings with each repeat opponent.
  4. Days. Home games on Saturday. On a trip the first game is on Sunday and the second on the next
     Thursday (four nights, the minimum), which keeps the nights away from Perth to six per trip.

Outputs (data/processed/travel_model/ and site/data/):
    fixture_2027          one row per round
    fixture_2027_legs     every journey, with date, mode, km and hours
    fixture_2027_summary  travel hours, km, flights and nights away against two baselines
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from itertools import combinations, permutations

import networkx as nx
import numpy as np
import pandas as pd
from scipy.optimize import linear_sum_assignment

import config
from flights import PENALTY_H, apply_perth, journey
from road_trips import TEAM, distance_matrix
from travel_model import OUT_DIR_NAME, SITE_DATA_DIR

SITE_TABLES = ("fixture_2027", "fixture_2027_legs", "fixture_2027_summary")
DAY_OFFSET = {"Thu": 0, "Fri": 1, "Sat": 2, "Sun": 3}   # days after the round's Thursday
HOME_DAY, TRIP_FIRST_DAY, TRIP_SECOND_DAY = "Sat", "Sun", "Thu"
OUT_DAY, BACK_DAY, SINGLE_BACK_DAY = "Sat", "Fri", "Mon"   # Perth departure and return days that follow
PATTERN = ["H"] + ["A", "A", "H", "H"] * 5 + ["A", "A", "H"]   # 24 games, 12 home, 12 away
GAP_CAP = 13   # rounds; gaps beyond this do not improve the order further


# ---------------------------------------------------------------- travel time
def time_matrix(d: pd.DataFrame, lon: pd.Series) -> pd.DataFrame:
    """Door-to-door hours from row club's base to column club's base."""
    t = pd.DataFrame(0.0, index=d.index, columns=d.columns)
    for a in d.index:
        for b in d.columns:
            if a != b:
                t.at[a, b] = leg_hours(d.at[a, b], lon[b] > lon[a])
    return t


def leg_hours(km: float, eastbound: bool) -> float:
    if km < config.AWAY_TRIP_KM:
        return config.ROAD_GROUND_H + km / config.ROAD_KMH
    return config.FLIGHT_GROUND_H + km / (config.FLIGHT_KMH_EAST if eastbound else config.FLIGHT_KMH_WEST)


def pair_order(base: str, a: str, b: str, t: pd.DataFrame) -> tuple[tuple[str, str], float]:
    """Quicker direction for a two-game trip and its hours."""
    ab = t.at[base, a] + t.at[a, b] + t.at[b, base]
    ba = t.at[base, b] + t.at[b, a] + t.at[a, base]
    return ((a, b), ab) if ab <= ba else ((b, a), ba)


def best_pairs(base: str, away: list[str], t: pd.DataFrame,
               single_back: pd.Series | None = None) -> tuple[float, list[tuple[str, ...]]]:
    """Least-time plan with at most two away games in a row: (hours, trips in flying order).
    single_back: hours home after a one-game trip, when its return day differs from a two-game trip's."""
    g = nx.Graph()
    g.add_nodes_from(away)
    back = t[base] if single_back is None else single_back
    single = {a: t.at[base, a] + back[a] for a in away}
    for a, b in combinations(away, 2):
        saving = single[a] + single[b] - pair_order(base, a, b, t)[1]
        if saving > 0:
            g.add_edge(a, b, weight=saving)
    trips, hours = [], 0.0
    for a, b in sorted(tuple(sorted(p)) for p in nx.max_weight_matching(g)):   # sorted: the matching is a set
        order, h = pair_order(base, a, b, t)
        trips.append(order)
        hours += h
    paired = {x for trip in trips for x in trip}
    for a in away:
        if a not in paired:
            trips.append((a,))
            hours += single[a]
    return hours, trips


# ---------------------------------------------------------------- choices
def choose_away_set(base: str, opponents: list[str], t: pd.DataFrame, single_back: pd.Series, any_day, n_away: int):
    """t and single_back follow the trip days; any_day = (km, hours, flights) on any flying day, for the
    average-draw baseline of returning to Perth after every away game."""
    d_any, t_any, f_any = any_day
    best, return_home = None, []
    required = [c for c in config.FIXTURE_AWAY_REQUIRED if c in opponents]
    for away in combinations(opponents, n_away):
        return_home.append([sum(m.at[base, a] + m.at[a, base] for a in away) for m in (t_any, d_any, f_any)])
        if not set(required) <= set(away):   # the average draw covers every set; the proposal must include these
            continue
        hours, trips = best_pairs(base, list(away), t, single_back)
        if best is None or hours < best[0] - 1e-9:
            best = (hours, list(away), trips)
    assert best[0] < PENALTY_H, "no away set can be flown on the trip days"
    return best, [float(x) for x in np.mean(return_home, axis=0)]


def end_of_season_ratings(long: pd.DataFrame) -> pd.Series:
    last = long.sort_values("kickoff_utc").groupby("team_id").tail(1)
    return (last["team_elo_pre"] + config.ELO_K * last["margin_vs_elo"]).set_axis(last["team_id"])


def choose_repeats(away: list[str], opponents: list[str], rating: pd.Series, n: int) -> list[str]:
    target = rating[opponents].mean()
    def gap(rep):
        return abs((rating[opponents].sum() + rating[list(rep)].sum()) / (len(opponents) + n) - target)
    return list(min(combinations(sorted(away), n), key=lambda rep: (round(gap(rep), 9), rep)))


def round_of_game() -> list[int]:
    """Round number for each of the 24 games, skipping the bye rounds."""
    rounds, r = [], 0
    for g in range(1, len(PATTERN) + 1):
        r += 1
        rounds.append(r)
        if g in config.FIXTURE_BYE_AFTER_GAME:
            r += 1
    assert r == config.FIXTURE_ROUNDS, "pattern and byes must fill the season"
    return rounds


def order_season(trips: list[tuple[str, ...]], home_opps: list[str], repeats: list[str]):
    """Assign trips to away slots and opponents to home slots, maximising the gap between repeat meetings."""
    rounds = round_of_game()
    away_slots = [i for i, k in enumerate(PATTERN) if k == "A"]
    home_slots = [i for i, k in enumerate(PATTERN) if k == "H"]
    best = None
    for perm in permutations(range(len(trips))):
        away_round = {}
        for p, trip in enumerate(trips[i] for i in perm):
            for j, opp in enumerate(trip):
                away_round[opp] = rounds[away_slots[2 * p + j]]
        cost = np.zeros((len(home_opps), len(home_slots)))
        for i, opp in enumerate(home_opps):
            if opp in repeats:
                for j, s in enumerate(home_slots):
                    cost[i, j] = -min(abs(rounds[s] - away_round[opp]), GAP_CAP)
        rows, cols = linear_sum_assignment(cost)
        gaps = [-cost[r, c] for r, c in zip(rows, cols) if home_opps[r] in repeats]
        score = (min(gaps), sum(gaps))
        if best is None or score > best[0]:
            best = (score, perm, {home_slots[c]: home_opps[r] for r, c in zip(rows, cols)})
    _, perm, home_at = best
    games = [None] * len(PATTERN)
    for p, trip_index in enumerate(perm):
        for j, opp in enumerate(trips[trip_index]):
            games[away_slots[2 * p + j]] = ("Away", opp, p + 1, j + 1, len(trips[trip_index]))
    for s, opp in home_at.items():
        games[s] = ("Home", opp, None, None, None)
    return games, rounds


# ---------------------------------------------------------------- build the fixture
def build(long: pd.DataFrame, teams: pd.DataFrame):
    opponents = sorted(set(long.loc[long["season"] == long["season"].max(), "team_id"]))
    clubs = [TEAM] + opponents
    d_gc = distance_matrix(teams, clubs)
    tt = teams.set_index("team_id")
    t_gc = time_matrix(d_gc, tt["lon"])
    any_day = apply_perth(d_gc, t_gc, teams)
    d, t, _ = apply_perth(d_gc, t_gc, teams, day_out=OUT_DAY, day_back=BACK_DAY)
    single_back = apply_perth(d_gc, t_gc, teams, day_back=SINGLE_BACK_DAY)[1][TEAM]
    names, venue = tt["display_name"], tt["base_name"]

    n_away = len(PATTERN) // 2
    (hours, away, trips), average_draw = choose_away_set(TEAM, opponents, t, single_back, any_day, n_away)
    assert set(config.FIXTURE_AWAY_REQUIRED) & set(opponents) <= set(away), "required away games missing"
    repeats = choose_repeats(away, opponents, end_of_season_ratings(long), config.FIXTURE_REPEAT_OPPONENTS)
    home_opps = sorted([o for o in opponents if o not in away] + repeats)
    games, rounds = order_season(trips, home_opps, repeats)

    start = date.fromisoformat(config.FIXTURE_SEASON_START)
    meetings = {o: 0 for o in opponents}
    rows, legs = [], []
    by_round = {rounds[i]: g for i, g in enumerate(games)}
    for r in range(1, config.FIXTURE_ROUNDS + 1):
        thursday = start + timedelta(weeks=r - 1)
        if r not in by_round:
            rows.append({"round": r, "week_of": thursday.isoformat(), "date": "", "day": "", "home_away": "Bye",
                         "opponent": "", "venue": "", "meeting": "", "trip": None})
            continue
        kind, opp, trip, leg_no, trip_len = by_round[r]
        day = HOME_DAY if kind == "Home" else (TRIP_FIRST_DAY if leg_no == 1 else TRIP_SECOND_DAY)
        game_date = thursday + timedelta(days=DAY_OFFSET[day])
        meetings[opp] += 1
        total = 2 if opp in repeats else 1
        rows.append({"round": r, "week_of": thursday.isoformat(), "date": game_date.isoformat(), "day": day,
                     "home_away": kind, "opponent": names[opp],
                     "venue": "Perth" if kind == "Home" else venue[opp],
                     "meeting": f"{meetings[opp]} of {total}", "trip": trip})
        if kind == "Away":
            prev = TEAM if leg_no == 1 else by_round_trip(by_round, trip, 1)
            legs.append(leg(trip, game_date - timedelta(days=1 if leg_no == 1 else 0), prev, opp, d_gc, t_gc, tt,
                            depart_override=None if leg_no == 1 else rows[-2]["date"]))
            if leg_no == trip_len:
                legs.append(leg(trip, game_date + timedelta(days=1), opp, TEAM, d_gc, t_gc, tt))
    fixture = pd.DataFrame(rows)
    fixture["trip"] = fixture["trip"].astype("Int64")
    fixture["nights_since_last_game"] = nights_between(fixture)
    legs = pd.DataFrame(legs)
    check_rules(fixture)
    summary = summarise(legs, away, any_day, average_draw, names, repeats)
    return fixture, legs, summary


def by_round_trip(by_round: dict, trip: int, leg_no: int) -> str:
    return next(g[1] for g in by_round.values() if g[0] == "Away" and g[2] == trip and g[3] == leg_no)


def leg(trip, travel_date, a, b, d_gc, t_gc, tt, depart_override=None):
    """One journey. Legs to or from Perth take the route flying on the travel date; others are one
    great-circle flight, or road under AWAY_TRIP_KM."""
    if depart_override:   # move to the second city the day after the first game
        travel_date = date.fromisoformat(depart_override) + timedelta(days=1)
    day, names = travel_date.strftime("%a"), tt["display_name"]
    row = {"trip": trip, "date": travel_date.isoformat(), "day": day,
           "from": "Perth" if a == TEAM else names[a], "to": "Perth" if b == TEAM else names[b]}
    if TEAM in (a, b):
        club = b if a == TEAM else a
        j = journey(club, tt.at[club, "lat"], tt.at[club, "lon"], a == TEAM, day)
        assert j is not None, f"no flight between Perth and {names[club]} on {day} {travel_date}"
        route = " to ".join(config.AIRPORTS[x][0] for x in j.path) + (", then road" if j.road_km else "")
        return {**row, "mode": "Flight", "route": route, "carrier": j.carrier, "flights": j.flights,
                "km": j.km, "hours": j.hours}
    km = float(d_gc.at[a, b])
    road = km < config.AWAY_TRIP_KM
    return {**row, "mode": "Road" if road else "Flight", "route": "Road" if road else "Direct flight",
            "carrier": "", "flights": 0 if road else 1, "km": km, "hours": float(t_gc.at[a, b])}


def nights_between(fixture: pd.DataFrame) -> list:
    out, last = [], None
    for dstr in fixture["date"]:
        if not dstr:
            out.append(None)
            continue
        cur = date.fromisoformat(dstr)
        out.append(None if last is None else (cur - last).days)
        last = cur
    return pd.array(out, dtype="Int64")


def check_rules(fixture: pd.DataFrame) -> None:
    games = fixture[fixture["home_away"] != "Bye"]
    assert len(games) == len(PATTERN)
    assert games["day"].isin(config.FIXTURE_GAME_DAYS).all(), "games only Thursday to Sunday"
    assert games["nights_since_last_game"].dropna().min() >= config.FIXTURE_MIN_NIGHTS_BETWEEN, "minimum rest"
    streak = longest = 0
    for kind in fixture["home_away"]:
        streak = streak + 1 if kind == "Away" else 0
        longest = max(longest, streak)
    assert longest <= config.FIXTURE_MAX_AWAY_IN_A_ROW, "away games in a row"
    assert (games["home_away"] == "Home").sum() == (games["home_away"] == "Away").sum()


def summarise(legs, away, any_day, average_draw, names, repeats) -> pd.DataFrame:
    avg_return_home_hours, avg_return_home_km, avg_return_home_flights = average_draw
    d, t, f = any_day
    n_away = len(away)
    flights = int(legs["flights"].sum())
    trip_nights = []
    for trip, g in legs.groupby("trip"):
        trip_nights.append((date.fromisoformat(g["date"].max()) - date.fromisoformat(g["date"].min())).days)
    rows = [
        {"scenario": "average_draw", "label": "Average draw, returning to Perth after each away game",
         "travel_hours": avg_return_home_hours, "km": avg_return_home_km, "flights": avg_return_home_flights,
         "nights_away": 2 * n_away},
        {"scenario": "proposed_opponents", "label": "Proposed away opponents, returning after each game",
         "travel_hours": float(sum(t.at[TEAM, a] + t.at[a, TEAM] for a in away)),
         "km": float(sum(d.at[TEAM, a] + d.at[a, TEAM] for a in away)),
         "flights": int(sum(f.at[TEAM, a] + f.at[a, TEAM] for a in away)), "nights_away": 2 * n_away},
        {"scenario": "proposed_fixture", "label": "Proposed fixture with two-game road trips",
         "travel_hours": float(legs["hours"].sum()), "km": float(legs["km"].sum()), "flights": flights,
         "nights_away": int(sum(trip_nights))},
    ]
    s = pd.DataFrame(rows)
    s["hours_saved_vs_average"] = s.loc[0, "travel_hours"] - s["travel_hours"]
    s["pct_saved_vs_average"] = s["hours_saved_vs_average"] / s.loc[0, "travel_hours"]
    s["away_opponents"] = ["", ", ".join(sorted(names[a] for a in away)), ", ".join(sorted(names[a] for a in away))]
    s["repeat_opponents"] = ["", "", ", ".join(sorted(names[a] for a in repeats))]
    return s


def main() -> int:
    src = config.PROCESSED_DIR / "team_matches.parquet"
    if not src.exists():
        print("Run src/build_dataset.py first.")
        return 1
    long = pd.read_parquet(src, columns=["season", "team_id", "kickoff_utc", "team_elo_pre", "margin_vs_elo"])
    teams = pd.read_csv(config.REF_DIR / "teams.csv")
    fixture, legs, summary = build(long, teams)
    out = config.PROCESSED_DIR / OUT_DIR_NAME
    out.mkdir(parents=True, exist_ok=True)
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name, table in zip(SITE_TABLES, (fixture, legs, summary)):
        table.to_csv(out / f"{name}.csv", index=False, float_format="%.5g")
        table.to_json(SITE_DATA_DIR / f"{name}.json", orient="records", double_precision=4)
    print(summary.drop(columns=["label"]).round(2).to_string(index=False))
    print()
    print(fixture.to_string(index=False))
    print()
    print(legs.round(1).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
