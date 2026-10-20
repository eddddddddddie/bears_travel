"""End-to-end test on synthetic fixtures (no network). Run: python tests/test_pipeline.py

Also checks that the committed site/data/*.json tables match their data/processed/travel_model/*.csv
counterparts in row count and columns (both are tracked, so no scraped data is needed).

The fixture HTML follows the 11-cell row layout nrlR parses. Re-check the parser against a
live cached page after the first real scrape (see README, 'First run checks').
"""
import json
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import config  # noqa: E402
import build_dataset  # noqa: E402
import elo  # noqa: E402
import fetch_lines  # noqa: E402
import draw_2027  # noqa: E402
import fixture_2027  # noqa: E402
import flights  # noqa: E402
import road_trips  # noqa: E402
import scrape_rlp  # noqa: E402
import travel_model  # noqa: E402
from common import local_to_utc, utc_offset_hours  # noqa: E402


def row(mid, date, time, home, hs, away, aws, venue, crowd="10,000"):
    return (f"<tr><td>NRL</td><td>{date}</td><td>{time}</td><td>{home}</td><td>{hs}</td><td>{away}</td>"
            f"<td>{aws}</td><td>Ref</td><td>{venue}</td><td>{crowd}</td>"
            f"<td><a href='/matches/{mid}'>View</a></td></tr>")


def hdr(label):
    return f"<tr><td colspan='11'>{label}</td></tr>"


HTML_2025 = "<table>" + "".join([
    "<tr><td colspan='11'>To view more info on a match, click the View button.</td></tr>",
    hdr("Round 1"),
    row(1001, "Mar 7", "Fri 7:50pm", "Parramatta", 20, "Warriors", 18, "CommBank"),
    row(1002, "8", "Sat 5:30pm", "Brisbane", 30, "Melbourne", 12, "Suncorp"),
    hdr("Round 2"),
    row(1003, "15", "Sat 5:00pm", "Warriors", 24, "Brisbane", 10, "Go Media"),
    row(1004, "16", "Sun 4:05pm", "Melbourne", 22, "Parramatta", 20, "AAMI Park"),
    hdr("Round 3"),
    row(1005, "Apr 12", "Sat 7:30pm", "Brisbane", 16, "Warriors", 20, "Suncorp"),
    row(1006, "13", "Sun 2:00pm", "Melbourne", 18, "Parramatta", 6, "Mystery Oval"),
    hdr("Grand Final"),
    row(1007, "Oct 5", "Sun 7:30pm", "Brisbane", 26, "Melbourne", 22, "Accor"),
]) + "</table>"

HTML_1997 = "<table>" + "".join([
    hdr("Round 15"),
    row(924, "Jul 13", "Sun 2:30pm", "Canterbury", 38, "Perth Reds", 26, "Belmore"),
    hdr("Round 16"),
    row(949, "Aug 10", "Sun 2:30pm", "Western Reds", 30, "Hunter", 22, "WACA"),
]) + "</table>"


def make_asb(path):
    note = pd.DataFrame([["Data note row"] + [None] * 9])
    hdr_row = pd.DataFrame([["Date", "Home Team", "Away Team", "Home Score", "Away Score",
                             "Home Odds Close", "Away Odds Close", "Home Line Open", "Home Line Close",
                             "Home Line Odds Close"]])
    data = pd.DataFrame([
        [datetime(2025, 3, 7), "Parramatta Eels", "New Zealand Warriors", 20, 18, 1.80, 2.05, -2.5, -1.5, 1.90],
        [datetime(2025, 3, 8), "Brisbane Broncos", "Melbourne Storm", 30, 12, 2.10, 1.75, 2.5, 3.5, 1.90],
        # listed with home and away reversed and one day late: must still join and flip the sign
        [datetime(2025, 3, 16), "New Zealand Warriors", "Brisbane Broncos", 24, 10, 1.60, 2.40, -6.5, -7.5, 1.90],
    ])
    pd.concat([note, hdr_row, data]).to_excel(path, header=False, index=False)


def test_pipeline():
    tmp = Path(tempfile.mkdtemp())
    for name in ("RAW_DIR", "INTERIM_DIR", "PROCESSED_DIR"):
        setattr(config, name, tmp / name.lower())
        getattr(config, name).mkdir(parents=True)

    # DST checks used throughout the features
    mar = local_to_utc(datetime(2025, 3, 15, 19, 50), "Australia/Sydney")
    jun = local_to_utc(datetime(2025, 6, 14, 19, 50), "Australia/Sydney")
    assert utc_offset_hours("Australia/Sydney", mar) - utc_offset_hours("Australia/Perth", mar) == 3
    assert utc_offset_hours("Australia/Sydney", jun) - utc_offset_hours("Australia/Perth", jun) == 2
    dec07 = local_to_utc(datetime(2007, 12, 15, 12, 0), "Australia/Perth")
    assert utc_offset_hours("Australia/Perth", dec07) == 9  # WA daylight saving trial 2006 to 2009

    df = pd.concat([scrape_rlp.parse_results_html(HTML_2025, 2025, "NRL", "nrl-2025"),
                    scrape_rlp.parse_results_html(HTML_1997, 1997, "Super League (Aus)", "super-league-1997")])
    assert len(df) == 9 and not df["date_weekday_mismatch"].any()
    assert df.loc[df["rlp_match_id"] == 1003, "round_label"].item() == "Round 2"
    assert df.loc[df["rlp_match_id"] == 1001, "kickoff_local"].item() == "19:50"
    df.to_csv(config.INTERIM_DIR / "rlp_results.csv", index=False)

    make_asb(config.RAW_DIR / "asb_nrl.xlsx")
    fetch_lines.read_asb(config.RAW_DIR / "asb_nrl.xlsx").to_csv(config.INTERIM_DIR / "asb_lines.csv", index=False)

    assert build_dataset.main() == 0
    out = pd.read_parquet(config.PROCESSED_DIR / "team_matches.parquet")
    get = lambda mid, team: out[(out["match_key"] == str(mid)) & (out["team_id"] == team)].iloc[0]

    assert len(out) == 18
    w1 = get(1001, "warriors")                      # Auckland NZDT +13 to Sydney AEDT +11
    assert w1["tz_shift_from_base_h"] == -2 and w1["travel_direction"] == "west"
    assert round(w1["kickoff_base_clock_h"], 2) == 21.83   # 7:50pm Sydney = 9:50pm Auckland
    assert get(1003, "brisbane")["tz_shift_from_base_h"] == 3     # AEST +10 to NZDT +13
    assert get(1005, "warriors")["tz_shift_from_base_h"] == -2    # after DST ends: NZST +12 to AEST +10
    assert get(1002, "melbourne")["tz_shift_from_base_h"] == -1   # AEDT +11 to AEST +10
    assert get(1003, "warriors")["days_rest"] == round((datetime(2025, 3, 15, 4) - datetime(2025, 3, 7, 8, 50)).total_seconds() / 86400, 2)
    perth = get(924, "perth_reds")                  # AWST +8 to AEST +10
    assert perth["tz_shift_from_base_h"] == 2 and 3200 < perth["km_from_base"] < 3400
    assert bool(get(1007, "brisbane")["is_final"]) and not bool(get(1005, "brisbane")["is_final"])

    assert get(1001, "parramatta")["team_line_close"] == -1.5 and get(1001, "warriors")["team_line_close"] == 1.5
    assert get(1003, "warriors")["team_line_close"] == -7.5 and get(1003, "warriors")["margin_vs_line"] == 6.5
    assert get(1003, "brisbane")["team_line_close"] == 7.5
    assert not out["asb_score_mismatch"].fillna(False).any()

    # Elo: every club in the fixture starts the season at 0 (re-centred), so the first two
    # matches are hand-checkable from K and the home advantage
    k, ha = config.ELO_K, config.ELO_HOME_ADV
    p1 = get(1001, "parramatta")
    assert p1["team_elo_pre"] == 0 and p1["elo_expected_margin"] == ha and p1["margin_vs_elo"] == 2 - ha
    assert get(1001, "warriors")["elo_expected_margin"] == -ha
    w3, b3 = get(1003, "warriors"), get(1003, "brisbane")
    assert abs(w3["team_elo_pre"] + k * (2 - ha)) < 1e-9 and abs(b3["team_elo_pre"] - k * (18 - ha)) < 1e-9
    assert abs(w3["elo_expected_margin"] - (w3["team_elo_pre"] - b3["team_elo_pre"] + ha)) < 1e-9
    assert abs(w3["elo_expected_margin"] + b3["elo_expected_margin"]) < 1e-9
    assert out[["team_elo_pre", "elo_expected_margin", "margin_vs_elo"]].notna().all().all()

    # Elo season transitions: successor from its parent, expansion start, return after a missed season
    seq = pd.DataFrame({"season": [1998, 1999, 2000],
                        "date": pd.to_datetime(["1998-04-01", "1999-04-01", "2000-04-01"]),
                        "home_team_id": ["a", "c", "b"], "away_team_id": ["b", "d", "c"],
                        "home_score": [20, 10, 6], "away_score": [0, 10, 6]})
    r = elo.run_elo(seq, k=0.1, home_adv=0, carry=0.5, new_team=-4, parents={"c": ("a",)})
    # 1998: a +2, b -2. 1999: c = 2 x 0.5 = 1, d = -4, centred to 2.5 and -2.5.
    # 2000: b = -2 x 0.5^2 (two seasons on), c = 2.0 x 0.5 after its 1999 update, centred to -0.75 and 0.75
    assert r.round(9).values.tolist() == [[0, 0], [2.5, -2.5], [-0.75, 0.75]]

    # Phase 2 match frame: one row per match from the home side, travel as home minus away
    mf = travel_model.match_frame(out).set_index("match_key")
    assert len(mf) == 9
    reds = mf.loc["924"]                            # Canterbury at home to the Perth Reds
    assert reds["band_3000_6000"] == -1 and reds["band_150_1000"] == 0
    assert -3.4 < reds["km_1000"] < -3.2 and reds["tz_east_h"] == -2 and reds["elo_diff"] == 0
    assert travel_model.pair_offsets("Australia/Perth", "Australia/Sydney", "2027-06-01", "2027-06-30") == 2
    assert travel_model.pair_offsets("Australia/Perth", "Australia/Sydney", "2027-11-01", "2027-11-30") == 3

    unmatched = pd.read_csv(config.PROCESSED_DIR / "unmatched_venues.csv")
    assert unmatched["venue_raw"].tolist() == ["Mystery Oval"]


def test_site_tables():
    # config.ROOT, not PROCESSED_DIR: test_pipeline points PROCESSED_DIR at a temp directory
    csv_dir = config.ROOT / "data" / "processed" / travel_model.OUT_DIR_NAME
    for name in (*travel_model.SITE_TABLES, *flights.SITE_TABLES, *road_trips.SITE_TABLES, *fixture_2027.SITE_TABLES,
                 *draw_2027.SITE_TABLES):
        json_path, csv_path = travel_model.SITE_DATA_DIR / f"{name}.json", csv_dir / f"{name}.csv"
        assert json_path.exists() and csv_path.exists(), f"{name}: missing {json_path.name} or {csv_path.name}"
        records = json.loads(json_path.read_text(encoding="utf-8"))
        table = pd.read_csv(csv_path)
        assert len(records) == len(table), f"{name}: {len(records)} JSON rows, {len(table)} CSV rows"
        assert records and all(list(r) == list(table.columns) for r in records), f"{name}: columns differ"


def test_road_trips():
    """Two-game trip pairing on a toy map with a known best plan."""
    # Base P; A1 and A2 are 10 km apart and 1,000 km from P; B1 and B2 likewise, 2,000 km from P.
    ids = ["P", "A1", "A2", "B1", "B2", "C"]
    km = {("P", "A1"): 1000, ("P", "A2"): 1000, ("P", "B1"): 2000, ("P", "B2"): 2000, ("P", "C"): 500,
          ("A1", "A2"): 10, ("B1", "B2"): 10, ("A1", "B1"): 1500, ("A1", "B2"): 1500, ("A2", "B1"): 1500,
          ("A2", "B2"): 1500, ("A1", "C"): 1400, ("A2", "C"): 1400, ("B1", "C"): 2400, ("B2", "C"): 2400}
    d = pd.DataFrame(0.0, index=ids, columns=ids)
    for (a, b), v in km.items():
        d.at[a, b] = d.at[b, a] = v
    total, trips = road_trips.best_trips("P", ["A1", "A2", "B1", "B2"], d)
    assert sorted(trips) == [("A1", "A2"), ("B1", "B2")] and total == (1000 + 10 + 1000) + (2000 + 10 + 2000)
    # An odd number of games leaves one single trip; C (saving 100 with A1 or A2) stays on its own
    total, trips = road_trips.best_trips("P", ["A1", "A2", "C"], d)
    assert sorted(trips) == [("A1", "A2"), ("C",)] and total == 2010 + 1000
    f = (d >= config.AWAY_TRIP_KM).astype(int)
    assert road_trips.flights("P", [("A1", "A2")], f) == 2   # the 10 km leg is road travel


def test_fixture_rules():
    """Fixture travel times, trip pairing and the rule checks, on toy inputs."""
    # Eastbound flights are quicker than the same leg westbound; short legs go by road
    assert fixture_2027.leg_hours(3300, True) < fixture_2027.leg_hours(3300, False)
    assert fixture_2027.leg_hours(20, True) == fixture_2027.leg_hours(20, False) < 1
    # Two grounds 20 km apart in the east pair into one trip; the third ground stays a single trip
    ids = ["P", "S1", "S2", "M"]
    km = {("P", "S1"): 3300, ("P", "S2"): 3300, ("P", "M"): 2700, ("S1", "S2"): 20, ("S1", "M"): 700, ("S2", "M"): 700}
    lon = pd.Series({"P": 116.0, "S1": 151.0, "S2": 151.1, "M": 145.0})
    d = pd.DataFrame(0.0, index=ids, columns=ids)
    for (a, b), v in km.items():
        d.at[a, b] = d.at[b, a] = v
    t = fixture_2027.time_matrix(d, lon)
    _, trips = fixture_2027.best_pairs("P", ["S1", "S2"], t)
    assert len(trips) == 1 and sorted(trips[0]) == ["S1", "S2"]
    # Rule checks: a valid season passes, three away games in a row or a Monday game fails
    pattern = fixture_2027.PATTERN
    days = ["Sat" if k == "H" else ("Sun" if i == 0 or pattern[i - 1] != "A" else "Thu") for i, k in enumerate(pattern)]
    ok = pd.DataFrame({"home_away": ["Home" if k == "H" else "Away" for k in pattern], "day": days,
                       "nights_since_last_game": pd.array([None] + [7] * (len(pattern) - 1), dtype="Int64")})
    fixture_2027.check_rules(ok)
    for bad in (ok.assign(home_away=["Away"] * 3 + list(ok["home_away"][3:])), ok.assign(day=["Mon"] + days[1:])):
        try:
            fixture_2027.check_rules(bad)
        except AssertionError:
            continue
        raise AssertionError("check_rules accepted a fixture that breaks the rules")


def test_draw_round_robin():
    """The circle method gives 17 rounds in which every club plays once and every pair meets once."""
    others = [f"c{i:02d}" for i in range(17)]
    seen = set()
    for c in range(len(others)):
        pairs = draw_2027.circle_round(others, c)
        clubs = [x for p in pairs for x in p]
        assert len(pairs) == 9 and len(set(clubs)) == 18 and road_trips.TEAM in clubs
        seen.update(frozenset(p) for p in pairs)
    assert len(seen) == 18 * 17 // 2


def test_perth_flights():
    """Routes from Perth: connections, flying days, road to the base and the matrix overrides."""
    teams = pd.read_csv(config.REF_DIR / "teams.csv").set_index("team_id")
    go = lambda c, east=True, day=None: flights.journey(c, teams.at[c, "lat"], teams.at[c, "lon"], east, day)
    tsv, syd = go("north_qld"), go("sydney_roosters")
    assert tsv.path == ("PER", "BNE", "TSV") and tsv.flights == 2 and tsv.road_km == 0
    assert tsv.hours > syd.hours + config.FLIGHT_CONNECT_H and go("north_qld", east=False).path == ("TSV", "BNE", "PER")
    assert syd.hours < go("sydney_roosters", east=False).hours   # eastbound is quicker
    # Auckland flies Mon, Wed and Sat eastbound; Tue, Thu and Sun back; nothing else serves it
    assert go("warriors", day="Sat") is not None and go("warriors", day="Fri") is None
    assert go("warriors", east=False, day="Fri") is None and go("warriors", east=False, day="Sun") is not None
    # Newcastle on a day without its flight: Sydney, then road
    ntl = go("newcastle", day="Sat")
    assert ntl.path == ("PER", "SYD") and ntl.road_km > config.AIRPORT_ROAD_KM and go("newcastle", day="Fri").path == ("PER", "NTL")
    # Matrices: Perth rows and columns replaced; other legs untouched; a day with no flight gets the penalty
    clubs = ["perth_bears", "north_qld", "warriors", "brisbane"]
    d = road_trips.distance_matrix(teams.reset_index(), clubs)
    t = fixture_2027.time_matrix(d, teams["lon"])
    d2, t2, f2 = flights.apply_perth(d, t, teams.reset_index(), day_back="Fri")
    assert d2.at["perth_bears", "north_qld"] > d.at["perth_bears", "north_qld"] and f2.at["perth_bears", "north_qld"] == 2
    assert d2.at["brisbane", "north_qld"] == d.at["brisbane", "north_qld"] and f2.at["brisbane", "north_qld"] == 1
    assert t2.at["warriors", "perth_bears"] == flights.PENALTY_H and t2.at["perth_bears", "warriors"] < 12


if __name__ == "__main__":
    test_pipeline()
    test_site_tables()
    test_perth_flights()
    test_road_trips()
    test_fixture_rules()
    test_draw_round_robin()
    print("All checks passed.")
