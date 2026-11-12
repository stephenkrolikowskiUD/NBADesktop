// No API keys belong in this file. GITHUB_TOKEN is a Cloudflare secret.
const API = 'https://api.github.com/repos/stephenkrolikowskiUD/NBADesktop/actions/workflows/nba-engine.yml';
const STATUS = 'https://docs.google.com/spreadsheets/d/12gBgVx_RCsIytjZHjfZWgLtG-R-zcPbYYE-CVFd4EDw/gviz/tq?tqx=out:csv&sheet=NBA_Status';
const zone = 'America/New_York';

export function eastern(ms) {
  return Object.fromEntries(new Intl.DateTimeFormat('en-US', {
    timeZone: zone, year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
  }).formatToParts(new Date(ms)).filter(p => p.type !== 'literal').map(p => [p.type, p.value]));
}

export function slotAt(ms) {
  const p = eastern(ms);
  return { date: `${p.year}-${p.month}-${p.day}`, hour: p.hour, minute: p.minute };
}

async function github(env, path, init = {}, fetcher = fetch) {
  if (!env.GITHUB_TOKEN) throw new Error('GITHUB_TOKEN is not configured');
  let response;
  try {
    response = await fetcher(API + path, {
      ...init, redirect: 'manual', signal: AbortSignal.timeout(15000),
      headers: { Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${env.GITHUB_TOKEN}`, 'User-Agent': 'nba-engine-dispatcher',
        'X-GitHub-Api-Version': '2022-11-28', 'Content-Type': 'application/json' },
    });
  } catch (error) {
    // Classify without echoing request headers, token values or provider bodies.
    const kind = ['TypeError', 'TimeoutError', 'AbortError'].includes(error?.name) ? error.name : 'NetworkError';
    throw new Error(`GitHub ${init.method || 'GET'} ${path.split('?')[0] || '/'} failed or timed out (${kind})`);
  }
  if (response.status >= 300 && response.status < 400) throw new Error('GitHub redirect refused; credentials were not forwarded');
  if (!response.ok) throw new Error(`GitHub HTTP ${response.status}; check token, workflow and inputs`);
  return response;
}

async function slotRuns(env, date, fetcher) {
  const response = await github(env, `/runs?event=workflow_dispatch&branch=main&per_page=100&created=${date}`, {}, fetcher);
  const data = await response.json();
  if (!Array.isArray(data.workflow_runs)) throw new Error('Invalid GitHub runs response');
  return data.workflow_runs.filter(r => r.display_title === `NBA Engine ${date}@10:00`);
}

export async function dispatch(env, date, fetcher = fetch) {
  const runs = await slotRuns(env, date, fetcher);
  if (runs.some(r => r.status !== 'completed' || r.conclusion === 'success')) {
    return { action: 'already_dispatched', slot: `${date}@10:00` };
  }
  // Do not retry ambiguous network failures here. The backup and engine guard
  // handle recovery without an uncontrolled burst of paid engine runs.
  await github(env, '/dispatches', {
    method: 'POST', body: JSON.stringify({ ref: 'main', inputs: { slate_date: date, slot: '10:00' } }),
  }, fetcher);
  return { action: 'dispatched', slot: `${date}@10:00` };
}

export function parseCSV(text) {
  const rows = []; let row = [], field = '', quoted = false;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"') {
      if (quoted && text[i + 1] === '"') { field += '"'; i++; }
      else quoted = !quoted;
    } else if (!quoted && (c === ',' || c === '\n')) {
      row.push(field); field = '';
      if (c === '\n') { rows.push(row); row = []; }
    } else if (c !== '\r' || quoted) field += c;
  }
  if (quoted) throw new Error('Invalid status CSV');
  if (field || row.length) { row.push(field); rows.push(row); }
  const headers = rows.shift() || [];
  return rows.map(values => Object.fromEntries(headers.map((key, i) => [key, values[i] || ''])));
}

export async function watchdog(env, date, now = Date.now(), fetcher = fetch) {
  const runs = await slotRuns(env, date, fetcher);
  if (!runs.some(r => r.conclusion === 'success')) throw new Error(`No successful 10 AM workflow for ${date}`);
  let response;
  try {
    response = await fetcher(`${STATUS}&_=${now}`, { signal: AbortSignal.timeout(15000), cache: 'no-store' });
  } catch { throw new Error('Cannot read public NBA_Status'); }
  if (!response.ok) throw new Error(`NBA_Status HTTP ${response.status}`);
  const rows = parseCSV(await response.text());
  const healthy = rows.some(r => {
    const time = Date.parse(r.UPDATED_AT_UTC);
    if (!Number.isFinite(time) || time > now) return false;
    const local = slotAt(time);
    return r.SLATE_DATE === date && r.SLOT_KEY === `${date}@10:00` && r.RUN_ID
      && ['READY', 'NO_GAMES'].includes(r.STATUS)
      && local.date === date && Number(local.hour) >= 10;
  });
  if (!healthy) throw new Error(`10 AM publication for ${date} is missing, stale or not READY/NO_GAMES`);
  return { action: 'publication_verified', slot: `${date}@10:00` };
}

async function reportFailure(env, message, fetcher) {
  console.error(message);
  if (!env.ALERT_WEBHOOK_URL) {
    console.error('No alert destination configured; inspect Cloudflare logs');
    return;
  }
  try {
    const result = await fetcher(env.ALERT_WEBHOOK_URL, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: `NBA morning dispatcher: ${message}` }),
      signal: AbortSignal.timeout(10000), redirect: 'manual',
    });
    if (!result.ok) console.error(`Alert delivery failed: HTTP ${result.status}`);
  } catch { console.error('Alert delivery failed'); }
}

export async function runScheduled(controller, env, now = Date.now(), fetcher = fetch) {
  const slot = slotAt(controller.scheduledTime);
  if (slot.hour !== '10' || !['00', '20'].includes(slot.minute)) return { action: 'inactive_DST_slot' };
  try {
    const lag = now - controller.scheduledTime;
    if (lag < 0 || lag > 10 * 60000) throw new Error('Cloudflare trigger arrived outside its ten-minute delivery window');
    const result = slot.minute === '00'
      ? await dispatch(env, slot.date, fetcher)
      : await watchdog(env, slot.date, now, fetcher);
    console.log(JSON.stringify(result));
    return result;
  } catch (error) {
    await reportFailure(env, error.message, fetcher);
    throw error;
  }
}

export default {
  async scheduled(controller, env) { await runScheduled(controller, env); },
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (request.method === 'GET' && path === '/') {
      return Response.json({ service: 'nba-engine-dispatcher', version: 2,
        schedule: '10:00 America/New_York; watchdog 10:20',
        tokenConfigured: Boolean(env.GITHUB_TOKEN), alertsConfigured: Boolean(env.ALERT_WEBHOOK_URL),
        note: 'Health endpoint only; does not confirm cron installation or publication.' });
    }
    // Optional one-off activation test. Visiting the public URL never starts an engine.
    if (request.method !== 'POST' || !['/dispatch', '/check', '/connectivity'].includes(path)) return new Response('Not found', { status: 404 });
    if (!env.DISPATCH_TEST_TOKEN || request.headers.get('Authorization') !== `Bearer ${env.DISPATCH_TEST_TOKEN}`) {
      return new Response('Unauthorized', { status: 401 });
    }
    if (path === '/connectivity') {
      try {
        const response = await github(env, '/runs?per_page=1');
        const data = await response.json();
        if (!Array.isArray(data.workflow_runs)) throw new Error('Invalid GitHub runs response');
        return Response.json({ githubReadAccess: true, engineStarted: false,
          note: 'Read access verified; dispatch write permission is not tested.' });
      } catch (error) { return Response.json({ error: error.message }, { status: 502 }); }
    }
    const now = Date.now(), local = slotAt(now);
    if (Number(local.hour) < 10) return new Response('Test only after 10 AM ET', { status: 409 });
    try {
      return Response.json(path === '/dispatch'
        ? await dispatch(env, local.date) : await watchdog(env, local.date, now));
    } catch (error) { return Response.json({ error: error.message }, { status: 502 }); }
  },
};
