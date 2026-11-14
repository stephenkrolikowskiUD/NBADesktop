# Cross-Sport Comparison Policy

Policy ID: first-pregame-selection-v1

NBA, NHL and NFL graders produce Comparison_Performance and Comparison_Audit using the same comparison_reporting.py implementation. Existing Pick_Performance/dashboard headline reports remain sport-specific; they are NOT relabeled as standardized comparisons.

## Counting rules

- Count the earliest evidenced pregame selection per sport, surface, game, player, market and side. Subsequent line, price, model and board-refresh changes do not add another exposure.
- Choose that first record before checking its outcome or price. An unresolved, DNP or unpriced first record cannot be replaced with a convenient later result.
- OVER and UNDER remain separate selections. Different markets and players remain separate but can be correlated; these counts are not independent bets or simulated parlays.
- Use game date in America/New_York for reporting windows: N dates including today. Exclude future games. All-time includes retained history, not a common season across sports.
- Wins/(wins + losses) is hit rate. PUSH and DNP are excluded from that denominator. Pending/unresolved records remain pending.
- Recorded-price ROI risks one unit per priced settled selection. Wins use the original captured American odds, losses lose one unit, pushes return the stake and count in stakes. DNP and missing/invalid prices are excluded from ROI, never assigned default odds.
- Require publication strictly before game start. Missing identities, malformed lines, unknown timing and unsupported sides are excluded with reasons.
- Use source player names normalized for case/whitespace; records without a name are excluded. Legacy naming changes may prevent deduplication and are not guessed.

## Evidence and surfaces

PUBLIC_BOARD (NFL) and RESEARCH_FEED (NBA/NHL) stay separate. Feed availability is not proof of shortlist selection or user viewing.

DIRECT means verified feed receipt. RECONSTRUCTED means a matching successful engine write completed before the game. RECORDED_PUBLICATION is NFL's explicit board publication record; it is not relabeled as a verified readback receipt. Each surface has an ALL aggregate and separate evidence groups. Do not sum the ALL row with its evidence rows.

This comparison policy measures recorded pregame selections, not predictive validation. It does not import one sport's model-quality gates into another, change grades, infer missing prices, or rewrite publication history.

## Deployment and review

Push each repository and run its grader, then inspect Comparison_Performance and Comparison_Audit in each Google Sheet. No engine run or Odds API calls are required. SOURCE_ROW refers to the source Daily_Picks sheet row for that snapshot. Empty outputs replace stale comparison tabs.

The common report is additive. Existing dashboard headlines retain their existing policy until a separate UI migration explicitly switches their data source and labels. Use Comparison_Performance, not those legacy headlines, for cross-sport comparisons.

