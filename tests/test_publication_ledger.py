import unittest
from datetime import datetime, timezone
from publication_ledger import identify, snapshot, cohort, publish_receipts, FIELDS, TAB

class WorksheetNotFound(Exception): pass
class Sheet:
    def __init__(self, rows=None): self.rows=rows or []; self.header=FIELDS
    def get_all_records(self): return self.rows
    def row_values(self, n): return self.header
    def update(self, values, range_name): self.header=values[0]
    def append_rows(self, rows, **kwargs): self.rows += [dict(zip(self.header,r)) for r in rows]
class Book:
    def __init__(self, rows): self.sheets={"Daily_Picks":Sheet(rows)}
    def worksheet(self,name):
        if name not in self.sheets: raise WorksheetNotFound()
        return self.sheets[name]
    def add_worksheet(self,title,**kwargs):
        self.sheets[title]=Sheet(); return self.sheets[title]

class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.rows=identify([{"player":"Example","line":2.5,"lean":"OVER",
            "COMMENCE_TIME":"2026-11-13T23:00:00Z","PICK_ODDS":-110,"PICK_BOOK":"book"}],"run","TEST")
        self.now=datetime(2026,11,13,20,tzinfo=timezone.utc)
    def test_verified_and_idempotent(self):
        book=Book(self.rows)
        publish_receipts(book,self.rows,"TEST",self.now)
        publish_receipts(book,self.rows,"TEST",self.now)
        receipts=book.worksheet(TAB).rows
        self.assertEqual(len(receipts),1)
        self.assertEqual(cohort(self.rows[0],receipts),"VERIFIED_PREGAME_FEED")
        self.assertEqual(cohort(dict(self.rows[0],HIT="YES"),receipts),"VERIFIED_PREGAME_FEED")
        self.assertEqual(cohort(dict(self.rows[0],line=3.5),receipts),"UNVERIFIED_CAPTURE")
    def test_no_backfill(self):
        self.assertEqual(cohort(self.rows[0],[]),"UNVERIFIED_CAPTURE")
        self.assertEqual(cohort({},[]),"UNVERIFIED_CAPTURE")
    def test_late(self):
        book=Book(self.rows)
        publish_receipts(book,self.rows,"TEST",datetime(2026,11,14,tzinfo=timezone.utc))
        self.assertEqual(cohort(self.rows[0],book.worksheet(TAB).rows),"LATE_OR_UNVERIFIED")
    def test_failed_readback(self):
        book=Book([dict(self.rows[0],PICK_ODDS=-120)])
        with self.assertRaises(RuntimeError): publish_receipts(book,self.rows,"TEST",self.now)
        self.assertNotIn(TAB,book.sheets)
    def test_numeric_roundtrip(self):
        self.assertEqual(snapshot({"line":2}),snapshot({"line":"2.0"}))
    def test_new_run_new_identity(self):
        self.assertNotEqual(self.rows[0]["PUBLICATION_ID"],
            identify(self.rows,"new-run","TEST")[0]["PUBLICATION_ID"])
if __name__ == "__main__": unittest.main()

