# Perth Bears travel analysis

[![test](https://github.com/eddddddddddie/bears_travel/actions/workflows/test.yml/badge.svg)](https://github.com/eddddddddddie/bears_travel/actions/workflows/test.yml)

Builds one row per team per match for NRL 1998 to 2026 and the 1995 to 1997 seasons that included the Western Reds, with travel distance, time zone shift (daylight saving applied by date), rest, kick-off time on the team's home clock, rolling travel load, the closing betting line and a pre-match Elo rating for each side (Phase 1). Phase 2 estimates what travel costs a team, tests whether the betting market prices it, and projects the Perth Bears' 2027 travel load.

**Dashboard:** [bears.thisaithat.com](https://bears.thisaithat.com) presents the Phase 2 results: what each distance band costs, how much of home advantage is travel, what the betting market charges, and the Bears' 2027 outlook. It is a static site in `site/`, drawn from the JSON copies of the Phase 2 tables that `travel_model.py` writes to `site/data/`.

## Static site

`site/` is served as is, with no build step:

| File | Contents |
|---|---|
| `site/index.html` | The dashboard page (d3 7.9.0 from cdnjs, Barlow from Google Fonts) |
| `site/assets/dash.js` | Loads the JSON tables and gives the page the small `dash` data API it uses |
| `site/data/*.json` | Written by `travel_model.py` on every run; commit them with the model outputs |
| `site/data/land_au_nz.json` | Australia and New Zealand land outline for the map, from Natural Earth 1:50m (public domain), written by `site_assets.py` |
| `site/og-image.png` | 1200 x 630 link preview with the headline figures, written by `site_assets.py` from `site/data/` |

Preview it locally with `python -m http.server 8000 --directory site` and open http://localhost:8000.

Hosting is Cloudflare Pages connected to this repository: production branch `main`, no build command, build output directory `site`, custom domain `bears.thisaithat.com`. Each push to `main` redeploys.

## Data in this repository

The repository holds code, reference tables and derived outputs. Source data is not redistributed, and `.gitignore` keeps it out:

| Not tracked | Why | How to rebuild |
|---|---|---|
| `data/raw/` | Cached Rugby League Project pages and the AusSportsBetting workbook | `scrape_rlp.py`, `fetch_lines.py` |
| `data/interim/` | Match results and betting lines extracted from those sources | `scrape_rlp.py`, `fetch_lines.py` |
| `data/processed/team_matches.{parquet,csv}` | One row per team per match, including closing lines and odds | `build_dataset.py` |

Tracked outputs are aggregates: `validation_report.txt`, the `unmatched_*.csv` checks, the Phase 2 tables in `data/processed/travel_model/` and their JSON copies in `site/data/`. A fresh clone needs the full run order below before `build_dataset.py` or `travel_model.py` will run.

## Run order

```
pip install -r requirements.txt
python tests/test_pipeline.py      # offline check on synthetic fixtures
python src/scrape_rlp.py           # about 32 season pages, cached in data/raw/rlp/
python src/fetch_lines.py          # AusSportsBetting workbook, cached in data/raw/
python src/build_dataset.py        # writes data/processed/
python src/elo.py                  # optional: refit the Elo parameters (needs scipy)
python src/travel_model.py         # Phase 2 models, tables in data/processed/travel_model/ and site/data/
python src/site_assets.py          # map outline and link-preview image for site/ (needs matplotlib)
```

Repeat `build_dataset.py` after each edit to the reference tables. It does not re-download anything.

The `Makefile` wraps the same steps (`PYTHON` defaults to `python`; pass script flags with `ARGS`, e.g. `make scrape ARGS=--refresh`):

| Target | Runs |
|---|---|
| `make scrape` | `scrape_rlp.py` (network) |
| `make lines` | `fetch_lines.py` (network) |
| `make build` | `build_dataset.py` |
| `make elo` | `elo.py`, optional refit |
| `make model` | `travel_model.py` |
| `make assets` | `site_assets.py` |
| `make test` | `tests/test_pipeline.py` |
| `make all` | `build`, `model`, `test` |

`make all` does not scrape or download. Run `make scrape` and `make lines` first on a fresh clone, and again only to pick up new results or lines.

`requirements.txt` pins the versions the committed outputs were produced with. GitHub Actions (`.github/workflows/test.yml`) runs `tests/test_pipeline.py` on Python 3.12 for every push and pull request. Besides the synthetic-fixture pipeline test, it checks that each `site/data/*.json` table in `travel_model.SITE_TABLES` has the same rows and columns as its CSV in `data/processed/travel_model/`, so rerun `travel_model.py` and commit both together.

## Sources

| Source | Used for | Notes |
|---|---|---|
| Rugby League Project season results pages | Results, date, local kick-off time, venue, crowd, round | Same pages and row layout as nrlR's `fetch_results`. Scraped directly because nrlR rejects seasons before 1998 and numbers rounds by month change. |
| AusSportsBetting `nrl.xlsx` | Closing line and closing head-to-head odds | Head-to-head odds from 2009; lines from 2013. Mid-2024 to 2025 has opening lines only. Joined on team pair and date (within `LINE_JOIN_MAX_DAYS`, 3), with the line sign flipped when ASB lists home and away the other way round. |
| `reference/teams.csv` | Team aliases and home base by season range | Season ranges resolve reused names (Gold Coast Chargers and Titans; Perth Reds and Bears). |
| `reference/venues.csv` | Venue aliases, coordinates, IANA time zone | Coordinates are approximate (within about 2 km), which is immaterial at intercity scale. Sponsor names are listed as aliases. |
| `reference/base_overrides.csv` | Temporary bases (Warriors 2020 to 2022, 2021 Queensland relocation) | All rows are `verified = 0`. See known gaps. |

## Output: `data/processed/team_matches.parquet`

| Column | Definition |
|---|---|
| `margin` | Points for minus points against |
| `team_line_close` | Closing handicap from this team's side; negative means favourite |
| `margin_vs_line` | `margin + team_line_close`; the Phase 2 outcome (points above market expectation) |
| `implied_win_prob` | Closing head-to-head probability with bookmaker margin removed proportionally |
| `team_elo_pre`, `opp_elo_pre` | Elo rating of each side before the match, in points above the league average (see Elo ratings) |
| `elo_expected_margin` | `team_elo_pre - opp_elo_pre` plus the home advantage for the designated home side (minus it for the away side) |
| `margin_vs_elo` | `margin - elo_expected_margin`; points above the Elo expectation, available for every season |
| `km_from_base` | Great-circle km from the team's base on that date to the venue |
| `km_from_prev_venue` | Great-circle km from the previous match venue (base for a season opener) |
| `tz_shift_from_base_h` | Venue UTC offset minus base UTC offset at kick-off, folded into -12 to +12; positive means the team travelled east (Brisbane to Las Vegas is +6) |
| `tz_shift_from_prev_venue_h` | As above, measured from the previous venue |
| `kickoff_base_clock_h` | Kick-off as a decimal hour on the team's home clock (19.83 = 7:50pm) |
| `days_rest` | Days between kick-offs, from UTC times; blank for season openers |
| `km_21d_return_home` | Travel in the previous 21 days assuming a return home after each match, plus the trip to this match |
| `km_21d_venue_to_venue` | Travel in the previous 21 days assuming direct venue-to-venue legs |
| `trips_21d` | Matches in the window (including this one) more than 150 km from base |
| `at_base_venue` | Venue within 50 km of the base; separates true home games from relocated "home" games |
| `opp_*`, `*_diff` | The opponent's values for the same match, and team minus opponent differences |
| `is_final`, `covid_season`, `kickoff_imputed`, `base_override_verified`, `asb_score_mismatch` | Flags for filtering in Phase 2 |

The build also writes `unmatched_teams.csv`, `unmatched_venues.csv` and `validation_report.txt`.

## Elo ratings

`src/elo.py` makes one chronological pass over the same matches as the build (excluded rounds and unscored games dropped). Ratings are in points: the home side's expected margin is the rating difference plus a fixed home advantage, and after each match both ratings move by K times the error, in opposite directions. Between seasons each rating keeps a fixed share of its value and the active clubs are re-centred on 0.

| Club situation | Starting rating |
|---|---|
| 1995 clubs, including the Western Reds | 0; 1995 is a burn-in season and is not used in the fit |
| Expansion clubs (Adelaide Rams, Hunter Mariners, Melbourne, Titans, Dolphins, later the Perth Bears) | `ELO_NEW_TEAM` |
| Merged and successor clubs (St George Illawarra, Wests Tigers, Northern Eagles, Manly from 2003) | Mean of the parent clubs' carried ratings (`ELO_PARENTS` in `config.py`) |
| Clubs returning after a gap (South Sydney 2002) | Last rating, regressed once for each season missed |

K, home advantage, season carry-over and the expansion start are fitted by least squares on the pre-match expected margin for 1996 to 2012 (`ELO_FIT_SEASONS`); every prediction uses only earlier results. The fitted values are stored in `config.py`, so the build is reproducible; `python src/elo.py` refits and prints new values to copy in. The October 2026 fit gave K 0.056, home advantage 4.5 points, carry-over 0.60 and an expansion start of -0.7 points. On 2013 to 2026 matches with a closing line, `elo_expected_margin` correlates 0.86 with `-team_line_close` and has a mean absolute error of 13.6 points against 13.1 for the line. `validation_report.txt` repeats these figures on each build.

## First run checks

The first live run (October 2026) cleared these checks. Repeat them when adding seasons or after RLP changes its pages.

1. **Season slugs.** All slugs in `src/config.py` load, including 1995 to 1997. `scrape_rlp.py` lists any that fail.
2. **Parser against the live page.** Rows follow nrlR's 11-cell layout except Super League 1997, which drops the leading competition cell (10 cells); the parser shifts those rows. Round headers longer than 60 characters are not read as round labels. After a re-scrape, compare one round of `data/interim/rlp_results.csv` against the website.
3. **Unmatched names.** Add every name in `unmatched_venues.csv` and `unmatched_teams.csv` to the alias lists in `reference/`, then rerun the build. Each venue cell on an RLP results page links to `/venues/N`; that page gives the full name, town and usually coordinates, and is the quickest way to identify a shortened or sponsor name. Checking that each RLP venue ID resolves to a single `venue_id` catches aliases that point at the wrong ground for an era.
4. **Kick-off times.** RLP lists local time at the venue. Many games before 2001, and some in 2003, list only the weekday. Rows without a time are flagged `kickoff_imputed` and use 3pm local only to evaluate UTC offsets; exclude them from any kick-off clock analysis.
5. **Line join.** `validation_report.txt` gives line coverage by season and the count of score mismatches between RLP and ASB. The two current mismatches (2013 Round 24 Cronulla v Roosters, 2015 Round 8 Newcastle v North Queensland) are ASB typos; the dataset uses RLP scores.

## Phase 2: travel model

`src/travel_model.py` reads `team_matches.parquet` and fits OLS models on one row per match, seen from the designated home side, with every travel feature entered as team minus opponent. Each season has its own intercept: the home advantage left when both sides are local, equally rested and equally rated. Standard errors are heteroskedasticity-robust (HC1).

| Model | Outcome | Sample | Question |
|---|---|---|---|
| `travel_cost` | `margin`, controlling for Elo difference | 1996 to 2026 | What does travel cost? |
| `travel_cost_line_era` | `margin`, controlling for Elo difference | 2013+ with a closing line | Same, on the market sample |
| `market_priced` | `-team_line_close`, controlling for Elo difference | 2013+ with a closing line | What does the market charge for travel? |
| `market_missed` | `margin_vs_line` | 2013+ with a closing line | What does the market leave out? |

Every model excludes 2020 and 2021 (unverified temporary bases), 1995 (Elo burn-in) and the Las Vegas games. Each is fitted in two designs:

- **`bands`** (headline): distance bands from the base (150 to 1,000, 1,000 to 2,000, 2,000 to 3,000, and 3,000 km and over; baseline within 150 km).
- **`linear`** (sensitivity check): km per 1,000 plus east and west time-zone hours.

Both designs also include days of rest (capped at 4 to 14) and km travelled in the previous 21 days.

Outputs in `data/processed/travel_model/`, sized for a dashboard:

| File | Contents |
|---|---|
| `coefficients.csv` | Every term of every model and design, with 95% intervals |
| `travel_effects.csv` | The same without the season intercepts, with readable labels |
| `home_advantage_by_season.csv` | Raw home margin and the home-ground part left after travel, rest and Elo |
| `team_season_travel.csv` | Season km (return-home basis), trips, time-zone hours, margin and `margin_vs_elo` per club, with projected 2027 rows (`projected = True`) |
| `projection_2027_pairs.csv`, `projection_2027_clubs.csv` | Distance, time zone and projected travel cost for every club pair and club in an 18-team 2027 competition |
| `perth_reds_1995_1997.csv` | Western Reds home and away results against Elo |
| `model_report.txt` | Summary tables |

Results from the October 2026 run (points of margin for the travelling team, 95% intervals):

- **Home advantage is mostly not travel.** About 1 point of a 3 to 5 point home advantage comes from the visitors' journey. The home-ground part has fallen from about 4.5 points before 2010 to about 2 points since 2022.
- **Distance cost is a step plus a long-haul penalty.** Trips of 150 to 1,000 km cost -1.4 (-2.4 to -0.4). Trips of 1,000 to 3,000 km are no worse (about -0.4, intervals spanning 0). Trips of 3,000 km and over cost -4.3 (-7.9 to -0.7). Most trips over 3,000 km are between North Queensland and Auckland, or to Darwin, Perth and Townsville; few involve Perth.
- **Time zone cannot be separated from distance.** Nearly every time-zone crossing is a New Zealand or Perth trip. In the linear design the hour terms come out positive (about +1.1 per hour), which offsets a straight line that overstates the cost of New Zealand trips; it is not evidence that crossing time zones helps. A tropical-venue term (Townsville, Darwin) was tested and is near 0.
- **Rest and recent travel load show no effect** (both within ±0.3 points per unit).
- **Elo is well calibrated.** The Elo difference coefficient is 1.03 (0.96 to 1.09).
- **The market prices travel about evenly by distance.** It charges about 1.0 point for trips of 150 to 1,000 km and about 1.7 points for every longer band. The point estimates suggest the market overprices 1,000 to 3,000 km trips by about 1 point and underprices trips over 3,000 km by about 1.75. No `market_missed` term is distinguishable from 0, so there is no evidence of a betting edge.
- **Perth Bears 2027.** In a balanced draw, 16 of the Bears' 17 opponents are based more than 3,000 km away (Melbourne is about 2,700 km). The bands design puts the cost at about 4 points per away game, roughly 9 percentage points of win probability in an even match. Opponents pay the same in Perth, so the projected season total is 0 by construction. The effect is a home-away swing of about 8 points, against about 1.4 for a Sydney club. On a return-home basis the Bears would travel about 82,000 km, more than any club in any season since 1995.
- **Western Reds 1995 to 1997.** Away from Perth the Reds averaged about 5.5 points below Elo, which already allows for a normal home advantage. At home they were about 0.4 below Elo, so visiting teams did not lose the same ground in Perth. With about 30 games on each side the difference is within noise, but it is the only direct evidence on Perth trips. It fits eastward travel being harder than westward.

## Known gaps

- **Temporary bases are unverified.** The Warriors rows use the Central Coast (May 2020 to July 2021), a south-east Queensland hub (July to October 2021) and Redcliffe (2022, to June); the start and end dates are approximate. The 2021 relocation rows for the Sydney clubs, Raiders and Knights use a single Brisbane point because club-specific bases are not recorded here. Melbourne's 2020 Sunshine Coast base is not included. The Phase 2 models exclude 2020 and 2021; confirm these from club and NRL announcements before using those seasons.
- **Return-home assumption.** Normal NRL practice is to fly home after each away match, so `km_21d_return_home` is the default load measure. The venue-to-venue measure covers multi-week trips and hubs. Phase 2 uses the return-home measure for prior load and has not compared the two.
- **Lines cover 2013 to mid-2024 and 2026 only.** 2009 to 2012 have head-to-head odds (`implied_win_prob`) but no line. Mid-2024 to 2025 have `team_line_open` but no closing line. Earlier rows use `margin_vs_elo` as the outcome instead.
- **Phase 2 projection is a balanced draw.** Away games are placed at the opponent's base, so neutral venues, Magic Round, Las Vegas and any Bears home games played outside Perth are ignored. The 3,000 km band is estimated mostly from non-Perth trips, and the projection assumes Perth trips cost the same as other trips of that length. Replace the pairs with the published 2027 draw when it is released.
- **Elo home advantage has declined.** The home advantage is fixed at its 1996 to 2012 value. From 2013 the designated home side averages about 2 points below its Elo expectation, and Elo's home expectation is about 2.2 points above the closing line, so `margin_vs_elo` carries an era trend for home and away sides. The fixed home advantage also applies at neutral venues (Magic Round, Las Vegas, relocated games). The Phase 2 models avoid both by using `team_elo_pre - opp_elo_pre` with a separate home intercept for each season.
- **Elo in 1995 and 1997.** No results before 1995 are used, so 1995 ratings start level and are unreliable. In 1997 the ARL and Super League clubs are rated in separate pools that only meet from 1998.
- **World Club Challenge excluded.** The 67 Super League 1997 World Club Challenge games (`EXCLUDE_ROUND_RE`) are dropped. Super League clubs, including the Perth Reds, played some of these in England mid-season, so 1997 Super League rest and rolling travel figures understate actual travel.
- **Cancelled and forfeited games.** Six 1996 Round 1 games were cancelled or forfeited during the Super League dispute and are dropped (no scores).
- **Daylight saving.** Offsets come from the IANA time zone database through Python's `zoneinfo`, which includes Western Australia's 2006 to 2009 trial. Install `tzdata` on Windows.
