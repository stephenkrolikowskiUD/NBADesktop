# NBA accuracy audit - 2026-11-20

## Scope and evidence

Read-only exports of Comparison_Selections, Daily_Picks and Player_Stats were
examined. The standardized first-selection cohort covers October 20 through
November 19, 2026. No engine run, Sheet write, or retrospective outcome change
was performed. Historical player context is reconstructed from today's log
export, not an archived copy of what the engine knew at selection time.

## Findings

1. Direction selection is overwhelmingly asymmetric: 427 of 433 selections
   were overs. The numeric estimator evaluates both sides, but Gemini controls
   the candidate set that reaches final qualification. This is a candidate
   selection concern, not evidence that all rejected unders are profitable.
2. The cohort has 422 decisive results, 200 wins (47.4%), and -9.86% return at
   recorded prices. RA overs won 12 of 39 (-41.0% return); PR overs won 15 of
   36 (-21.0%); PTS overs won 26 of 66 (-26.8%). These are descriptive slices,
   not independently validated rules for choosing the opposite side.
3. Role expansion is plausible but not established as the cause of under
   losses. Both expanded-role unders lost, but there are only two. Expanded-role
   overs won 25 of 44; contracted-role overs won 14 of 38. Role classification
   compares the last three positive-minute games with up to ten earlier games
   (at least five required), using a change of at least three minutes or 15%.
   Teammate availability at publication was not archived, so no causal injury
   conclusion is supported. Usage is a box-score proxy, not official usage rate.
4. Recorded probabilities are overconfident in the newer policy cohort.
   The 60-70% bucket averaged 63.0% predicted probability but won 47.1% across
   68 decisive picks. The 50-60% bucket averaged 56.4% and won 41.7% across 96.
   Missing historical probabilities were not reconstructed or invented.
5. A selection-order defect applied the three-per-market cap before market
   validation and model qualification. Rejected candidates could consume slots
   and suppress later qualified candidates. PR/PRA preference also ran too early.

## Changes implemented

- Apply the existing market cap and PR/PRA preference only after qualification,
  in estimated-EV order. Record SELECTION_POLICY=qualified-ev-ranked-cap-v1.
- Persist recent/earlier minutes, usage proxy, sample count, role trend and team
  change diagnostics on new picks. These fields do not alter probabilities.
- Add a reproducible CSV audit and a numeric snapshot replay validator. Replay
  rejects late captures, invalid timestamps and same-day/future game history.
- Keep model probabilities and EV thresholds unchanged. No automatic injury
  boost, forced under quota, or reversal of losing market categories.

## Chronological validation limits

A descriptive split through November 11 versus later dates gives 45.5% versus
54.4% hit rates and -13.9% versus +5.1% recorded-price returns. The later segment
is not an untouched holdout: policy changes and correlated selections prevent
interpreting this as proof of improvement. No thresholds were fitted to it.

An honest full replay still needs archived candidate sets, paired prices,
quote timestamps, historical inputs and injury/role context captured before
tipoff. The replay helper tests numeric reproducibility, not Gemini candidate
selection or future profitability. Automatic GitHub artifact export is not
wired: explicit approval for its storage/access boundary is pending.

## Reproduce locally

```sh
python3 nba_role_audit.py --selections comparison-selections.csv \
  --ledger daily-picks.csv --logs player-stats.csv --output audit.json
```

Next: collect approved point-in-time inputs, compare candidate selection against
both-side numeric coverage, and evaluate frozen rules on later dates. Do not
optimize probability corrections on this same reporting sample.
