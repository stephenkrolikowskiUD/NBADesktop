"""Describe which scheduled slot a GitHub Actions run belongs to and how late it arrived.

Diagnostics only: this module never changes which slate the engine processes.
GitHub exposes the triggering cron string as ``github.event.schedule``. It
identifies a time of day, not the original date of a delayed trigger. The engine
uses nba_dispatch.py separately to reject stale inferred dates and deduplicate.

Usage in a workflow step (no secrets needed):
    python nba_schedule.py   # reads GITHUB_EVENT_NAME, EVENT_SCHEDULE, GITHUB_RUN_ID, ...
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo('America/New_York')


def _field(text, low, high):
    values = set()
    for part in text.split(','):
        value = int(part)
        if not low <= value <= high:
            raise ValueError(f'cron value {value} outside {low}-{high}')
        values.add(value)
    return sorted(values)


def parse_daily_cron(cron):
    """Parse 'M H * * *' (comma lists allowed). Other cron shapes are rejected."""
    parts = (cron or '').split()
    if len(parts) != 5 or parts[2:] != ['*', '*', '*']:
        raise ValueError(f'unsupported cron for slot diagnostics: {cron!r}')
    return _field(parts[0], 0, 59), _field(parts[1], 0, 23)


def intended_fire(cron, now, tz=EASTERN):
    """Most recent nominal fire time of a daily cron at or before ``now``, in ``tz``.

    A slot that does not exist locally (DST spring-forward gap) is treated as
    advancing to the next valid wall time, matching GitHub's documented behavior.
    """
    minutes, hours = parse_daily_cron(cron)
    local_now = now.astimezone(tz)
    best = None
    for day_offset in (0, -1, -2):
        day = local_now.date() + timedelta(days=day_offset)
        for hour in hours:
            for minute in minutes:
                naive = datetime(day.year, day.month, day.day, hour, minute)
                candidate = naive.replace(tzinfo=tz)
                # Detect a nonexistent wall time: round-tripping through UTC changes it.
                roundtrip = candidate.astimezone(timezone.utc).astimezone(tz)
                if roundtrip.replace(tzinfo=None) != naive:
                    candidate = roundtrip
                if candidate <= local_now and (best is None or candidate > best):
                    best = candidate
        if best is not None:
            return best
    raise ValueError(f'no fire time found for {cron!r}')


def describe_trigger(event_name, cron, now, tz=EASTERN):
    """Return a plain dict describing the trigger. ``now`` must be timezone-aware."""
    if now.tzinfo is None:
        raise ValueError('now must be timezone-aware')
    local_now = now.astimezone(tz)
    info = {
        'event': event_name or 'unknown',
        'cron': cron or '',
        'started_utc': now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'started_eastern': local_now.strftime('%Y-%m-%d %H:%M:%S %Z'),
        'slot': '',
        'slot_date': '',
        'late_minutes': '',
        'slot_ambiguous': '',
        'crossed_date': '',
    }
    if event_name != 'schedule' or not cron:
        return info
    try:
        minutes, hours = parse_daily_cron(cron)
        fire = intended_fire(cron, now, tz)
    except ValueError as error:
        info['slot'] = f'unparsed ({error})'
        return info
    info.update({
        'slot': fire.strftime('%H:%M %Z'),
        'slot_date': fire.date().isoformat(),
        # Subtract in UTC: same-tzinfo subtraction in Python uses wall-clock time across DST.
        'late_minutes': str(int((now.astimezone(timezone.utc) - fire.astimezone(timezone.utc)).total_seconds() // 60)),
        # With several times in one entry, a late run may belong to an earlier slot;
        # the reported slot is the latest possible one and lateness is a lower bound.
        'slot_ambiguous': 'yes' if len(minutes) * len(hours) > 1 else 'no',
        'crossed_date': 'yes' if fire.date() != local_now.date() else 'no',
    })
    return info


def main(environ=os.environ, out=sys.stdout):
    info = describe_trigger(
        environ.get('GITHUB_EVENT_NAME', ''),
        environ.get('EVENT_SCHEDULE', ''),
        datetime.now(timezone.utc),
    )
    info['run_id'] = environ.get('GITHUB_RUN_ID', '')
    info['run_attempt'] = environ.get('GITHUB_RUN_ATTEMPT', '')
    info['sha'] = environ.get('GITHUB_SHA', '')[:7]
    info['requested_slate'] = environ.get('NBA_SLATE_DATE', '')
    line = 'TRIGGER ' + ' '.join(f'{key}={value!r}' for key, value in info.items())
    print(line, file=out)
    summary = environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as handle:
            handle.write('### Trigger\n\n| field | value |\n| --- | --- |\n')
            for key, value in info.items():
                handle.write(f'| {key} | `{value}` |\n')
    return info


if __name__ == '__main__':
    main()
