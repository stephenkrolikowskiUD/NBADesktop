import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
import math
import re
import unicodedata
import pandas as pd
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from nba_readiness import performance_eligibility,dashboard_stats,PERFORMANCE_POLICY,selection_key

class TrackingTests(unittest.TestCase):
    def market(self,**changes):
        row={'DATE':'2026-10-20','player':'Example','prop_type':'PTS','lean':'OVER','line':20.5,'BOOK':'draftkings','ODDS':150,'EVENT_ID':'event',
             'MARKET_VALIDATED':True,'QUOTE_TIME':'2026-10-20T20:00:00Z','PUBLISHED_AT_UTC':'2026-10-20T20:01:00Z','COMMENCE_TIME':'2026-10-20T23:00:00Z',
             'HIT':'YES','confidence':'SMASH'}
        return {**row,**changes}
    def test_tracking_rejects_legacy_missing_and_invalid_provenance(self):
        self.assertTrue(performance_eligibility(self.market(),'2026-27')[0])
        for changes in [{'DATE':'2026-04-20'},{'DATE':''},{'ODDS':50},{'QUOTE_TIME':''},{'MARKET_VALIDATED':False},{'EVENT_ID':''},{'PUBLISHED_AT_UTC':'2026-10-21T00:00:00Z'},{'QUOTE_TIME':'2026-10-20T20:02:00Z'}]:
            self.assertFalse(performance_eligibility(self.market(**changes),'2026-27')[0],changes)
    def test_lean_feed_keeps_only_active_analysis_seasons(self):
        history=pd.DataFrame([{'PLAYER_NAME':'A','SEASON':'2025-26','PTS':20,'GAME_DATE':'2026-01-01','UNUSED':'large'},
                              {'PLAYER_NAME':'A','SEASON':'2026-27','PTS':10,'GAME_DATE':'2026-10-20','UNUSED':'large'},
                              {'PLAYER_NAME':'B','SEASON':'2025-26','PTS':30,'GAME_DATE':'2026-01-01','UNUSED':'large'}])
        tonight=pd.DataFrame([{'PLAYER_NAME':'A','ANALYSIS_SEASON':'2025-26'}])
        result=dashboard_stats(history,tonight)
        self.assertEqual(len(result),1);self.assertEqual(result.iloc[0]['PTS'],20);self.assertNotIn('UNUSED',result)
        self.assertEqual(len(dashboard_stats(history,tonight.iloc[:0])),0)
    def test_performance_uses_recorded_odds_dedupes_and_excludes_pushes(self):
        wanted={'normalize_person_name','normalize_prop_metric','normalize_confidence','pick_perf_prepare_df','pick_perf_rate','wilson_lower_bound','pick_perf_metrics_row'}
        tree=ast.parse((ROOT/'NBAGrader5-4.py').read_text())
        namespace={'pd':pd,'np':np,'re':re,'unicodedata':unicodedata,'math':math,'PERFORMANCE_SEASON':'2026-27','PERFORMANCE_POLICY':PERFORMANCE_POLICY,
                   'performance_eligibility':performance_eligibility,'selection_key':selection_key,'PICK_PERF_WILSON_Z':1.96,'PICK_PERF_MIN_SAMPLE':25,'timestamp_est':''}
        exec(compile(ast.Module(body=[node for node in tree.body if isinstance(node,ast.FunctionDef) and node.name in wanted],type_ignores=[]),'grader-test','exec'),namespace)
        good=self.market();loss=self.market(player='Loss',HIT='NO',ODDS=-200);push=self.market(player='Push',HIT='PUSH')
        prepared=namespace['pick_perf_prepare_df'](pd.DataFrame([good,good,loss,push,self.market(DATE='2026-04-20')]))
        self.assertEqual(len(prepared),3)
        stats=namespace['pick_perf_metrics_row'](prepared,'overall','','all_time')
        self.assertEqual(stats['N_PICKS_DECISIVE'],2);self.assertEqual(stats['HIT_RATE'],.5)
        self.assertAlmostEqual(stats['ROI_PER_PICK'],100*.5/3,places=3)
        self.assertTrue(math.isnan(stats['AVG_CLV_EDGE']))

if __name__=='__main__':unittest.main()
