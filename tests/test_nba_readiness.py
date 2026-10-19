"""Offline launch regression checks. No credentials, network, or live sheet writes."""
import ast
import math
import json
from types import SimpleNamespace
import contextlib
import io
import os
from pathlib import Path
import runpy
import sys
import unittest
from unittest.mock import patch
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd
import gspread
from nba_api.stats.endpoints import leaguegamelog, leaguedashteamstats, scoreboardv3, commonallplayers

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import nba_sources
import nba_injuries
from nba_readiness import event_is_on_date, season_for_date, validate_market_picks, replace_sheet_values


class Sheet:
    def __init__(self, rows=None, fail=False):
        self.rows = rows or []
        self.fail = fail
        self.row_count, self.col_count = 1000, 150
        self.clear_calls = []
    def get_all_values(self):
        return self.rows
    def get_all_records(self):
        return [dict(zip(self.rows[0], row)) for row in self.rows[1:]] if self.rows else []
    def resize(self, rows, cols):
        self.row_count, self.col_count = rows, cols
    def update(self, values, **kwargs):
        if self.fail:
            raise RuntimeError('Simulated write failure')
        self.rows = values
    def batch_clear(self, ranges):
        self.clear_calls.extend(ranges)
    def append_rows(self, rows, **kwargs):
        self.rows.extend(rows)
    def append_row(self, row, **kwargs):
        self.rows.append(row)


class Workbook:
    def __init__(self, seed):
        self.tabs = {'Player_Stats': Sheet([list(seed.columns)] + seed.values.tolist())}
    def worksheet(self, name):
        if name not in self.tabs:
            raise gspread.exceptions.WorksheetNotFound(name)
        return self.tabs[name]
    def add_worksheet(self, title, rows, cols):
        self.tabs[title] = Sheet()
        return self.tabs[title]


class Response:
    status_code = 200
    headers = {}
    def __init__(self, payload):
        self.payload = payload
    def json(self):
        return self.payload


class FrameResponse:
    def __init__(self, frame):
        self.frame = frame
    def get_data_frames(self):
        return [self.frame.copy()]


def log_row(date, team='BOS', points=10):
    return {'PLAYER_ID':1,'PLAYER_NAME':'Example Player','TEAM_ABBREVIATION':team,
            'GAME_DATE':date,'MATCHUP':f'{team} vs. NYK','WL':'W','MIN':30,
            'PTS':points,'REB':4,'AST':3,'STL':1,'BLK':0,'TOV':2,
            'FG3M':1,'FG3A':3,'FGA':12,'FTA':2,'FGM':4}


class SafetyTests(unittest.TestCase):
    def test_season_boundaries(self):
        self.assertEqual(season_for_date('2026-10-20'), '2026-27')
        self.assertEqual(season_for_date('2026-04-20'), '2025-26')
    def test_eastern_event_date(self):
        self.assertTrue(event_is_on_date({'commence_time':'2026-10-21T02:00:00Z'}, '2026-10-20'))
        self.assertFalse(event_is_on_date({'commence_time':'2026-10-21T02:00:00Z'}, '2026-10-21'))
        self.assertFalse(event_is_on_date({'commence_time':'2026-10-20T20:00:00'}, '2026-10-20'))
        self.assertFalse(event_is_on_date({}, '2026-10-20'))
    def test_only_real_lines_and_priced_sides(self):
        market = {'PLAYER_NAME':'Player','METRIC':'PTS','DK_LINE':20.5,'OVER_ODDS':-110,'UNDER_ODDS':None}
        good = {'player':'Player','prop_type':'PTS','line':20.5,'lean':'OVER'}
        picks = [good, {**good,'line':21.5}, {**good,'line':None}, {**good,'lean':'UNDER'}, {**good,'player':'Invented'}]
        self.assertEqual(len(validate_market_picks(picks,[market],str.lower)),1)
    def test_failed_write_preserves_old_rows(self):
        ws=Sheet([['old'],['keep']],fail=True)
        with self.assertRaises(RuntimeError): replace_sheet_values(ws,[['new']])
        self.assertEqual(ws.rows,[['old'],['keep']])
        self.assertEqual(ws.clear_calls,[])
    def test_success_clears_obsolete_tails(self):
        ws=Sheet([['a','b'],['c','d'],['e','f']])
        replace_sheet_values(ws,[['a']])
        self.assertEqual(ws.clear_calls,['A2:B3','B1:B1'])


class GraderTests(unittest.TestCase):
    def test_valid_and_invalid_pick_grading(self):
        tree=ast.parse((ROOT/'NBAGrader5-4.py').read_text())
        funcs=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'safe_float','grade_pick'}]
        namespace={'math':math}
        exec(compile(ast.Module(body=funcs,type_ignores=[]),'<grader-helpers>','exec'),namespace)
        grade=namespace['grade_pick']
        self.assertEqual(grade(21,20.5,'OVER'),('YES','HIT'))
        self.assertEqual(grade(20,20.5,'UNDER'),('YES','HIT'))
        self.assertEqual(grade(20,20,'OVER'),('PUSH','PUSH'))
        self.assertEqual(grade(20,20.5,'garbage'),('',''))
        self.assertEqual(grade(float('nan'),20.5,'OVER'),('',''))


class EngineTests(unittest.TestCase):
    def run_engine(self, *, games=True, current_logs=True, logs_fail=False, ai=False, bad_line=False):
        today=datetime.now(ZoneInfo('America/New_York')).date().isoformat()
        season=season_for_date(today)
        prior=f'{int(season[:4])-1}-12-01'
        seed=pd.DataFrame([log_row(prior, points=99)])
        fresh=pd.DataFrame([log_row(today, points=12)]) if current_logs else seed.iloc[:0].copy()
        book=Workbook(seed)
        roster=pd.DataFrame([{'DISPLAY_FIRST_LAST':'Example Player','TEAM_ABBREVIATION':'LAL'}])
        event={'id':'today','commence_time':today+'T23:59:59-04:00','home_team':'Los Angeles Lakers','away_team':'New York Knicks'}
        def get(url, **kwargs):
            if url.endswith('/events'): return Response([event] if games else [])
            if url.endswith('/odds'):
                markets=[{'key':'player_points','outcomes':[
                    {'description':'Example Player','name':'Over','point':10.5,'price':-110},
                    {'description':'Example Player','name':'Under','point':10.5,'price':-110}]}] if ai else []
                return Response([] if '/events/' not in url else {'bookmakers':[{'key':'draftkings','markets':markets}]})
            raise AssertionError(f'Unexpected request {url}')
        def logs(**kwargs):
            if logs_fail: raise RuntimeError('Simulated NBA timeout')
            return FrameResponse(fresh if kwargs['season_type_all_star']=='Regular Season' else fresh.iloc[:0])
        client=type('Client',(),{'open_by_key':lambda self,key:book})()
        with contextlib.ExitStack() as stack:
            for obj,attr,value in [
                (gspread,'authorize',lambda creds:client),
                (leaguegamelog,'LeagueGameLog',logs),
                (commonallplayers,'CommonAllPlayers',lambda **kwargs:FrameResponse(roster)),
                (leaguedashteamstats,'LeagueDashTeamStats',lambda **kwargs:(_ for _ in ()).throw(RuntimeError('Team feed unavailable'))),
            ]: stack.enter_context(patch.object(obj,attr,value))
            stack.enter_context(patch('google.oauth2.service_account.Credentials.from_service_account_info',return_value=object()))
            stack.enter_context(patch('requests.get',side_effect=get))
            stack.enter_context(patch.object(nba_injuries,'fetch_official_report',return_value={'slate_date':today,'source':'fixture','reported_at':datetime.now(ZoneInfo('UTC')).isoformat(),'official':True,'submitted_teams':['LAL','NYK'],'players':[]}))
            stack.enter_context(patch.object(nba_sources,'schedule',return_value=[{'homeTeam':{'teamTricode':'LAL'},'awayTeam':{'teamTricode':'NYK'},'gameTimeUTC':today+'T23:59:59-04:00'}] if games else []))
            stack.enter_context(patch.object(nba_sources,'refresh_seed',side_effect=RuntimeError('Fresh NBA logs unavailable')))
            if ai:
                pick={'rank':1,'player':'Example Player','team':'WRONG','game':'LAL @ NYK',
                      'prop_type':'PTS','line':99.5 if bad_line else 10.5,'lean':'OVER',
                      'confidence':'STRONG','rationale':'Fixture only','injury_context':''}
                model=SimpleNamespace(generate_content=lambda **kwargs:SimpleNamespace(text=json.dumps([pick])))
                stack.enter_context(patch('google.genai.Client',return_value=SimpleNamespace(models=model)))
            stack.enter_context(patch('time.sleep'))
            stack.enter_context(patch('atexit.register'))
            stack.enter_context(patch('sys.excepthook'))
            stack.enter_context(patch.dict(os.environ,{'GOOGLE_SERVICE_ACCOUNT_JSON':'{}','ODDS_API_KEY':'fixture','GITHUB_ACTIONS':'true','GEMINI_API_KEY':'fixture' if ai else '', 'NBA_BACKUP_DIR':'/tmp/nba-test-backups'}))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            try:
                runpy.run_path(str(ROOT/'NBAEnginev5-4.py'))
            except SystemExit as exc:
                if exc.code not in (0,None): raise
        return book, today
    def test_scheduled_refresh_trade_and_season_isolation(self):
        book,today=self.run_engine()
        logs=book.tabs['Player_Stats'].get_all_records()
        self.assertEqual(len(logs),2)
        current=next(r for r in logs if r['GAME_DATE']==today)
        self.assertEqual(current['Seas_PTS'],12)
        tonight=book.tabs['Tonights_Opponent'].get_all_records()[0]
        self.assertEqual(tonight['CURRENT_TEAM'],'LAL')
        self.assertEqual(tonight['TONIGHT_OPP'],'NYK')
        self.assertEqual(tonight['L5_GAMES_PLAYED'],1)
        self.assertTrue(tonight['LIMITED_SAMPLE'])
        self.assertTrue(tonight['COMMENCE_TIME'])
        self.assertEqual(book.tabs['NBA_Status'].get_all_records()[0]['STATUS'],'DEGRADED')
    def test_opening_week_does_not_relabel_prior_stats(self):
        book,_=self.run_engine(current_logs=False)
        rows=book.tabs['Tonights_Opponent'].get_all_records()
        self.assertEqual(rows[0]['BASELINE_TYPE'],'PRIOR_SEASON')
        self.assertEqual(rows[0]['CURRENT_GAMES'],0)
        self.assertNotEqual(rows[0]['ANALYSIS_SEASON'],book.tabs['NBA_Status'].get_all_records()[0]['SEASON'])
    def test_off_day_publishes_empty_snapshot(self):
        book,_=self.run_engine(games=False)
        status=book.tabs['NBA_Status'].get_all_records()[0]
        self.assertIsNotNone(datetime.fromisoformat(status['UPDATED_AT_UTC']).tzinfo)
        self.assertEqual(status['STATUS'],'NO_GAMES')
        self.assertEqual(book.tabs['DK_Player_Props'].get_all_records(),[])
    def test_off_day_skips_unavailable_player_log_api(self):
        book,_=self.run_engine(games=False,logs_fail=True)
        self.assertEqual(book.tabs['NBA_Status'].get_all_records()[0]['STATUS'],'NO_GAMES')
    def test_ai_pick_matches_market_and_uses_verified_team(self):
        book,_=self.run_engine(ai=True)
        picks=book.tabs['Daily_Picks'].get_all_records()
        self.assertEqual(len(picks),1)
        self.assertEqual(picks[0]['line'],10.5)
        self.assertEqual(picks[0]['team'],'LAL')
        self.assertEqual(picks[0]['opponent'],'NYK')
        self.assertEqual(picks[0]['BOOK'],'draftkings')
    def test_ai_invented_line_never_published(self):
        book,_=self.run_engine(ai=True,bad_line=True)
        self.assertNotIn('Daily_Picks',book.tabs)
    def test_log_failure_stops_publication(self):
        with self.assertRaisesRegex(RuntimeError,'Fresh NBA logs unavailable'):
            self.run_engine(logs_fail=True)


if __name__=='__main__': unittest.main()
