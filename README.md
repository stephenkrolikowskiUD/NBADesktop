# NBA DFS Dashboard

## Publication evidence (2026-11-13)

### Historical reconstruction

Legacy ID matching correction: older pick-generation IDs differ from engine run IDs, and older logs have no publication_run_id link. Unrelated newer linked runs no longer disable timestamp reconstruction for that history. The fallback checks all overlapping engine runs, requires a unique interval, and rejects a conflicting linked ID. Successful picks writes and pregame completion remain mandatory. Direct receipt failures cannot be rescued.

A read-only audit of the November 13 retained data found 360 previously unmatched rows eligible for this legacy reconstruction; these are not necessarily settled picks or independent bets. Push this correction and rerun the NBA grader, then refresh the dashboard. No engine run or Odds API credits are required. Grades, selection policy, and directly verified publication records are unchanged.

The grader now reads Run_Log to classify older picks as RECONSTRUCTED_PREGAME_FEED when one matching engine run succeeded (OK/WARN, no error), logged a positive Daily_Picks write count, and finished before the stored game start. Matching uses run/publication ID first, then a unique explicit-timezone RUN_TIME interval for legacy rows. Ambiguous, missing, failed, or late evidence never becomes a pregame success. A failed or altered direct receipt cannot be rescued by reconstruction.

Publication_Audit records each row's cohort, source run, completion time, and reason. This is inference from retained logs, not a claim that historical selections were read back or viewed. It does not rewrite Daily_Picks or Publication_Ledger, alter grades, or relax performance eligibility. Run the updated grader to reconstruct available history; no engine run or Odds API credits are required for this classification. Coverage depends on retained run logs and game start times, and has not yet been measured against live Sheets.

The engine assigns each emitted pick a publication ID and verifies its saved player, market, side, line, captured price, and game start against Daily_Picks. After feed/status readback it appends a snapshot to Publication_Ledger and verifies that receipt. Failed verification fails the run and marks the slate failed.

The grader reports publication_cohort separately: VERIFIED_PREGAME_FEED, RECONSTRUCTED_PREGAME_FEED, UNVERIFIED_CAPTURE, and LATE_OR_UNVERIFIED. Stats shows these groups without relabeling the existing overall record. Research labels and selection rules are unchanged. Direct receipts verify availability in the research feed; reconstructed rows infer it from successful run logs. Neither proves a browser impression, dynamic shortlist selection, or predictive validation. Repeated publications are not independent bets; existing sport-specific performance eligibility/deduplication remains in effect.

Deployment: push the helper, grader, and frontend updates together. The updated grader can reconstruct historical evidence from retained Run_Log records without rerunning the engine. The updated engine creates direct receipts going forward. Historical rows are never retroactively marked directly verified. Missing evidence leaves captures unverified; unexpected ledger or run-log read errors stop grading rather than silently downgrade evidence.

Validation: offline publication fixtures cover readback mismatch, late publication, numeric round trips, immutable selection snapshots, and retry identity. Live Sheets publication and physical-device verification are still required after deployment.

### Compact slate banner (2026-11-13)

Replaced the large slate status card with a slim top banner. Slate date, update time, and stale/limited/failed status remain visible; tap to expand the full explanation. Text can wrap on narrow screens rather than clipping warnings. Frontend only; no engine run needed.

## Player analysis polish (2026-11-13)

- Mobile charts reserve space for bars and readable date/opponent labels, with horizontal scrolling contained inside the chart.
- Phone controls use 16px text, the header can wrap, and split cards wrap on narrow screens.
- H2H and home/away distinguish missing values from real zeroes, including incomplete combo metrics. Missing H2H no longer claims a first meeting.
- Recent-chart captions show the displayed and loaded game counts. Split sample sizes are explicitly unavailable until provided by the feed.
- Frontend only; no model, selection, grading, or engine changes. Deploy Pages and refresh. Dashboard regression tests cover zero/missing/combo split values; physical-device visual verification remains outstanding.

A personal NBA player-prop research dashboard for turning daily player data, live prop markets, AI picks, and grader feedback into one mobile-first view.

This repo is the GitHub Pages frontend for the NBA system. The engine writes data to Google Sheets, the grader closes the loop after games, and `index.html` reads the workbook through public Sheets CSV endpoints.

## What It Does

- Shows player logs, opponent context, home/away splits, teammate correlations, props, AI picks, and performance feedback.
- Supports combo-style prop work such as PRA, PR, PA, and RA alongside standard player props.
- Surfaces best bets, smart slips, streaks, due spots, heaters, Props, Stats, and Game Entry views.
- Displays Pick Performance analytics so confidence tiers, prop types, leans, CLV buckets, and ROI can be judged from graded history.
- Surfaces multi-book best-price routing when the engine has current prop data.

## How It Works

1. `NBAEnginev5-4.py` pulls NBA data, props, opponent context, and Gemini picks.
2. The engine writes dashboard tabs to the NBA Google Sheet.
3. `index.html` loads those tabs through Google Sheets CSV endpoints.
4. `NBAGrader5-4.py` grades completed picks and writes `HIT`, `ACTUAL_STAT`, and `RESULT` back to `Daily_Picks`.
5. Pick Performance turns that graded history into the Stats tab.

## Key Tabs

- **Dash**: selected-player context, logs, props, splits, matchup notes, and teammate context.
- **Log**: game-log focused view.
- **Picks**: AI picks, best bets, slips, streaks, due spots, props, and heaters.
- **Stats**: Pick Performance hit rate, ROI, CLV, confidence tiers, prop types, and drift checks.
- **Game Entry**: single-game auto-entry builder.
- **Info**: method notes and glossary.

## Run Mode

NBA runs through GitHub Actions on the Eastern-time schedule below, with manual workflow dispatch still available. NBA Stats requests can time out on runners; the engine uses verified ESPN fallbacks where configured.

## Data Sources

- Google Sheets workbook: `12gBgVx_RCsIytjZHjfZWgLtG-R-zcPbYYE-CVFd4EDw`
- NBA API through `nba_api`
- The Odds API
- Gemini output from the engine

## Current Experiments

- Pick Performance driven prompt tuning.
- Multi-book best-price routing.
- Game Entry, a fast single-game entry builder.
- Re-evaluating the NBA AST rule when the next regular season produces fresh data.

## Important Notes

- Keep the dashboard file named `index.html`; GitHub Pages depends on it.
- No private API keys live in this repo or in the HTML.
- Public Sheet IDs are identifiers, not secrets.
- This is a personal research tool, not betting advice.

## October 2026 Launch QA

The launch fixes retain Stats, Game Entry, multi-book pricing, and the existing Odds API cost controls. `nba_readiness.py`, `nba_sources.py`, and `nba_injuries.py` must be alongside the engine and grader, including when running in Colab. Install `requirements-nba.txt` before running.

The dashboard requires a dated, completed `NBA_Status` sheet from the updated engine. Deploy the engine and dashboard together; until the engine publishes that status, the dashboard withholds unverified or mixed snapshots. Availability that cannot be verified also withholds affected picks.

The engine runs daily at 10 a.m., 6 p.m., and 9 p.m. America/New_York time. The grader runs daily at 2 a.m. and 10 a.m.; the second pass catches late finals and corrections. Both retain manual dispatch and share a concurrency group to avoid overlapping sheet writes, including the simultaneous 10 a.m. triggers. Live authenticated execution and injury coverage still need launch QA.

Offline checks (no live sheet writes):

```sh
NBA_CACHE_DIR=/tmp/nba-qa-cache python3 -m unittest discover -s tests -q
node tests/test_nba_dashboard.cjs
```

## Review Follow-up

The review fixes allow off-day status pages to load alongside older historical tabs, publish ISO timestamps for Safari, and leave missing player boxes pending. Game Entry requires verified availability, no minutes restriction, a future game start, an exact market line, and valid odds. Player-name joins normalize accents and suffixes; unmatched injury-report names disable healthy-by-absence inference for the affected team. AI generation failures appear as publication warnings. GitHub Actions retains Sheet backups as artifacts for seven days, including after failed runs.

For staging, set `NBA_SHEET_ID` for both Python scripts (or the GitHub Actions repository variable). Open the dashboard with `?sheetId=STAGING_SHEET_ID`. With no override, all components use the existing production workbook. Stage the engine and frontend together: the frontend now requires `NBA_Status.UPDATED_AT_UTC` for freshness.

Published slate data stays visible as it ages. The header shows the engine update time and warns after four hours; age alone never clears picks, props, or history. Published picks remain visible after tipoff with lock badges, while started, unavailable, or minutes-restricted players are excluded from new entries. Rerun the engine when updated prices and injury reports are needed. Do not overlap Colab writers with the engine or grader; Actions concurrency cannot coordinate a separate Colab session. An authenticated Python 3.12 staging run, current official injury PDF access/layout, postponed/suspended ESPN coverage, real API quota consumption, and actual iPhone QA remain release requirements. Direct dependency pins are not a complete transitive lockfile.

Selection identity intentionally includes line, book, and event. A changed market can be a distinct selection, while an identical market is not appended again. Latest-run convergence does not currently record a separate observation when Gemini repeats an existing selection; that behavior needs a separate observation log rather than duplicate graded picks.

## New-season Launch Tracking

The grader publishes season-specific tabs such as `Pick_Performance_2026_27` and `Pick_Performance_2026_27_Snapshots`. The original historical tabs and Daily_Picks rows are retained. Performance excludes other seasons, duplicate selections, unvalidated markets, invalid odds, missing book/event identity, stale quotes, and unverified publication timing. New engine picks record UTC publication time, market-validation policy, and baseline/sample metadata. Returns use equal units at each recorded entry price; these are research-selection returns, not a record of placed wagers. Closing-line metrics remain unavailable until closing quote provenance is verified.

Game Entry uses equal weights for every AI tier and metric. Historical Wilson scores cannot affect its ranking. AI labels remain visible as unvalidated research labels. No AST/PR penalties, baseline blending, or new AI-agreement filters were added.

The frontend reads `Dashboard_Player_Stats`, containing only tonight's roster and each player's explicitly selected analysis season (prior season until five current-season games, where prior history exists), plus the columns needed by existing views. Full `Player_Stats` remains the engine/grader history. The new feed is resized after a successful write to avoid unused grid allocation. This reduces frontend payload and allocation; it does not establish that the entire workbook is below Google's capacity limit. Deploy the engine before switching the frontend, and verify the new feed in the staging workbook.

`research/2025-26/` contains an unchanged original ledger and a deduplicated research copy with five verified regrades. Unsupported historical lines are flagged; none qualifies for new-season performance. Claude's claimed ten regrades cannot be reconciled without the row-level list, so the manifest records this unresolved item. These are local import-ready exports; no live historical tab has been created.

Additional offline contract check:

```sh
NBA_CACHE_DIR=/tmp/nba-qa-cache NBA_BACKUP_DIR=/tmp/nba-qa-backups python3 tests/export_engine_fixture.py /tmp/nba-contract.json
NBA_CONTRACT_FIXTURE=/tmp/nba-contract.json node tests/test_nba_dashboard.cjs
```

Authenticated staging still requires credentials and a separate workbook: run the engine, load the dashboard with `?sheetId=STAGING_ID`, and run the grader with the same `NBA_SHEET_ID`. Verify fresh and off-day states, eligible performance, exact prices, prior-season context, game locks, and Safari/mobile behavior before production publication. Offline contract checks do not establish live API coverage or deployment readiness.

## Upcoming Slates

The engine defaults to `NBA_SLATE_DATE=next`: it selects the first date with an upcoming regular-season game, looking from today through the next seven days. Use `NBA_SLATE_DATE=today` to force today's slate, or an explicit Eastern date such as `2026-10-20` for opening day. Invalid dates, past dates, and dates beyond seven days are rejected. A failed schedule lookup stops selection rather than silently skipping an unknown date.

GitHub Actions exposes this as the **slate_date** field under **Run workflow**, defaulting to `next`. On Monday October 19, running with `next` or `2026-10-20` selects Tuesday's opening slate. The dashboard uses the published target date consistently for player rows, AI picks, convergence, and Game Entry, and shows the date in the feed status. Publication/quote timestamps remain actual UTC times; future game statistics are never fabricated.

On GitHub runners, seeded player history is refreshed through ESPN final boxes first, avoiding known stats.nba.com timeouts. `NBA_PLAYER_LOG_SOURCE=nba` restores NBA Stats as the first source if needed. The complete historical seed remains available for explicitly labeled prior-season context.

Targeting tomorrow does not bypass availability or market validation. Official reports must cover the target slate and still be fresh at run time. Missing reports, missing quoted lines, or restricted players can therefore leave picks withheld. Rerun when those inputs are available and again before tipoff; the dashboard keeps the saved snapshot visible with its age and an aging-data warning. Engine quote and injury freshness checks still apply when generating new picks.

The injury reader reconstructs PDF rows from their displayed coordinates, including reports whose extraction order is reversed. Regression tests include the public October 19, 2026 4:30 p.m. report. Teams marked `NOT YET SUBMITTED` remain unknown; a submitted team's unlisted players can pass the existing availability checks. Engine logs show report time, pending teams, availability counts, and report-fetch/parser failures. Game-level Vegas context is restricted to the target Eastern date and exact scheduled matchup before team rows are merged.

Team logos are served locally from `assets/team-logos/`, sourced from the official NBA logo CDN. Abbreviations remain visible when an image is unavailable.

ESPN roster fallback uses verified numeric team IDs for all 30 teams, avoiding NBA/ESPN abbreviation differences such as NOP/NO. Returned roster team identity is still checked. Uncaught engine errors publish `FAILED` when possible and record `CRASH` in Run_Log; the original traceback and failing process exit remain visible.

GitHub schedules use `timezone: America/New_York`, following Eastern daylight-saving changes. The 2 a.m. grader advances to the next valid time on spring-forward day. Scheduled engine runs target `today`; manual runs retain the `slate_date` input and default `next`. Schedules become active after these workflows are pushed to the default branch.

### Scheduled run timing: actual behavior (checked November 7, 2026)

GitHub `schedule` triggers are best-effort, and in this repo they arrive hours late. GitHub's docs say scheduled runs can be delayed under load (especially at the top of the hour) and can be dropped.

- From October 23 to November 7, no scheduled engine run was created before 1:08 PM ET. The 10 AM engine run usually shows up between 1 and 4:30 PM, a few minutes after the grader's 10 AM run. Evening runs show up 2 to 5.5 hours late. Some days have fewer runs than scheduled.
- The lateness happens before any runner is involved. The run record is created late, then jobs start about 2 seconds later and runs finish within about 8 minutes. Nothing waits on the `nba-sheet-writer` group for long.
- It is not caused by `timezone:`. In May 2026 the crons were plain UTC (some at :15 and :45 past the hour) and were still 1 to 4 hours late.
- Splitting the cron entries (November 2) did not change delivery. Each time now has its own entry only so that `github.event.schedule` tells us which slot fired.

Each run starts with a **Log trigger** step (`nba_schedule.py`). It prints the event, cron, slot, slot date, minutes late, run ID, SHA, and requested slate to the log and the job summary. It reads no secrets and does not change the slate. A late 9 PM trigger that arrives after midnight is flagged `crossed_date=yes`. Because scheduled runs use `today`, that run processes the new day's slate, not the evening it was meant to refresh.

A green run does not mean a fresh slate. Check `NBA_Status` and `Run_Log` too. If the dashboard must refresh at 10:00 sharp, an external trigger that calls `workflow_dispatch` is needed; GitHub's native cron cannot promise that. This is proposed and has not been set up.

Assists (AST) are eligible for AI selection alongside other supplied metrics. They use the same quoted-market and availability validation and the existing three-picks-per-metric cap; there is no AST-wide blacklist.

October 24 reliability follow-up: the ESPN history refresh reports and skips malformed records while retaining verified seed rows, and retries a seven-day correction window. The grader's final-box reader remains strict by default. Scheduled runners explicitly use ESPN for seeded game logs. All 30 roster IDs are checked against ESPN's team identities.

Early-season analysis uses an unmixed prior season until five current-season games are available; current-only players retain their actual limited sample. Hit-rate cells show the source season and sample size. Existing prior-season edge discounts and market/injury validation remain in effect. Gemini HTTP 402 prepaid-credit failures stop additional model passes and appear explicitly in NBA_Status and the AI Picks empty state. Adding credits to the configured Google AI Studio project is an account action the user must complete; code changes cannot clear that billing block.
# October 30 Injury Feed Repair

- Official injury fetching now prefers report links published on the current
  season's NBA index, then uses the existing bounded filename fallback. Only
  approved NBA PDF URLs and reports within six hours are accepted. Player
  availability remains unknown when official access fails.
- The official index returned HTTP 403 during local diagnosis. Discovery and
  parser regressions pass, but live recovery is NOT verified; this code cannot
  guarantee restoration of an upstream denied request. The existing explicit
  injury CSV input remains available for a verified dated report.
- Dashboard picks must match the current publication RUN_ID. Failed generation
  cannot reuse earlier same-day picks. Daily_Picks remains append-only history.
- Removed the hardcoded May 4 snapshot label; the summary now identifies the
  actual target slate and run. No live engine or Sheets writes during repair.

### November 2 selection audit repair

- Removed UNDER-only quotas and LEAN rejection. Gemini receives both sides'
  estimated returns; PRA is checked against the selected side, not OVER edge.
- Historical non-push rates are shrunk toward the two-sided no-vig market
  probability using 20 prior observations. Prior-season/returning reliability
  reduces effective historical weight, not just the displayed edge.
- Exact-market-validated candidates must have positive side edge and estimated
  return of at least 5% (8% for PRA). These are explicit provisional policy
  settings, not backtest-validated thresholds or guaranteed profits.
- No confidence promotion from repeated Gemini answers. New qualified picks
  remain LEAN until higher confidence is supported by prospective evidence.
- Rows record MODEL_POLICY=nba-symmetric-market-prior-v1, MODEL_PROBABILITY,
  MODEL_EV_PCT and MODEL_GAMES. Existing historical grades are unchanged.
- Gemini still nominates candidates; this repair does not establish exhaustive
  market coverage or calibrated predictive accuracy. Live deployment and an
  engine run are required before the board changes.

### Publication correctness repair (2026-11-09)

- Revalidated selections are published under each new run ID, even when the
  same selection appeared earlier that day. Deduplication applies within a
  publication, not across publications. Old rows and entry prices are retained;
  the grader's existing selection-identity deduplication counts the first
  eligible graded publication once, not each refresh as a separate bet.
- This is not automatic carry-forward: a selection must pass the current run's
  existing eligibility, exact-market, model and pre-tipoff checks again.
- Before final status publication, the engine reads back Daily_Picks and checks
  that the current run contains exactly the expected selection identities.
  A mismatch publishes FAILED and exits with an error rather than reporting READY.
- A credit-guard abort records the credit warning, publishes FAILED and exits
  nonzero. Missing Gemini credentials produce a DEGRADED publication with a
  reason instead of silently implying that pick generation succeeded.
- Offline verification: 86 Python tests and the dashboard regression suite pass.
  No live engine, paid API request, or Google Sheets write was used for testing.
  Push these changes and run the engine to deploy; a second successful run should
  retain any repeated qualifying picks under the latest NBA_Status RUN_ID.
  These changes do not repair GitHub schedule delivery or alter model policy.

### Grading and history integrity repair (2026-11-09)

- The grader installs the crash logger and raises an error on missing required
  grading columns. An empty ledger remains a normal no-work case.
- Verified final player boxes can be graded even when other players or games
  have incomplete data. Gaps produce Run_Log warnings; missing participation
  evidence never becomes an automatic DNP or loss. Unresolved missing boxes
  older than seven days are flagged for review and remain retryable.
- Before writing grades, the grader re-reads the ledger and rejects edits or
  reorders to its original snapshot. Appended rows are allowed. This is an
  optimistic safety check, not an atomic Sheets transaction: avoid manual edits
  during grading, and keep engine/grader writes serialized.
- Name matches must be unambiguous; an explicitly mismatched team is rejected.
  Invalid line/side/stat inputs are warned about rather than written as grades.
- Mixed ESPN fallback IDs and NBA IDs are reconciled only when normalized names
  and an overlapping game date/team agree, with exactly one positive NBA ID.
  Two positive IDs or an unproven bridge fail closed. Matching records are
  deduplicated before metrics; the newest supplied record wins. This also repairs
  already mixed seeds when sufficient evidence exists, without name-only merging.
- These repairs do not change pick probabilities, thresholds, or scheduling.
  Validation uses mocked APIs and Sheets only; production history has not been
  rewritten. Push, then run the engine and grader to apply the repairs live.

### Player panel corrections (2026-11-11)

The player panel corrections on 2026-11-11 let Home/Away and H2H mini charts
grow to fit bars and labels without overlapping headings. L5/L10 hit counts
use available games, capped at five/ten. Season scope and H2H calculations
are unchanged. Frontend deployment and refresh only; no engine run required.
Regression coverage includes one, three, nine and twelve available games.

### Static Pages deployment (2026-11-10)

The root `.nojekyll` file disables Jekyll processing for this plain HTML/JS
dashboard. Keep it tracked: documentation contains GitHub Actions expressions
that Liquid otherwise interprets as invalid template syntax. This change needs
a Pages deployment, not an engine or grader run.

### External 10 AM dispatcher (2026-11-11)

Dispatcher v2 fixes a Workers runtime incompatibility: `redirect: "error"` threw
before sending GitHub requests. Use `manual` and reject redirects without
forwarding credentials. The authenticated POST /connectivity endpoint tests
GitHub read access without starting an engine. Error diagnostics identify the
operation and exception category without exposing tokens. Redeploy Worker code;
keep existing cron triggers and GITHUB_TOKEN. Production success remains unverified.

Cloudflare dispatcher code and slot guards are implemented locally. **Not yet
deployed or verified unattended.** Follow [the activation guide](docs/SCHEDULE_DISPATCHER_SETUP.md)
after pushing these changes. The personal Cloudflare Worker needs the new code
and two UTC cron triggers; creating a Hello World Worker is not sufficient.

The dispatcher sends an explicit Eastern slate date and 10:00 slot. Native GitHub
crons remain backups. A pre-API guard skips a duplicate only after a verified
READY/NO_GAMES publication; failed/degraded slots remain retryable. Run_Log and
NBA_Status carry the slot and publication identity. The 10:20 watchdog checks
both GitHub and the published status. Notifications require a separate personal
webhook; until configured, failures are logged only. All tests are offline and
no paid engine run was triggered as part of implementation.

### Pregame props and diagnostic safety (2026-11-10)

- Paid player-props requests exclude started games and unknown/invalid start
  times. Start time is checked again before every market batch.
- Timeouts, HTTP errors and malformed response structures are isolated per
  batch. Successful pulls remain available; failed coverage is recorded in
  NBA_Status and Run_Log and produces DEGRADED rather than a normal empty-market
  state. A low-credit abort still stops the run explicitly.
- Gemini is not called when there are no priced player markets.
- Engine/grader console diagnostics, uncaught errors handled by RunLogger,
  persisted Run_Log diagnostics and NBA_Status warnings redact registered API
  secrets and credential query parameters. Environment, Colab and prompted
  engine keys are registered. This does not retroactively clean historical logs
  or control third-party library logging; rotate any previously exposed key.
- Include the new nba_markets.py and nba_security.py modules in the commit.
  All verification is offline with mocked APIs/Sheets; model policy and workflow
  schedules are unchanged.
