"""Flights to and from Perth, following Qantas Group routes instead of one great-circle flight.

Qantas flies nonstop from Perth to every 2027 opponent's city except Townsville, which connects in
Brisbane; Jetstar flies Perth to the Gold Coast; Auckland and Newcastle are served three days a week
(config.PERTH_ROUTES, checked October 2026). A journey's door-to-door hours are FLIGHT_GROUND_H, plus air
time on each flight at FLIGHT_KMH_EAST or FLIGHT_KMH_WEST, plus FLIGHT_CONNECT_H for each connection, plus
any road from the airport to the base longer than AIRPORT_ROAD_KM (Brisbane to Robina when flying Qantas
only, Sydney to Newcastle on days with no Newcastle flight).

Used by road_trips.py, fixture_2027.py and draw_2027.py for every leg that starts or ends in Perth. Legs
between two eastern clubs stay one great-circle flight (or road under AWAY_TRIP_KM). The travel model and
its 2027 projection keep great-circle distance from base, the measure the model was fitted on.

Output (data/processed/travel_model/ and site/data/):
    perth_flights_2027   the route from Perth to each 2027 opponent, for the site's distance map
"""
from __future__ import annotations

import sys
from dataclasses import dataclass

import pandas as pd

import config
from common import haversine_km
from travel_model import OUT_DIR_NAME, SITE_DATA_DIR

PERTH = "perth_bears"
PENALTY_H = 1e4   # hours for a journey with no flight on its day; callers assert it is never chosen
SITE_TABLES = ("perth_flights_2027",)


@dataclass(frozen=True)
class Journey:
    path: tuple[str, ...]   # airports in flying order
    carrier: str
    days: tuple[str, ...] | None   # days the first route flies in this direction; None = daily
    air_km: float
    road_km: float          # road beyond AIRPORT_ROAD_KM between the club's end airport and its base
    hours: float

    @property
    def flights(self) -> int:
        return len(self.path) - 1

    @property
    def km(self) -> float:
        return self.air_km + self.road_km


def airport_km(a: str, b: str) -> float:
    return float(haversine_km(*config.AIRPORTS[a][1:], *config.AIRPORTS[b][1:]))


def journey(club: str, lat: float, lon: float, east: bool, day: str | None = None) -> Journey | None:
    """Perth to the club's base (east=True) or back, on `day` if given; None when nothing flies that day."""
    for stops, carrier, days_east, days_west in config.PERTH_ROUTES[config.CLUB_AIRPORT.get(club, "SYD")]:
        days = days_east if east else days_west
        if config.FLIGHTS_QANTAS_ONLY and carrier == "Jetstar":
            continue
        if day is not None and days is not None and day not in days:
            continue
        path = ("PER", *stops) if east else (*reversed(stops), "PER")
        air_h = sum(airport_km(a, b) / (config.FLIGHT_KMH_EAST if config.AIRPORTS[b][2] > config.AIRPORTS[a][2]
                                        else config.FLIGHT_KMH_WEST) for a, b in zip(path, path[1:]))
        road = float(haversine_km(*config.AIRPORTS[stops[-1]][1:], lat, lon))
        road_km = road if road > config.AIRPORT_ROAD_KM else 0.0
        hours = (config.FLIGHT_GROUND_H + air_h + config.FLIGHT_CONNECT_H * (len(path) - 2)
                 + road_km / config.ROAD_KMH)
        return Journey(path, carrier, days, sum(airport_km(a, b) for a, b in zip(path, path[1:])), road_km, hours)
    return None


def apply_perth(d: pd.DataFrame, t: pd.DataFrame | None, teams: pd.DataFrame, day_out: str | None = None,
                day_back: str | None = None):
    """Replace legs to and from Perth in a great-circle km matrix d (and hours matrix t) with flown routes.

    Returns (km, hours, flights): flights counts the flights on each leg (0 for road). day_out and
    day_back restrict Perth departures and returns to services flying on that day; a leg with none gets
    PENALTY_H hours.
    """
    d, f = d.copy(), (d >= config.AWAY_TRIP_KM).astype(int)
    t = None if t is None else t.copy()
    tt = teams.set_index("team_id")
    for c in d.index:
        if c == PERTH:
            continue
        for east, day, (a, b) in ((True, day_out, (PERTH, c)), (False, day_back, (c, PERTH))):
            j = journey(c, tt.at[c, "lat"], tt.at[c, "lon"], east, day)
            if j is None:
                if t is not None:
                    t.at[a, b] = PENALTY_H
                continue
            d.at[a, b], f.at[a, b] = j.km, j.flights
            if t is not None:
                t.at[a, b] = j.hours
    return d, t, f


def days_label(days) -> str:
    return "Daily" if days is None else ", ".join(days)


def build(teams: pd.DataFrame) -> pd.DataFrame:
    clubs = teams[(teams["last_season"] >= 2026) & (teams["team_id"] != PERTH)].set_index("team_id")
    per = teams.set_index("team_id").loc[PERTH]
    rows = []
    for c, r in clubs.iterrows():
        out, back = journey(c, r["lat"], r["lon"], True), journey(c, r["lat"], r["lon"], False)
        end = config.AIRPORTS[out.path[-1]]
        via = config.AIRPORTS[out.path[1]] if out.flights > 1 else None
        options = config.PERTH_ROUTES[config.CLUB_AIRPORT.get(c, "SYD")]
        other = [o for o in options[1:] if not (config.FLIGHTS_QANTAS_ONLY and o[1] == "Jetstar")]
        alt = ""
        if out.days is not None and other:
            stops, carrier = other[0][0], other[0][1]
            alt = f"Other days: {carrier} to {config.AIRPORTS[stops[-1]][0]}, then road"
        rows.append({
            "team_id": c, "club": r["display_name"], "base": r["base_name"],
            "route": " to ".join(config.AIRPORTS[a][0] for a in out.path) + (", then road" if out.road_km else ""),
            "carrier": out.carrier, "stops": out.flights - 1,
            "days_east": days_label(out.days), "days_west": days_label(back.days), "other_days": alt,
            "great_circle_km": float(haversine_km(per["lat"], per["lon"], r["lat"], r["lon"])),
            "flown_km": out.km, "hours_east": out.hours, "hours_west": back.hours,
            "via": via[0] if via else "", "via_lat": via[1] if via else None, "via_lon": via[2] if via else None,
            "base_lat": r["lat"], "base_lon": r["lon"],
        })
    return pd.DataFrame(rows).sort_values("great_circle_km", ascending=False).reset_index(drop=True)


def main() -> int:
    table = build(pd.read_csv(config.REF_DIR / "teams.csv"))
    out = config.PROCESSED_DIR / OUT_DIR_NAME
    out.mkdir(parents=True, exist_ok=True)
    SITE_DATA_DIR.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "perth_flights_2027.csv", index=False, float_format="%.5g")
    table.to_json(SITE_DATA_DIR / "perth_flights_2027.json", orient="records", double_precision=4)
    print(table.drop(columns=["base", "via_lat", "via_lon", "base_lat", "base_lon"]).round(1).to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
