"""Offline checks for scheduled-trigger diagnostics. No network, credentials, or Sheets."""
import io
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nba_schedule as ns  # noqa: E402

ET = ZoneInfo('America/New_York')
ROOT = Path(__file__).resolve().parents[1]


def et(*args, **kwargs):
    return datetime(*args, tzinfo=ET, **kwargs)


class TriggerDiagnostics(unittest.TestCase):
    def test_late_morning_trigger_keeps_its_slot(self):
        # Observed Nov 4: 10 AM slot delivered at 4:13 PM EST.
        info = ns.describe_trigger('schedule', '0 10 * * *', et(2026, 11, 4, 16, 13))
        self.assertEqual(info['slot'], '10:00 EST')
        self.assertEqual(info['slot_date'], '2026-11-04')
        self.assertEqual(info['late_minutes'], '373')
        self.assertEqual(info['slot_ambiguous'], 'no')
        self.assertEqual(info['crossed_date'], 'no')

    def test_evening_trigger_after_midnight_reports_previous_date(self):
        # Observed Oct 23: a run created 1:40 AM EDT, after the 9 PM slot.
        info = ns.describe_trigger('schedule', '0 21 * * *', et(2026, 10, 23, 1, 40))
        self.assertEqual(info['slot_date'], '2026-10-22')
        self.assertEqual(info['crossed_date'], 'yes')
        self.assertEqual(info['late_minutes'], '280')

    def test_independent_evening_slots(self):
        self.assertEqual(ns.describe_trigger('schedule', '0 18 * * *', et(2026, 11, 6, 21, 38))['slot'], '18:00 EST')
        self.assertEqual(ns.describe_trigger('schedule', '0 21 * * *', et(2026, 11, 6, 21, 38))['slot'], '21:00 EST')

    def test_combined_entry_is_flagged_ambiguous(self):
        info = ns.describe_trigger('schedule', '0 18,21 * * *', et(2026, 11, 6, 21, 38))
        self.assertEqual(info['slot'], '21:00 EST')
        self.assertEqual(info['slot_ambiguous'], 'yes')

    def test_fall_back_day(self):
        # Nov 1 2026: clocks fall back at 2 AM. 10 AM is EST that day.
        info = ns.describe_trigger('schedule', '0 10 * * *', datetime(2026, 11, 1, 15, 30, tzinfo=timezone.utc))
        self.assertEqual(info['slot'], '10:00 EST')
        self.assertEqual(info['late_minutes'], '30')
        # The day before, 10 AM was EDT (14:00 UTC).
        info = ns.describe_trigger('schedule', '0 10 * * *', datetime(2026, 10, 31, 14, 30, tzinfo=timezone.utc))
        self.assertEqual(info['slot'], '10:00 EDT')
        self.assertEqual(info['late_minutes'], '30')

    def test_trigger_across_fall_back_midnight_uses_real_elapsed_time(self):
        # 9 PM EDT Oct 31 to 2:04 AM EST Nov 1 is 6h04m of real time (an extra hour).
        info = ns.describe_trigger('schedule', '0 21 * * *', et(2026, 11, 1, 2, 4))
        self.assertEqual(info['slot_date'], '2026-10-31')
        self.assertEqual(info['late_minutes'], '364')
        # Repeated 1 AM hour: the second 1:30 (EST, fold=1) is an hour later than the first.
        first = ns.describe_trigger('schedule', '0 21 * * *', et(2026, 11, 1, 1, 30))
        second = ns.describe_trigger('schedule', '0 21 * * *', et(2026, 11, 1, 1, 30, fold=1))
        self.assertEqual(int(second['late_minutes']) - int(first['late_minutes']), 60)

    def test_spring_forward_gap_advances(self):
        # Mar 14 2027: 2 AM does not exist; GitHub advances to the next valid time.
        info = ns.describe_trigger('schedule', '0 2 * * *', et(2027, 3, 14, 3, 30))
        self.assertEqual(info['slot'], '03:00 EDT')
        self.assertEqual(info['late_minutes'], '30')

    def test_manual_run_has_no_slot(self):
        info = ns.describe_trigger('workflow_dispatch', '', et(2026, 11, 4, 12, 3))
        self.assertEqual(info['slot'], '')
        self.assertEqual(info['event'], 'workflow_dispatch')

    def test_unsupported_cron_is_reported_not_raised(self):
        info = ns.describe_trigger('schedule', '*/5 * * * 1', et(2026, 11, 4, 12, 3))
        self.assertTrue(info['slot'].startswith('unparsed'))

    def test_naive_time_rejected(self):
        with self.assertRaises(ValueError):
            ns.describe_trigger('schedule', '0 10 * * *', datetime(2026, 11, 4, 10))

    def test_main_prints_only_whitelisted_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            summary = os.path.join(tmp, 'summary.md')
            env = {
                'GITHUB_EVENT_NAME': 'schedule', 'EVENT_SCHEDULE': '0 10 * * *',
                'GITHUB_RUN_ID': '1', 'GITHUB_RUN_ATTEMPT': '1', 'GITHUB_SHA': 'abcdef123456',
                'NBA_SLATE_DATE': 'today', 'GITHUB_STEP_SUMMARY': summary,
                'ODDS_API_KEY': 'should-never-print',
            }
            out = io.StringIO()
            ns.main(env, out)
            text = out.getvalue() + Path(summary).read_text()
        self.assertIn("cron='0 10 * * *'", text)
        self.assertIn("sha='abcdef1'", text)
        self.assertNotIn('should-never-print', text)


class WorkflowSchedules(unittest.TestCase):
    """One time per cron entry, so github.event.schedule names the slot exactly."""

    def crons(self, name):
        text = (ROOT / '.github' / 'workflows' / name).read_text()
        lines = [line.strip() for line in text.splitlines()]
        crons = [line.split(':', 1)[1].strip().strip("'\"") for line in lines if line.startswith('- cron:')]
        zones = [line for line in lines if line.startswith('timezone:')]
        self.assertEqual(len(zones), len(crons))
        self.assertTrue(all(zone == 'timezone: America/New_York' for zone in zones))
        return crons

    def test_engine_slots(self):
        self.assertEqual(self.crons('nba-engine.yml'), ['0 10 * * *', '0 18 * * *', '0 21 * * *'])

    def test_grader_slots(self):
        self.assertEqual(self.crons('nba-grader.yml'), ['0 2 * * *', '0 10 * * *'])

    def test_workflows_log_trigger_without_inline_expression(self):
        for name in ('nba-engine.yml', 'nba-grader.yml'):
            text = (ROOT / '.github' / 'workflows' / name).read_text()
            self.assertIn('EVENT_SCHEDULE: ${{ github.event.schedule }}', text)
            self.assertIn('run: python nba_schedule.py', text)
            self.assertNotIn('toJSON(github', text)


if __name__ == '__main__':
    unittest.main()
