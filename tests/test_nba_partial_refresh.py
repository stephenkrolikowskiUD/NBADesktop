"""Early-season history and incomplete provider response regressions."""
from datetime import datetime
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import pandas as pd
import requests
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import nba_sources
from nba_readiness import select_analysis_history,gemini_billing_exhausted
from test_nba_sources import box_fixture

GAME={'gameStatus':3,'ESPN_EVENT_ID':'FINAL-1','homeTeam':{'teamTricode':'BOS'},'awayTeam':{'teamTricode':'DET'}}

class PartialRefreshTests(unittest.TestCase):
    def test_missing_minutes_skips_only_that_row_for_refresh_but_grading_stays_strict(self):
        payload=box_fixture();payload['boxscore']['players'][0]['statistics'][0]['athletes'][0]['stats'][0]='--'
        with patch.object(nba_sources,'schedule',return_value=[GAME]),patch.object(nba_sources,'_get',return_value=payload):
            with self.assertRaisesRegex(ValueError,'MIN'):nba_sources.final_boxes('2026-10-23')
            issues=[];rows=nba_sources.final_boxes('2026-10-23',on_error=issues.append)
        self.assertEqual(len(rows),9);self.assertEqual(len(issues),1)
        self.assertEqual(issues[0]['player'],'BOS Player 0');self.assertEqual(issues[0]['event'],'FINAL-1')
        self.assertTrue(all(row['FINAL'] and not row['DNP'] for row in rows))

    def test_clock_minutes_are_parsed_without_inventing_zero(self):
        self.assertAlmostEqual(nba_sources._played_number('34:30',minutes=True),34.5)
        for value in ['--','nan','inf','-1','4:99']:
            self.assertIsNone(nba_sources._played_number(value,minutes=True))

    def test_bad_game_does_not_discard_other_complete_games(self):
        other={**GAME,'ESPN_EVENT_ID':'FINAL-2'}
        good=box_fixture();good['header']['competitions'][0]['id']='FINAL-2'
        def get(path,params,**kwargs):return {} if params['event']=='FINAL-1' else good
        with patch.object(nba_sources,'schedule',return_value=[GAME,other]),patch.object(nba_sources,'_get',side_effect=get):
            issues=[];rows=nba_sources.final_boxes('2026-10-23',on_error=issues.append)
        self.assertEqual(len(rows),10);self.assertEqual(len(issues),1)
        self.assertEqual({row['ESPN_EVENT_ID'] for row in rows},{'FINAL-2'})

    def test_network_failure_retains_verified_seed_and_exposes_retry_date(self):
        seed=pd.DataFrame([{'PLAYER_NAME':'Known','PLAYER_ID':123,'GAME_DATE':'2026-10-22','TEAM_ABBREVIATION':'BOS','PTS':20}])
        with patch.object(nba_sources,'final_boxes',side_effect=requests.Timeout('fixture timeout')):
            result=nba_sources.refresh_seed(seed,'2026-10-24','2026-27')
        self.assertEqual(list(result.PTS),[20]);self.assertTrue(result.attrs['REFRESH_WARNINGS'])
        self.assertIn('2026-10-23',{item['date'] for item in result.attrs['REFRESH_WARNINGS']})

    def test_small_current_sample_keeps_unmixed_labeled_prior_baseline(self):
        for count in (0,1,4,5):
            rows=pd.DataFrame([{'PLAYER_NAME':'A','SEASON':'2025-26','PTS':99}]*8+[{'PLAYER_NAME':'A','SEASON':'2026-27','PTS':10}]*count)
            result,meta=select_analysis_history(rows,'2026-27')
            self.assertEqual(meta.iloc[0]['CURRENT_GAMES'],count)
            expected='2026-27' if count>=5 else '2025-26'
            self.assertEqual(set(result.SEASON),{expected});self.assertEqual(meta.iloc[0]['ANALYSIS_SEASON'],expected)

    def test_rookie_keeps_real_current_sample_without_fabricated_history(self):
        frame=pd.DataFrame([{'PLAYER_NAME':'Rookie','SEASON':'2026-27','PTS':10}])
        result,meta=select_analysis_history(frame,'2026-27')
        self.assertEqual(len(result),1);self.assertEqual(meta.iloc[0]['BASELINE_TYPE'],'CURRENT')

    def test_billing_block_is_distinct_from_transient_rate_limit(self):
        self.assertTrue(gemini_billing_exhausted(RuntimeError('402 RESOURCE_EXHAUSTED: prepayment credits depleted')))
        self.assertFalse(gemini_billing_exhausted(RuntimeError('429 RESOURCE_EXHAUSTED: rate limit')))

if __name__=='__main__':unittest.main()
