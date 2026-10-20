// Bear-ai panel: asks /api/chat (functions/api/chat.js) and streams the answer, with the numbers
// behind it and suggested follow-ups. Shows nothing unless the function says Bear-ai is switched on,
// so the static page still works on its own and in the claude.ai copy.
(() => {
  const API = '/api/chat';
  const MAX_EXCHANGES = 6;   // earlier question-answer pairs sent with a new question
  const STARTERS = [
    'How much does travel cost the Bears per away game?',
    'Has home advantage fallen over time?',
    'Do betting lines price long trips?',
    'How much would two-game road trips save?',
  ];
  const TABLE_NAMES = {
    travel_effects: 'Travel model estimates', home_advantage_by_season: 'Home advantage by season',
    home_advantage_trend: 'Home advantage trend', travel_cost_by_era: 'Travel impact by era',
    team_season_travel: 'Season travel by club', projection_2027_clubs: '2027 projection by club',
    projection_2027_pairs: '2027 projection by trip', perth_reds_1995_1997: 'Western Reds 1995 to 1997',
    road_trips_summary: 'Road trips', road_trips_schedule: 'Road trip plans', road_trips_pair_savings: 'Road trip pair savings',
    fixture_2027: 'Proposed 2027 fixture', fixture_2027_legs: 'Proposed fixture travel legs',
    fixture_2027_summary: 'Proposed fixture summary', draw_2027: 'Full 2027 draw', draw_2027_clubs: 'Full 2027 draw by club',
  };

  const root = document.querySelector('.pb');
  const launch = document.getElementById('bai-launch');
  const panel = document.getElementById('bai');
  if (!root || !launch || !panel) return;
  const log = panel.querySelector('.bai-log');
  const form = panel.querySelector('form');
  const input = form.querySelector('textarea');
  const send = form.querySelector('button[type="submit"]');
  const left = panel.querySelector('.bai-left');
  const challenge = panel.querySelector('.bai-check');

  let config = null;
  let history = [];          // [{role, content}] finished turns only
  let busy = false;
  let widget = null;
  let pendingToken = null;   // {resolve, reject} while Turnstile runs

  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };

  // A safe subset of Markdown: paragraphs, bullet and numbered lists, bold. Everything is text nodes.
  function inline(parent, text) {
    text.split(/(\*\*[^*]+\*\*)/g).forEach((part) => {
      if (/^\*\*[^*]+\*\*$/.test(part)) parent.appendChild(el('strong', null, part.slice(2, -2)));
      else if (part) parent.appendChild(document.createTextNode(part));
    });
  }
  function render(target, md) {
    target.replaceChildren();
    let list = null;
    md.split('\n').forEach((raw) => {
      const line = raw.trimEnd();
      const bullet = line.match(/^\s*[-*•]\s+(.*)$/);
      const num = line.match(/^\s*\d+[.)]\s+(.*)$/);
      if (bullet || num) {
        const tag = bullet ? 'ul' : 'ol';
        if (!list || list.tagName.toLowerCase() !== tag) { list = el(tag); target.appendChild(list); }
        const li = el('li'); inline(li, (bullet || num)[1]); list.appendChild(li);
        return;
      }
      list = null;
      if (!line.trim()) return;
      const heading = line.match(/^#{1,6}\s+(.*)$/);
      const p = el('p', heading ? 'bai-h' : null); inline(p, heading ? heading[1] : line); target.appendChild(p);
    });
  }

  function numbers(tables) {
    const box = el('details', 'bai-nums');
    box.appendChild(el('summary', null, `Show the numbers (${tables.length})`));
    tables.forEach(({ args, table }) => {
      const wrap = el('div', 'bai-tbl');
      wrap.appendChild(el('p', 'bai-cap', TABLE_NAMES[args.table] || args.table));
      const t = el('table');
      const head = el('tr');
      table.columns.forEach((c) => head.appendChild(el('th', null, c.replace(/_/g, ' '))));
      t.appendChild(el('thead')).appendChild(head);
      const body = t.appendChild(el('tbody'));
      table.rows.forEach((r) => {
        const tr = el('tr');
        r.forEach((v) => tr.appendChild(el('td', null, v)));
        body.appendChild(tr);
      });
      wrap.appendChild(t);
      if (table.note) wrap.appendChild(el('p', 'bai-note', table.note));
      box.appendChild(wrap);
    });
    return box;
  }

  function chips(items, cls) {
    const row = el('div', cls);
    items.forEach((q) => {
      const b = el('button', 'bai-chip', q);
      b.type = 'button';
      b.addEventListener('click', () => ask(q));
      row.appendChild(b);
    });
    return row;
  }

  function showStarters() {
    if (log.children.length) return;
    const intro = el('div', 'bai-intro');
    intro.appendChild(el('p', null, 'Ask about travel in the NRL and the Bears\' 2027 outlook. Answers are worked out from the tables behind this page.'));
    intro.appendChild(chips(STARTERS, 'bai-chips'));
    log.appendChild(intro);
  }

  function scrollDown() { log.scrollTop = log.scrollHeight; }

  function setBusy(on) {
    busy = on;
    send.disabled = on;
    input.readOnly = on;
    log.querySelectorAll('.bai-chip').forEach((b) => { b.disabled = on; });
  }

  // Turnstile: loaded on first open, run once per question (tokens are single use).
  function loadTurnstile() {
    if (!config.siteKey || window.turnstile) return Promise.resolve();
    return new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
      s.async = true;
      s.onload = resolve; s.onerror = () => reject(new Error('The bot check did not load.'));
      document.head.appendChild(s);
    });
  }
  async function token() {
    if (!config.siteKey) return null;
    await loadTurnstile();
    const theme = getComputedStyle(root).colorScheme.includes('dark') ? 'dark' : 'light';
    if (widget == null) {
      widget = window.turnstile.render(challenge, {
        sitekey: config.siteKey, execution: 'execute', appearance: 'interaction-only', theme, action: 'ask',
        callback: (t) => { if (pendingToken) { pendingToken.resolve(t); pendingToken = null; } },
        'error-callback': () => { if (pendingToken) { pendingToken.reject(new Error('The bot check failed. Reload the page and ask again.')); pendingToken = null; } },
      });
    } else {
      window.turnstile.reset(widget);
    }
    return new Promise((resolve, reject) => {
      pendingToken = { resolve, reject };
      window.turnstile.execute(widget);
    });
  }

  async function ask(question) {
    question = question.trim();
    if (!question || busy) return;
    setBusy(true);
    log.querySelector('.bai-intro')?.remove();
    log.querySelectorAll('.bai-next').forEach((n) => n.remove());
    log.appendChild(el('div', 'bai-q', question));
    const turn = el('div', 'bai-a');
    const text = el('div', 'bai-text');
    const status = el('p', 'bai-status', 'Working it out');
    turn.append(status, text);
    log.appendChild(turn);
    input.value = '';
    scrollDown();

    let answer = '';
    const tables = [];
    let followups = [];
    let failed = null;
    const messages = [...history.slice(-2 * MAX_EXCHANGES), { role: 'user', content: question }];
    try {
      const res = await fetch(API, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ messages, section: location.hash.slice(1) || 'top', token: await token() }),
      });
      if (!res.ok || !res.body || !(res.headers.get('content-type') || '').includes('ndjson')) {
        const out = await res.json().catch(() => ({}));
        throw new Error(out.error || 'Bear-ai is unavailable right now.');
      }
      const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
      let buf = '';
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += value;
        const lines = buf.split('\n');
        buf = lines.pop();
        lines.filter(Boolean).forEach((line) => {
          const e = JSON.parse(line);
          if (e.type === 'text') { answer += e.text; render(text, answer); status.hidden = true; }
          else if (e.type === 'tool') { if (e.table) tables.push(e); status.textContent = 'Checking the numbers'; }
          else if (e.type === 'followups') followups = e.items;
          else if (e.type === 'error') failed = e.message;
          else if (e.type === 'done' && e.remaining != null) {
            left.textContent = e.remaining > 0 ? `${e.remaining} question${e.remaining === 1 ? '' : 's'} left today.` : 'That was your last question for today.';
          }
        });
        scrollDown();
      }
    } catch (err) {
      failed = err.message || 'Bear-ai is unavailable right now.';
    }
    status.remove();
    if (failed) turn.appendChild(el('p', 'bai-err', failed));
    if (answer.trim()) {
      history.push({ role: 'user', content: question }, { role: 'assistant', content: answer.trim() });
    }
    if (tables.length) turn.appendChild(numbers(tables));
    if (followups.length && !failed) log.appendChild(chips(followups, 'bai-chips bai-next'));
    setBusy(false);
    scrollDown();
    input.focus({ preventScroll: true });
  }

  function open() {
    panel.hidden = false;
    launch.setAttribute('aria-expanded', 'true');
    root.classList.add('bai-on');
    showStarters();
    loadTurnstile().catch(() => {});
    input.focus({ preventScroll: true });
  }
  function close() {
    panel.hidden = true;
    launch.setAttribute('aria-expanded', 'false');
    root.classList.remove('bai-on');
    launch.focus({ preventScroll: true });
  }

  launch.addEventListener('click', () => (panel.hidden ? open() : close()));
  panel.querySelector('.bai-close').addEventListener('click', close);
  panel.querySelector('.bai-new').addEventListener('click', () => {
    if (busy) return;
    history = [];
    log.replaceChildren();
    showStarters();
    input.focus({ preventScroll: true });
  });
  panel.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
  form.addEventListener('submit', (e) => { e.preventDefault(); ask(input.value); });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); ask(input.value); }
  });

  fetch(API).then((r) => (r.ok ? r.json() : null)).then((c) => {
    if (!c || !c.enabled) return;
    config = c;
    left.textContent = `Up to ${c.perVisitor} questions a day.`;
    launch.hidden = false;
  }).catch(() => {});
})();
