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

def annotate(frame, book):
    receipts = records(book)
    frame = frame.copy()
    frame["publication_cohort"] = [cohort(row, receipts) for row in frame.to_dict("records")]
    return frame

