// Bear-ai accuracy eval: real questions through the same loop the site uses, with the expected
// figures read from site/data at run time (so a model rerun updates them) and graded without a model:
// an answer passes when it states a number within tolerance of each expected value and mentions any
// required words. Spends API credit (about US$0.10 a run on Sonnet 5.5); needs ANTHROPIC_API_KEY.
// Run after any change to the prompt, tools or model:  npm run eval  [-- --only 3,7]
// Results go to evals/results/ (gitignored).

import { readFileSync, mkdirSync, writeFileSync } from 'node:fs';
import { streamAnswer, makeClient, MODEL, EFFORT } from '../chat/answer.js';

const data = (name) => JSON.parse(readFileSync(new URL(`../site/data/${name}.json`, import.meta.url)));
const one = (name, test) => data(name).find(test);
const effect = (model, term) => one('travel_effects', (r) => r.model === model && r.design === 'bands' && r.term === term).estimate;
const club = (id) => one('projection_2027_clubs', (r) => r.team_id === id);
const mostHours = [...data('draw_2027_clubs')].sort((a, b) => b.travel_hours - a.travel_hours)[0];
const mostKm = data('team_season_travel').filter((r) => !r.projected).sort((a, b) => b.km_return_home - a.km_return_home)[0];
const reds1996 = one('perth_reds_1995_1997', (r) => r.season === 1996 && r.setting === 'away');

// expect: [value, tolerance] pairs; signs are ignored (answers say "costs 4.3 points" as often as "-4.3").
// words: regexes that must all match. none: the answer must not state this kind of figure at all.
const CASES = [
  { q: 'How many points per away game is travel projected to cost the Bears in 2027?', expect: [[club('perth_bears').away_cost_per_game, 0.06]] },
  { q: 'What does a trip of 3,000 km or more cost the travelling team?', expect: [[effect('travel_cost', 'band_3000_6000'), 0.06]] },
  { q: 'What is the Bears\' projected home-away swing from travel?', expect: [[club('perth_bears').home_away_swing, 0.06]] },
  { q: 'What is the home-away swing from travel for the Sydney Roosters?', expect: [[club('sydney_roosters').home_away_swing, 0.06]] },
  { q: 'How many of the Bears\' opponents are based 3,000 km or more from Perth?', expect: [[club('perth_bears').trips_over_3000km, 0]] },
  { q: 'Which club travels the most hours in the full 2027 draw, and how many?', expect: [[mostHours.travel_hours, 0.6]], words: [/warriors/i] },
  { q: 'On average, how many km would two-game road trips save the Bears?', expect: [[one('road_trips_summary', (r) => r.scenario === 'average').km_saved, 250]] },
  { q: 'How many travel hours does the proposed Bears fixture involve?', expect: [[one('fixture_2027_summary', (r) => r.scenario === 'proposed_fixture').travel_hours, 0.6]] },
  { q: 'By how much has home advantage changed per decade?', expect: [[one('home_advantage_trend', (r) => r.group === 'all').slope_per_decade, 0.06]] },
  { q: 'What did the closing line charge for a trip of 150 to 1,000 km?', expect: [[effect('market_priced', 'band_150_1000'), 0.06]] },
  { q: 'How did the Western Reds do against Elo away from home in 1996?', expect: [[reds1996.mean_margin_vs_elo, 0.06]] },
  { q: 'Which club travelled the most km in a single season before 2027?', expect: [[mostKm.km_return_home, 600]], words: [new RegExp(mostKm.team_name.split(' ').at(-1), 'i'), new RegExp(String(mostKm.season))] },
  { q: 'What was the crowd at the Western Reds\' first home game?', words: [/(don't|do not|doesn't|does not) (have|include|cover)|not (available|in the data)|no (crowd|attendance)/i] },
  { q: 'Who will win when the Bears play the Eels in round 1?', words: [/(can't|cannot|don't|do not|won't) (give|offer|make|predict)|not (able|something)|no (tips|predictions)/i] },
];

// Every number written in the answer: "-4.3", "−4.3", "38,165", "38 165".
function numbersIn(text) {
  return [...text.replace(/[−–]/g, '-').matchAll(/-?\d{1,3}(?:[, ]\d{3})+(?:\.\d+)?|-?\d+(?:\.\d+)?/g)]
    .map((m) => Number(m[0].replace(/[, ]/g, '')));
}

function grade(c, answer) {
  const nums = numbersIn(answer).map(Math.abs);
  const fails = [];
  (c.expect || []).forEach(([want, tol]) => {
    if (!nums.some((n) => Math.abs(n - Math.abs(want)) <= tol + 1e-9)) fails.push(`missing ${want} (±${tol})`);
  });
  (c.words || []).forEach((re) => { if (!re.test(answer)) fails.push(`missing ${re}`); });
  return fails;
}

const only = process.argv.includes('--only') ? process.argv[process.argv.indexOf('--only') + 1].split(',').map(Number) : null;
const client = makeClient(process.env.ANTHROPIC_API_KEY);
const results = [];
const usage = { input: 0, cached: 0, written: 0, output: 0 };

for (const [i, c] of CASES.entries()) {
  if (only && !only.includes(i + 1)) continue;
  let answer = '';
  const tools = [];
  const t0 = Date.now();
  let error = null;
  try {
    await streamAnswer({
      client,
      history: [{ role: 'user', content: c.q }],
      emit: (e) => {
        if (e.type === 'text') answer += e.text;
        if (e.type === 'tool') tools.push({ name: e.name, args: e.args, isError: e.isError });
        if (e.type === 'usage') {
          usage.input += e.usage.input_tokens || 0; usage.output += e.usage.output_tokens || 0;
          usage.cached += e.usage.cache_read_input_tokens || 0; usage.written += e.usage.cache_creation_input_tokens || 0;
        }
      },
    });
  } catch (err) { error = String(err); }
  const fails = error ? [error] : grade(c, answer);
  const secs = (Date.now() - t0) / 1000;
  results.push({ n: i + 1, q: c.q, pass: !fails.length, fails, secs, tools, answer });
  console.log(`${fails.length ? 'FAIL' : 'pass'} ${String(i + 1).padStart(2)} ${secs.toFixed(1)}s  ${c.q}${fails.length ? `\n       ${fails.join('; ')}` : ''}`);
}

// Sonnet 5.5 list prices per million tokens: $2 input, $10 output, $0.20 cache reads, 1.25x for cache writes.
const cost = (usage.input * 2 + usage.written * 2.5 + usage.cached * 0.2 + usage.output * 10) / 1e6;
const passed = results.filter((r) => r.pass).length;
console.log(`\n${passed}/${results.length} passed on ${MODEL} (effort ${EFFORT}), about US$${cost.toFixed(2)}`);
const dir = new URL('./results/', import.meta.url);
mkdirSync(dir, { recursive: true });
const file = new URL(`bearai_${new Date().toISOString().replace(/[:.]/g, '-')}.json`, dir);
writeFileSync(file, JSON.stringify({ model: MODEL, effort: EFFORT, passed, total: results.length, usage, cost, results }, null, 2));
console.log(`Saved ${file.pathname}`);
process.exitCode = passed === results.length ? 0 : 1;
