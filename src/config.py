"""Paths and source settings for the Phase 1 travel dataset."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REF_DIR = ROOT / "reference"
RAW_DIR = ROOT / "data" / "raw"
INTERIM_DIR = ROOT / "data" / "interim"
PROCESSED_DIR = ROOT / "data" / "processed"

# Rugby League Project season results pages: {base}/seasons/{slug}/results.html
RLP_BASE_URL = "https://www.rugbyleagueproject.org"

# (season, competition label, RLP slug). The nrl-YYYY slugs match the format nrlR uses.
# All slugs, including 1995 to 1997, were confirmed on the first live scrape (October 2026).
# scrape_rlp.py reports any slug that fails to load.
SEASON_PAGES = (
    [(1995, "ARL", "arl-1995"), (1996, "ARL", "arl-1996"),
     (1997, "ARL", "arl-1997"), (1997, "Super League (Aus)", "super-league-1997")]
    + [(y, "NRL", f"nrl-{y}") for y in range(1998, 2027)]
)

# AusSportsBetting historical NRL results and odds: head-to-head odds from 2009, lines from 2013.
# Mid-2024 to 2025 has opening lines only (no closing line or closing odds).
ASB_URL = "https://www.aussportsbetting.com/historical_data/nrl.xlsx"

HTTP_HEADERS = {"User-Agent": "Mozilla/5.0 (research script; Perth Bears travel analysis)"}
REQUEST_PAUSE_S = 2.0          # pause between live requests to RLP

# Feature settings
AT_BASE_KM = 50                # venue within this distance of the team base counts as a home-base game
AWAY_TRIP_KM = 150             # venue beyond this distance counts as a trip (proxy for a flight or long drive)
ROLLING_WINDOW_DAYS = 21       # cumulative travel window
DEFAULT_KICKOFF_LOCAL = "15:00"  # used only to evaluate UTC offsets when kick-off time is missing
COVID_SEASONS = (2020, 2021)
LINE_JOIN_MAX_DAYS = 3         # ASB dates can be a day or two off RLP; the same pair never meets twice this close
# Rounds dropped before the build: Super League 1997's World Club Challenge games were against
# English clubs, mostly in England, and sit outside the Australian competition
EXCLUDE_ROUND_RE = r"(?i)world club challenge"

# Elo-style ratings (src/elo.py), in points relative to the league average.
# Refit with `python src/elo.py` and paste the printed values here.
ELO_FIT_SEASONS = (1996, 2012)  # 1995 is burn-in (every club starts at 0); 2013+ is checked against closing lines
ELO_K = 0.0556                  # share of the margin error applied to each rating after a match
ELO_HOME_ADV = 4.49            # points added to the designated home side's expectation
ELO_SEASON_CARRY = 0.595        # share of a rating kept into the next season (the rest regresses to 0)
ELO_NEW_TEAM = -0.73           # starting rating for expansion clubs after 1995
# Clubs that start from their predecessors' ratings (mean of the parents' carried ratings).
# Manly returns in 2003 from the Northern Eagles joint venture.
ELO_PARENTS = {
    "st_george_illawarra": ("st_george", "illawarra"),
    "wests_tigers": ("balmain", "western_suburbs"),
    "northern_eagles": ("manly", "north_sydney"),
    "manly": ("northern_eagles",),
}

# 2027 fixture proposal (src/fixture_2027.py). The draw is not published; these are assumptions.
FIXTURE_SEASON_START = "2027-03-04"   # Thursday of Round 1; each round is the Thursday to Sunday of one week
FIXTURE_ROUNDS = 27                   # 24 games and 3 byes, the byes in the State of Origin window
FIXTURE_BYE_AFTER_GAME = (12, 15, 19) # byes fall after these game numbers (rounds 13, 17 and 22)
FIXTURE_REPEAT_OPPONENTS = 7          # clubs met twice, once home and once away
FIXTURE_AWAY_REQUIRED = ("warriors",)  # away games the draw must include (a trip to New Zealand)
FIXTURE_MAX_AWAY_IN_A_ROW = 2
FIXTURE_MIN_NIGHTS_BETWEEN = 4
FIXTURE_GAME_DAYS = ("Thu", "Fri", "Sat", "Sun")
# Door-to-door travel time: per flight, a fixed allowance for transfers, check-in and taxi plus air time
# at an average block speed that is faster eastbound (prevailing westerlies; Perth to Sydney is about 4 h
# eastbound and 5 h westbound). Legs under AWAY_TRIP_KM go by road.
FLIGHT_GROUND_H = 2.5
FLIGHT_KMH_EAST = 820
FLIGHT_KMH_WEST = 680
ROAD_KMH = 70
ROAD_GROUND_H = 0.5
