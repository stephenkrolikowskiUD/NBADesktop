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
