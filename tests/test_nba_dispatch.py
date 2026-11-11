import unittest
import os
import runpy
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import Mock, patch
import gspread
from nba_dispatch import resolve_slot, guard_run, verify_publication, published_slot
from run_logger import RunLogger


def at(value):
    return datetime.fromisoformat(value)


class DispatchTests(unittest.TestCase):
    def test_engine_duplicate_exits_before_network(self):
        book = Mock()
        book.worksheet.return_value.get_all_records.return_value = [dict(
            slot_key='2026-11-11@10:00', sport='NBA', kind='engine', status='OK',
            publication_status='READY', publication_run_id='abc')]
        gc = Mock()
        gc.open_by_key.return_value = book
        with patch('gspread.authorize', return_value=gc), \
             patch('google.oauth2.service_account.Credentials.from_service_account_info'), \
             patch('atexit.register'), patch('sys.excepthook'), \
             patch('requests.get') as network, \
             patch('nba_dispatch.resolve_slot', return_value=('2026-11-11@10:00', '2026-11-11', '')), \
             patch.dict(os.environ, {'GOOGLE_SERVICE_ACCOUNT_JSON': '{}', 'NBA_CACHE_DIR': '/tmp/nba-dispatch-test-cache'}):
            with self.assertRaises(SystemExit) as error:
                runpy.run_path(str(Path(__file__).resolve().parents[1] / 'NBAEnginev5-4.py'))
            self.assertEqual(error.exception.code, 0)
            network.assert_not_called()

    def test_native_dst_and_midnight(self):
        env = {'GITHUB_EVENT_NAME': 'schedule', 'EVENT_SCHEDULE': '0 10 * * *'}
        for value in ['2026-11-01T15:00:00+00:00', '2027-03-14T14:00:00+00:00']:
            key, day, reason = resolve_slot(env, at(value))
            self.assertEqual(key, day + '@10:00')
            self.assertEqual(reason, '')
        env['EVENT_SCHEDULE'] = '0 21 * * *'
        key, day, reason = resolve_slot(env, at('2026-11-11T05:30:00+00:00'))
        self.assertEqual(key, '2026-11-10@21:00')
        self.assertTrue(reason)

    def test_explicit_date_and_manual(self):
        now = at('2026-11-11T15:01:00+00:00')
        self.assertEqual(resolve_slot({}, now), ('', '', ''))
        env = {'NBA_DISPATCH_SLOT': '10:00', 'NBA_SLATE_DATE': 'today'}
        with self.assertRaises(ValueError): resolve_slot(env, now)
        env['NBA_SLATE_DATE'] = '2026-11-10'
        self.assertTrue(resolve_slot(env, now)[2])
        env['NBA_SLATE_DATE'] = '2026-11-11'
        self.assertEqual(resolve_slot(env, now)[2], '')

    def test_late_and_early_native(self):
        env = {'GITHUB_EVENT_NAME': 'schedule', 'EVENT_SCHEDULE': '0 10 * * *'}
        for value in ['2026-11-11T14:59:00+00:00', '2026-11-11T22:00:00+00:00']:
            self.assertTrue(resolve_slot(env, at(value))[2])

    def test_publication_not_just_success(self):
        row = dict(slot_key='2026-11-11@10:00', status='OK', sport='NBA', kind='engine',
                   publication_status='READY', publication_run_id='abc')
        self.assertTrue(published_slot([row], row['slot_key']))
        for status in ['FAIL', 'CRASH', 'SKIP']:
            self.assertFalse(published_slot([{**row, 'status': status}], row['slot_key']))
        for status in ['', 'UPDATING', 'FAILED', 'AWAITING_PROPS', 'DEGRADED', 'WARMUP']:
            self.assertFalse(published_slot([{**row, 'publication_status': status}], row['slot_key']))
        self.assertFalse(published_slot([row], '2026-11-11@18:00'))

    def test_serialized_duplicate_and_retry(self):
        # Both workflow entry points share nba-sheet-writer, so the second reads
        # the first's final marker before any engine data fetching begins.
        rows = []
        book = Mock()
        book.worksheet.return_value.get_all_records.side_effect = lambda: rows
        env = {'NBA_DISPATCH_SLOT': '10:00', 'NBA_SLATE_DATE': '2026-11-11'}
        now = at('2026-11-11T15:05:00+00:00')
        logger = RunLogger(None, 'fixture', 'NBA', 'engine')
        guard_run(book, logger, env, now)
        rows.append(dict(slot_key=logger.slot_key, sport='NBA', kind='engine', status='CRASH'))
        guard_run(book, logger, env, now)
        rows.append(dict(slot_key=logger.slot_key, sport='NBA', kind='engine', status='WARN',
                         publication_status='READY', publication_run_id='abc'))
        native = {'GITHUB_EVENT_NAME': 'schedule', 'EVENT_SCHEDULE': '0 10 * * *'}
        with self.assertRaises(SystemExit) as error: guard_run(book, logger, native, now)
        self.assertEqual(error.exception.code, 0)
        self.assertEqual(logger.status, 'SKIP')
        book.worksheet.side_effect = RuntimeError('Read unavailable')
        with self.assertRaises(RuntimeError): guard_run(book, logger, env, now)

    def test_readback(self):
        book = Mock()
        logger = SimpleNamespace(slot_key='2026-11-11@10:00')
        book.worksheet.return_value.get_all_records.return_value = [dict(
            SLATE_DATE='2026-11-11', SLOT_KEY=logger.slot_key, RUN_ID='abc', STATUS='READY')]
        verify_publication(book, logger, '2026-11-11', 'abc', 'READY')
        self.assertEqual(logger.publication_run_id, 'abc')
        with self.assertRaises(RuntimeError): verify_publication(book, logger, '2026-11-11', 'old', 'READY')

    def test_log_mapping_and_skip(self):
        ws = Mock()
        ws.col_count = 40
        headers = ['custom'] + list(reversed(RunLogger.HEADERS))
        ws.row_values.return_value = headers
        gc = Mock()
        gc.open_by_key.return_value.worksheet.return_value = ws
        logger = RunLogger(gc, 'fixture', 'NBA', 'engine')
        logger.status = 'SKIP'
        logger.slot_key = '2026-11-11@10:00'
        logger.finalize_and_write()
        row = dict(zip(headers, ws.append_row.call_args.args[0]))
        self.assertEqual(row['status'], 'SKIP')
        self.assertEqual(row['slot_key'], logger.slot_key)
        self.assertEqual(row['custom'], '')


if __name__ == '__main__':
    unittest.main()
