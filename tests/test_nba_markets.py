import contextlib
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nba_markets import fetch_batch, is_pregame
from nba_security import redact, safe_print, register_secret
from run_logger import RunLogger


class MarketSafetyTests(unittest.TestCase):
    def test_non_environment_secret_is_redacted(self):
        with patch('nba_security._secrets',set()):
            register_secret('colab-secret-value')
            self.assertNotIn('colab-secret-value',redact('error: colab-secret-value'))

    def test_pregame_excludes_started_missing_naive_and_invalid_times(self):
        now=datetime(2026,11,10,20,tzinfo=timezone.utc)
        self.assertTrue(is_pregame({'commence_time':'2026-11-10T21:00:00Z'},now))
        for start in ('2026-11-10T20:00:00Z','2026-11-10T19:00:00Z','2026-11-10T21:00:00','bad',None):
            self.assertFalse(is_pregame({'commence_time':start},now))
        self.assertFalse(is_pregame({},now))

    def test_timeout_does_not_erase_other_batches(self):
        good=SimpleNamespace(status_code=200,json=lambda:{'bookmakers':[]})
        warn=Mock();quota=Mock()
        with patch('nba_markets.requests.get',side_effect=[good,requests.Timeout('url?apiKey=secret'),good]):
            results=[fetch_batch('fixture',{},quota,warn) for _ in range(3)]
        self.assertEqual(results,[{'bookmakers':[]},None,{'bookmakers':[]}])
        self.assertEqual(quota.call_count,2)
        self.assertNotIn('secret',warn.call_args.args[0])

    def test_http_and_malformed_json_warn(self):
        for response in (SimpleNamespace(status_code=500),
                         SimpleNamespace(status_code=200,json=Mock(side_effect=ValueError('bad JSON'))),
                         SimpleNamespace(status_code=200,json=lambda:{'bookmakers':[{}]})):
            warn=Mock()
            with patch('nba_markets.requests.get',return_value=response):
                self.assertIsNone(fetch_batch('fixture',{},Mock(),warn))
            warn.assert_called_once()

    def test_quota_abort_is_not_swallowed(self):
        with patch('nba_markets.requests.get',return_value=SimpleNamespace(status_code=200)):
            with self.assertRaises(SystemExit):
                fetch_batch('fixture',{},Mock(side_effect=SystemExit(2)),Mock())

    def test_redaction_precedes_truncation_and_console_output(self):
        with patch.dict(os.environ,{'ODDS_API_KEY':'sensitive-value'}):
            text='failure https://example.test?apiKey=unknown-secret&region=us sensitive-value'
            cleaned=redact(text)
            self.assertNotIn('unknown-secret',cleaned)
            self.assertNotIn('sensitive-value',cleaned)
            self.assertIn('&region=us',cleaned)
            output=io.StringIO()
            with contextlib.redirect_stdout(output):safe_print(text)
            self.assertEqual(output.getvalue().strip(),cleaned)
            logger=RunLogger(None,'fixture','NBA','engine')
            logger.warn(text);logger.fail(RuntimeError(text))
            self.assertNotIn('sensitive-value',str(logger.warnings)+logger.error)
            self.assertNotIn('unknown-secret',str(logger.warnings)+logger.error)


if __name__=='__main__':unittest.main()
