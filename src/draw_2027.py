"""The full 2027 competition draw, scheduled around the Perth Bears' proposed fixture.

Input: data/processed/travel_model/fixture_2027.csv (fixture_2027.py), fixed as given.

Assumptions, as for the Bears' fixture: 18 clubs and 27 rounds, with rounds 13, 17 and 22 a bye
weekend for every club (State of Origin), so each of the other 24 rounds has 9 games. Every club
plays the other 17 once and 7 of them twice (once at each home ground), so 12 home and 12 away.
Games are at the home club's base.

Rules for every club: no more than FIXTURE_MAX_AWAY_IN_A_ROW away games in a row (counted over
games, so a bye weekend does not reset it), at least FIXTURE_MIN_NIGHTS_BETWEEN nights between games,
games only Thursday to Sunday. Also no more than three home games in a row.

Steps:
  1. Match-ups. A circle-method round robin for 18 clubs, with the other 17 clubs labelled so that the
     Bears meet the right club in each round. A round in which the Bears meet a repeat opponent is
     played twice (the same 9 match-ups), so every club has exactly 7 repeat opponents, met in the
     same rounds as the Bears' repeats and so just as far apart.
  2. Home and away. A mixed-integer program (scipy.optimize.milp, HiGHS) chooses the host of every
     game. It keeps the Bears' fixture, gives every club 12 home games and the run limits above, and
     minimises total door-to-door travel time for all 18 clubs, where two away games in consecutive
     rounds are one trip (base, first ground, second ground, base) as in the Bears' fixture.
  3. Days. Each round's games fill one Thursday, two Friday, three Saturday and three Sunday slots,
     with the Bears' days fixed, assigned round by round to avoid short turnarounds.

Outputs:
    data/processed/travel_model/draw_2027.csv, draw_2027_clubs.csv (and JSON copies in site/data/)
    site/data/draw_2027.csv, the download linked from the site
"""
from __future__ import annotations

import sys
from datetime import date, timedelta

import numpy as np
import pandas as pd
from scipy.optimize import Bounds, LinearConstraint, linear_sum_assignment, milp
from scipy.sparse import lil_matrix, vstack

import config
from fixture_2027 import DAY_OFFSET, time_matrix
from road_trips import TEAM, distance_matrix
from travel_model import OUT_DIR_NAME, SITE_DATA_DIR

SITE_TABLES = ("draw_2027", "draw_2027_clubs")
DOWNLOAD_NAME = "perth_bears_2027_draw.csv"
SLOTS = ["Thu", "Fri", "Fri", "Sat", "Sat", "Sat", "Sun", "Sun", "Sun"]
MAX_HOME_IN_A_ROW = 3


# ---------------------------------------------------------------- 1. match-ups
def circle_round(others: list[str], c: int) -> list[tuple[str, str]]:
    """Round c of the circle method: TEAM plays others[c]; the rest pair symmetrically around it."""
    n = len(others)
    pairs = [(TEAM, others[c])]
    pairs += [(others[(c + i) % n], others[(c - i) % n]) for i in range(1, n // 2 + 1)]
    return pairs


def matchups(bears: pd.DataFrame, ids: dict) -> dict[int, list[tuple[str, str]]]:
    games = bears[bears["home_away"] != "Bye"]
    first_seen = list(dict.fromkeys(ids[o] for o in games["opponent"]))   # opponents by first meeting
    index = {o: c for c, o in enumerate(first_seen)}
    return {int(r.round): circle_round(first_seen, index[ids[r.opponent]]) for r in games.itertuples()}


# ---------------------------------------------------------------- 2. home and away
def orient(rounds: dict, bears: pd.DataFrame, ids: dict, t: pd.DataFrame, clubs: list[str]):
    order = sorted(rounds)
    meetings: dict[frozenset, list[int]] = {}
    for r in order:
        for a, b in rounds[r]:
            meetings.setdefault(frozenset((a, b)), []).append(r)
    pairs = sorted(meetings, key=lambda p: tuple(sorted(p)))
    var = {p: i for i, p in enumerate(pairs)}   # 1 = the alphabetically first club hosts the first meeting
    nv = len(pairs)

    def home_expr(club: str, r: int, opp: str):
        """Home indicator of club in round r as (coef, var index, constant)."""
        p = frozenset((club, opp))
        first = sorted(p)[0] == club
        second_meeting = len(meetings[p]) == 2 and meetings[p][1] == r
        hosts_if_1 = first != second_meeting
        return (1.0, var[p], 0.0) if hosts_if_1 else (-1.0, var[p], 1.0)

    schedule = {c: [] for c in clubs}   # club -> [(round, opponent)]
    for r in order:
        for a, b in rounds[r]:
            schedule[a].append((r, b))
            schedule[b].append((r, a))
    byes = set(range(1, config.FIXTURE_ROUNDS + 1)) - set(order)
    base = {c: c for c in clubs}

    # chained away games: consecutive rounds with no bye between
    chains = []
    for c in clubs:
        for (r1, o1), (r2, o2) in zip(schedule[c], schedule[c][1:]):
            if r2 == r1 + 1:
                saving = t.at[o1, base[c]] + t.at[base[c], o2] - t.at[o1, o2]
                if saving > 0:
                    chains.append((c, r1, o1, r2, o2, saving))
    nz = len(chains)
    n = nv + nz
    cost = np.zeros(n)
    const = 0.0
    for c in clubs:
        for r, o in schedule[c]:
            coef, v, k = home_expr(c, r, o)
            trip = t.at[base[c], o] + t.at[o, base[c]]
            # travel when away = (1 - home) * trip
            cost[v] -= coef * trip
            const += (1 - k) * trip
    for j, ch in enumerate(chains):
        cost[nv + j] = -ch[5]

    rows, lo, hi = [], [], []

    def add(terms: list[tuple[float, int]], lower: float, upper: float):
        row = lil_matrix((1, n))
        for coef, v in terms:
            row[0, v] += coef
        rows.append(row)
        lo.append(lower)
        hi.append(upper)

    for c in clubs:
        exprs = [home_expr(c, r, o) for r, o in schedule[c]]
        total_k = sum(k for _, _, k in exprs)
        add([(coef, v) for coef, v, _ in exprs], 12 - total_k, 12 - total_k)
        for w in range(len(exprs) - config.FIXTURE_MAX_AWAY_IN_A_ROW):   # at least one home in any 3 games
            win = exprs[w:w + config.FIXTURE_MAX_AWAY_IN_A_ROW + 1]
            k = sum(x[2] for x in win)
            add([(coef, v) for coef, v, _ in win], 1 - k, np.inf)
        for w in range(len(exprs) - MAX_HOME_IN_A_ROW):                 # at most three home in any 4 games
            win = exprs[w:w + MAX_HOME_IN_A_ROW + 1]
            k = sum(x[2] for x in win)
            add([(coef, v) for coef, v, _ in win], -np.inf, MAX_HOME_IN_A_ROW - k)
    for j, (c, r1, o1, r2, o2, _) in enumerate(chains):                  # chain only if away both rounds
        for r, o in ((r1, o1), (r2, o2)):
            coef, v, k = home_expr(c, r, o)                               # z <= 1 - home
            add([(1.0, nv + j), (coef, v)], -np.inf, 1 - k)
    for row in bears[bears["home_away"] != "Bye"].itertuples():           # the Bears' fixture is fixed
        coef, v, k = home_expr(TEAM, int(row.round), ids[row.opponent])
        want = 1.0 if row.home_away == "Home" else 0.0
        add([(coef, v)], want - k, want - k)

    A = vstack(rows).tocsr()
    res = milp(cost, constraints=LinearConstraint(A, lo, hi), integrality=np.ones(n),
               bounds=Bounds(0, 1), options={"time_limit": 300, "disp": False})
    if not res.success:
        raise RuntimeError(f"No draw found: {res.message}")
    x = np.round(res.x).astype(int)
    games = []
    for r in order:
        for a, b in rounds[r]:
            coef, v, k = home_expr(a, r, b)
            a_home = (coef * x[v] + k) > 0.5
            games.append({"round": r, "home_id": a if a_home else b, "away_id": b if a_home else a,
                          "repeat": len(meetings[frozenset((a, b))]) == 2})
    return pd.DataFrame(games), byes, float(res.fun + const)


# ---------------------------------------------------------------- 3. days
def assign_days(games: pd.DataFrame, bears: pd.DataFrame) -> pd.DataFrame:
    bears_day = {int(r.round): r.day for r in bears.itertuples() if r.home_away != "Bye"}
    last_day: dict[str, tuple[int, str]] = {}
    days = {}
    for r in sorted(games["round"].unique()):
        rg = games[games["round"] == r]
        slots = list(SLOTS)
        fixed = rg[(rg["home_id"] == TEAM) | (rg["away_id"] == TEAM)]
        slots.remove(bears_day[r])
        free = rg.drop(fixed.index)
        cost = np.zeros((len(free), len(slots)))
        for i, g in enumerate(free.itertuples()):
            for j, day in enumerate(slots):
                for club in (g.home_id, g.away_id):
                    if club in last_day and last_day[club][0] == r - 1:
                        nights = 7 + DAY_OFFSET[day] - DAY_OFFSET[last_day[club][1]]
                        cost[i, j] += max(0, 6 - nights) ** 2   # 4 nights costs 4, 5 nights 1
        rows_, cols = linear_sum_assignment(cost)
        days[fixed.index[0]] = bears_day[r]
        for i, j in zip(rows_, cols):
            days[free.index[i]] = slots[j]
        for idx in rg.index:
            for club in (games.at[idx, "home_id"], games.at[idx, "away_id"]):
                last_day[club] = (r, days[idx])
    out = games.assign(day=pd.Series(days))
    start = date.fromisoformat(config.FIXTURE_SEASON_START)
    out["date"] = [(start + timedelta(weeks=int(r) - 1, days=DAY_OFFSET[d])).isoformat()
                   for r, d in zip(out["round"], out["day"])]
    slot_order = {d: i for i, d in enumerate(dict.fromkeys(SLOTS))}
    return out.sort_values(["round", "day"], key=lambda s: s.map(slot_order) if s.name == "day" else s,
                           kind="stable").reset_index(drop=True)


# ---------------------------------------------------------------- checks and club travel
def club_travel(draw: pd.DataFrame, clubs: list[str], d: pd.DataFrame, t: pd.DataFrame, names: pd.Series) -> pd.DataFrame:
    rows = []
    for c in clubs:
        g = draw[(draw["home_id"] == c) | (draw["away_id"] == c)].sort_values("round")
        seq = [(int(r.round), r.away_id == c, r.home_id, r.date, r.day) for r in g.itertuples()]
        home_n = sum(not away for _, away, *_ in seq)
        away_run = run = home_run = hrun = 0
        for _, away, *_ in seq:
            run = run + 1 if away else 0
            hrun = 0 if away else hrun + 1
            away_run, home_run = max(away_run, run), max(home_run, hrun)
        nights = [(date.fromisoformat(b[3]) - date.fromisoformat(a[3])).days for a, b in zip(seq, seq[1:])]
        km = hours = flights = trips2 = 0.0
        i = 0
        while i < len(seq):
            r, away, host, *_ = seq[i]
            if not away:
                i += 1
                continue
            stops = [c, host]
            if i + 1 < len(seq) and seq[i + 1][1] and seq[i + 1][0] == r + 1 and \
                    t.at[host, c] + t.at[c, seq[i + 1][2]] > t.at[host, seq[i + 1][2]]:
                stops.append(seq[i + 1][2])
                trips2 += 1
                i += 1
            stops.append(c)
            for a, b in zip(stops, stops[1:]):
                km += d.at[a, b]
                hours += t.at[a, b]
                flights += d.at[a, b] >= config.AWAY_TRIP_KM
            i += 1
        rows.append({"team_id": c, "club": names[c], "games": len(seq), "home": home_n, "away": len(seq) - home_n,
                     "longest_away_run": away_run, "longest_home_run": home_run, "min_nights_between": min(nights),
                     "two_game_trips": int(trips2), "flights": int(flights), "km": km, "travel_hours": hours})
    return pd.DataFrame(rows).sort_values("travel_hours", ascending=False).reset_index(drop=True)


def check(draw: pd.DataFrame, clubs_table: pd.DataFrame, bears: pd.DataFrame, names: pd.Series) -> None:
    per_round = draw.groupby("round")
    assert (per_round.size() == len(SLOTS)).all(), "9 games in every playing round"
    for r, g in per_round:
        teams = list(g["home_id"]) + list(g["away_id"])
        assert len(set(teams)) == 2 * len(g), f"a club plays twice in round {r}"
    assert draw["day"].isin(config.FIXTURE_GAME_DAYS).all()
    per_slot = draw.groupby(["round", "day"]).size()
    assert all(n <= SLOTS.count(day) for (_, day), n in per_slot.items()), "slots per day"
    c = clubs_table
    assert (c["games"] == 24).all() and (c["home"] == 12).all()
    assert (c["longest_away_run"] <= config.FIXTURE_MAX_AWAY_IN_A_ROW).all()
    assert (c["longest_home_run"] <= MAX_HOME_IN_A_ROW).all()
    assert (c["min_nights_between"] >= config.FIXTURE_MIN_NIGHTS_BETWEEN).all()
    pair_counts = draw.assign(p=[frozenset(x) for x in zip(draw["home_id"], draw["away_id"])]).groupby("p").agg(
        n=("round", "size"), hosts=("home_id", "nunique"))
    assert len(pair_counts) == len(c) * (len(c) - 1) // 2, "every pair meets"
    assert ((pair_counts["n"] == 1) | ((pair_counts["n"] == 2) & (pair_counts["hosts"] == 2))).all()
    assert (pair_counts["n"] == 2).sum() == len(c) * config.FIXTURE_REPEAT_OPPONENTS // 2
    mine = draw[(draw["home_id"] == TEAM) | (draw["away_id"] == TEAM)].set_index("round")
    for r in bears[bears["home_away"] != "Bye"].itertuples():
        g = mine.loc[int(r.round)]
        assert (g["home_id"] == TEAM) == (r.home_away == "Home") and g["day"] == r.day, f"Bears round {r.round} changed"


def main() -> int:
    src = config.PROCESSED_DIR / OUT_DIR_NAME / "fixture_2027.csv"
    if not src.exists():
        print("Run src/fixture_2027.py first.")
        return 1
    bears = pd.read_csv(src, dtype={"date": "string", "day": "string"}).fillna({"opponent": "", "day": ""})
    teams = pd.read_csv(config.REF_DIR / "teams.csv")
    seasons = pd.read_parquet(config.PROCESSED_DIR / "team_matches.parquet", columns=["season", "team_id"])
    opponents = sorted(set(seasons.loc[seasons["season"] == seasons["season"].max(), "team_id"]))
    clubs = [TEAM] + opponents
    tt = teams.set_index("team_id")
    names = tt["display_name"]
    ids = {names[c]: c for c in clubs}
    d = distance_matrix(teams, clubs)
    t = time_matrix(d, tt["lon"])

    rounds = matchups(bears, ids)
    games, byes, total_hours = orient(rounds, bears, ids, t, clubs)
    draw = assign_days(games, bears)
    draw["home"] = draw["home_id"].map(names)
    draw["away"] = draw["away_id"].map(names)
    draw["venue"] = draw["home_id"].map(tt["base_name"])
    draw["meeting"] = ""
    for p, g in draw.assign(p=[frozenset(x) for x in zip(draw["home_id"], draw["away_id"])]).groupby("p"):
        draw.loc[g.index, "meeting"] = ["1 of 2", "2 of 2"] if len(g) == 2 else ["1 of 1"]
    clubs_table = club_travel(draw, clubs, d, t, names)
    check(draw, clubs_table, bears, names)

    draw = draw[["round", "date", "day", "home", "away", "venue", "meeting", "home_id", "away_id"]]
    out = config.PROCESSED_DIR / OUT_DIR_NAME
    for name, table in zip(SITE_TABLES, (draw, clubs_table)):
        table.to_csv(out / f"{name}.csv", index=False, float_format="%.5g")
        table.to_json(SITE_DATA_DIR / f"{name}.json", orient="records", double_precision=4)
    download = draw.drop(columns=["home_id", "away_id"]).rename(columns=str.title)
    download.to_csv(SITE_DATA_DIR / DOWNLOAD_NAME, index=False)

    print(f"{len(draw)} games in {draw['round'].nunique()} rounds; byes in rounds {sorted(byes)}; "
          f"league travel {total_hours:,.0f} hours")
    print(clubs_table.drop(columns=["team_id"]).round(1).to_string(index=False))
    print()
    print(draw[draw["round"] <= 2].drop(columns=["home_id", "away_id"]).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
