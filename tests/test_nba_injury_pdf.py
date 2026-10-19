from datetime import datetime,timezone
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import nba_injuries
from nba_readiness import slate_market_events

TEAMS={'Boston Celtics':'BOS','Detroit Pistons':'DET','Philadelphia 76ers':'PHI','New York Knicks':'NYK','Oklahoma City Thunder':'OKC','San Antonio Spurs':'SAS'}

class OfficialPDFTests(unittest.TestCase):
    def report(self,date='2026-10-20'):
        return nba_injuries.report_from_pdf((ROOT/'tests/fixtures/nba-injury-2026-10-19-1630.pdf').read_bytes(),date,TEAMS,'NBA official fixture')
    def test_real_pdf_timestamp_rows_and_submission_states(self):
        report=self.report()
        self.assertEqual(report['reported_at'],'2026-10-19T16:30:00-04:00')
        self.assertEqual(report['submitted_teams'],['BOS'])
        self.assertEqual(set(report['unsubmitted_teams']),{'DET','PHI','NYK','OKC','SAS'})
        self.assertEqual(report['players'][0]['player'],'Devin Carter')
        self.assertEqual(report['players'][0]['status'],'OUT')
        self.assertNotIn('Page',report['players'][0]['detail'])
    def test_partial_report_clears_only_submitted_team(self):
        roster=[{'PLAYER_NAME':'Devin Carter','CURRENT_TEAM':'BOS'},{'PLAYER_NAME':'Jayson Tatum','CURRENT_TEAM':'BOS'},{'PLAYER_NAME':'Cade Cunningham','CURRENT_TEAM':'DET'}]
        result=nba_injuries.apply_availability(roster,self.report(),'2026-10-20',datetime(2026,10,19,20,35,tzinfo=timezone.utc))
        self.assertEqual([row['AVAILABILITY'] for row in result],['OUT','AVAILABLE','UNKNOWN'])
    def test_other_slate_never_infers_healthy(self):
        with self.assertRaises(ValueError):self.report('2026-10-21')
    def test_fetch_errors_preserve_diagnostic_details(self):
        class HTTP:
            def get(self,url):raise nba_injuries.FeedError('HTTP 403')
        report=nba_injuries.fetch_official_report(HTTP(),'2026-10-20',TEAMS,datetime(2026,10,19,20,35,tzinfo=timezone.utc))
        self.assertEqual(len(report['errors']),7)
        self.assertEqual(report['errors'][0]['detail'],'HTTP 403')
        self.assertIn('2026-10-19_04_30PM.pdf',report['errors'][0]['url'])
    def test_vegas_events_require_both_target_date_and_scheduled_pair(self):
        valid={'id':'opening','home_team':'Detroit Pistons','away_team':'Boston Celtics','commence_time':'2026-10-20T19:00:00Z'}
        later={**valid,'id':'later','commence_time':'2026-10-23T19:00:00Z'}
        wrong_pair={**valid,'id':'wrong','away_team':'New York Knicks'}
        result=slate_market_events([valid,later,wrong_pair],'2026-10-20',{'BOS':'DET','DET':'BOS','NYK':'PHI','PHI':'NYK'},TEAMS)
        self.assertEqual([row['id'] for row in result],['opening'])

if __name__=='__main__':unittest.main()
