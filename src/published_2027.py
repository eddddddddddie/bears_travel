"""Travel in the published 2027 NRL draw, for the Perth Bears and every other club.

Input: reference/nrl_draw_2027.csv (src/fetch_draw.py, from nrl.com, published 20 November 2026), with
team and venue names resolved through reference/teams.csv and venues.csv. Unlike the proposed fixture
(fixture_2027.py) and its draw (draw_2027.py), games are at the published grounds, so Magic Round in
Brisbane, the Las Vegas round and games away from a club's base (Darwin, Bathurst, Wagga Wagga, Gosford,
Christchurch, Dunedin) count as travel.

Travel rules, as in the proposal:
  - A club travels for every game more than AT_BASE_KM from its base, home or away. Two such games in
    consecutive rounds are one trip (base, first ground, second ground, base) when going on is quicker
    than going home in between; a bye round breaks a trip.
  - Door-to-door hours per leg: FLIGHT_GROUND_H plus air time at FLIGHT_KMH_EAST or FLIGHT_KMH_WEST, or
    road under AWAY_TRIP_KM. Legs to or from Perth follow the Qantas Group route (flights.py) to the
    airport nearest the ground.
  - Perth Bears journeys are dated. The team leaves Perth on the latest day before the game with a
    flight on its route (the day before, unless the route does not fly then: Auckland is served on
    Mondays, Wednesdays and Saturdays), moves to a second ground the day after the first game, and flies
    home on the first day after its last game with a flight. Other clubs' legs to and from Perth use any
    day the route flies, as in draw_2027.py.

Impact on margin (Perth Bears only): each game's travel impact is the band cost (bands design,
travel_effects.csv) of the Bears' great-circle trip from base to the ground, less the opponent's.
A balanced draw gives a season total of 0 (travel_model.bears_projection); the published draw differs
because the Bears play 11 games in Perth, 12 away and one at a neutral ground.

Outputs (data/processed/travel_model/ and site/data/):
    published_2027_draw      every game, with local date and kick-off
    published_2027_bears     the Bears' 27 rounds, with byes, rest and travel impact
    published_2027_legs      every Bears journey, with date, route, km and hours
    published_2027_clubs     season travel for every club
    published_2027_summary   the Bears' season travel and impact, with road trips and returning home
    site/data/nrl_2027_draw.csv, the download linked from the site
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

import config
from common import AliasResolver, haversine_km
from fixture_2027 import leg_hours
from flights import journey
from road_trips import TEAM
from travel_model import BANDS, EXCLUDE_KM, OUT_DIR_NAME, SITE_DATA_DIR

SEASON = 2027
DRAW_CSV = config.REF_DIR / f"nrl_draw_{SEASON}.csv"
SITE_TABLES = ("published_2027_draw", "published_2027_bears", "published_2027_legs", "published_2027_clubs",
               "published_2027_summary")
DOWNLOAD_NAME = "nrl_2027_draw.csv"
PERTH_TZ = "Australia/Perth"
MAX_WAIT_DAYS = 6   # furthest a Perth departure or return is moved to find a flight


# ---------------------------------------------------------------- inputs
def resolve(draw: pd.DataFrame, teams: pd.DataFrame, venues: pd.DataFrame) -> pd.DataFrame:
    """Team and venue ids, local date and kick-off at the ground. Existing clubs' rows end in 2026 until
    2027 results are scraped, so a team name falls back to its 2026 meaning."""
    team_res, venue_res = AliasResolver(teams, "team_id"), AliasResolver(venues, "venue_id")
    def team_id(name):
        row = team_res.resolve(name, SEASON) or team_res.resolve(name, SEASON - 1)
        return row["team_id"] if row else None
    def venue_id(name):
        row = venue_res.resolve(name, SEASON)
        return row["venue_id"] if row else None
    d = draw.copy()
    d["home_id"], d["away_id"] = d["home"].map(team_id), d["away"].map(team_id)
    d["venue_id"] = d["venue"].map(venue_id)
    missing = sorted(set(d.loc[d["home_id"].isna(), "home"]) | set(d.loc[d["away_id"].isna(), "away"])
                     | set(d.loc[d["venue_id"].isna(), "venue"]))
    if missing:
        raise ValueError(f"Add aliases to reference/teams.csv or venues.csv for: {missing}")
    v = venues.drop_duplicates("venue_id").set_index("venue_id")
    d["city"] = d["venue_id"].map(v["city"])
    d["lat"], d["lon"], d["tz"] = (d["venue_id"].map(v[c]) for c in ("lat", "lon", "tz"))
    utc = pd.to_datetime(d["kickoff_utc"], utc=True)
    local = [t.to_pydatetime().astimezone(ZoneInfo(z)) for t, z in zip(utc, d["tz"])]
    d["date"] = [x.date().isoformat() for x in local]
    d["day"] = [x.strftime("%a") for x in local]
    d["kickoff_local"] = [x.strftime("%H:%M") for x in local]
    d["kickoff_perth"] = [t.to_pydatetime().astimezone(ZoneInfo(PERTH_TZ)).strftime("%a %d %b %H:%M") for t in utc]
    d["home_name"], d["away_name"] = (d[c].map(teams.set_index("team_id")["display_name"]) for c in ("home_id", "away_id"))
    return d.sort_values(["round", "kickoff_utc"]).reset_index(drop=True)


def band_costs() -> pd.Series:
    effects = pd.read_csv(config.PROCESSED_DIR / OUT_DIR_NAME / "travel_effects.csv")
    return effects[(effects["model"] == "travel_cost") & (effects["design"] == "bands")].set_index("term")["estimate"]


def band_cost(km: float, costs: pd.Series) -> float:
    """Points of margin for a side travelling km from its base: 0 within 150 km, NaN beyond EXCLUDE_KM."""
    if km <= BANDS[0][0]:
        return 0.0
    for lo, hi in BANDS:
        if lo < km <= hi:
            return float(costs[f"band_{lo}_{hi}"])
    return float("nan")


# ---------------------------------------------------------------- places and legs
def nearest_airport(lat: float, lon: float) -> str:
    codes = [c for c in config.AIRPORTS if c != "PER"]
    km = [float(haversine_km(lat, lon, *config.AIRPORTS[c][1:])) for c in codes]
    return codes[int(np.argmin(km))]


def place(name: str, lat: float, lon: float, airport: str | None = None) -> dict:
    perth = config.AIRPORTS["PER"]
    return {"name": name, "lat": float(lat), "lon": float(lon),
            "perth": float(haversine_km(lat, lon, perth[1], perth[2])) < config.AWAY_TRIP_KM,
            "airport": airport or nearest_airport(lat, lon)}


def eastbound(a: dict, b: dict) -> bool:
    """True when the short way from a to b is east (across the Pacific for Las Vegas)."""
    return ((b["lon"] - a["lon"] + 540) % 360 - 180) > 0


def leg(a: dict, b: dict, day: str | None = None) -> dict | None:
    """One journey: km, hours, flights, mode and route. Legs to or from Perth take the Qantas Group route
    to the airport nearest the other end (on `day` if given; None when nothing flies then)."""
    if a["perth"] and b["perth"]:
        return {"mode": "None", "route": "", "carrier": "", "flights": 0, "km": 0.0, "hours": 0.0}
    if a["perth"] or b["perth"]:
        east = a["perth"]
        far = b if east else a
        j = journey(None, far["lat"], far["lon"], east, day, airport=far["airport"])
        if j is None:
            return None
        path = " to ".join(config.AIRPORTS[x][0] for x in j.path) + (", then road" if j.road_km else "")
        return {"mode": "Flight", "route": path, "carrier": j.carrier, "flights": j.flights, "km": j.km,
                "hours": j.hours}
    km = float(haversine_km(a["lat"], a["lon"], b["lat"], b["lon"]))
    road = km < config.AWAY_TRIP_KM
    return {"mode": "Road" if road else "Flight", "route": "Road" if road else "Direct flight", "carrier": "",
            "flights": 0 if road else 1, "km": km, "hours": leg_hours(km, eastbound(a, b))}


# ---------------------------------------------------------------- trips
def club_games(d: pd.DataFrame, club: str, base: dict) -> list[dict]:
    g = d[(d["home_id"] == club) | (d["away_id"] == club)]
    out = []
    for r in g.itertuples():
        ground = place(f"{r.venue}, {r.city}", r.lat, r.lon)
        out.append({"round": int(r.round), "date": r.date, "home": r.home_id == club,
                    "opp": r.away_id if r.home_id == club else r.home_id, "ground": ground,
                    "travel": float(haversine_km(base["lat"], base["lon"], r.lat, r.lon)) > config.AT_BASE_KM})
    return sorted(out, key=lambda x: x["round"])


def trips(games: list[dict], base: dict) -> list[list[dict]]:
    """Games away from base grouped into trips: consecutive rounds, going on quicker than going home."""
    out, i = [], 0
    while i < len(games):
        if not games[i]["travel"]:
            i += 1
            continue
        trip = [games[i]]
        while i + 1 < len(games) and games[i + 1]["travel"] and games[i + 1]["round"] == trip[-1]["round"] + 1:
            cur, nxt = trip[-1]["ground"], games[i + 1]["ground"]
            via_home = leg(cur, base)["hours"] + leg(base, nxt)["hours"]
            if leg(cur, nxt)["hours"] >= via_home:
                break
            trip.append(games[i + 1])
            i += 1
        out.append(trip)
        i += 1
    return out


def club_travel(d: pd.DataFrame, teams: pd.DataFrame) -> pd.DataFrame:
    tt = teams.set_index("team_id")
    rows = []
    for club in sorted(set(d["home_id"]) | set(d["away_id"])):
        base = place(tt.at[club, "base_name"], tt.at[club, "lat"], tt.at[club, "lon"],
                     None if club == TEAM else config.CLUB_AIRPORT.get(club, "SYD"))
        games = club_games(d, club, base)
        km = hours = flights = 0.0
        plan = trips(games, base)
        for trip in plan:
            stops = [base] + [g["ground"] for g in trip] + [base]
            for a, b in zip(stops, stops[1:]):
                x = leg(a, b)
                km, hours, flights = km + x["km"], hours + x["hours"], flights + x["flights"]
        run = longest = 0
        for g in games:
            run = 0 if g["home"] else run + 1
            longest = max(longest, run)
        dates = [date.fromisoformat(g["date"]) for g in games]
        rows.append({"team_id": club, "club": tt.at[club, "display_name"], "games": len(games),
                     "home": sum(g["home"] for g in games),
                     "home_at_base": sum(g["home"] and not g["travel"] for g in games),
                     "games_away_from_base": sum(g["travel"] for g in games),
                     "longest_away_run": longest,
                     "min_nights_between": min((b - a).days for a, b in zip(dates, dates[1:])),
                     "trips": len(plan), "two_game_trips": sum(len(t) > 1 for t in plan),
                     "flights": int(flights), "km": km, "travel_hours": hours})
    return pd.DataFrame(rows).sort_values("travel_hours", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------- the Bears
def flying_day(start: date, step: int, far: dict, east: bool, limit: date | None) -> tuple[date, dict]:
    """First day from start, moving by step, with a flight between Perth and far; not past limit."""
    for k in range(MAX_WAIT_DAYS + 1):
        day = start + timedelta(days=step * k)
        if limit is not None and (day - limit).days * step > 0:
            break
        x = leg(*(({"perth": True}, far) if east else (far, {"perth": True})), day.strftime("%a"))
        if x is not None:
            return day, x
    raise AssertionError(f"no flight between Perth and {far['name']} near {start}")


def bears(d: pd.DataFrame, teams: pd.DataFrame, costs: pd.Series):
    tt = teams.set_index("team_id")
    base = place("Perth", tt.at[TEAM, "lat"], tt.at[TEAM, "lon"])
    games = club_games(d, TEAM, base)
    plan = trips(games, base)
    trip_of = {g["round"]: (n + 1, j + 1) for n, t in enumerate(plan) for j, g in enumerate(t)}
    by_round = {g["round"]: g for g in games}
    mine = d[(d["home_id"] == TEAM) | (d["away_id"] == TEAM)].set_index("round")

    rows, last = [], None
    for r in range(1, int(d["round"].max()) + 1):
        if r not in by_round:
            rows.append({"round": r, "date": "", "day": "", "kickoff_local": "", "kickoff_perth": "",
                         "home_away": "Bye", "opponent": "", "venue": "", "city": ""})
            continue
        g, m = by_round[r], mine.loc[r]
        kind = "Home" if g["home"] and not g["travel"] else ("Away" if not g["home"] else "Neutral")
        bears_km = float(haversine_km(base["lat"], base["lon"], m.lat, m.lon))
        opp_km = float(haversine_km(tt.at[g["opp"], "lat"], tt.at[g["opp"], "lon"], m.lat, m.lon))
        cur = date.fromisoformat(g["date"])
        rows.append({"round": r, "date": g["date"], "day": m.day, "kickoff_local": m.kickoff_local,
                     "kickoff_perth": m.kickoff_perth, "home_away": kind, "opponent": tt.at[g["opp"], "display_name"],
                     "venue": m.venue, "city": m.city, "nights_since_last_game": None if last is None else (cur - last).days,
                     "trip": trip_of.get(r, (None,))[0], "km_from_perth": bears_km, "opponent_km": opp_km,
                     "bears_band_cost": band_cost(bears_km, costs), "opponent_band_cost": band_cost(opp_km, costs)})
        rows[-1]["travel_impact"] = rows[-1]["bears_band_cost"] - rows[-1]["opponent_band_cost"]
        last = cur
    fixture = pd.DataFrame(rows)
    for c in ("trip", "nights_since_last_game"):
        fixture[c] = fixture[c].astype("Int64")

    legs = []
    game_dates = [date.fromisoformat(g["date"]) for g in games]
    for n, trip in enumerate(plan, start=1):
        first, final = date.fromisoformat(trip[0]["date"]), date.fromisoformat(trip[-1]["date"])
        before = [x for x in game_dates if x < first]
        after = [x for x in game_dates if x > final]
        out_day, x = flying_day(first - timedelta(days=1), -1, trip[0]["ground"], True,
                                before[-1] + timedelta(days=1) if before else None)
        legs.append({"trip": n, "date": out_day, "from": "Perth", "to": trip[0]["ground"]["name"], **x})
        for a, b in zip(trip, trip[1:]):
            legs.append({"trip": n, "date": date.fromisoformat(a["date"]) + timedelta(days=1),
                         "from": a["ground"]["name"], "to": b["ground"]["name"], **leg(a["ground"], b["ground"])})
        back_day, x = flying_day(final + timedelta(days=1), 1, trip[-1]["ground"], False,
                                 after[0] - timedelta(days=1) if after else None)
        legs.append({"trip": n, "date": back_day, "from": trip[-1]["ground"]["name"], "to": "Perth", **x})
    legs = pd.DataFrame(legs)
    legs.insert(2, "day", [x.strftime("%a") for x in legs["date"]])
    legs["date"] = [x.isoformat() for x in legs["date"]]

    # the same grounds, flying home after every game, on any day the route flies
    single = [leg(base, g["ground"]) | {"back": leg(g["ground"], base)} for t in plan for g in t]
    nights = sum((date.fromisoformat(g["date"].max()) - date.fromisoformat(g["date"].min())).days
                 for _, g in legs.groupby("trip"))
    played = fixture[fixture["home_away"] != "Bye"]
    byes = [str(r) for r in fixture.loc[fixture["home_away"] == "Bye", "round"]]
    common = {"games": len(played), "home_in_perth": int((played["home_away"] == "Home").sum()),
              "away": int((played["home_away"] == "Away").sum()), "neutral": int((played["home_away"] == "Neutral").sum()),
              "bye_rounds": f"{', '.join(byes[:-1])} and {byes[-1]}" if len(byes) > 1 else "".join(byes),
              "season_travel_impact": float(played["travel_impact"].sum()),
              "impact_per_away_game": float(played.loc[played["home_away"] != "Home", "bears_band_cost"].mean())}
    summary = pd.DataFrame([
        {"scenario": "published_draw", "label": "Published draw with two-game road trips",
         "travel_hours": float(legs["hours"].sum()), "km": float(legs["km"].sum()), "flights": int(legs["flights"].sum()),
         "trips": len(plan), "two_game_trips": sum(len(t) > 1 for t in plan), "nights_away": int(nights), **common},
        {"scenario": "published_return_home", "label": "Published grounds, returning to Perth after each game",
         "travel_hours": float(sum(s["hours"] + s["back"]["hours"] for s in single)),
         "km": float(sum(s["km"] + s["back"]["km"] for s in single)),
         "flights": int(sum(s["flights"] + s["back"]["flights"] for s in single)),
         "trips": len(single), "two_game_trips": 0, "nights_away": 2 * len(single), **common},
    ])
    return fixture, legs, summary


# ---------------------------------------------------------------- run
def build(draw: pd.DataFrame, teams: pd.DataFrame, venues: pd.DataFrame, costs: pd.Series):
    d = resolve(draw, teams, venues)
    fixture, legs, summary = bears(d, teams, costs)
    clubs = club_travel(d, teams)
    b = clubs["team_id"] == TEAM   # one figure for the Bears on the page: the dated journeys
    clubs.loc[b, ["km", "travel_hours", "flights"]] = [summary.at[0, "km"], summary.at[0, "travel_hours"],
                                                       summary.at[0, "flights"]]
    clubs = clubs.sort_values("travel_hours", ascending=False).reset_index(drop=True)
    games = d[["round", "date", "day", "kickoff_local", "home_name", "away_name", "venue", "city", "home_id", "away_id"]]
    games = games.rename(columns={"home_name": "home", "away_name": "away"})
    return games, fixture, legs, clubs, summary


def main() -> int:
    if not DRAW_CSV.exists():
        print("Run src/fetch_draw.py first.")
        return 1
    draw = pd.read_csv(DRAW_CSV)
    teams = pd.read_csv(config.REF_DIR / "teams.csv")
    venues = pd.read_csv(config.REF_DIR / "venues.csv")
    tables = build(draw, teams, venues, band_costs())
    out = config.PROCESSED_DIR / OUT_DIR_NAME
    out.mkdir(parents=True, exist_ok=True)
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name, table in zip(SITE_TABLES, tables):
        table.to_csv(out / f"{name}.csv", index=False, float_format="%.5g")
        table.to_json(SITE_DATA_DIR / f"{name}.json", orient="records", double_precision=4)
    games, fixture, legs, clubs, summary = tables
    download = games.drop(columns=["home_id", "away_id"]).rename(
        columns={"kickoff_local": "Kick-off (local)"}).rename(columns=str.title)
    download.to_csv(SITE_DATA_DIR / DOWNLOAD_NAME, index=False)
    print(summary.drop(columns=["label"]).round(2).T.to_string())
    print()
    print(fixture.drop(columns=["kickoff_perth"]).round(2).to_string(index=False))
    print()
    print(legs.round(1).to_string(index=False))
    print()
    print(clubs.drop(columns=["team_id"]).round(1).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
