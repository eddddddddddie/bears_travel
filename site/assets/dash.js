// Minimal stand-in for the claude.ai dashboard runtime, so the same page code runs as a static site.
// Loads the JSON tables travel_model.py writes to site/data/ and exposes the small `dash` API the page uses:
// data(id), calc(id, {inputs, fn}), onData(fn), setLink(name).
(() => {
  const SOURCES = {
    effects: 'data/travel_effects.json',
    home_adv: 'data/home_advantage_by_season.json',
    proj2027: 'data/projection_2027_clubs.json',
    pairs: 'data/projection_2027_pairs.json',
    club_seasons: 'data/team_season_travel.json',
    reds: 'data/perth_reds_1995_1997.json',
    land: 'data/land_au_nz.json',
  };
  const state = Object.fromEntries(Object.keys(SOURCES).map((id) => [id, { status: 'loading', data: [] }]));
  const calcs = {};
  const subscribers = [];

  const data = (id) => (calcs[id] ? calcs[id]() : state[id] || { status: 'missing', data: [] });
  const run = () => subscribers.forEach((fn) => {
    try { fn(); } catch (err) { console.error(err); }
  });

  window.dash = {
    data,
    calc(id, { inputs, fn }) {
      let result = null;
      calcs[id] = () => {
        const ins = inputs.map(data);
        const waiting = ins.find((d) => d.status !== 'ok');
        if (waiting) return { status: waiting.status, data: [] };
        result = result || { status: 'ok', data: fn(...ins.map((d) => d.data)) };
        return result;
      };
    },
    onData(fn) { subscribers.push(fn); fn(); },
    setLink(name) { history.replaceState(null, '', `#${name}`); },
  };

  Promise.all(Object.entries(SOURCES).map(([id, url]) => fetch(url)
    .then((res) => { if (!res.ok) throw new Error(`${url}: HTTP ${res.status}`); return res.json(); })
    .then((rows) => { state[id] = { status: 'ok', data: rows }; },
      (err) => { state[id] = { status: 'error', data: [], message: String(err) }; console.error(err); })))
    .then(run);

  let timer;
  addEventListener('resize', () => { clearTimeout(timer); timer = setTimeout(run, 150); });
})();
