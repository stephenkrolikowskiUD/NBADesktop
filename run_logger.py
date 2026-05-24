"""Run_Log heartbeat writer. One row per engine/grader run, written to Run_Log sheet."""
import os, json
from datetime import datetime
import pytz

class RunLogger:
    HEADERS = [
        'run_id', 'sport', 'kind', 'started_at', 'finished_at', 'duration_sec',
        'status', 'trigger', 'rows_written', 'picks_generated', 'picks_graded',
        'hits', 'misses', 'dnp_count', 'not_found_count', 'warnings', 'error', 'git_sha',
    ]

    def __init__(self, gspread_client, sheet_id, sport, kind):
        self.gc = gspread_client
        self.sheet_id = sheet_id
        self.sport = sport
        self.kind = kind
        self.eastern = pytz.timezone('US/Eastern')
        self.started_at_dt = datetime.now(self.eastern)
        self.started_at = self.started_at_dt.strftime('%Y-%m-%d %H:%M:%S %Z')
        utc_iso = datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')
        self.run_id = f"{sport}-{kind}-{utc_iso}"
        self.rows_written = {}
        self.warnings = []
        self.picks_generated = 0
        self.picks_graded = 0
        self.hits = 0
        self.misses = 0
        self.dnp_count = 0
        self.not_found_count = 0
        self.error = ""
        self.status = "OK"

    def record_write(self, sheet_name, row_count):
        try:
            self.rows_written[sheet_name] = int(row_count or 0)
        except (TypeError, ValueError):
            self.rows_written[sheet_name] = 0

    def warn(self, msg):
        self.warnings.append(str(msg)[:200])

    def fail(self, exc):
        self.status = "CRASH"
        self.error = f"{type(exc).__name__}: {str(exc)[:300]}"

    def finalize_and_write(self):
        finished_dt = datetime.now(self.eastern)
        finished_at = finished_dt.strftime('%Y-%m-%d %H:%M:%S %Z')
        duration = int((finished_dt - self.started_at_dt).total_seconds())
        if self.status == "OK" and self.warnings:
            self.status = "WARN"
        if self.status == "OK" and self.kind == "engine" and not self.rows_written:
            self.status = "FAIL"
            self.error = self.error or "No sheets written"
        row = [
            self.run_id, self.sport, self.kind,
            self.started_at, finished_at, duration, self.status,
            os.environ.get('GITHUB_EVENT_NAME', 'local'),
            json.dumps(self.rows_written),
            self.picks_generated, self.picks_graded,
            self.hits, self.misses, self.dnp_count, self.not_found_count,
            "; ".join(self.warnings)[:500],
            self.error,
            os.environ.get('GITHUB_SHA', 'local')[:7],
        ]
        try:
            sh = self.gc.open_by_key(self.sheet_id)
            try:
                ws = sh.worksheet('Run_Log')
            except Exception:
                ws = sh.add_worksheet(title='Run_Log', rows=1000, cols=len(self.HEADERS))
                ws.append_row(self.HEADERS)
            ws.append_row(row, value_input_option='RAW')
            print(f"📝 Run_Log: {self.status} ({duration}s) — {self.run_id}")
        except Exception as e:
            print(f"⚠️ Failed to write Run_Log row: {e}")
