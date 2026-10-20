// Bear-ai's data tools: fixed queries over the JSON tables the dashboard draws from (site/data/),
// so every number in an answer was computed here, not recalled or worked out by the model.
// No model-written code runs: the model picks a table, columns, filters and an aggregate.
// Shared by the Cloudflare Pages Function (functions/api/chat.js), tests and the eval.

import travelEffects from '../site/data/travel_effects.json' with { type: 'json' };
import homeAdvantage from '../site/data/home_advantage_by_season.json' with { type: 'json' };
import homeTrend from '../site/data/home_advantage_trend.json' with { type: 'json' };
import costByEra from '../site/data/travel_cost_by_era.json' with { type: 'json' };
import teamSeasons from '../site/data/team_season_travel.json' with { type: 'json' };
import projClubs from '../site/data/projection_2027_clubs.json' with { type: 'json' };
import projPairs from '../site/data/projection_2027_pairs.json' with { type: 'json' };
import reds from '../site/data/perth_reds_1995_1997.json' with { type: 'json' };
import roadSummary from '../site/data/road_trips_summary.json' with { type: 'json' };
import roadSchedule from '../site/data/road_trips_schedule.json' with { type: 'json' };
import roadPairs from '../site/data/road_trips_pair_savings.json' with { type: 'json' };
import fixture from '../site/data/fixture_2027.json' with { type: 'json' };
import fixtureLegs from '../site/data/fixture_2027_legs.json' with { type: 'json' };
import fixtureSummary from '../site/data/fixture_2027_summary.json' with { type: 'json' };
import draw from '../site/data/draw_2027.json' with { type: 'json' };
import drawClubs from '../site/data/draw_2027_clubs.json' with { type: 'json' };

const POINTS = 'Points of match margin for the travelling team, against playing within 150 km of its base (negative = worse).';

// Each table: rows, what it is, and what every exposed column means. Columns not listed here
// (map coordinates, ids that duplicate names) are dropped before the model sees anything.
export const TABLES = {
  travel_effects: {
    rows: travelEffects,
    about: 'Every regression term of the Phase 2 travel models (1996 to 2026, excluding 1995, 2020, 2021 and the Las Vegas games), with 95% intervals. ' + POINTS
      + ' The headline is model=travel_cost, design=bands. market_priced terms are what the closing line charges for travel; market_missed terms are what the line leaves out (margin minus line).',
    columns: {
      model: 'travel_cost (margin, 1996 to 2026), travel_cost_line_era (same on 2013+ matches with a closing line), market_priced (closing line), market_missed (margin vs line), travel_cost_recent (2010 to 2026 only)',
      design: 'bands (headline: distance bands from base), linear (km and time-zone hours; sensitivity check), bands_era (bands split into three eras), bands_trend (bands plus change per decade)',
      outcome: 'Outcome variable of the model',
      sample: 'Seasons in the fit',
      term: 'Term id',
      label: 'Readable term name',
      estimate: 'Coefficient',
      std_error: 'Robust (HC1) standard error',
      ci_low: '95% interval, lower', ci_high: '95% interval, upper',
      p_value: 'p-value',
      n_matches: 'Matches in the fit',
      r2: 'R squared',
    },
  },
  home_advantage_by_season: {
    rows: homeAdvantage,
    about: 'Home advantage by season. raw_home_margin is the designated home side\'s average margin; home_ground_adv is the part left after travel, rest and Elo (the season intercept of the bands model; blank for seasons not in the fit).',
    columns: {
      season: 'Season', raw_home_margin: 'Average home margin, points', matches: 'Matches',
      home_ground_adv: 'Home-ground part after travel, rest and Elo, points',
      home_ground_ci_low: '95% interval, lower', home_ground_ci_high: '95% interval, upper',
      elo_home_adv: 'Fixed home advantage used by the Elo ratings',
    },
  },
  home_advantage_trend: {
    rows: homeTrend,
    about: 'Change in raw home margin per decade, regular season 1995 to 2026 excluding 2020, 2021 and the Las Vegas games: all matches, split by whether the visitors travelled more than 150 km, and the difference between the two slopes.',
    columns: {
      group: 'all, visitors_travelled, visitors_local, difference', label: 'Readable group',
      slope_per_decade: 'Points of home margin per decade', ci_low: '95% interval, lower', ci_high: '95% interval, upper',
      p_value: 'p-value', margin_2010: 'Fitted home margin in 2010, points', matches: 'Matches',
      first_season: 'First season', last_season: 'Last season',
    },
  },
  travel_cost_by_era: {
    rows: costByEra,
    about: 'Cost of each distance band in three eras (bands_era design of travel_cost). ' + POINTS,
    columns: {
      term: 'Term id', label: 'Band', era: '1996 to 2009, 2010 to 2019 or 2022 to 2026',
      estimate: 'Points', ci_low: '95% interval, lower', ci_high: '95% interval, upper', p_value: 'p-value',
      trips: 'Matches with a trip in this band and era',
      p_eras_equal: 'p-value of the test that the band\'s three era costs are equal',
    },
  },
  team_season_travel: {
    rows: teamSeasons,
    about: 'One row per club per season, 1995 to 2026, plus projected 2027 rows (projected = true, balanced draw, no results). Season km assumes the team returns home after each away game.',
    columns: {
      season: 'Season', team_name: 'Club', matches: 'Matches played', trips: 'Matches more than 150 km from base',
      km_return_home: 'Season travel, km, returning home after each away game', mean_trip_km: 'Average km per trip',
      tz_hours_crossed: 'Time-zone hours crossed over the season', margin: 'Average match margin, points',
      margin_vs_elo: 'Average points above the Elo expectation', projected: 'true for the 2027 projection rows',
    },
  },
  projection_2027_clubs: {
    rows: projClubs,
    about: 'Projected 2027 travel effect per club in an 18-club competition with a balanced draw (every opponent home and away, away games at the opponent\'s base). '
      + 'Opponents pay the same cost when they visit, so each club\'s season total is 0 by construction; the per-game cost and the home-away swing are the meaningful outputs. '
      + 'Main columns use the 1996 to 2026 band costs; *_recent columns use the 2010 to 2026 fit.',
    columns: {
      team_name: 'Club', mean_away_km: 'Average km to away opponents', mean_abs_tz_h: 'Average time-zone hours to away opponents',
      trips_over_3000km: 'Opponents based 3,000 km or more away',
      away_cost_per_game: 'Average points per away game (negative = worse)', home_gain_per_game: 'Average points gained per home game from visitors\' travel',
      home_away_swing: 'Gap between expected home and away margin due to travel, points',
      away_win_prob_change: 'Change in win probability per away game in an even match (fraction; -0.09 = 9 percentage points)',
      season_km_return_home: 'Season km returning home after each away game',
      away_cost_per_game_recent: 'As away_cost_per_game, 2010 to 2026 fit', home_away_swing_recent: 'As home_away_swing, 2010 to 2026 fit',
      away_win_prob_change_recent: 'As away_win_prob_change, 2010 to 2026 fit',
    },
  },
  projection_2027_pairs: {
    rows: projPairs,
    about: 'Every ordered pair of 2027 clubs: the trip from team to opponent\'s base, its band and projected cost. ' + POINTS,
    columns: {
      team_name: 'Travelling club', opp_name: 'Opponent (host)', km: 'Great-circle km between bases',
      tz_shift_h: 'Time-zone shift, hours (positive = travelling east)', band: 'Distance band',
      away_cost: 'Points for the travelling team', home_gain: 'Points for the host',
    },
  },
  perth_reds_1995_1997: {
    rows: reds,
    about: 'The Western Reds (Perth) in 1995 to 1997, home and away, against Elo. About 30 games each side, so differences are within noise.',
    columns: {
      season: 'Season', setting: 'away or home (Perth)', matches: 'Matches', mean_margin: 'Average margin, points',
      mean_margin_vs_elo: 'Average points above the Elo expectation', mean_km: 'Average km travelled', mean_tz_shift_h: 'Average time-zone shift, hours',
    },
  },
  road_trips_summary: {
    rows: roadSummary,
    about: 'Perth Bears 2027 travel if two consecutive away games are played on one trip (Perth, A, B, Perth) instead of returning home after each, with at most two away games in a row. Computed over all 6,188 possible sets of 12 away opponents.',
    columns: {
      scenario: 'average, median, best or worst set of away opponents', label: 'Readable scenario', away_sets: 'Away sets covered',
      km_return_home: 'Season km returning home after each away game', km_road_trips: 'Season km with two-game trips', km_saved: 'km saved',
      flights_return_home: 'Flights returning home', flights_road_trips: 'Flights with two-game trips',
      away_opponents: 'Away opponents (single sets only)', pct_saved: 'Share of km saved (fraction)', two_game_trips: 'Two-game trips in the plan',
    },
  },
  road_trips_schedule: {
    rows: roadSchedule,
    about: 'The trips in each road-trip scenario.',
    columns: {
      scenario: 'Scenario', trip: 'Trip number', games: 'Games on the trip', route: 'Route', stop1: 'First opponent', stop2: 'Second opponent',
      km: 'Trip km', km_if_return_home: 'km if the team returned home between games', km_saved: 'km saved',
    },
  },
  road_trips_pair_savings: {
    rows: roadPairs,
    about: 'For every pair of opponents, km saved by the Bears playing both on one trip instead of two return trips from Perth.',
    columns: { club_a: 'Club', club_b: 'Club', km_between: 'km between the two bases', km_saved: 'km saved by pairing them' },
  },
  fixture_2027: {
    rows: fixture,
    about: 'A proposed Perth Bears 2027 fixture built to cut travel time: at most two away games in a row, at least four nights between games, games Thursday to Sunday, at least one away game in New Zealand. Byes have no opponent. It is the Bears\' preferred position for negotiation, not the real draw (not yet published).',
    columns: {
      round: 'Round', week_of: 'Thursday of the round', date: 'Game date', day: 'Day', home_away: 'Home, Away or Bye', opponent: 'Opponent',
      venue: 'Venue (city of the host base)', meeting: 'Meeting with this opponent, e.g. 1 of 2', trip: 'Away trip number', nights_since_last_game: 'Nights since the previous game',
    },
  },
  fixture_2027_legs: {
    rows: fixtureLegs,
    about: 'Travel legs of the proposed fixture\'s away trips. Travel time is door to door (2.5 hours per flight plus air time; road legs at 70 km/h).',
    columns: { trip: 'Trip number', date: 'Date', day: 'Day', from: 'From', to: 'To', mode: 'Flight or Road', km: 'km', hours: 'Hours' },
  },
  fixture_2027_summary: {
    rows: fixtureSummary,
    about: 'The proposed fixture against an average draw that returns to Perth after every away game.',
    columns: {
      scenario: 'average_draw, proposed_opponents or proposed_fixture', label: 'Readable scenario', travel_hours: 'Season travel hours', km: 'Season km',
      flights: 'Flights', nights_away: 'Nights away from Perth', hours_saved_vs_average: 'Hours saved against the average draw',
      pct_saved_vs_average: 'Share of hours saved (fraction)', away_opponents: 'Away opponents', repeat_opponents: 'Clubs met twice',
    },
  },
  draw_2027: {
    rows: draw,
    about: 'A full 2027 competition draw scheduled around the proposed Bears fixture under the same rules for every club (up to two away and three home games in a row). Illustrative, not the NRL\'s draw.',
    columns: {
      round: 'Round', date: 'Date', day: 'Day', home: 'Home club', away: 'Away club', venue: 'Venue (city of the home base)', meeting: 'Meeting, e.g. 1 of 2',
    },
  },
  draw_2027_clubs: {
    rows: drawClubs,
    about: 'Each club\'s travel and schedule shape in the full 2027 draw.',
    columns: {
      club: 'Club', games: 'Games', home: 'Home games', away: 'Away games', longest_away_run: 'Longest run of away games', longest_home_run: 'Longest run of home games',
      min_nights_between: 'Fewest nights between games', two_game_trips: 'Two-game away trips', flights: 'Flights', km: 'Season km', travel_hours: 'Season travel hours, door to door',
    },
  },
};

const AGGS = ['mean', 'sum', 'min', 'max', 'median', 'count'];
const MAX_ROWS = 60;
// String columns with at most this many values have them listed in the schema, so the model can filter exactly.
const LIST_VALUES = 40;

class ToolError extends Error {}

const cols = (t) => Object.keys(TABLES[t].columns);
const isNum = (v) => typeof v === 'number' && Number.isFinite(v);

function table(name) {
  if (!TABLES[name]) throw new ToolError(`Unknown table "${name}". Tables: ${Object.keys(TABLES).join(', ')}.`);
  return name;
}

function checkCols(t, list, what) {
  const allowed = cols(t);
  const bad = list.filter((c) => !allowed.includes(c));
  if (bad.length) throw new ToolError(`Unknown ${what} ${bad.join(', ')} for ${t}. Columns: ${allowed.join(', ')}.`);
}

// filters: {column: value | [values] | {min, max}}. Text matches ignore case. Unknown columns are
// errors, never skipped: skipping a filter would silently answer over every row.
function applyFilters(t, rows, filters) {
  if (filters == null) return rows;
  if (typeof filters !== 'object' || Array.isArray(filters)) throw new ToolError('filters must be an object of column: condition.');
  checkCols(t, Object.keys(filters), 'filter column');
  const norm = (v) => (typeof v === 'string' ? v.trim().toLowerCase() : v);
  const tests = Object.entries(filters).map(([c, cond]) => {
    if (Array.isArray(cond)) { const set = cond.map(norm); return (r) => set.includes(norm(r[c])); }
    if (cond && typeof cond === 'object') {
      const { min, max } = cond;
      if (min == null && max == null) throw new ToolError(`Range filter on ${c} needs min and/or max.`);
      return (r) => isNum(r[c]) && (min == null || r[c] >= min) && (max == null || r[c] <= max);
    }
    return (r) => norm(r[c]) === norm(cond);
  });
  return rows.filter((r) => tests.every((f) => f(r)));
}

function sortRows(t, rows, sortBy, descending) {
  if (sortBy == null) return rows;
  checkCols(t, [sortBy], 'sort column');
  const dir = descending === false ? 1 : -1;
  return [...rows].sort((a, b) => {
    const x = a[sortBy]; const y = b[sortBy];
    if (x == null) return 1; if (y == null) return -1;
    return (x < y ? -1 : x > y ? 1 : 0) * dir;
  });
}

// Round for the model and the reader: enough figures to quote, not floating-point noise.
export function fmt(v) {
  if (v == null) return '';
  if (!isNum(v)) return String(v);
  if (Number.isInteger(v)) return String(v);
  const a = Math.abs(v);
  if (a !== 0 && a < 0.001) return v.toPrecision(2);
  if (a < 1) return String(Number(v.toPrecision(3)));
  return String(Math.round(v * 100) / 100);
}

function result(columns, rows, note) {
  const esc = (s) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
  const body = rows.map((r) => columns.map((c) => esc(fmt(r[c]))).join(','));
  const text = [columns.join(','), ...body].join('\n') + (note ? `\n${note}` : '');
  return { text, table: { columns, rows: rows.map((r) => columns.map((c) => fmt(r[c]))), note: note || '' } };
}

export function getRows({ table: name, filters, columns, sort_by: sortBy, descending, limit } = {}) {
  const t = table(name);
  const show = columns && columns.length ? columns : cols(t);
  checkCols(t, show, 'column');
  const n = Math.min(Math.max(1, Number(limit) || 30), MAX_ROWS);
  const matched = sortRows(t, applyFilters(t, TABLES[t].rows, filters), sortBy, descending);
  const note = matched.length === 0 ? 'No rows match these filters.'
    : matched.length > n ? `Showing ${n} of ${matched.length} matching rows.` : `${matched.length} rows.`;
  return result(show, matched.slice(0, n), note);
}

function agg(values, how) {
  const v = values.filter(isNum);
  if (how === 'count') return values.length;
  if (!v.length) return null;
  if (how === 'sum') return v.reduce((a, b) => a + b, 0);
  if (how === 'mean') return v.reduce((a, b) => a + b, 0) / v.length;
  if (how === 'min') return Math.min(...v);
  if (how === 'max') return Math.max(...v);
  const s = [...v].sort((a, b) => a - b); const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

export function aggregate({ table: name, metrics, agg: how = 'mean', group_by: groupBy, filters, sort_by: sortBy, descending } = {}) {
  const t = table(name);
  if (!AGGS.includes(how)) throw new ToolError(`agg must be one of ${AGGS.join(', ')}.`);
  if (!Array.isArray(metrics) || !metrics.length) throw new ToolError('metrics must be a non-empty list of columns.');
  checkCols(t, metrics, 'metric');
  const groups = groupBy == null ? [] : Array.isArray(groupBy) ? groupBy : [groupBy];
  checkCols(t, groups, 'group_by column');
  const rows = applyFilters(t, TABLES[t].rows, filters);
  if (how !== 'count') {
    const text = metrics.filter((m) => rows.length && rows.every((r) => r[m] != null && !isNum(r[m])));
    if (text.length) throw new ToolError(`${text.join(', ')} is not numeric; use agg count or get_rows.`);
  }
  const buckets = new Map();
  rows.forEach((r) => {
    const key = JSON.stringify(groups.map((g) => r[g]));
    if (!buckets.has(key)) buckets.set(key, []);
    buckets.get(key).push(r);
  });
  let out = [...buckets.values()].map((rs) => {
    const o = Object.fromEntries(groups.map((g) => [g, rs[0][g]]));
    metrics.forEach((m) => { o[`${how}_${m}`] = agg(rs.map((r) => r[m]), how); });
    o.rows = rs.length;
    return o;
  });
  const columns = [...groups, ...metrics.map((m) => `${how}_${m}`), 'rows'];
  if (sortBy != null) {
    if (!columns.includes(sortBy)) throw new ToolError(`sort_by must be one of ${columns.join(', ')}.`);
    const dir = descending === false ? 1 : -1;
    out = out.sort((a, b) => ((a[sortBy] ?? -Infinity) - (b[sortBy] ?? -Infinity)) * dir);
  }
  const note = rows.length ? `${rows.length} rows aggregated.` : 'No rows match these filters.';
  return result(columns, out.slice(0, MAX_ROWS), note);
}

export function correlate({ table: name, x, y, filters } = {}) {
  const t = table(name);
  checkCols(t, [x, y], 'column');
  const pts = applyFilters(t, TABLES[t].rows, filters).filter((r) => isNum(r[x]) && isNum(r[y]));
  if (pts.length < 3) throw new ToolError(`Only ${pts.length} rows have both ${x} and ${y}; need at least 3.`);
  const n = pts.length;
  const mx = pts.reduce((a, r) => a + r[x], 0) / n; const my = pts.reduce((a, r) => a + r[y], 0) / n;
  let sxy = 0; let sxx = 0; let syy = 0;
  pts.forEach((r) => { sxy += (r[x] - mx) * (r[y] - my); sxx += (r[x] - mx) ** 2; syy += (r[y] - my) ** 2; });
  const r = sxy / Math.sqrt(sxx * syy);
  const row = { x, y, n, pearson_r: r, slope_y_per_x: sxy / sxx };
  return result(['x', 'y', 'n', 'pearson_r', 'slope_y_per_x'], [row],
    'Association only, with no controls: it does not show cause.');
}

const FILTERS_SCHEMA = {
  type: 'object',
  description: 'Optional. {column: value} for an exact match (text ignores case), {column: [values]} for any of several, {column: {"min": a, "max": b}} for a numeric range (either bound optional).',
};

// eager_input_streaming: inputs stream as generated and are not validated by the API, so run()
// checks every argument and returns a fixable error instead of guessing.
export const TOOLS = [
  {
    name: 'get_rows',
    description: 'List rows of one table, optionally filtered, with chosen columns, sorted and limited (max 60). Use it to look up values, rank clubs or list fixtures.',
    input_schema: {
      type: 'object',
      properties: {
        table: { type: 'string', enum: Object.keys(TABLES) },
        filters: FILTERS_SCHEMA,
        columns: { type: 'array', items: { type: 'string' }, description: 'Columns to return; all when omitted.' },
        sort_by: { type: 'string' },
        descending: { type: 'boolean', description: 'Default true.' },
        limit: { type: 'integer', description: 'Default 30, max 60.' },
      },
      required: ['table'],
    },
    eager_input_streaming: true,
  },
  {
    name: 'aggregate',
    description: 'Aggregate numeric columns of one table (mean, sum, min, max, median or count), optionally filtered and grouped, e.g. average season km by season, or total travel hours across clubs.',
    input_schema: {
      type: 'object',
      properties: {
        table: { type: 'string', enum: Object.keys(TABLES) },
        metrics: { type: 'array', items: { type: 'string' } },
        agg: { type: 'string', enum: AGGS },
        group_by: { type: 'array', items: { type: 'string' } },
        filters: FILTERS_SCHEMA,
        sort_by: { type: 'string', description: 'An output column, e.g. mean_km_return_home.' },
        descending: { type: 'boolean' },
      },
      required: ['table', 'metrics'],
    },
    eager_input_streaming: true,
  },
  {
    name: 'correlate',
    description: 'Pearson correlation and least-squares slope between two numeric columns of one table, optionally filtered. An association with no controls; the regression tables are the controlled estimates.',
    input_schema: {
      type: 'object',
      properties: {
        table: { type: 'string', enum: Object.keys(TABLES) },
        x: { type: 'string' }, y: { type: 'string' },
        filters: FILTERS_SCHEMA,
      },
      required: ['table', 'x', 'y'],
    },
    eager_input_streaming: true,
  },
];

const RUN = { get_rows: getRows, aggregate, correlate };

// The API does not validate eagerly streamed inputs, and a cut-off input can still parse as a
// partial object, so check every argument against the tool's schema before running it.
function schemaErrors(schema, args) {
  const errs = schema.required.filter((k) => args[k] == null).map((k) => `${k} is required`);
  Object.entries(args).forEach(([k, v]) => {
    const p = schema.properties[k];
    if (!p) { errs.push(`unknown argument ${k}`); return; }
    if (v == null) return;
    const ok = {
      string: () => typeof v === 'string' && (!p.enum || p.enum.includes(v)),
      boolean: () => typeof v === 'boolean',
      integer: () => Number.isInteger(v),
      object: () => typeof v === 'object' && !Array.isArray(v),
      array: () => Array.isArray(v) && v.every((x) => typeof x === 'string'),
    }[p.type]();
    if (!ok) errs.push(`${k} must be ${p.enum ? `one of ${p.enum.join(', ')}` : p.type === 'array' ? 'a list of strings' : `a ${p.type}`}`);
  });
  return errs;
}

// -> {text, isError, table}. table ({columns, rows, note}) feeds the "Show the numbers" view.
export function run(name, args) {
  if (!RUN[name]) return { text: `Unknown tool ${name}.`, isError: true, table: null };
  if (!args || typeof args !== 'object' || Array.isArray(args)) {
    return { text: 'INVALID_JSON: tool input was not a JSON object. Retry.', isError: true, table: null };
  }
  const errs = schemaErrors(TOOLS.find((t) => t.name === name).input_schema, args);
  if (errs.length) return { text: `ERROR: ${errs.join('; ')}. Fix the call and retry.`, isError: true, table: null };
  try {
    const out = RUN[name](args);
    return { text: out.text, isError: false, table: out.table };
  } catch (err) {
    if (err instanceof ToolError) return { text: `ERROR: ${err.message}`, isError: true, table: null };
    throw err;
  }
}

// The schema block of the system prompt: each table, its row count, columns and, for short
// text columns, every value (so club names and model ids can be matched exactly).
export function describe() {
  return Object.entries(TABLES).map(([name, t]) => {
    const lines = [`## ${name} (${t.rows.length} rows)`, t.about];
    Object.entries(t.columns).forEach(([c, d]) => {
      const vals = [...new Set(t.rows.map((r) => r[c]).filter((v) => typeof v === 'string' && v !== ''))];
      const list = vals.length && vals.length <= LIST_VALUES ? ` Values: ${vals.join(' | ')}` : '';
      lines.push(`- ${c}: ${d}.${list}`);
    });
    return lines.join('\n');
  }).join('\n\n');
}
