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

Hosting is the Cloudflare Worker `bears-travel`, deployed by Workers Builds on each push to `main`: `wrangler.jsonc` serves `site/` as static assets and routes `/api/*` to `worker/index.js` (Bear-ai). Custom domain `bears.thisaithat.com`.

## Bear-ai

Bear-ai is the dashboard's chat assistant. It answers questions about the analysis from the same tables the page draws (`site/data/*.json`), in the manner of Wharf-ai on the Fremantle dashboard: Claude (`claude-sonnet-5-5`, effort low) calls fixed queries over the tables and quotes only numbers they return; it never writes code. Each answer can show the numbers behind it and suggests three follow-up questions.

| File | Contents |
|---|---|
| `chat/tools.js` | The tables, a description of every column, and three tools: `get_rows`, `aggregate`, `correlate` |
| `chat/answer.js` | The prompt and the streaming tool loop |
| `worker/index.js`, `worker/chat.js`, `wrangler.jsonc` | The site's Cloudflare Worker: serves `site/` as static assets and runs `/api/chat` (bot check, daily caps, streamed answer) |
| `site/assets/bearai.js` | The panel on the page; hidden unless `/api/chat` reports Bear-ai is switched on |
| `tests/bearai.test.mjs` | Offline checks (`npm test`): tools against plain reads of the JSON, bad calls, input limits |
| `evals/bearai_eval.mjs` | Accuracy eval (`npm run eval`): 14 questions, expected figures read from `site/data/`, about US$0.15 a run |

The site is public, so every question spends API credit. Each question needs a Cloudflare Turnstile token, and questions are capped per visitor (a hash of the IP address; addresses are not stored) and across all visitors per Perth day, counted in Workers KV. A question costs about US$0.01, so the default caps (10 per visitor, 150 in total) limit spend to about US$1.50 a day. KV counts are eventually consistent, so a burst can pass a cap by a few questions.

Setup (the site is the `bears-travel` Worker, deployed by Workers Builds from `main`):

1. Workers KV: create a namespace (Storage & Databases, Workers KV) and add its ID to `kv_namespaces` in `wrangler.jsonc` as `{ "binding": "BEARAI_KV", "id": "<namespace id>" }`. The binding has to be in the config: a deploy from the repo replaces bindings added only in the dashboard.
2. Turnstile: add a widget for `bears.thisaithat.com` (managed mode).
3. On the Worker (Settings, Variables and Secrets), add `ANTHROPIC_API_KEY`, `TURNSTILE_SITE_KEY` and `TURNSTILE_SECRET`, all as secrets (the site key is public, but secrets survive deploys). A key in its own Anthropic workspace with a monthly spend limit keeps the cost bounded whatever happens to the caps.
4. Optional: `BEARAI_USER_CAP` and `BEARAI_DAILY_CAP`.

Until the key, Turnstile and KV are all set, `/api/chat` reports Bear-ai as off and the page shows no button.

Local run: `npm install`, then `npm run dev -- --var ANTHROPIC_API_KEY:$ANTHROPIC_API_KEY` (or put the key in a gitignored `.dev.vars` file) and open http://localhost:8787. `BEARAI_DEV=1` skips the bot check, and the caps when no KV is bound. Rerun the eval after changing the prompt, the tools or the model.

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
python src/road_trips.py           # Bears two-game road trip plans, same output folders (needs networkx)
python src/fixture_2027.py         # proposed Bears 2027 fixture, same output folders
python src/draw_2027.py            # full 2027 competition draw around it, plus site/data/perth_bears_2027_draw.csv
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
| `make roads` | `road_trips.py` |
| `make fixture` | `fixture_2027.py` |
| `make draw` | `draw_2027.py` |
| `make assets` | `site_assets.py` |
| `make test` | `tests/test_pipeline.py` |
| `make all` | `build`, `model`, `roads`, `fixture`, `draw`, `test` |

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
| `travel_cost_recent` | `margin`, controlling for Elo difference | 2010 to 2026 | What does travel cost in the recent era? (bands design only) |

Every model excludes 2020 and 2021 (unverified temporary bases), 1995 (Elo burn-in) and the Las Vegas games. Each is fitted in two designs:

- **`bands`** (headline): distance bands from the base (150 to 1,000, 1,000 to 2,000, 2,000 to 3,000, and 3,000 km and over; baseline within 150 km).
- **`linear`** (sensitivity check): km per 1,000 plus east and west time-zone hours.

Both designs also include days of rest (capped at 4 to 14) and km travelled in the previous 21 days.

Two more designs of `travel_cost` test whether travel costs less than it used to. Both keep the season intercepts, Elo difference, rest and prior load:

- **`bands_era`**: each distance band split into three eras, 1996 to 2009, 2010 to 2019 and 2022 to 2026 (`ERAS`), with a Wald test that a band's three era costs are equal.
- **`bands_trend`**: the four bands plus each band times (season - 2010) / 10, a straight-line change in its cost per decade.

The home advantage trend (`home_advantage_trend`) is a separate, simpler regression: the designated home side's raw margin on (season - 2010) / 10, regular season only, 1995 to 2026 excluding 2020 and 2021 and the Las Vegas games, for all matches and split by whether the visitors travelled more than 150 km. The difference row is the interaction term from one regression on all matches.

Outputs in `data/processed/travel_model/`, sized for a dashboard:

| File | Contents |
|---|---|
| `coefficients.csv` | Every term of every model and design, with 95% intervals |
| `travel_effects.csv` | The same without the season intercepts, with readable labels |
| `home_advantage_by_season.csv` | Raw home margin and the home-ground part left after travel, rest and Elo |
| `home_advantage_trend.csv` | Change in raw home margin per decade, for all matches and split by visitors' travel, with 95% intervals |
| `travel_cost_by_era.csv` | Each band's cost in each era from `bands_era`, with 95% intervals, matches with such a trip and the p-value for equal eras |
| `team_season_travel.csv` | Season km (return-home basis), trips, time-zone hours, margin and `margin_vs_elo` per club, with projected 2027 rows (`projected = True`) |
| `projection_2027_pairs.csv`, `projection_2027_clubs.csv` | Distance, time zone and projected travel cost for every club pair and club in an 18-team 2027 competition; the `*_recent` club columns use the 2010 to 2026 band costs |
| `perth_reds_1995_1997.csv` | Western Reds home and away results against Elo |
| `model_report.txt` | Summary tables |
| `road_trips_summary.csv`, `road_trips_schedule.csv`, `road_trips_pair_savings.csv` | Perth Bears road trip plans (`road_trips.py`, see below) |

Results from the October 2026 run (points of margin for the travelling team, 95% intervals):

- **Home advantage is mostly not travel.** About 1 point of a 3 to 5 point home advantage comes from the visitors' journey. The home-ground part has fallen from about 4.5 points before 2010 to about 2 points since 2022.
- **Distance cost is a step plus a long-haul penalty.** Trips of 150 to 1,000 km cost -1.4 (-2.4 to -0.4). Trips of 1,000 to 3,000 km are no worse (about -0.4, intervals spanning 0). Trips of 3,000 km and over cost -4.3 (-7.9 to -0.7). Most trips over 3,000 km are between North Queensland and Auckland, or to Darwin, Perth and Townsville; few involve Perth.
- **Time zone cannot be separated from distance.** Nearly every time-zone crossing is a New Zealand or Perth trip. In the linear design the hour terms come out positive (about +1.1 per hour), which offsets a straight line that overstates the cost of New Zealand trips; it is not evidence that crossing time zones helps. A tropical-venue term (Townsville, Darwin) was tested and is near 0.
- **Home advantage has declined, mostly where the visitors travelled.** In regular-season matches from 1995 to 2026 (excluding 2020 and 2021), the raw home margin changed by -1.2 points per decade (-1.7 to -0.6). Where the visitors travelled more than 150 km the change was -1.6 (-2.3 to -0.9); where they were based within 150 km it was -0.5 (-1.4 to +0.3). The difference between the two slopes is -1.0 (-2.1 to +0.0, p = 0.06), so the gap is borderline on its own.
- **The cost of short trips has fallen; the cost of long trips has not measurably changed.** In `bands_era`, a 150 to 1,000 km trip cost -2.7 (-4.1 to -1.3) in 1996 to 2009, +0.0 (-1.6 to +1.6) in 2010 to 2019 and -0.8 (-3.2 to +1.5) in 2022 to 2026; the test of equal eras gives p = 0.04. The 1,000 to 2,000 and 2,000 to 3,000 km bands show no era differences (p = 0.90 and 0.88). Trips of 3,000 km and over cost -4.2 (-8.2 to -0.1), -2.5 (-9.9 to +4.9) and -11.2 (-25.5 to +3.1) in the three eras (p = 0.57), on 67, 23 and 10 matches. The straight-line `bands_trend` terms are not distinguishable from 0 (150 to 1,000 km +0.7 per decade, -0.4 to +1.7; 3,000 km and over -1.0, -4.9 to +2.9), so the short-trip change looks more like a step around 2010 than a steady trend. The fall in short-trip cost is consistent with the faster decline in home margin where visitors travel, since most travelling visitors make trips of 150 to 1,000 km.
- **Rest and recent travel load show no effect** (both within ±0.3 points per unit).
- **Elo is well calibrated.** The Elo difference coefficient is 1.03 (0.96 to 1.09).
- **The market prices travel about evenly by distance.** It charges about 1.0 point for trips of 150 to 1,000 km and about 1.7 points for every longer band. The point estimates suggest the market overprices 1,000 to 3,000 km trips by about 1 point and underprices trips over 3,000 km by about 1.75. No `market_missed` term is distinguishable from 0, so there is no evidence of a betting edge.
- **Perth Bears 2027.** In a balanced draw, 16 of the Bears' 17 opponents are based more than 3,000 km away (Melbourne is about 2,700 km). The bands design puts the cost at about 4 points per away game, roughly 9 percentage points of win probability in an even match. Opponents pay the same in Perth, so the projected season total is 0 by construction. The effect is a home-away swing of about 8 points, against about 1.4 for a Sydney club. On a return-home basis the Bears would travel about 82,000 km, more than any club in any season since 1995.
- **Perth Bears 2027 on recent-era costs.** With the bands design fitted on 2010 to 2026 only (`travel_cost_recent`), the cost is -4.8 points per away game (about 11 percentage points of win probability) and the home-away swing about 9.7 points, against about 0.8 for a Sydney club. The site headline and the main projection columns use the full 1996 to 2026 fit. The recent fit's lower short-trip cost narrows the swing for east-coast clubs, but the Bears' figure is set by trips of 3,000 km and over, and that cost shows no fall between eras. The recent estimate for that band, -5.1 (-12.0 to +1.7), rests on 33 matches since 2010, most of them North Queensland against the Warriors, and its interval is about twice as wide as the full-period one. The full period is the better-supported estimate of a long-trip cost; the recent projection is reported beside it on the site and in `projection_2027_clubs` (`away_cost_per_game_recent`, `home_away_swing_recent`, `away_win_prob_change_recent`).
- **Western Reds 1995 to 1997.** Away from Perth the Reds averaged about 5.5 points below Elo, which already allows for a normal home advantage. At home they were about 0.4 below Elo, so visiting teams did not lose the same ground in Perth. With about 30 games on each side the difference is within noise, but it is the only direct evidence on Perth trips. It fits eastward travel being harder than westward.

## Road trips

`src/road_trips.py` estimates how much travel the Perth Bears would save by staying away between two consecutive away games instead of returning to Perth, with no more than two away games in a row. A season's away travel is then a set of one-game trips (Perth, A, Perth) and two-game trips (Perth, A, B, Perth). The plan with the least km is the pairing of away games with the largest total saving, where pairing A and B saves `d(Perth, A) + d(Perth, B) - d(A, B)`. That is a maximum-weight matching, solved exactly with networkx. Trips longer than two games would make it a vehicle routing problem.

The 2027 draw is not published, so the plan is computed for every possible set of 12 away opponents from the 17 (6,188 sets), with each away game at the opponent's base and great-circle distances between bases. Legs under `AWAY_TRIP_KM` (150 km) count as road travel, not flights.

Results from the October 2026 run:

- Every set of away opponents pairs fully into six two-game trips.
- Average season travel falls from 82,202 km (returning to Perth after each game) to 44,036 km, 46% less, and flights fall from 24 to about 14.
- The saving ranges from 36,500 to 40,100 km across sets, so it does not depend much on which clubs the Bears play away.
- Typical pairs are two Sydney clubs, the Broncos with the Dolphins or Titans, the Storm with the Raiders, and the Warriors with a Queensland or New South Wales club.
- The travel model found no measurable effect of travel in the previous 21 days on results, so the saving is reported in km and flights, not in points of margin. Two-game trips would also put the Bears' home games in pairs.

## Proposed 2027 fixture

`src/fixture_2027.py` builds a full 2027 fixture the Bears could put to the NRL to reduce their travel time. Rules: no more than two away games in a row, at least four nights between games, games only Thursday to Sunday. Assumptions (in `config.py`, `FIXTURE_*` and the travel-time settings): at least one away game in New Zealand (`FIXTURE_AWAY_REQUIRED`); 27 rounds from the week of Thursday 4 March 2027 with 24 games and three byes in the State of Origin window; every other club played once and seven clubs twice (home and away); no Magic Round or overseas round; away games at the opponent's base. Travel time is door to door: 2.5 hours per flight for transfers, check-in and taxiing, plus air time at 820 km/h eastbound and 680 km/h westbound (Perth to Sydney about 4 hours east and 5 hours west), and road legs under 150 km at 70 km/h.

Method:

1. For every set of 12 away opponents from the 17 that includes the Warriors, the least-time plan with at most two away games in a row is a maximum-weight matching on the hours saved by pairing two away games; the set with the least total time is proposed.
2. The seven clubs met twice are chosen from the away set so the average end-2026 rating of the Bears' 24 opponents is closest to the average of all 17. This does not affect travel.
3. The season runs home, away trip, two home games, and so on, with byes in rounds 13, 17 and 22; trips and home opponents are ordered to maximise the gap between the two meetings with each repeat opponent (`scipy.optimize.linear_sum_assignment`).
4. Home games are on Saturdays; each trip is a Sunday game then a Thursday game (the four-night minimum), flying east on the Saturday, moving by road on the Monday and flying home on the Friday.

Results from the October 2026 run:

- Away games: eight Sydney clubs, the Broncos and the Dolphins in five two-game trips whose second leg is by road, and the Storm then the Warriors on one trip (Perth, Melbourne, Auckland, Perth). The Cowboys, Titans, Knights, Raiders and Sea Eagles are played at home only.
- Travel time falls from 171 hours (average draw, returning to Perth after each away game) to 95 hours, 44% less; flights from 24 to 13; km from about 82,200 to 44,200. Without the New Zealand requirement the optimum is 89 hours and 12 flights, with every away game in New South Wales or south-east Queensland.
- Nights away from Perth rise from 24 to 36, because the team stays in the east between the two games of each trip.
- The fixture is the Bears' preferred position for negotiation: a real draw has to meet every club's constraints.

### Full 2027 draw

`src/draw_2027.py` schedules the other clubs' games around the Bears' proposed fixture, which it keeps unchanged. Every club follows the same rules: no more than two away games in a row (counted over games, so a bye weekend does not reset it), no more than three home games in a row, at least four nights between games, games Thursday to Sunday. With no byes outside rounds 13, 17 and 22, those three rounds are bye weekends for every club, leaving 24 rounds of nine games.

1. Match-ups come from a circle-method round robin for 18 clubs, labelled so the Bears meet the right club in each round. Each round in which the Bears meet a repeat opponent is played twice, so every club has exactly seven repeat opponents, spaced as far apart as the Bears' repeats (13 to 21 rounds).
2. A mixed-integer program (`scipy.optimize.milp`, HiGHS) chooses the home team in every game: 12 home games per club, the run limits, the Bears' fixture fixed, and total door-to-door travel time for all 18 clubs minimised, counting two away games in consecutive rounds as one trip.
3. Each round's games fill one Thursday, two Friday, three Saturday and three Sunday slots, with the Bears' days fixed, assigned round by round to avoid four- and five-night turnarounds.

`check` asserts every rule for every club on each run. The October 2026 run gives 1,185 travel hours across the league; the Warriors (103 hours) and the Bears (95) travel most, St George Illawarra least (38). The site's fixture chapter shows the draw by round or by club, the travel by club, and links `site/data/perth_bears_2027_draw.csv` for download. The draw ignores shared stadiums, broadcast slots and venue availability.

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
