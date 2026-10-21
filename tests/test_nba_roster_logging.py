"""Roster API identity and real-process crash reporting regressions."""
from pathlib import Path
import json
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import nba_sources

class RosterLoggingTests(unittest.TestCase):
    def test_all_thirty_rosters_use_verified_numeric_ids(self):
        codes=sorted(nba_sources.ESPN_TEAM_IDS)
        self.assertEqual(len(codes),30)
        self.assertEqual(len(set(nba_sources.ESPN_TEAM_IDS.values())),30)
        self.assertEqual(nba_sources.ESPN_TEAM_IDS['NOP'],'3')
        games=[{'homeTeam':{'teamTricode':codes[i]},'awayTeam':{'teamTricode':codes[i+1]}} for i in range(0,30,2)]
        by_id={value:key for key,value in nba_sources.ESPN_TEAM_IDS.items()}
        aliases={'NOP':'NO','GSW':'GS','NYK':'NY','SAS':'SA','UTA':'UTAH','WAS':'WSH'}
        def response(path,**kwargs):
            self.assertRegex(path,r'^/teams/\d+/roster$')
            team=by_id[path.split('/')[2]]
            return {'team':{'abbreviation':aliases.get(team,team)},'athletes':[{'id':'123','displayName':team+' Player'}]}
        with patch.object(nba_sources,'_get',side_effect=response) as get:
            rows=nba_sources.current_roster(games)
        self.assertEqual(len(rows),30);self.assertEqual(set(rows.TEAM_ABBREVIATION),set(codes));self.assertEqual(get.call_count,30)

    def test_wrong_roster_still_stops_publication(self):
        games=[{'homeTeam':{'teamTricode':'NOP'},'awayTeam':{'teamTricode':'BOS'}}]
        with patch.object(nba_sources,'_get',return_value={'team':{'abbreviation':'DET'},'athletes':[{'id':'1','displayName':'Wrong Player'}]}):
            with self.assertRaisesRegex(ValueError,'Roster mismatch'):nba_sources.current_roster(games)

    def test_uncaught_error_logs_crash_even_after_status_write(self):
        root=str(Path(__file__).resolve().parents[1])
        program='''import atexit,json,sys
sys.path.insert(0,ROOT)
from run_logger import RunLogger
class Sheet:
 def row_values(self,n):return RunLogger.HEADERS
 def append_row(self,row,**kwargs):print('SAVED_ROW='+json.dumps(row))
class Book:
 def worksheet(self,name):return Sheet()
class Client:
 def open_by_key(self,key):return Book()
logger=RunLogger(Client(),'fixture','NBA','engine')
logger.record_write('NBA_Status',1)
logger.install_exception_hook(on_failure=lambda:print('ENGINE_STATUS=FAILED'))
atexit.register(logger.finalize_and_write)
raise RuntimeError('simulated roster failure')
'''.replace('ROOT',repr(root))
        result=subprocess.run([sys.executable,'-c',program],capture_output=True,text=True)
        self.assertEqual(result.returncode,1)
        self.assertIn('ENGINE_STATUS=FAILED',result.stdout)
        row=json.loads(next(line.removeprefix('SAVED_ROW=') for line in result.stdout.splitlines() if line.startswith('SAVED_ROW=')))
        self.assertEqual(row[6],'CRASH');self.assertIn('simulated roster failure',row[16])
        self.assertIn('RuntimeError: simulated roster failure',result.stderr)

if __name__=='__main__':unittest.main()
