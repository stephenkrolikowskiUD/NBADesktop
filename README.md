# NBA DFS Dashboard

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

NBA currently runs manually in Colab. GitHub Actions workflow files exist, but `nba_api` calls are not reliable from GitHub Actions IP ranges.

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

The dashboard requires a fresh `NBA_Status` sheet from the updated engine. Deploy the engine and dashboard together; until the engine publishes that status, the dashboard deliberately hides stale actionable data. Availability that cannot be verified also withholds affected picks.

Workflows remain manual. Engine and grader share a concurrency group to avoid overlapping sheet writes. Live authenticated execution and injury coverage still need launch QA.

Offline checks (no live sheet writes):

```sh
NBA_CACHE_DIR=/tmp/nba-qa-cache python3 -m unittest discover -s tests -q
node tests/test_nba_dashboard.cjs
```

## Review Follow-up

The review fixes allow off-day status pages to load alongside older historical tabs, publish ISO timestamps for Safari, and leave missing player boxes pending. Game Entry requires verified availability, no minutes restriction, a future game start, an exact market line, and valid odds. Player-name joins normalize accents and suffixes; unmatched injury-report names disable healthy-by-absence inference for the affected team. AI generation failures appear as publication warnings. GitHub Actions retains Sheet backups as artifacts for seven days, including after failed runs.

For staging, set `NBA_SHEET_ID` for both Python scripts (or the GitHub Actions repository variable). Open the dashboard with `?sheetId=STAGING_SHEET_ID`. With no override, all components use the existing production workbook. Stage the engine and frontend together: the frontend now requires `NBA_Status.UPDATED_AT_UTC` for freshness.

The four-hour freshness limit still applies to manual operation: rerun before it expires, including before the evening slate. Do not overlap Colab writers with the engine or grader; Actions concurrency cannot coordinate a separate Colab session. An authenticated Python 3.12 staging run, current official injury PDF access/layout, postponed/suspended ESPN coverage, real API quota consumption, and actual iPhone QA remain release requirements. Direct dependency pins are not a complete transitive lockfile.

Selection identity intentionally includes line, book, and event. A changed market can be a distinct selection, while an identical market is not appended again. Latest-run convergence does not currently record a separate observation when Gemini repeats an existing selection; that behavior needs a separate observation log rather than duplicate graded picks.

## New-season Launch Tracking

The grader publishes season-specific tabs such as `Pick_Performance_2026_27` and `Pick_Performance_2026_27_Snapshots`. The original historical tabs and Daily_Picks rows are retained. Performance excludes other seasons, duplicate selections, unvalidated markets, invalid odds, missing book/event identity, stale quotes, and unverified publication timing. New engine picks record UTC publication time, market-validation policy, and baseline/sample metadata. Returns use equal units at each recorded entry price; these are research-selection returns, not a record of placed wagers. Closing-line metrics remain unavailable until closing quote provenance is verified.

Game Entry uses equal weights for every AI tier and metric. Historical Wilson scores cannot affect its ranking. AI labels remain visible as unvalidated research labels. No AST/PR penalties, baseline blending, or new AI-agreement filters were added.

The frontend reads `Dashboard_Player_Stats`, containing only tonight's roster and each player's explicitly selected analysis season, plus the columns needed by existing views. Full `Player_Stats` remains the engine/grader history. The new feed is resized after a successful write to avoid unused grid allocation. This reduces frontend payload and allocation; it does not establish that the entire workbook is below Google's capacity limit. Deploy the engine before switching the frontend, and verify the new feed in the staging workbook.

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

Targeting tomorrow does not bypass availability or market validation. Official reports must cover the target slate and still be fresh at run time. Missing reports, missing quoted lines, or restricted players can therefore leave picks withheld. Rerun when those inputs are available and again before tipoff; the four-hour dashboard freshness limit remains in effect.

The injury reader reconstructs PDF rows from their displayed coordinates, including reports whose extraction order is reversed. Regression tests include the public October 19, 2026 4:30 p.m. report. Teams marked `NOT YET SUBMITTED` remain unknown; a submitted team's unlisted players can pass the existing availability checks. Engine logs show report time, pending teams, availability counts, and report-fetch/parser failures. Game-level Vegas context is restricted to the target Eastern date and exact scheduled matchup before team rows are merged.
