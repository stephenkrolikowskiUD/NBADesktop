"""Slot identity and publication guards; called under the shared Sheets writer lock."""
import os
from datetime import datetime, timezone
import gspread
from nba_schedule import EASTERN, intended_fire, parse_daily_cron

HEALTHY = {'READY', 'NO_GAMES'}
SLOTS = {'10:00', '18:00', '21:00'}


def resolve_slot(env, now):
    """Return (key, explicit date, skip reason). Manual runs without a slot are unchanged.

    Native cron has no original delivery date. Infer the most recent occurrence,
    accepting only same-date delivery within six hours. This cannot identify an
    exact whole-day delay; an external dispatcher supplies an unambiguous date.
    """
    local = now.astimezone(EASTERN)
    if env.get('GITHUB_EVENT_NAME') == 'schedule':
        cron = env.get('EVENT_SCHEDULE', '')
        minutes, hours = parse_daily_cron(cron)
        if len(minutes) != 1 or len(hours) != 1:
            raise ValueError('Scheduled engine requires one time per cron entry')
        fire = intended_fire(cron, now)
        slot, day = fire.strftime('%H:%M'), fire.date().isoformat()
    else:
        slot = env.get('NBA_DISPATCH_SLOT', '').strip()
        if not slot:
            return '', '', ''
        day = env.get('NBA_SLATE_DATE', '')
        if len(day) != 10:
            raise ValueError('A dispatch slot requires an explicit YYYY-MM-DD slate date')
        fire = datetime.fromisoformat(f'{day}T{slot}:00').replace(tzinfo=EASTERN)
        if fire.date().isoformat() != day:
            raise ValueError('Invalid slate date')
    if slot not in SLOTS:
        raise ValueError('Unsupported dispatch slot')
    key = f'{day}@{slot}'
    if day != local.date().isoformat():
        return key, day, 'stale or future slate date'
    age = (now.astimezone(timezone.utc) - fire.astimezone(timezone.utc)).total_seconds()
    if age < 0 or (env.get('GITHUB_EVENT_NAME') == 'schedule' and age > 6 * 3600):
        return key, day, 'outside allowed delivery window'
    return key, day, ''


def published_slot(rows, key):
    return any(str(r.get('slot_key', '')) == key
               and r.get('sport') == 'NBA' and r.get('kind') == 'engine'
               and r.get('status') in {'OK', 'WARN'}
               and r.get('publication_status') in HEALTHY
               and r.get('publication_run_id')
               for r in rows)


def guard_run(book, logger, env=None, now=None):
    env = os.environ if env is None else env
    key, day, reason = resolve_slot(env, now or datetime.now(timezone.utc))
    logger.slot_key = key
    if not key:
        return
    if not reason:
        try:
            rows = book.worksheet('Run_Log').get_all_records()
        except gspread.exceptions.WorksheetNotFound:
            rows = []
        # Other read failures must stop before paid calls, not bypass deduplication.
        if published_slot(rows, key):
            reason = 'slot already published'
    if reason:
        logger.status = 'SKIP'
        logger.warn(reason)
        print(f'SCHEDULE SKIP {key}: {reason}')
        raise SystemExit(0)
    env['NBA_SLATE_DATE'] = day
    print(f'SCHEDULE RUN {key}')


def verify_publication(book, logger, day, run_id, status):
    """Only a read-back final publication earns the durable slot marker."""
    rows = book.worksheet('NBA_Status').get_all_records()
    if not any(str(r.get('SLATE_DATE')) == day and r.get('RUN_ID') == run_id
               and r.get('STATUS') == status
               and str(r.get('SLOT_KEY', '')) == logger.slot_key for r in rows):
        raise RuntimeError('NBA_Status publication verification failed')
    logger.publication_status = status
    logger.publication_run_id = run_id
