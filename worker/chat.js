// Bear-ai's server side at /api/chat, routed here by worker/index.js.
//   GET  -> {enabled, siteKey, perVisitor}: whether the page should show Bear-ai
//   POST {messages, section, token} -> NDJSON stream of the events chat/answer.js emits, then
//        {type: 'done', remaining} or {type: 'error', message}
//
// The site is public and every question spends API credit, so each POST needs a Cloudflare
// Turnstile token, and questions are capped per visitor (hashed IP, never stored raw) and in total
// per Perth day, counted in Workers KV. KV is eventually consistent, so a burst can overshoot a cap
// by a few questions; the caps bound spend, they are not exact.
//
// Settings (Worker > Settings > Variables and Secrets; the KV binding is in wrangler.jsonc):
//   ANTHROPIC_API_KEY  secret; a key in its own workspace with a spend limit is safest
//   TURNSTILE_SECRET, TURNSTILE_SITE_KEY  secrets, from Cloudflare Turnstile (the site key is public,
//                      but secrets survive deploys from the repo)
//   BEARAI_KV          KV namespace binding for the counters
//   BEARAI_USER_CAP    questions per visitor per day (default 10)
//   BEARAI_DAILY_CAP   questions per day across everyone (default 150)
//   BEARAI_DEV = "1"   local only: skip Turnstile and caps when they are not configured

import { streamAnswer, friendlyError, makeClient, SECTIONS } from '../chat/answer.js';

const MAX_TURNS = 13;            // six earlier exchanges plus the new question
const MAX_QUESTION = 500;        // characters
const MAX_TURN = 6000;
const MAX_HISTORY = 24000;

const json = (body, status = 200) => new Response(JSON.stringify(body), {
  status, headers: { 'content-type': 'application/json', 'cache-control': 'no-store' },
});

const perthDay = () => new Date(Date.now() + 8 * 3600e3).toISOString().slice(0, 10);
const caps = (env) => ({
  user: Number(env.BEARAI_USER_CAP) || 10,
  daily: Number(env.BEARAI_DAILY_CAP) || 150,
});
const dev = (env) => env.BEARAI_DEV === '1';
const configured = (env) => Boolean(env.ANTHROPIC_API_KEY && env.TURNSTILE_SECRET && env.TURNSTILE_SITE_KEY && env.BEARAI_KV);

export function handleGet(env) {
  const enabled = Boolean(env.ANTHROPIC_API_KEY) && (configured(env) || dev(env));
  return json({ enabled, siteKey: env.TURNSTILE_SITE_KEY || null, perVisitor: caps(env).user });
}

async function sha256(text) {
  const buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].slice(0, 12).map((b) => b.toString(16).padStart(2, '0')).join('');
}

async function verifyTurnstile(env, token, ip) {
  if (typeof token !== 'string' || !token || token.length > 4096) return false;
  const form = new FormData();
  form.append('secret', env.TURNSTILE_SECRET);
  form.append('response', token);
  if (ip) form.append('remoteip', ip);
  const res = await fetch('https://challenges.cloudflare.com/turnstile/v0/siteverify', { method: 'POST', body: form });
  const out = await res.json().catch(() => ({}));
  return out.success === true;
}

// Plain-text turns only, alternating and ending with the reader's question, so a request cannot
// smuggle in tool calls or grow without bound.
export function checkHistory(messages) {
  if (!Array.isArray(messages) || !messages.length || messages.length > MAX_TURNS) return 'Too many or too few messages.';
  let total = 0;
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i];
    if (!m || m.role !== (i % 2 ? 'assistant' : 'user') || typeof m.content !== 'string' || !m.content.trim()) return 'Messages are malformed.';
    if (m.content.length > MAX_TURN) return 'A message is too long.';
    total += m.content.length;
  }
  if (messages.length % 2 === 0) return 'The last message must be a question.';
  if (messages.at(-1).content.length > MAX_QUESTION) return `Questions are limited to ${MAX_QUESTION} characters.`;
  if (total > MAX_HISTORY) return 'This conversation is too long. Start a new one.';
  return null;
}

export async function handlePost(request, env, ctx) {
  if (!env.ANTHROPIC_API_KEY || !(configured(env) || dev(env))) return json({ error: 'Bear-ai is not switched on.' }, 503);
  let body;
  try { body = await request.json(); } catch { return json({ error: 'Bad request.' }, 400); }
  const problem = checkHistory(body.messages);
  if (problem) return json({ error: problem }, 400);

  const ip = request.headers.get('cf-connecting-ip') || '';
  if (env.TURNSTILE_SECRET && !(await verifyTurnstile(env, body.token, ip))) {
    return json({ error: 'The bot check failed. Reload the page and ask again.' }, 403);
  }

  // Count the question before answering, so an abandoned or failed answer still counts.
  const limit = caps(env);
  let remaining = null;
  if (env.BEARAI_KV) {
    const day = perthDay();
    const userKey = `q:${day}:${await sha256(`${ip}|${day}`)}`;
    const allKey = `q:${day}:all`;
    const [mine, all] = await Promise.all([env.BEARAI_KV.get(userKey), env.BEARAI_KV.get(allKey)]).then((v) => v.map((x) => Number(x) || 0));
    if (all >= limit.daily) return json({ error: 'Bear-ai has answered all the questions it can today. Try again tomorrow.' }, 429);
    if (mine >= limit.user) return json({ error: `You have asked ${limit.user} questions today, the daily limit. Try again tomorrow.` }, 429);
    const ttl = { expirationTtl: 2 * 86400 };
    await Promise.all([env.BEARAI_KV.put(userKey, String(mine + 1), ttl), env.BEARAI_KV.put(allKey, String(all + 1), ttl)]);
    remaining = limit.user - mine - 1;
  }

  const { readable, writable } = new TransformStream();
  const writer = writable.getWriter();
  const enc = new TextEncoder();
  let closed = false;
  const emit = (event) => {
    if (closed || event.type === 'usage' || event.type === 'step') return;
    writer.write(enc.encode(`${JSON.stringify(event)}\n`)).catch(() => { closed = true; });
  };
  const section = typeof body.section === 'string' && SECTIONS[body.section] ? body.section : null;

  const run = async () => {
    const usage = { input: 0, cached: 0, output: 0, requests: 0 };
    try {
      await streamAnswer({
        client: makeClient(env.ANTHROPIC_API_KEY),
        history: body.messages,
        section,
        isClosed: () => closed,
        emit: (e) => {
          if (e.type === 'usage') {
            usage.requests += 1;
            usage.input += e.usage.input_tokens || 0;
            usage.cached += e.usage.cache_read_input_tokens || 0;
            usage.output += e.usage.output_tokens || 0;
          }
          emit(e);
        },
      });
      emit({ type: 'done', remaining });
    } catch (err) {
      console.error('bearai', err);
      emit({ type: 'error', message: friendlyError(err) });
    } finally {
      console.log('bearai usage', JSON.stringify(usage));
      writer.close().catch(() => {});
    }
  };
  // The answer keeps streaming after this handler returns the Response.
  ctx.waitUntil(run());

  return new Response(readable, {
    headers: { 'content-type': 'application/x-ndjson; charset=utf-8', 'cache-control': 'no-store', 'x-content-type-options': 'nosniff' },
  });
}
