// Offline checks for Bear-ai (no API calls): the tools agree with plain reads of site/data, bad calls
// come back as fixable errors, the FOLLOWUPS line is split off, and the server rejects bad history.
// Run with `npm test` (node --test).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as T from '../chat/tools.js';
import { FollowupFilter, SYSTEM_TEXT } from '../chat/answer.js';
import { checkHistory } from '../worker/chat.js';

const load = (name) => JSON.parse(readFileSync(new URL(`../site/data/${name}.json`, import.meta.url)));
const rows = (out) => out.table.rows.map((r) => Object.fromEntries(out.table.columns.map((c, i) => [c, r[i]])));

test('every table and column the tools expose exists in site/data', () => {
  for (const [name, t] of Object.entries(T.TABLES)) {
    assert.ok(t.rows.length > 0, name);
    for (const c of Object.keys(t.columns)) assert.ok(t.rows.some((r) => c in r), `${name}.${c}`);
  }
});

test('get_rows filters and sorts like a plain read', () => {
  const out = T.run('get_rows', { table: 'projection_2027_clubs', columns: ['team_name', 'home_away_swing'], sort_by: 'home_away_swing', limit: 1 });
  assert.equal(out.isError, false);
  const top = [...load('projection_2027_clubs')].sort((a, b) => b.home_away_swing - a.home_away_swing)[0];
  assert.equal(rows(out)[0].team_name, top.team_name);
  assert.equal(rows(out)[0].home_away_swing, T.fmt(top.home_away_swing));

  const away = T.run('get_rows', { table: 'fixture_2027', filters: { home_away: 'AWAY' } });
  assert.equal(away.table.rows.length, load('fixture_2027').filter((r) => r.home_away === 'Away').length);

  const range = T.run('get_rows', { table: 'team_season_travel', filters: { season: { min: 2010, max: 2012 }, team_name: 'Melbourne Storm' } });
  assert.deepEqual(rows(range).map((r) => r.season).sort(), ['2010', '2011', '2012']);
});

test('aggregate matches plain sums and means', () => {
  const sum = T.run('aggregate', { table: 'draw_2027_clubs', metrics: ['travel_hours'], agg: 'sum' });
  const want = load('draw_2027_clubs').reduce((a, r) => a + r.travel_hours, 0);
  assert.equal(rows(sum)[0].sum_travel_hours, T.fmt(want));

  const by = T.run('aggregate', { table: 'perth_reds_1995_1997', metrics: ['mean_margin_vs_elo'], group_by: ['setting'] });
  const away = load('perth_reds_1995_1997').filter((r) => r.setting === 'away');
  assert.equal(rows(by).find((r) => r.setting === 'away').mean_mean_margin_vs_elo,
    T.fmt(away.reduce((a, r) => a + r.mean_margin_vs_elo, 0) / away.length));
});

test('bad calls are fixable errors, never silent answers', () => {
  const cases = [
    ['get_rows', { table: 'nope' }],
    ['get_rows', { table: 'travel_effects', filters: { teem: 'x' } }],
    ['get_rows', { table: 'travel_effects', columns: 'label' }],
    ['get_rows', { table: 'travel_effects', limit: 'ten' }],
    ['get_rows', { table: 'travel_effects', extra: 1 }],
    ['aggregate', { table: 'team_season_travel', metrics: ['team_name'] }],
    ['aggregate', { table: 'team_season_travel', metrics: ['km_return_home'], agg: 'mode' }],
    ['correlate', { table: 'team_season_travel', x: 'km_return_home' }],
    ['get_rows', 'not an object'],
    ['drop_tables', {}],
  ];
  for (const [name, args] of cases) {
    const out = T.run(name, args);
    assert.equal(out.isError, true, JSON.stringify(args));
    assert.equal(out.table, null);
  }
});

test('the schema lists exact values for short text columns', () => {
  assert.match(SYSTEM_TEXT, /Values: .*Perth Bears/);
  assert.match(SYSTEM_TEXT, /## travel_effects \(\d+ rows\)/);
});

test('FOLLOWUPS line is held back even when split across chunks', () => {
  const f = new FollowupFilter();
  const text = 'The impact is -4.3 points.\n\nFOLLOWUPS: What about Melbourne? | Has it changed? | 3. And the Reds?';
  let shown = '';
  for (let i = 0; i < text.length; i += 5) shown += f.push(text.slice(i, i + 5));
  const { rest, followups } = f.end();
  assert.equal(shown + rest, 'The impact is -4.3 points.');
  assert.deepEqual(followups, ['What about Melbourne?', 'Has it changed?', 'And the Reds?']);
});

test('the server accepts only alternating plain-text turns ending in a short question', () => {
  assert.equal(checkHistory([{ role: 'user', content: 'Hi?' }]), null);
  assert.equal(checkHistory([{ role: 'user', content: 'a' }, { role: 'assistant', content: 'b' }, { role: 'user', content: 'c' }]), null);
  assert.ok(checkHistory([]));
  assert.ok(checkHistory([{ role: 'assistant', content: 'a' }]));
  assert.ok(checkHistory([{ role: 'user', content: [{ type: 'tool_result' }] }]));
  assert.ok(checkHistory([{ role: 'user', content: 'a' }, { role: 'assistant', content: 'b' }]));
  assert.ok(checkHistory([{ role: 'user', content: 'x'.repeat(501) }]));
  assert.ok(checkHistory(Array.from({ length: 15 }, (_, i) => ({ role: i % 2 ? 'assistant' : 'user', content: 'q' }))));
});
