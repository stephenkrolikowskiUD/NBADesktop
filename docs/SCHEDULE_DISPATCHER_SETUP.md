# NBA 10 AM Cloudflare Dispatcher

## Status

Built and tested offline on 2026-11-11. Not deployed, no live dispatch performed,
and no unattended morning verified. Personal Cloudflare account only. The Worker
and GITHUB_TOKEN secret have been created by the user; the last screenshot still
showed Hello World. This setup replaces that placeholder, not the NBA engine.

## Activate in this order

1. Commit and push all dispatcher changes to NBADesktop's default branch, main.
   Include nba_dispatch.py, the workflow, engine, logger, Cloudflare folder,
   tests, README and these docs. Do this before enabling Cloudflare: GitHub must
   accept the new slot input and the engine must have its duplicate guard.
2. In the personal Cloudflare account, open nba-engine-dispatcher > Edit code.
   Select Active / Latest. Replace all Hello World code with the contents of
   cloudflare/nba-engine-dispatcher/worker.mjs and Deploy. Keeping the editor's
   filename worker.js is fine; it is the same ES module code.
3. Keep the existing Production secret GITHUB_TOKEN. Never put it in the code.
   It needs Actions read/write for stephenkrolikowskiUD/NBADesktop only. Track its
   expiration and rotate before expiry.
4. Under Settings > Triggers > Cron Triggers, add exactly these two schedules:

   - `0 14,15 * * *` (dispatch)
   - `20 14,15 * * *` (publication check)

   Cloudflare uses UTC. The code acts only at 10:00 and 10:20 America/New_York,
   ignoring the other UTC hour automatically across daylight saving changes.
   Allow up to 15 minutes for trigger configuration to propagate.
5. Visit the Worker URL. It should return JSON, not Hello World, with
   tokenConfigured true. This checks code deployment only, not cron installation,
   GitHub authorization, token expiry, or a successful engine publication.

The checked-in wrangler.toml contains the same schedule if we later switch to
Wrangler deployment. Do not use two independently maintained configurations.

## Verify before calling it done

- Optional controlled live test, after 10 AM ET: add a temporary secret
  DISPATCH_TEST_TOKEN containing a fresh random value. An authenticated POST to
  /dispatch with Authorization: Bearer <that value> dispatches today's explicit
  10:00 slot. It can spend one normal engine run of Odds credits; get user approval
  before doing this. Never send tokens in URLs or screenshots. GET cannot dispatch.
- Follow GitHub's `NBA Engine YYYY-MM-DD@10:00` run. Confirm the engine prints
  SCHEDULE RUN, uses that exact slate, finishes, and NBA_Status shows the same
  SLOT_KEY, a nonempty RUN_ID, fresh UPDATED_AT_UTC, and READY or NO_GAMES.
- An authenticated POST to /check checks both the workflow and public status.
  Delete DISPATCH_TEST_TOKEN after testing to disable both manual endpoints.
- On the next morning, confirm an actual Cloudflare cron invocation at 10:00 ET,
  a promptly created GitHub workflow, and the 10:20 publication_verified log.
  Repeat for three mornings before calling scheduling reliable. Neither provider
  guarantees an exact-second start or completion by 10:00.

## Alerts

The watchdog throws and logs an error for missing/failed runs, unreadable status,
wrong dates/slots, stale timestamps, and any state other than READY or NO_GAMES.
DEGRADED, AWAITING_PROPS and WARMUP require attention, even after a green workflow.

To receive a notification, add a personal Slack-compatible incoming-webhook URL
as secret ALERT_WEBHOOK_URL. The Worker sends JSON with a text field. Do not use
a company workspace without authorization. Until an alert destination is chosen
and tested, failures are visible ONLY in Cloudflare logs: no email or push alert
is configured. The watchdog does not automatically launch another paid run.

## Duplicate and stale-run behavior

- The date@slot key is persisted in Run_Log and NBA_Status. Existing GitHub
  schedules stay as backups; all engine/grader writers keep nba-sheet-writer.
- Before loading API secrets or making paid requests, a slot run reads Run_Log.
  A matching NBA engine OK/WARN row blocks a duplicate only when it includes
  a read-back READY/NO_GAMES publication and its run ID. Old green runs alone
  do not block. Failures, degraded output and incomplete publication allow retry.
- SKIP is logged explicitly without wiping an existing dashboard. Ordinary
  manual runs with blank slot remain intentional refreshes and are not deduped.
- Native dates are inferred from the cron time, not supplied by GitHub. Delivery
  across midnight or over six hours late is skipped. An exact whole-day delay is
  inherently ambiguous and cannot be reliably detected from the cron string.
- If Run_Log cannot be read, a slot run fails before paid calls. If the final log
  append fails, the backup may repeat a pull; we prefer recovery to silently
  suppressing an unrecorded publication. A manual run outside the shared workflow
  lock is not protected against races.
- GitHub's shared concurrency group has one pending slot and can replace pending
  work. This solution bypasses delayed cron delivery, not runner outages or writer
  contention. A dispatcher network timeout is not blindly retried: the request
  may already have been accepted. Check GitHub before retrying.
- The watchdog reads the existing public NBA_Status CSV. Changing the spreadsheet
  or its sharing settings requires updating/testing the Worker URL. It checks the
  final publication marker, not every player row or pick quality.

## Offline verification

Run the Python unittest suite, node tests/test_nba_dispatcher.mjs and
node tests/test_nba_dashboard.cjs. Tests use mocked GitHub, Sheets and data APIs.
They cover DST, date rollover, stale and duplicate skips, retries, publication
read-back, header mapping, authorization, and a green run with bad publication.

Official references:
- https://developers.cloudflare.com/workers/configuration/cron-triggers/
- https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event
