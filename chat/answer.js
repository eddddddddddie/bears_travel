// Bear-ai's answer loop: Claude answers by calling the data tools in tools.js, streaming text as
// it goes. Used by the site's Worker (worker/chat.js) and the eval (evals/bearai_eval.mjs).

import Anthropic from '@anthropic-ai/sdk';
import * as T from './tools.js';

export const MODEL = 'claude-sonnet-5-5';
// "low" is the documented starting point for chat on Sonnet 5.5 (Wharf-ai measured it at about
// 40% faster than "medium" with no loss on its eval).
export const EFFORT = 'low';
const MAX_TOKENS = 8000;
const MAX_STEPS = 8;             // model requests per question before the loop stops
const MAX_BAD_STREAMS = 2;       // re-asks after a tool input the SDK could not parse at all
// Server-side refusal fallback: a declined request is retried on a fallback model in the same call.
const BETAS = ['server-side-fallback-2026-07-01'];

// Sections of the page, so the reader's position can be named in the per-question note.
export const SECTIONS = {
  top: 'Overview (headline projection)', map: 'Distance from Perth to each 2027 opponent', clubs: 'Season travel by club',
  cost: 'Impact of travel by distance', home: 'Home advantage and its travel component', market: 'Travel in the betting market',
  reds: 'Western Reds, 1995 to 1997', bears: 'Perth Bears 2027 outlook', roads: 'Two-game road trips for the Bears',
  fixture: 'A 2027 fixture for the Bears to propose', method: 'Method and limits', about: 'About this analysis',
};

const INTRO = `You are Bear-ai, the analyst on an independent dashboard about travel in the NRL and the Perth Bears,
who join the competition in 2027. You answer questions from the dashboard's tables: models of what travel
cost NRL teams from 1996 to 2026 (public match results and Elo ratings; closing betting lines from 2013),
season travel for every club since 1995, the Western Reds of 1995 to 1997, and the Bears' projected 2027
travel, road-trip plans, a proposed fixture and a full illustrative draw.

How to answer:
- Use the tools for every number you state. Call them as often as you need, several in parallel if useful.
  Do not quote a figure from memory or work one out in your head beyond restating tool output. A simple
  step from two tool numbers is fine (a difference, a ratio, a percentage) if both numbers are in the
  answer, in plain words. Never mention tools, tables or column names to the reader; the page shows the
  numbers under your answer.
- Give regression estimates with their 95% interval when it matters, e.g. "-4.3 points (-7.9 to -0.7)".
  Say "points of margin". Negative costs mean worse for the travelling team.
- If a tool returns an error, fix the call and try again.
- Say plainly when a question needs data we do not have. We do NOT have: individual match results or odds
  (only aggregates), player data, injuries, crowd figures, ticketing, finances, kick-off times by game, the
  real 2027 NRL draw (not yet published), or anything after the 2026 season.
- Key caveats, to use when relevant:
  - Time-zone effects cannot be separated from distance: nearly every time-zone crossing is a New Zealand
    or Perth trip. The linear design's positive hour terms are not evidence that crossing time zones helps.
  - Trips of 3,000 km and over are mostly North Queensland to Auckland, or to Darwin, Perth and Townsville;
    few involve Perth. The Bears' projection assumes Perth trips cost the same as other trips that long.
  - The 2027 projection is a balanced draw with away games at opponents' bases. Opponents pay the same cost
    in Perth, so each club's season total is 0 by construction; the per-game cost and the home-away swing
    are what mean something.
  - The main projection uses 1996 to 2026 costs; the recent-era (2010 to 2026) figures are a sensitivity.
  - The Western Reds sample is about 30 games each side, so it is suggestive, not conclusive.
  - The proposed fixture and full draw are illustrations built under stated rules, not the NRL's draw.
  - Correlations and splits are associations, not causes.
- The betting-market section asks whether closing lines priced travel. Do not give betting advice, tips
  or predictions for future games.
- Stay on this dashboard's subject. For anything else, say briefly that you only answer questions about
  this analysis.
- End every answer with one last line, in exactly this form and nothing after it:
  FOLLOWUPS: <question> | <question> | <question>
  Three short follow-up questions (under 10 words each) a reader might ask next, answerable from these
  tables and different from what was just asked. Plain text, no formatting.
- Keep answers short for a side panel: lead with the answer, then the few numbers that support it. Short
  bullet lists rather than tables. Write ranges as "1996 to 2026". Do not use em dashes.
`;

// Stable for the life of the deployment, so it is cached with the tools.
export const SYSTEM_TEXT = `${INTRO}\nTABLES\n\n${T.describe()}`;

export const MARKER = 'FOLLOWUPS:';

export function parseFollowups(text) {
  const parts = text.split('|').map((p) => p.replace(/^[\s*_\-\d.)]+|[\s*_]+$/g, ''));
  return parts.filter((p) => p.length > 3 && p.length < 120).slice(0, 3);
}

// Passes answer text through but holds back the trailing FOLLOWUPS line, keeping a few characters
// in hand so a marker split across chunks is still caught.
export class FollowupFilter {
  constructor() { this.buf = ''; this.tail = ''; this.capturing = false; }
  push(chunk) {
    if (this.capturing) { this.tail += chunk; return ''; }
    this.buf += chunk;
    const i = this.buf.indexOf(MARKER);
    if (i >= 0) {
      const out = this.buf.slice(0, i).replace(/[ *_\n]+$/, '');
      this.tail = this.buf.slice(i + MARKER.length);
      this.capturing = true; this.buf = '';
      return out;
    }
    // Trailing whitespace waits too, in case the marker follows it.
    const safe = this.buf.slice(0, this.buf.length - (MARKER.length - 1)).trimEnd().length;
    if (safe <= 0) return '';
    const out = this.buf.slice(0, safe);
    this.buf = this.buf.slice(safe);
    return out;
  }
  end() {
    const rest = this.capturing ? '' : this.buf;
    const line = this.tail.trim().split('\n')[0] || '';
    return { rest, followups: line ? parseFollowups(line) : [] };
  }
}

export function makeClient(apiKey) {
  // 60 s between streamed events (the API sends pings while the model thinks), two SDK retries.
  return new Anthropic({ apiKey, maxRetries: 2, timeout: 60_000 });
}

export function friendlyError(err) {
  if (err instanceof Anthropic.APIConnectionTimeoutError) return 'Bear-ai took too long to respond. Please ask again.';
  if (err instanceof Anthropic.APIConnectionError) return 'Bear-ai could not reach the Claude API. Please ask again.';
  if (err instanceof Anthropic.RateLimitError || err instanceof Anthropic.InternalServerError) {
    return 'The Claude API is busy right now. Wait a few seconds and ask again.';
  }
  return 'Something went wrong while answering. Please ask again.';
}

/**
 * Answer the last question in history, calling emit(event) as it goes:
 *   {type: 'text', text}                          answer text (FOLLOWUPS line removed)
 *   {type: 'step', n}                             model request n is starting
 *   {type: 'tool', name, args, table, isError}    a tool ran; table feeds "Show the numbers"
 *   {type: 'followups', items}                    up to three suggested questions
 *   {type: 'usage', usage}                        token usage of one model request
 * history: [{role, content}] plain-text turns ending with the user's question. Within one question
 * the message list is append-only and assistant turns go back whole (thinking and tool_use blocks),
 * as preserved thinking requires; only final text is kept between questions.
 * isClosed(): true once the reader has gone, so no more requests are spent.
 */
export async function streamAnswer({ client, history, section, emit, isClosed = () => false, model = MODEL, effort = EFFORT }) {
  let note = 'The reader is on the dashboard page.';
  if (section && SECTIONS[section]) note = `The reader is looking at the section "${SECTIONS[section]}".`;
  // Tools render before system, so this breakpoint caches tools, rules and the table schema.
  const system = [
    { type: 'text', text: SYSTEM_TEXT, cache_control: { type: 'ephemeral' } },
    { type: 'text', text: note },
  ];
  const messages = history.map((m) => ({ role: m.role, content: m.content }));
  const filter = new FollowupFilter();
  const say = (s) => { const out = filter.push(s); if (out) emit({ type: 'text', text: out }); };
  let wroteText = false;
  let emptyRetries = 0;
  let badStreams = 0;

  try {
    for (let step = 0; step < MAX_STEPS; step++) {
      if (isClosed()) return;
      emit({ type: 'step', n: step });
      let response;
      let startedText = false;
      try {
        const stream = client.beta.messages.stream({
          model,
          max_tokens: MAX_TOKENS,
          thinking: { type: 'adaptive' },
          output_config: { effort },
          system,
          tools: T.TOOLS,
          messages,
          betas: BETAS,
          fallbacks: 'default',
        });
        // The SDK materialises eager tool inputs at content_block_stop, so a JSON error rejects
        // this loop or finalMessage(); both are inside the try.
        for await (const event of stream) {
          if (event.type === 'content_block_delta' && event.delta.type === 'text_delta') {
            if (!startedText && wroteText) say('\n\n');
            startedText = true; wroteText = true;
            say(event.delta.text);
          }
        }
        response = await stream.finalMessage();
      } catch (err) {
        if (err instanceof Anthropic.APIError || badStreams >= MAX_BAD_STREAMS) throw err;
        badStreams += 1;   // tool input the SDK could not parse at all: ask again
        continue;
      }
      emit({ type: 'usage', usage: response.usage });

      if (response.stop_reason === 'refusal') { say('\n\nBear-ai can\'t help with that one.'); return; }
      if (response.stop_reason === 'max_tokens') { say('\n\n(Answer cut short: it hit the length limit.)'); return; }
      if (response.stop_reason !== 'tool_use') return;
      const calls = response.content.filter((b) => b.type === 'tool_use');
      if (!calls.length) {
        // "tool_use" with no tool call is seen now and then; an empty tool-result turn is a 400.
        if (emptyRetries++ < 2) continue;
        say('\n\nSorry, Bear-ai couldn\'t work that one out. Please ask again.');
        return;
      }
      messages.push({ role: 'assistant', content: response.content });
      const results = calls.map((block) => {
        const out = T.run(block.name, block.input);
        emit({ type: 'tool', name: block.name, args: block.input, table: out.table, isError: out.isError });
        return { type: 'tool_result', tool_use_id: block.id, content: out.text, is_error: out.isError };
      });
      // All results for this step go back in one user message.
      messages.push({ role: 'user', content: results });
    }
    say('\n\n(Stopped after too many steps. Try a narrower question.)');
  } finally {
    const { rest, followups } = filter.end();
    if (rest) emit({ type: 'text', text: rest });
    if (followups.length) emit({ type: 'followups', items: followups });
  }
}
