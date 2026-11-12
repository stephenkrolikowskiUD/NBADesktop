import assert from 'node:assert/strict';
import worker, {slotAt, runScheduled, dispatch, watchdog, parseCSV} from '../cloudflare/nba-engine-dispatcher/worker.mjs';

const env = {GITHUB_TOKEN: 'fixture-only'};
let calls = [];
const fetcher = async (url, init) => {
  // Match Workers, not Node's more permissive redirect support.
  if (init.redirect === 'error') throw new TypeError('Unsupported redirect mode');
  assert.equal(init.redirect, 'manual');
  calls.push([url, init]);
  return url.includes('/runs?') ? Response.json({workflow_runs: []}) : new Response(null, {status: 204});
};
for (const time of ['2026-11-01T15:00:00Z', '2027-03-14T14:00:00Z']) {
  const now = Date.parse(time);
  assert.equal(slotAt(now).hour, '10');
  calls = [];
  await runScheduled({scheduledTime: now}, env, now, fetcher);
  assert.equal(calls.length, 2);
  assert.deepEqual(JSON.parse(calls[1][1].body), {
    ref: 'main', inputs: {slate_date: time.slice(0, 10), slot: '10:00'},
  });
}
for (const time of ['2026-11-01T14:00:00Z', '2027-03-14T15:00:00Z']) {
  calls = [];
  await runScheduled({scheduledTime: Date.parse(time)}, env, Date.parse(time), fetcher);
  assert.equal(calls.length, 0);
}
assert.equal(slotAt(Date.parse('2026-11-11T04:59:59Z')).date, '2026-11-10');
assert.equal(slotAt(Date.parse('2026-11-11T05:00:00Z')).date, '2026-11-11');
await assert.rejects(runScheduled({scheduledTime: Date.parse('2026-11-11T15:00:00Z')}, env,
  Date.parse('2026-11-11T15:11:00Z'), fetcher), /delivery window/);
assert.equal((await worker.fetch(new Request('https://fixture/'), env)).status, 200);
assert.equal((await worker.fetch(new Request('https://fixture/dispatch', {method: 'POST'}), env)).status, 401);
assert.equal((await worker.fetch(new Request('https://fixture/connectivity', {method: 'POST'}), env)).status, 401);
assert.equal((await (await worker.fetch(new Request('https://fixture/'), env)).json()).version, 2);
await assert.rejects(dispatch(env, '2026-11-11', async () => new Response(null, {status: 302})), /redirect refused/);
await assert.rejects(dispatch(env, '2026-11-11', async () => {throw new TypeError('fixture-only secret');}),
  error => error.message.includes('TypeError') && !error.message.includes('fixture-only'));
const originalFetch = globalThis.fetch;
try {
  globalThis.fetch = async (url, init) => {
    assert.equal(init.method || 'GET', 'GET');
    assert.ok(url.endsWith('/runs?per_page=1'));
    assert.equal(init.redirect, 'manual');
    return Response.json({workflow_runs: []});
  };
  const response = await worker.fetch(new Request('https://fixture/connectivity', {
    method: 'POST', headers: {Authorization: 'Bearer temporary-test'},
  }), {...env, DISPATCH_TEST_TOKEN: 'temporary-test'});
  assert.equal(response.status, 200);
  assert.equal((await response.json()).engineStarted, false);
} finally { globalThis.fetch = originalFetch; }
await assert.rejects(dispatch(env, '2026-11-11', async () => new Response('', {status: 401})), /HTTP 401/);
await assert.rejects(dispatch(env, '2026-11-11', async () => {throw new Error('private request details');}), /failed or timed out/);
const run = {display_title: 'NBA Engine 2026-11-11@10:00', status: 'completed', conclusion: 'success'};
assert.equal((await dispatch(env, '2026-11-11', async () => Response.json({workflow_runs: [run]}))).action, 'already_dispatched');
assert.equal(parseCSV('a,b\r\n"one,two","three""four"')[0].b, 'three"four');
const csv = 'SLATE_DATE,STATUS,RUN_ID,UPDATED_AT_UTC,SLOT_KEY\n2026-11-11,READY,abc,2026-11-11T15:08:00Z,2026-11-11@10:00';
const check = (body) => watchdog(env, '2026-11-11', Date.parse('2026-11-11T15:20:00Z'), async url =>
  url.includes('/runs?') ? Response.json({workflow_runs: [run]}) : new Response(body));
assert.equal((await check(csv)).action, 'publication_verified');
for (const body of [csv.replace('READY', 'DEGRADED'), csv.replace('15:08', '14:08'),
  csv.replace('@10:00', '@18:00'), '<html>Sign in</html>']) await assert.rejects(check(body), /publication/);
console.log('Dispatcher offline tests passed');
