// The site's Worker. Static files in site/ are served straight from Workers assets; only /api/*
// reaches this code (run_worker_first in wrangler.jsonc), where /api/chat is Bear-ai.

import { handleGet, handlePost } from './chat.js';

export default {
  async fetch(request, env, ctx) {
    const { pathname } = new URL(request.url);
    if (pathname === '/api/chat') {
      if (request.method === 'GET') return handleGet(env);
      if (request.method === 'POST') return handlePost(request, env, ctx);
      return new Response('Method not allowed', { status: 405, headers: { allow: 'GET, POST' } });
    }
    if (pathname.startsWith('/api/')) return new Response('Not found', { status: 404 });
    return env.ASSETS.fetch(request);
  },
};
