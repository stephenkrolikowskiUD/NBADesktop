"""Cross-sport first-selection reporting. Does not change source grades."""
import math
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pandas as pd

POLICY = "first-pregame-selection-v1"
ET = ZoneInfo("America/New_York")
WINDOWS = {"last_7d": 7, "last_30d": 30, "last_90d": 90, "all_time": None}
COLUMNS = ["POLICY", "SPORT", "SURFACE", "EVIDENCE", "TIME_WINDOW", "N_SELECTED",
           "N_HITS", "N_MISSES", "N_PUSHES", "N_DNP", "N_PENDING", "N_PRICED",
           "HIT_RATE", "NET_UNITS", "ROI_PCT"]

def clean(value):
    text = str(value).strip()
    return "" if text.lower() in {"", "none", "nan", "nat"} else text

def instant(value):
    text = clean(value)
    for suffix, offset in ((" EST", "-05:00"), (" EDT", "-04:00")):
        if text.endswith(suffix):
            wall = text[:-4]
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %I:%M:%S %p", "%Y-%m-%d %I:%M %p"):
                try:
                    text = datetime.strptime(wall, fmt).isoformat() + offset
                    break
                except ValueError:
                    pass
            break
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return result if result.tzinfo else None
    except ValueError:
        return None

def report(frame, sport, today=None):
    """Adapters supply evidence, surface, publication/start times and identity."""
    today = today or datetime.now(ET).date()
    audit, candidates = [], []
    for ordinal, row in enumerate(frame.to_dict("records")):
        published, start = instant(row.get("_report_at")), instant(row.get("_report_start"))
        identity = [clean(row.get(k)) for k in ("_report_game", "_report_player", "prop_type", "lean")]
        identity[2:] = [v.upper() for v in identity[2:]]
        evidence = clean(row.get("_report_evidence"))
        surface = clean(row.get("_report_surface"))
        reason = ""
        if evidence not in {"DIRECT", "RECONSTRUCTED", "RECORDED_PUBLICATION"}:
            reason = "unverified publication"
        elif not published or not start or published >= start:
            reason = "missing or late publication timing"
        elif start.astimezone(ET).date() > today:
            reason = "future game"
        elif not all(identity) or identity[3] not in {"OVER", "UNDER"} or not surface:
            reason = "missing selection identity"
        try:
            if not math.isfinite(float(row.get("line"))):
                reason = reason or "invalid line"
        except (ValueError, TypeError):
            reason = reason or "invalid line"
        item = dict(SOURCE_ROW=ordinal + 2, POLICY=POLICY, SPORT=sport,
                    STATUS=reason or "candidate", EVIDENCE=evidence, SURFACE=surface)
        audit.append(item)
        if not reason:
            # Model changes and price/line refreshes do not create new exposures.
            candidates.append((published, ordinal, tuple([surface] + identity), start, row, item))
    seen, selected = set(), []
    for published, ordinal, key, start, row, item in sorted(candidates, key=lambda x: (x[0], x[1])):
        if key in seen:
            item["STATUS"] = "repeat selection"
            continue
        seen.add(key)
        item["STATUS"] = "selected"
        selected.append((start.astimezone(ET).date(), row, item))
    output = []
    for window, days in WINDOWS.items():
        subset = [x for x in selected if days is None or x[0] >= today - timedelta(days=days - 1)]
        groups = {(x[2]["SURFACE"], "ALL") for x in subset}
        groups |= {(x[2]["SURFACE"], x[2]["EVIDENCE"]) for x in subset}
        for surface, evidence in sorted(groups):
            rows = [x[1] for x in subset if x[2]["SURFACE"] == surface and (evidence == "ALL" or x[2]["EVIDENCE"] == evidence)]
            counts = {v: 0 for v in ("YES", "NO", "PUSH", "DNP", "PENDING")}
            priced, profit = 0, 0.
            for row in rows:
                outcome = clean(row.get("HIT")).upper()
                counts[outcome if outcome in counts else "PENDING"] += 1
                try:
                    odds = float(row.get("_report_odds"))
                    valid_price = math.isfinite(odds) and abs(odds) >= 100 and clean(row.get("_report_book"))
                except (ValueError, TypeError):
                    valid_price = False
                if valid_price and outcome in {"YES", "NO", "PUSH"}:
                    priced += 1
                    profit += (100 / abs(odds) if odds < 0 else odds / 100) if outcome == "YES" else (-1 if outcome == "NO" else 0)
            decisive = counts["YES"] + counts["NO"]
            output.append(dict(zip(COLUMNS, [POLICY, sport, surface, evidence, window, len(rows),
                counts["YES"], counts["NO"], counts["PUSH"], counts["DNP"], counts["PENDING"], priced,
                counts["YES"]/decisive if decisive else None, profit, 100*profit/priced if priced else None])))
    return pd.DataFrame(output, columns=COLUMNS), pd.DataFrame(audit, columns=[
        "SOURCE_ROW", "POLICY", "SPORT", "STATUS", "EVIDENCE", "SURFACE"])

