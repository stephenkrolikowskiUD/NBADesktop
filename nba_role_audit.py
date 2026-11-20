"""Pregame role diagnostics and reproducible accuracy audit; no policy tuning."""
import argparse
import csv
import gzip
import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROLE_FIELDS = ['ROLE_AUDIT_POLICY', 'ROLE_HISTORY_N', 'ROLE_RECENT_MIN',
               'ROLE_EARLIER_MIN', 'ROLE_MIN_DELTA', 'ROLE_RECENT_USAGE',
               'ROLE_EARLIER_USAGE', 'ROLE_TREND', 'ROLE_TEAM_CHANGED']


def number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def name(value):
    text = unicodedata.normalize('NFKD', str(value))
    return re.sub(r'[^a-z0-9]', '', ''.join(c for c in text if not unicodedata.combining(c)).lower())


def mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def role_context(logs, before_date):
    """Last three versus previous ten positive-minute games, never same-day data.

    Expansion/contraction thresholds are diagnostic bins, not validated gates.
    Usage is an opportunity-per-minute proxy, not official usage percentage.
    """
    by_date = {}
    for row in logs:
        day = str(row.get('GAME_DATE', ''))[:10]
        minutes = number(row.get('MIN'))
        if day and day < before_date and minutes is not None and minutes > 0:
            by_date.setdefault(day, row)
    rows = [by_date[k] for k in sorted(by_date, reverse=True)][:13]
    recent, earlier = rows[:3], rows[3:]
    rm, em = mean([number(r.get('MIN')) for r in recent]), mean([number(r.get('MIN')) for r in earlier])
    def usage(group):
        usable = [r for r in group if all(number(r.get(k)) is not None for k in ('FGA', 'FTA', 'TOV'))]
        return (sum(float(r['FGA']) + .44 * float(r['FTA']) + float(r['TOV']) for r in usable)
                / sum(float(r['MIN']) for r in usable)) if usable else None
    delta = rm - em if rm is not None and em is not None else None
    enough = len(recent) == 3 and len(earlier) >= 5
    trend = 'INSUFFICIENT'
    if enough:
        threshold = max(3., .15 * em)
        trend = 'EXPANDED' if delta >= threshold else ('CONTRACTED' if delta <= -threshold else 'SIMILAR')
    teams = {str(r.get('TEAM_ABBREVIATION', '')).strip() for r in rows} - {''}
    return dict(zip(ROLE_FIELDS, ['recent3-earlier10-diagnostic-v1', len(rows), rm, em, delta,
                                usage(recent), usage(earlier), trend, len(teams) > 1]))


def write_snapshot(path, payload):
    """Run artifact contains inputs, not a publication receipt or validation claim."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, 'wt', encoding='utf-8') as handle:
        json.dump(payload, handle, default=str, allow_nan=False)


def replay_snapshot(payload, estimator):
    """Replay numeric estimates only; reject late snapshots and changed policies."""
    if payload.get('schema') != 'nba-accuracy-inputs-v1' or payload.get('model_policy') != 'nba-symmetric-market-prior-v1':
        raise ValueError('Unsupported snapshot or model policy')
    def instant(value):
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError('Timestamp must include timezone')
        return parsed
    captured = instant(payload['captured_at'])
    output = []
    for row in payload['candidates']:
        try:
            start, quote = instant(row['commence_time']), instant(row['quote_time'])
            if not quote <= captured < start:
                raise ValueError('Late snapshot or future quote')
            histories = row['history']
            if any(str(r.get('GAME_DATE', ''))[:10] >= payload['slate_date'] or not r.get('GAME_DATE') for r in histories):
                raise ValueError('Same-day/future or undated history')
            estimates = estimator([r.get(row['metric']) for r in histories], row['line'],
                                  row['over_odds'], row['under_odds'], row['reliability'])
            output.append({'player': row['player'], 'metric': row['metric'],
                           'status': 'replayed', 'estimates': estimates})
        except (KeyError, ValueError, TypeError) as exc:
            output.append({'player': row.get('player'), 'status': 'excluded', 'reason': str(exc)})
    return output


def score(rows):
    decisive = [r for r in rows if r['RESULT'] in {'YES', 'NO'}]
    settled = [r for r in rows if r['RESULT'] in {'YES', 'NO', 'PUSH'}]
    priced = [r for r in settled if number(r.get('ODDS')) is not None and abs(float(r['ODDS'])) >= 100]
    profit = sum((100 / abs(float(r['ODDS'])) if float(r['ODDS']) < 0 else float(r['ODDS']) / 100)
                 if r['RESULT'] == 'YES' else (-1 if r['RESULT'] == 'NO' else 0) for r in priced)
    return {'selected': len(rows), 'decisive': len(decisive),
            'wins': sum(r['RESULT'] == 'YES' for r in decisive),
            'hit_rate': mean([float(r['RESULT'] == 'YES') for r in decisive]),
            'priced_settled': len(priced), 'net_units': profit,
            'roi_pct': 100 * profit / len(priced) if priced else None}


def audit(selections, ledger, logs):
    """Use standardized first-pregame selections; join ledger only after identity check."""
    grouped_logs = defaultdict(list)
    for row in logs:
        grouped_logs[name(row.get('PLAYER_NAME'))].append(row)
    selected, rejected = [], Counter()
    seen = set()
    for row in sorted(selections, key=lambda r: r.get('PUBLISHED_AT', '')):
        if row.get('POLICY') != 'first-pregame-selection-v1' or row.get('EVIDENCE') not in {'DIRECT', 'RECONSTRUCTED', 'RECORDED_PUBLICATION'}:
            rejected['unsupported_evidence'] += 1
            continue
        key = tuple(row.get(k) for k in ('SURFACE', 'GAME_ID', 'PLAYER', 'MARKET', 'SIDE'))
        if key in seen:
            rejected['repeat'] += 1
            continue
        seen.add(key)
        enriched = dict(row)
        enriched.update(role_context(grouped_logs[name(row['PLAYER'])], row['GAME_DATE']))
        # Source rows can shift after an edit; never silently attach another pick's estimate.
        index = number(row.get('SOURCE_ROW'))
        source = ledger[int(index) - 2] if index is not None and index.is_integer() and 2 <= index < len(ledger) + 2 else {}
        match = (name(source.get('player')) == name(row['PLAYER'])
                 and source.get('prop_type') == row['MARKET'] and source.get('lean') == row['SIDE']
                 and number(source.get('line')) == number(row['LINE'])
                 and source.get('EVENT_ID') == row['GAME_ID'])
        enriched['MODEL_PROBABILITY'] = number(source.get('MODEL_PROBABILITY')) if match else None
        enriched['MODEL_POLICY'] = (source.get('MODEL_POLICY') or 'Unrecorded') if match else 'Unmatched'
        if not match:
            rejected['ledger_join_unavailable'] += 1
        selected.append(enriched)
    days = sorted({r['GAME_DATE'] for r in selected})
    cutoff = days[min(len(days) - 1, int(len(days) * .7))] if days else None
    groups = defaultdict(list)
    for row in selected:
        period = 'earlier' if row['GAME_DATE'] < cutoff else 'later'
        for dimension, value in [('market_side', row['MARKET'] + '/' + row['SIDE']),
                                  ('role_side', row['ROLE_TREND'] + '/' + row['SIDE']),
                                  ('period', period), ('model_policy', row['MODEL_POLICY'])]:
            groups[dimension, value].append(row)
    calibration = defaultdict(list)
    for row in selected:
        p = row['MODEL_PROBABILITY']
        if p is not None and 0 <= p <= 1 and row['RESULT'] in {'YES', 'NO'}:
            bucket = min(9, int(p * 10))
            calibration[row['MODEL_POLICY'], bucket].append(row)
    calibrated = []
    for (policy, bucket), rows in sorted(calibration.items()):
        calibrated.append({'policy': policy, 'bin': f'{bucket * 10}-{(bucket + 1) * 10}%',
                           'n': len(rows), 'mean_probability': mean([r['MODEL_PROBABILITY'] for r in rows]),
                           'hit_rate': mean([float(r['RESULT'] == 'YES') for r in rows]),
                           'brier': mean([(r['MODEL_PROBABILITY'] - float(r['RESULT'] == 'YES')) ** 2 for r in rows])})
    return {'overall': score(selected), 'date_range': [days[0], days[-1]] if days else [],
            'diagnostic_cutoff': cutoff, 'audit_counts': dict(rejected),
            'groups': [{'dimension': d, 'value': v, **score(rows)} for (d, v), rows in sorted(groups.items())],
            'calibration': calibrated, 'role_history_covered': sum(r['ROLE_TREND'] != 'INSUFFICIENT' for r in selected),
            'limitations': ['Role histories reconstructed from current game-log export; not archived point-in-time inputs.',
                           'No historical injury causality or expected-minutes forecast can be established from these logs.',
                           'Earlier/later split is descriptive, not an untouched holdout or full candidate replay.',
                           'Opposite-side ROI and no-vig calibration require paired entry prices; never infer them from losing picks.',
                           'Correlated selections are not independent samples. No thresholds fitted by this audit.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for arg in ('selections', 'ledger', 'logs', 'output'):
        parser.add_argument('--' + arg, required=True)
    args = parser.parse_args()
    def read(path):
        with open(path, encoding='utf-8-sig', newline='') as handle:
            return list(csv.DictReader(handle))
    result = audit(read(args.selections), read(args.ledger), read(args.logs))
    Path(args.output).write_text(json.dumps(result, indent=2, allow_nan=False) + '\n')
    print(json.dumps({k: result[k] for k in ('overall', 'date_range', 'role_history_covered', 'audit_counts')}, indent=2))


if __name__ == '__main__':
    main()
