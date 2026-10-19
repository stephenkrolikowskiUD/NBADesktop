"""Checks for the NHL-style NBA refresh/final-box path and availability gates."""
import contextlib
from datetime import datetime,timedelta,timezone
import io
import json
import os
from pathlib import Path
import runpy
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import gspread
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import nba_sources
import nba_injuries
from nba_readiness import box_metrics,non_push_rate,finite_odds_probability,selection_key,select_analysis_history,is_actionable_player
from test_nba_readiness import Sheet


def box_fixture():
    blocks=[]
    for team in ('BOS','DET'):
        players=[]
        for n in range(5):
            players.append({'athlete':{'id':str(n+1),'displayName':f'{team} Player {n}'},'stats':['30','7-15','2-5','4-4','8','10','1','0','2','20'],'didNotPlay':False})
        blocks.append({'team':{'abbreviation':team},'statistics':[{'labels':['MIN','FG','3PT','FT','REB','AST','STL','BLK','TO','PTS'],'athletes':players}]})
    return {'header':{'competitions':[{'id':'FINAL-1','status':{'type':{'completed':True}}}]},'boxscore':{'players':blocks}}


class SourcesTests(unittest.TestCase):
    def test_ai_eligibility_requires_verified_pregame_clearance(self):
        now=datetime.now(timezone.utc)
        row={'STATUS':'AVAILABLE','COMMENCE_TIME':(now+timedelta(hours=1)).isoformat()}
        self.assertTrue(is_actionable_player(row,now))
        for change in [{'STATUS':''},{'STATUS':'OUT'},{'MINUTES_RESTRICTED':True},{'COMMENCE_TIME':''},{'COMMENCE_TIME':(now-timedelta(seconds=1)).isoformat()}]:
            self.assertFalse(is_actionable_player({**row,**change},now),change)
        self.assertFalse(is_actionable_player({'PLAYER_NAME':'Props Fallback'},now))
    def test_complete_final_box(self):
        game={'gameStatus':3,'ESPN_EVENT_ID':'FINAL-1','homeTeam':{'teamTricode':'BOS'},'awayTeam':{'teamTricode':'DET'}}
        with patch.object(nba_sources,'schedule',return_value=[game]),patch.object(nba_sources,'_get',return_value=box_fixture()):
            rows=nba_sources.final_boxes('2026-04-10')
        self.assertEqual(len(rows),10);self.assertEqual(rows[0]['PTS'],20);self.assertTrue(rows[0]['FINAL'])
        self.assertEqual(rows[0]['MATCHUP'],'BOS vs. DET');self.assertEqual(rows[-1]['MATCHUP'],'DET @ BOS')
    def test_partial_box_fails_closed(self):
        payload=box_fixture();payload['boxscore']['players'][0]['statistics'][0]['athletes'][0]['stats']=[]
        game={'gameStatus':3,'ESPN_EVENT_ID':'FINAL-1','homeTeam':{'teamTricode':'BOS'},'awayTeam':{'teamTricode':'DET'}}
        with patch.object(nba_sources,'schedule',return_value=[game]),patch.object(nba_sources,'_get',return_value=payload):
            with self.assertRaises(ValueError):nba_sources.final_boxes('2026-04-10')
    def test_live_game_never_grades(self):
        with patch.object(nba_sources,'schedule',return_value=[{'gameStatus':2}]),patch.object(nba_sources,'_get') as request:
            self.assertEqual(nba_sources.final_boxes('2026-04-10'),[]);request.assert_not_called()
    def test_explicit_dnp_retained(self):
        payload=box_fixture();p=payload['boxscore']['players'][0]['statistics'][0]['athletes'][0];p['didNotPlay']=True;p['stats']=[]
        game={'gameStatus':3,'ESPN_EVENT_ID':'FINAL-1','homeTeam':{'teamTricode':'BOS'},'awayTeam':{'teamTricode':'DET'}}
        with patch.object(nba_sources,'schedule',return_value=[game]),patch.object(nba_sources,'_get',return_value=payload):rows=nba_sources.final_boxes('2026-04-10')
        self.assertTrue(rows[0]['DNP'])
    def test_push_and_nonfinite_rules(self):
        self.assertIsNone(non_push_rate([20]*5,20));self.assertEqual(non_push_rate([20,21,19],20),.5)
        for v in [None,0,float('nan'),float('inf'),99]:self.assertIsNone(finite_odds_probability(v))
    def test_triple_double_included(self):
        row=box_metrics({'PTS':20,'REB':10,'AST':10,'STL':1,'BLK':1,'TOV':2,'FG3M':2})
        self.assertEqual(row['DK_FP'],20+12.5+15+2+2-1+1+1.5+3)
    def test_selection_identity_preserves_book_and_line(self):
        row={'DATE':'2026-10-20','player':'A','prop_type':'PTS','lean':'OVER','line':20.5,'BOOK':'draftkings'}
        self.assertNotEqual(selection_key(row,str.lower),selection_key({**row,'BOOK':'fanduel'},str.lower))
        self.assertNotEqual(selection_key(row,str.lower),selection_key({**row,'line':21.5},str.lower))
    def test_rookie_and_prior_baseline_scope(self):
        rows=pd.DataFrame([{'PLAYER_NAME':'Veteran','SEASON':'2025-26','PTS':10}])
        data,meta=select_analysis_history(rows,'2026-27')
        self.assertEqual(data.iloc[0]['SEASON'],'2025-26');self.assertEqual(meta.iloc[0]['CURRENT_GAMES'],0)
        self.assertNotIn('Rookie',list(meta['PLAYER_NAME']))
    def test_current_scope_never_mixes_prior(self):
        rows=pd.DataFrame([{'PLAYER_NAME':'A','SEASON':'2025-26','PTS':99},{'PLAYER_NAME':'A','SEASON':'2026-27','PTS':10}])
        data,meta=select_analysis_history(rows,'2026-27');self.assertEqual(list(data.PTS),[10])
    def test_availability_unknown_and_stale_reports(self):
        now=datetime.now(timezone.utc);day=now.date().isoformat();people=[{'PLAYER_NAME':'A','CURRENT_TEAM':'BOS'}]
        self.assertEqual(nba_injuries.apply_availability(people,{},day,now)[0]['AVAILABILITY'],'UNKNOWN')
        report={'reported_at':(now-timedelta(hours=7)).isoformat(),'source':'fixture','slate_date':day,'official':True,'submitted_teams':['BOS']}
        self.assertEqual(nba_injuries.apply_availability(people,report,day,now)[0]['AVAILABILITY'],'UNKNOWN')
    def test_submitted_report_clears_absent_player(self):
        now=datetime.now(timezone.utc);day=now.date().isoformat();people=[{'PLAYER_NAME':'A','CURRENT_TEAM':'BOS'}]
        report={'reported_at':now.isoformat(),'source':'NBA fixture','slate_date':day,'official':True,'submitted_teams':['BOS']}
        self.assertEqual(nba_injuries.apply_availability(people,report,day,now)[0]['AVAILABILITY'],'AVAILABLE')
    def test_unmatched_report_name_disables_absence_clearance(self):
        now=datetime.now(timezone.utc);day=now.date().isoformat()
        people=[{'PLAYER_NAME':'Known Player','CURRENT_TEAM':'BOS'}]
        report={'reported_at':now.isoformat(),'source':'NBA fixture','slate_date':day,'official':True,'submitted_teams':['BOS'],
                'players':[{'player':'Unrecognized Player','team':'BOS','status':'OUT'}]}
        self.assertEqual(nba_injuries.apply_availability(people,report,day,now)[0]['AVAILABILITY'],'UNKNOWN')
    def test_roster_name_join_preserves_accented_history(self):
        roster=pd.DataFrame([{'DISPLAY_FIRST_LAST':'Hugo Gonzalez','TEAM_ABBREVIATION':'BOS'}])
        history=pd.DataFrame([{'PLAYER_NAME':'Hugo González','PLAYER_ID':123}])
        self.assertEqual(nba_sources.align_roster_names(roster,history).iloc[0]['DISPLAY_FIRST_LAST'],'Hugo González')
    def test_seed_refresh_keeps_nba_identity_across_name_variants(self):
        existing=pd.DataFrame([{'PLAYER_NAME':'Hugo González Jr.','PLAYER_ID':123,'GAME_DATE':'2026-10-21','TEAM_ABBREVIATION':'BOS'}])
        fresh={'PLAYER_NAME':'Hugo Gonzalez','ESPN_PLAYER_ID':'999','GAME_DATE':'2026-10-22','TEAM_ABBREVIATION':'BOS','DNP':False}
        with patch.object(nba_sources,'final_boxes',return_value=[fresh]):
            rows=nba_sources.refresh_seed(existing,'2026-10-23','2026-27')
        latest=rows.iloc[-1];self.assertEqual(latest['PLAYER_ID'],123);self.assertEqual(latest['PLAYER_NAME'],'Hugo González Jr.')


class GraderIntegrationTests(unittest.TestCase):
    def grade(self,final_rows):
        day=(datetime.now().date()-timedelta(days=1)).isoformat()
        headers=['DATE','player','team','prop_type','line','lean','HIT','ACTUAL_STAT','RESULT','RUN_NUMBER','confidence']
        ws=Sheet([headers,[day,'BOS Player 0','BOS','PTS','19.5','OVER','','','','1','LEAN']])
        ws.batch_update=lambda cells:[ws.rows[int(''.join(c for c in cell['range'] if c.isdigit()))-1].__setitem__(ord(cell['range'][0])-65,cell['values'][0][0]) for cell in cells]
        class Book:
            def __init__(self):self.tabs={'Daily_Picks':ws}
            def worksheet(self,title):
                if title not in self.tabs:raise gspread.exceptions.WorksheetNotFound(title)
                return self.tabs[title]
            def add_worksheet(self,title,rows,cols):
                self.tabs[title]=Sheet();return self.tabs[title]
        book=Book()
        client=type('Client',(),{'open_by_key':lambda self,key:book})()
        with tempfile.TemporaryDirectory() as backup, contextlib.ExitStack() as stack:
            stack.enter_context(patch('gspread.authorize',return_value=client));stack.enter_context(patch('google.oauth2.service_account.Credentials.from_service_account_info',return_value=object()))
            stack.enter_context(patch('atexit.register'));stack.enter_context(patch.object(nba_sources,'final_boxes',return_value=final_rows))
            stack.enter_context(patch.dict(os.environ,{'GOOGLE_SERVICE_ACCOUNT_JSON':'{}','NBA_BACKUP_DIR':backup}))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            runpy.run_path(str(ROOT/'NBAGrader5-4.py'))
        return dict(zip(ws.rows[0],ws.rows[1]))
    def test_played_final(self):
        row={'PLAYER_NAME':'BOS Player 0','PTS':20,'REB':10,'AST':10,'STL':1,'BLK':1,'TOV':2,'FG3M':2,'MIN':30,'DNP':False}
        result=self.grade([row]);self.assertEqual(result['HIT'],'YES');self.assertEqual(result['ACTUAL_STAT'],'20.0')
    def test_missing_player_stays_pending(self):self.assertEqual(self.grade([])['HIT'],'')
    def test_other_final_game_does_not_imply_dnp(self):
        row={'PLAYER_NAME':'Another Player','PTS':20,'REB':10,'AST':10,'STL':1,'BLK':1,'TOV':2,'FG3M':2,'MIN':30,'DNP':False}
        self.assertEqual(self.grade([row])['HIT'],'')
    def test_suffix_variants_find_box(self):
        row={'PLAYER_NAME':'BOS Player 0 Jr.','PTS':20,'REB':10,'AST':10,'STL':1,'BLK':1,'TOV':2,'FG3M':2,'MIN':30,'DNP':False}
        self.assertEqual(self.grade([row])['HIT'],'YES')
    def test_verified_dnp(self):self.assertEqual(self.grade([{'PLAYER_NAME':'BOS Player 0','DNP':True}])['HIT'],'DNP')


if __name__=='__main__':unittest.main()
