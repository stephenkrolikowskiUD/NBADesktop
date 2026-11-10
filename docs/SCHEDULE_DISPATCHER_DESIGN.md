# 10 AM Dispatcher: Design (proposed, not built)

Status: draft for approval. Nothing here has been provisioned, and no credentials have been created.

## Problem

GitHub's `schedule` triggers for this repo arrive 1 to 6 hours late, and some never arrive. From October 23 to November 7, 2026, no scheduled engine run was created before 1:08 PM ET. Once a run exists, runners pick it up in about 2 seconds. So the slow part is GitHub creating the run on schedule. Starting the run at 10:00 from outside GitHub, with `workflow_dispatch`, avoids that step.

What it still depends on: GitHub accepting the API call and assigning a runner. Both are currently fast, but neither is guaranteed.

## Shape

```
10:00 ET  external timer ──POST workflow_dispatch──▶ nba-engine.yml
                         inputs: slate_date=2026-11-08, slot=10:00
~10:20 ET external timer ──GET runs?event=workflow_dispatch──▶ look for this slot's run
                         missing / failed / no fresh slate ──▶ alert
late      GitHub native cron 10:00 (kept as backup) ──▶ same workflow
                         guard sees slot already done ──▶ exit before paid calls
```

The native crons stay in place. If the dispatcher misses, the late native run still covers the slot, the same as today.

## Slot identity

Every run works out a **slot key** of the form `YYYY-MM-DD@HH:MM`, using the Eastern date and slot.

| Trigger | Where the key comes from |
| --- | --- |
| Dispatcher | Its explicit `slate_date` and `slot` inputs. The dispatcher computes the date in `America/New_York` at fire time. |
| Native cron | `github.event.schedule` with `nba_schedule.intended_fire()`. Each cron line now holds one time, so this is exact. |
| Manual run with no `slot` | No key. It always runs, and the existing `slate_date` default `next` still applies. |

The dispatcher always sends an explicit date and never relies on `next` or `today`. A run that fires near midnight still knows which slate it is for.

## Duplicate guard (prevents paying for the same odds twice)

The guard is a new check in `NBAEnginev5-4.py`. It runs right after the Run_Log logger is created (around line 165), before the first Odds API call (line 171 onward).

1. Run_Log gets a `slot_key` column, added the same way existing headers are added.
2. If this run has a slot key, read Run_Log. If a row with the **same slot key** and status `OK` or `WARN` exists, skip the run.
3. A prior `FAIL` or `CRASH` row for the slot does **not** block it. A failed morning can be retried by the backup trigger or by hand.
4. If a native trigger's slot date is no longer today in Eastern time (for example, a 9 PM trigger that arrives at 1:30 AM), it is **stale**. Skip it without paid calls. Today that run quietly processes the next day's slate under a `today` request.
5. A skipped run writes a `SKIP` row with a reason (`duplicate` or `stale`) and exits successfully. It must not fall into the current "no sheets written → FAIL" rule.
6. The key covers the date **and** the slot, so the 6 PM and 9 PM refreshes are separate keys and never block each other.

**No race between duplicate checks:** both triggers use the `nba-sheet-writer` concurrency group, so they run one after the other. The second run starts after the first has written its Run_Log row. Serialization stays in place because both the engine and the grader write the same Sheets.

**Known gap:** if the first run's Run_Log write fails, the second run won't see it and will run again. That costs one duplicate pull, which is better than a missed morning.

**Existing GitHub behavior to know about:** a concurrency group holds only one pending run. A newer pending run cancels the older one. For example, an 18:00 run still pending when a 21:00 run queues gets cancelled. The backup trigger and the watchdog limit the damage. Switching the concurrency key to per-slot would remove the serialization, so it is **not** proposed.

## Workflow changes (in this repo)

- Add a `slot` input to `workflow_dispatch` (optional, `HH:MM`).
- Add `run-name: NBA Engine ${{ inputs.slot && format('{0} {1}', inputs.slate_date, inputs.slot) || github.event.schedule || 'manual' }}` so the watchdog can find its own run by title.
- Pass `NBA_SLOT_KEY` to the engine from the trigger step, computed by `nba_schedule.py`.
- Keep the `slate_date` default `next` for manual runs.

## "Green" is not "published"

At about 10:20 ET, the watchdog checks three things:
1. A run for this slot exists, and its conclusion is `success`.
2. The `NBA_Status` sheet shows `SLATE_DATE` equal to the expected date, `STATUS` not `FAILED`, and `UPDATED_AT_UTC` after 10:00 ET. The dashboard already reads this sheet publicly as CSV, so this check needs no extra credentials.
3. If either check fails, send an alert. A green run that skipped as a duplicate counts as fine, because the earlier run for that slot already published.

## Credentials

- A **fine-grained GitHub personal access token**, limited to `stephenkrolikowskiUD/NBADesktop` only, with **Actions: Read and write** permission. No contents, secrets, or admin access.
  - Scope: that permission also lets the token cancel, re-run, or delete runs in this repo. It cannot read repo secrets or push code.
  - Expiry: 90 days, with a calendar reminder to rotate it.
- Stored only in the host's secret store (macOS Keychain, or a Worker secret). Never committed, logged, or placed in a URL.
- No Odds, Gemini, or Google credentials leave GitHub. The dispatcher only starts the workflow.

## Hosting options

| | A. Mac launchd job | B. Cloudflare Worker Cron Trigger | C. Approved Underdog scheduler (e.g. Zoomies schedule) |
| --- | --- | --- | --- |
| Uptime | Only when the Mac is awake and online. Weekend mornings are at risk. | Hosted. Cron triggers usually fire within seconds. | Hosted. Depends on the platform. |
| Cost | Free | Free tier covers this (2 calls/day) | Existing |
| New vendor? | No | **Yes. Needs a Zip intake first** (org policy) | No, if it can store a secret and call GitHub |
| DST | launchd uses local time, so 10:00 ET is automatic | Cron is UTC only. Fire at 14:00 and 15:00 UTC, and have the code act only when ET hour is 10 | Check the platform |
| Maintainer | You | You | You, plus the platform owners |
| Alerting | macOS notification or email | Email or Slack webhook | Platform-native |

Rejected: a second GitHub repo's cron (same scheduler problem). A Claude scheduled agent (would put an LLM and a token in the loop just to make one HTTP call).

**Recommendation:** start with **A** this week. It's free and needs no approval, and it covers weekday mornings. File a Zip intake for **B**, or confirm **C**, to cover mornings when the laptop is asleep.

## Tests required before enabling

Built offline, no live calls:
- DST: dispatcher date and native slot on Nov 1 and Mar 14, including the repeated and skipped hours (diagnostics tests already cover these).
- Midnight: a native 21:00 trigger delivered at 00:30 or 01:30 → stale → skip. A dispatcher firing at 23:59:59 vs 00:00:01 → correct date.
- Concurrent triggers: dispatched and native runs for the same slot, in either order → exactly one proceeds past the guard.
- Failed prior run: a `CRASH` row for the slot → the next trigger runs.
- Independent evening slots: an `OK` row for 18:00 does not block 21:00, and a 10:00 row does not block either.
- A skip writes `SKIP` and not `FAIL`, and makes zero Odds API calls (mock `requests.get` and assert it is not called).

## Rollout

1. Merge the guard, the `slot` input, and `run-name`, with tests. Native behavior stays the same apart from duplicate and stale skips.
2. Create the token (you) and install the chosen dispatcher (you approve the host).
3. Have the dispatcher fire one manual test at a time you choose. It costs one normal run of Odds credits.
4. Watch the next 3 mornings. Mark it verified only after a 10:00 dispatcher run shows the right slot key, start time, logs, and a published `NBA_Status` slate.
