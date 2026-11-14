import unittest
from datetime import datetime, timezone
from publication_ledger import reconstruct, annotate
from test_publication_ledger import Book, identify, publish_receipts

class ReconstructionTests(unittest.TestCase):
    def setUp(self):
        self.row = {"player": "Example", "RUN_ID": "publication-1",
                    "RUN_TIME": "2026-11-13 09:05:00 AM EST",
                    "COMMENCE_TIME": "2026-11-14T00:00:00Z"}
        self.run = {"run_id": "engine-1", "publication_run_id": "publication-1",
                    "sport": "NBA", "kind": "engine", "status": "OK", "error": "",
                    "started_at": "2026-11-13 09:00:00 EST",
                    "finished_at": "2026-11-13 09:10:00 EST",
                    "rows_written": '{"Daily_Picks": 5}'}
    def result(self, row=None, runs=None):
        return reconstruct(row or self.row, runs if runs is not None else [self.run], "NBA")[0]
    def test_morning_success(self):
        self.assertEqual(self.result(), "RECONSTRUCTED_PREGAME_FEED")
    def test_late_finish_even_when_started_early(self):
        self.assertEqual(self.result(runs=[dict(self.run, finished_at="2026-11-13 19:01:00 EST")]),
                         "LATE_OR_UNVERIFIED")
    def test_at_start_is_not_pregame(self):
        self.assertEqual(self.result(runs=[dict(self.run, finished_at="2026-11-13 19:00:00 EST")]),
                         "LATE_OR_UNVERIFIED")
    def test_failure_or_missing_picks_write(self):
        for changes in ({"status":"FAIL"}, {"status":"CRASH"}, {"status":"SKIP"},
                        {"error":"failed"}, {"rows_written":'{"Schedule": 10}'},
                        {"rows_written":"invalid"}, {"publication_status":"FAILED"}):
            self.assertEqual(self.result(runs=[dict(self.run, **changes)]), "UNVERIFIED_CAPTURE")
    def test_wrong_sport_and_grader(self):
        for changes in ({"sport":"NHL"}, {"kind":"grader"}):
            self.assertEqual(self.result(runs=[dict(self.run, **changes)]), "UNVERIFIED_CAPTURE")
    def test_unique_legacy_interval(self):
        row = dict(self.row, RUN_ID="", RUN_TIME="2026-11-13 09:05 AM EST")
        self.assertEqual(self.result(row), "RECONSTRUCTED_PREGAME_FEED")
        self.assertEqual(self.result(row, [self.run, dict(self.run,run_id="other",status="FAIL")]),
                         "UNVERIFIED_CAPTURE")
    def test_no_time_guessing(self):
        for value in ("", "2026-11-13 09:05", "bad"):
            self.assertEqual(self.result(dict(self.row,RUN_ID="",RUN_TIME=value)), "UNVERIFIED_CAPTURE")
    def test_unknown_id_does_not_match_other_publication(self):
        self.assertEqual(self.result(dict(self.row,RUN_ID="another")), "UNVERIFIED_CAPTURE")
    def test_missing_game_time(self):
        self.assertEqual(self.result(dict(self.row,COMMENCE_TIME="")), "UNVERIFIED_CAPTURE")
    def test_dst_offset(self):
        row = dict(self.row,COMMENCE_TIME="2026-10-30T13:30:00Z")
        run = dict(self.run,started_at="2026-10-30 09:00:00 EDT",finished_at="2026-10-30 09:10:00 EDT")
        self.assertEqual(self.result(row,[run]), "RECONSTRUCTED_PREGAME_FEED")
    def test_direct_receipt_not_downgraded_or_rescued(self):
        import pandas as pd
        rows=identify([self.row],"publication-1","NBA")
        book=Book(rows)
        publish_receipts(book,rows,"NBA",datetime(2026,11,13,15,tzinfo=timezone.utc))
        result=annotate(pd.DataFrame(rows),book,"NBA")
        self.assertEqual(result.iloc[0]["publication_cohort"],"VERIFIED_PREGAME_FEED")
        rows[0]["line"]=999
        self.assertEqual(annotate(pd.DataFrame(rows),book,"NBA").iloc[0]["publication_cohort"],
                         "UNVERIFIED_CAPTURE")
    def test_missing_run_log_no_backfill(self):
        import pandas as pd
        self.assertEqual(annotate(pd.DataFrame([self.row]),Book([]),"NBA").iloc[0]["publication_cohort"],
                         "UNVERIFIED_CAPTURE")
if __name__ == "__main__": unittest.main()
