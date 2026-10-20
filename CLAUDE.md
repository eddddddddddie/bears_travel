# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Phase 1 of a Perth Bears travel analysis: a data pipeline that builds one row per team per match (NRL 1998 to 2026, plus the 1995 to 1997 ARL and Super League seasons that included the Perth/Western Reds) with travel distance, time zone shift, rest, kick-off time on the team's home clock, rolling travel load, the closing betting line and pre-match Elo ratings. Phase 2 (`src/travel_model.py`) fits travel cost and market-pricing models on that dataset and writes dashboard-sized tables, including a Perth Bears 2027 projection. README.md holds the full column dictionary, source notes, first-run checks and known gaps.

## Commands

Run everything from this directory (`bears_travel/`). The scripts import `config` and `common` as top-level modules, so they rely on `src/` being the script's own directory (`python src/x.py`), not on a package import.

```
pip install -r requirements.txt
python tests/test_pipeline.py      # offline end-to-end test on synthetic fixtures; prints "All checks passed."
python src/scrape_rlp.py           # [--refresh] scrape Rugby League Project, cache HTML in data/raw/rlp/
python src/fetch_lines.py          # [--refresh] download AusSportsBetting nrl.xlsx to data/raw/
python src/build_dataset.py        # rebuild data/processed/ from interim CSVs + reference/ (no network)
python src/elo.py                  # refit Elo parameters on ELO_FIT_SEASONS and print values for config.py (needs scipy)
python src/travel_model.py         # Phase 2: OLS travel models + dashboard tables in data/processed/travel_model/ (needs statsmodels)
```

There is one test function (`test_pipeline`) run as a plain script; `pytest tests/test_pipeline.py` also works. It needs no network: it redirects `config.RAW_DIR`, `INTERIM_DIR` and `PROCESSED_DIR` to a temp directory, parses inline fixture HTML, writes a synthetic ASB workbook, then runs `build_dataset.main()` and asserts specific feature values (DST-aware tz shifts, rest days, line sign flips, Elo updates from `config.ELO_K` and `ELO_HOME_ADV`). It also calls `elo.run_elo` directly on a three-match frame to check successor, expansion and returning-club starts, and checks `travel_model.match_frame` signs and `pair_offsets` daylight saving. The test does not fit the Phase 2 models (the fixture is too small). When changing feature logic, update the hard-coded expected values there.

There is no linter or formatter configured.

## Pipeline architecture

Three stages, connected only through files in `data/interim/`:

1. `scrape_rlp.py` → `data/interim/rlp_results.csv` (one row per match, raw team/venue names, local date and kick-off).
2. `fetch_lines.py` → `data/interim/asb_lines.csv` (optional; `build_dataset.py` runs without it and leaves line columns blank).
3. `build_dataset.py` → `data/processed/team_matches.{parquet,csv}`, `unmatched_teams.csv`, `unmatched_venues.csv`, `validation_report.txt`.
4. `travel_model.py` (Phase 2) reads `team_matches.parquet` → `data/processed/travel_model/*.csv` and `model_report.txt`.

Network access happens only in stages 1 and 2, and both cache downloads; stage 3 is the one to rerun after editing anything in `reference/`.

`build_dataset.main()` runs in a fixed order, and later steps depend on columns from earlier ones: `load_results` (drops excluded rounds and unscored games) → resolve names → `match_key` and `is_final` → kick-off UTC → `elo.add_elo` (home/away ratings on the match frame) → `to_long` (two rows per match; also sets `elo_expected_margin`, `margin_vs_elo`) → `attach_bases` → `add_travel_features` → `add_opponent_features` → `attach_lines`.

Key design points that span files:

- **Season-scoped name resolution.** `common.AliasResolver` maps raw team and venue names to rows in `reference/teams.csv` / `venues.csv` using pipe-separated `aliases` and `first_season`/`last_season`. The same alias can mean different entities in different eras (e.g. "ANZ" is QEII Stadium to 2003 and Stadium Australia from 2004; "Gold Coast" Chargers vs Titans). If two different ids match in one season it raises `ValueError`, so overlapping alias ranges must be fixed in the CSVs. Unresolved names don't fail the build: they go to `unmatched_*.csv`, and unmatched venues get no coordinates, so their rows have no travel features.
- **Time zones are evaluated at the kick-off instant.** RLP gives local time at the venue; it is converted to UTC with `zoneinfo` (via `common.local_to_utc`), and every offset or clock comparison (base, venue, previous venue) uses that UTC instant, so daylight saving (including WA's 2006 to 2009 trial and NZ DST) is applied by date. Missing kick-off times use `DEFAULT_KICKOFF_LOCAL` only for offset evaluation and are flagged `kickoff_imputed`.
- **Team base by date.** `teams.csv` gives the default base; `base_overrides.csv` replaces it for date ranges (Warriors 2020 to 2022, the 2021 Queensland relocation). All overrides are currently `verified = 0`.
- **Line sign convention.** `team_line_close` is from the team's side (negative = favourite) and `margin_vs_line = margin + team_line_close`. ASB rows are joined on the unordered team pair and a date within one day, and the line is negated when ASB's home/away order differs from the team's side. ASB has lines only from 2013, and only opening lines for mid-2024 to 2025 (`team_line_open`).
- **Elo.** `elo.run_elo` is a points-scale margin Elo: expected home margin = rating difference + `ELO_HOME_ADV`, update `ELO_K` x error, `ELO_SEASON_CARRY` kept between seasons, active clubs re-centred on 0 each season. New clubs start at 0 in the first season of the data and at `ELO_NEW_TEAM` after it; merged and successor clubs average their parents (`ELO_PARENTS`, which handles Manly returning from the Northern Eagles in 2003). The build uses the stored parameters in `config.py` and never refits; `python src/elo.py` refits on `ELO_FIT_SEASONS` (1996 to 2012, 1995 is burn-in), and the values must be copied into `config.py` by hand. `elo.fit_summary` writes the Elo section of `validation_report.txt` (MAE by period and agreement with closing lines from 2013). The home advantage is fixed, so from 2013 home sides run about 2 points under expectation.
- **Phase 2 design.** `travel_model.match_frame` keeps one row per match (designated home side) with travel features as home minus away, so a coefficient is points for the travelling team and its negative is the gain to the side it visits. Season dummies replace the constant and are each season's home-ground advantage. The `bands` design is the headline and the only one used for the 2027 projection. The `linear` design's time-zone terms are confounded with distance (almost all crossings are New Zealand or Perth trips) and come out positive, so don't present them as a jet-lag effect. The 2027 projection places away games at opponents' bases, so every club's season total is 0 by construction; the per-game cost and the home-away swing are the outputs that mean something. Findings are written up in the README with their date; rerun and update them when the data changes.
- **Time zone shifts are folded into -12 to +12** (`wrap_hours`), so trans-Pacific trips such as Las Vegas read as +5 or +6 east rather than -18.
- **Season openers.** `days_rest` is blank, and `km_from_prev_venue` / `tz_shift_from_prev_venue_h` fall back to the from-base values when the previous row for the team is in a different season.
- **Thresholds and season list live in `src/config.py`** (`AT_BASE_KM`, `AWAY_TRIP_KM`, `ROLLING_WINDOW_DAYS`, `COVID_SEASONS`, `LINE_JOIN_MAX_DAYS`, `EXCLUDE_ROUND_RE`, `SEASON_PAGES`, `ELO_*`).

## Parser notes

`scrape_rlp.parse_results_html` follows the 11-cell row layout used by nrlR's `fetch_results_rugbyproject` and has been checked against live pages for every season. Super League 1997 rows have 10 cells (no leading competition cell) and are shifted by one. A header row, if RLP adds one, overrides positions via `_header_map`. Round labels come from short (< 60 character) rows matching `ROUND_RE`, excluding "Bye:" rows. Dates may list only the day number, inheriting the month from the previous row. Check parser changes against cached pages in `data/raw/rlp/`.

To identify an unmatched venue name, use the `/venues/N` link in its RLP results cell: the venue page gives the full name, town and usually coordinates (cached copies in `data/raw/rlp_venues/`).

## Repository context

This directory is a git repository (branch `main`, no remote yet), kept as a public portfolio piece. `.gitignore` excludes `data/raw/`, `data/interim/` and `data/processed/team_matches.{parquet,csv}`: scraped pages, betting data and per-match rows with odds are not redistributed. Don't commit them or anything else holding per-match lines or odds; aggregate outputs (`validation_report.txt`, `data/processed/travel_model/`) are tracked. The README's "Data in this repository" section explains this to readers and links the dashboard.

The Phase 2 dashboard is a claude.ai artifact (linked in the README) built from five JSON copies of the `travel_model/` tables. It does not update when `travel_model.py` reruns: the uploads have to be replaced.

The parent folder (outside the repository) contains `bears_travel_phase1.zip` and `bears_travel_phase1_source.txt` (a single-file bundle of every project file, each section headed `===== FILE: <path> =====`). These are hand-made snapshots of Phase 1, are not regenerated automatically and are stale.

Prose in the README and code comments writes ranges as "1995 to 1997" rather than with dashes, and avoids marketing language; keep to that when editing docs.
