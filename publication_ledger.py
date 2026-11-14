"""Verified feed-publication receipts; research status is unchanged."""
import hashlib
import json
import math
from datetime import datetime, timezone

TAB = "Publication_Ledger"
FIELDS = ["PUBLICATION_ID", "RUN_ID", "SURFACE", "VERIFIED_AT_UTC",
          "TIMING", "SNAPSHOT_JSON"]
SNAPSHOT_FIELDS = ("DATE", "GAME_DATE", "EVENT_ID", "COMMENCE_TIME", "player",
                   "team", "prop_type", "lean", "line", "BOOK", "ODDS",
                   "QUOTE_TIME", "PICK_BOOK", "PICK_ODDS", "PICK_LINE",
                   "PRICE_CAPTURED_AT")
NUMERIC = {"line", "ODDS", "PICK_ODDS", "PICK_LINE"}

def snapshot(row):
    result = {}
    for key in SNAPSHOT_FIELDS:
        value = row.get(key, "")
        value = "" if value is None else str(value).strip()
        if value.lower() in {"nan", "none"}:
            value = ""
        if key in NUMERIC and value:
            try:
                number = float(value)
                value = format(number, ".12g") if math.isfinite(number) else ""
            except ValueError:
                pass
        result[key] = value
    return json.dumps(result, sort_keys=True, ensure_ascii=True)

def identify(rows, run_id, sport):
    output = []
    for row in rows:
        row = dict(row)
        payload = sport + "|" + run_id + "|" + snapshot(row)
        row["PUBLICATION_ID"] = hashlib.sha256(payload.encode()).hexdigest()
        row["RUN_ID"] = run_id
        output.append(row)
    return output

def timestamp(value):
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("Timezone required")
    return dt

def records(book):
    try:
        return book.worksheet(TAB).get_all_records()
    except Exception as exc:
        if exc.__class__.__name__ == "WorksheetNotFound":
            return []
        raise

def publish_receipts(book, expected, sport, now=None):
    if not expected:
        return
    saved = book.worksheet("Daily_Picks").get_all_records()
    now = now or datetime.now(timezone.utc)
    receipts = []
    for row in expected:
        matches = [r for r in saved if r.get("PUBLICATION_ID") == row["PUBLICATION_ID"]]
        if len(matches) != 1 or snapshot(matches[0]) != snapshot(row):
            raise RuntimeError("Published selection readback mismatch")
        try:
            pregame = now < timestamp(row.get("COMMENCE_TIME"))
        except (ValueError, TypeError):
            pregame = False
        receipts.append(dict(zip(FIELDS, [
            row["PUBLICATION_ID"], row["RUN_ID"], sport + "_RESEARCH_PICKS_FEED",
            now.isoformat(), "PREGAME" if pregame else "LATE_OR_UNVERIFIED",
            snapshot(row)])))
    existing = records(book)
    ids = {r.get("PUBLICATION_ID") for r in existing}
    new = [r for r in receipts if r["PUBLICATION_ID"] not in ids]
    try:
        ws = book.worksheet(TAB)
    except Exception as exc:
        if exc.__class__.__name__ != "WorksheetNotFound":
            raise
        ws = book.add_worksheet(title=TAB, rows=1000, cols=len(FIELDS))
        ws.update(values=[FIELDS], range_name="A1")
    if ws.row_values(1) != FIELDS:
        raise RuntimeError("Unexpected publication ledger schema")
    if new:
        ws.append_rows([[r[k] for k in FIELDS] for r in new], value_input_option="RAW")
    verified = records(book)
    for row in receipts:
        matches = [r for r in verified if r.get("PUBLICATION_ID") == row["PUBLICATION_ID"]]
        if len(matches) != 1 or matches[0]["SNAPSHOT_JSON"] != row["SNAPSHOT_JSON"]:
            raise RuntimeError("Publication receipt verification failed")

def cohort(row, receipts):
    matches = [r for r in receipts if r.get("PUBLICATION_ID") == row.get("PUBLICATION_ID")
               and row.get("PUBLICATION_ID")]
    if len(matches) != 1:
        return "UNVERIFIED_CAPTURE"
    receipt = matches[0]
    if receipt.get("SNAPSHOT_JSON") != snapshot(row):
        return "UNVERIFIED_CAPTURE"
    try:
        before_start = timestamp(receipt["VERIFIED_AT_UTC"]) < timestamp(row["COMMENCE_TIME"])
    except (ValueError, TypeError, KeyError):
        before_start = False
    return "VERIFIED_PREGAME_FEED" if before_start and receipt.get("TIMING") == "PREGAME" else "LATE_OR_UNVERIFIED"

def log_timestamp(value):
    """Parse historical run timestamps without assuming the machine timezone."""
    text = str(value).strip()
    try:
        return timestamp(text)
    except (ValueError, TypeError):
        pass
    offsets = {"EST": "-0500", "EDT": "-0400", "PST": "-0800", "PDT": "-0700"}
    parts = text.rsplit(" ", 1)
    if len(parts) != 2 or parts[1] not in offsets:
        raise ValueError("Explicit timezone required")
    text = parts[0] + " " + offsets[parts[1]]
    for fmt in ("%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %I:%M:%S %p %z",
                "%Y-%m-%d %I:%M %p %z"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise ValueError("Unsupported run timestamp")


def reconstruct(row, runs, sport):
    """Infer saved-feed availability, never manufacture a direct receipt."""
    candidates = [r for r in runs if str(r.get("sport", "")).upper() == sport
                  and str(r.get("kind", "")).lower() == "engine"]
    run_id = str(row.get("RUN_ID", "")).strip()
    matched = [r for r in candidates if run_id and run_id in
               (str(r.get("run_id", "")), str(r.get("publication_run_id", "")))]
    basis = "run ID"
    if not matched:
        # A known publication ID must not be reassigned to a different run.
        if run_id and any(str(r.get("publication_run_id", "")).strip() for r in candidates):
            return "UNVERIFIED_CAPTURE", "", "", "No matching run ID"
        try:
            pick_time = log_timestamp(row.get("RUN_TIME", ""))
        except ValueError:
            return "UNVERIFIED_CAPTURE", "", "", "Missing or invalid run time"
        # Old NHL times have minute precision. Match their possible 60-second
        # interval; overlapping runs remain ambiguous even if one failed.
        from datetime import timedelta
        import re
        minute_only = bool(re.search(r"\d{1,2}:\d{2} [AP]M ", str(row.get("RUN_TIME", ""))))
        upper = pick_time + timedelta(seconds=59 if minute_only else 0)
        matched = []
        for run in candidates:
            try:
                start, end = log_timestamp(run["started_at"]), log_timestamp(run["finished_at"])
                if start <= end and start <= upper and pick_time <= end:
                    matched.append(run)
            except (ValueError, KeyError):
                continue
        basis = "unique run-time interval"
    if len(matched) != 1:
        return "UNVERIFIED_CAPTURE", "", "", "Missing or ambiguous engine run"
    run = matched[0]
    evidence_id = str(run.get("run_id", ""))
    try:
        start, end = log_timestamp(run["started_at"]), log_timestamp(run["finished_at"])
        game = timestamp(row["COMMENCE_TIME"])
        written = json.loads(run.get("rows_written", "{}"))
        writes = int(written.get("Daily_Picks", 0))
    except (ValueError, TypeError, KeyError, AttributeError):
        return "UNVERIFIED_CAPTURE", evidence_id, "", "Missing write or game-time evidence"
    finished = end.isoformat()
    if (str(run.get("status", "")).upper() not in {"OK", "WARN"}
            or str(run.get("error", "")).strip()
            or str(run.get("publication_status", "")).upper() == "FAILED"
            or writes <= 0 or end < start or not evidence_id):
        return "UNVERIFIED_CAPTURE", evidence_id, finished, "Run failed or no picks write logged"
    if end >= game:
        return "LATE_OR_UNVERIFIED", evidence_id, finished, "Run finished at or after game start"
    return "RECONSTRUCTED_PREGAME_FEED", evidence_id, finished, basis + "; successful picks write before game"


def run_records(book):
    try:
        return book.worksheet("Run_Log").get_all_records()
    except Exception as exc:
        if exc.__class__.__name__ == "WorksheetNotFound":
            return []
        raise


def annotate(frame, book, sport):
    receipts, runs = records(book), run_records(book)
    frame = frame.copy()
    evidence = []
    for row in frame.to_dict("records"):
        direct = cohort(row, receipts)
        if row.get("PUBLICATION_ID") and str(row["PUBLICATION_ID"]).lower() != "nan":
            # A failed or altered direct receipt cannot be rescued by inference.
            evidence.append((direct, "", "", "Direct receipt policy"))
        else:
            evidence.append(reconstruct(row, runs, sport))
    for index, key in enumerate(("publication_cohort", "publication_evidence_run_id",
                                 "publication_evidence_finished_at", "publication_evidence_reason")):
        frame[key] = [item[index] for item in evidence]
    return frame

