# @title 🏀 NBA Daily Picks Grader — Run Morning After Games — 5-4 Baseline
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from itertools import combinations
import pytz
import math
import re
import unicodedata
import os, json
import atexit
import sys
import subprocess
import gspread
from google.auth import default
from google.oauth2.service_account import Credentials
try:
    from nba_api.stats.endpoints import leaguegamelog
except ImportError:
    print("📦 Installing missing nba_api package...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "nba_api"])
    from nba_api.stats.endpoints import leaguegamelog
try:
    from run_logger import RunLogger
except ImportError:
    class RunLogger:
        def __init__(self, *args, **kwargs):
            self.hits = 0
            self.misses = 0
            self.dnp_count = 0
            self.not_found_count = 0
            self.picks_graded = 0
        def record_write(self, *args, **kwargs):
            pass
        def warn(self, *args, **kwargs):
            pass
        def fail(self, *args, **kwargs):
            pass
        def finalize_and_write(self):
            pass

def get_gspread_client():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    svc_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON") or os.environ.get("GSPREAD_SERVICE_ACCOUNT_JSON")
    if svc_json:
        creds = Credentials.from_service_account_info(json.loads(svc_json), scopes=scopes)
        print("✅ Google auth via service account env")
        return gspread.authorize(creds)
    try:
        from google.colab import auth as colab_auth
        print("Authenticating with Google...")
        colab_auth.authenticate_user()
        creds, _ = default(scopes=scopes)
        print("✅ Google auth via Colab")
        return gspread.authorize(creds)
    except Exception as e:
        raise RuntimeError("Google auth unavailable. Set GOOGLE_SERVICE_ACCOUNT_JSON or run in Colab.") from e

gc = get_gspread_client()

SHEET_NAME = 'NBA_Dashboard_Data'
SHEET_ID = '12gBgVx_RCsIytjZHjfZWgLtG-R-zcPbYYE-CVFd4EDw'
SNAPSHOT_DATE = "2026-05-04"
sh = gc.open_by_key(SHEET_ID)
print(f"✅ Connected to Google Sheet: {SHEET_ID}")
runlog = RunLogger(gc, SHEET_ID, sport='NBA', kind='grader')
atexit.register(runlog.finalize_and_write)
RETRY_DNP_LOOKBACK_DAYS = 7
eastern = pytz.timezone('US/Eastern')
now_est = datetime.now(eastern)
today_str = now_est.strftime('%Y-%m-%d')
timestamp_est = now_est.strftime('%Y-%m-%d %I:%M:%S %p EST')
PICK_PERF_MIN_SAMPLE = 25
PICK_PERF_STANDARD_ODDS = -115
PICK_PERF_WILSON_Z = 1.96
PICK_PERF_DRIFT_ALERT_PP = 10
PICK_PERF_TIME_WINDOWS = {
    'last_7d': 7,
    'last_30d': 30,
    'last_90d': 90,
    'all_time': None,
}
PICK_PERF_SNAPSHOT_WINDOWS = ('all_time', 'last_30d')
PICK_PERF_DIMENSIONS = (
    'confidence_norm',
    'prop_type_norm',
    'lean_norm',
    'consensus_bucket',
    'clv_bucket',
    'has_lineup_risk',
    'day_of_week',
    'RUN_NUMBER',
)

# --- 2. LOAD DAILY_PICKS ---
print("\nLoading Daily_Picks...")
try:
    ws = sh.worksheet('Daily_Picks')
    all_rows = ws.get_all_values()
except Exception as e:
    print(f"❌ Could not find Daily_Picks sheet: {e}")
    raise

if len(all_rows) <= 1:
    print("⚠️ No picks to grade — sheet is empty.")
    headers = ['DATE', 'HIT']
    df_picks = pd.DataFrame(columns=headers)
else:
    headers = all_rows[0]
    data = all_rows[1:]
    df_picks = pd.DataFrame(data, columns=headers)
    print(f"📋 Found {len(df_picks)} total picks across {df_picks['DATE'].nunique()} dates")

def safe_float(val, default=None):
    if val is None:
        return default
    if isinstance(val, str):
        val = val.strip().replace(',', '')
        if not val or val.upper() in {'N/A', 'NA', 'NONE', 'NULL', 'DNP'}:
            return default
    try:
        num = float(val)
        if math.isnan(num) or math.isinf(num):
            return default
        return num
    except (TypeError, ValueError):
        return default

def current_nba_season(now=None):
    now = now or datetime.now(pytz.timezone('US/Eastern'))
    start_year = now.year if now.month >= 9 else now.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"

def fetch_nba_gamelog_df(season, season_type, max_attempts=3):
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = leaguegamelog.LeagueGameLog(
                player_or_team_abbreviation='P',
                league_id='00',
                season=season,
                season_type_all_star=season_type,
                timeout=90,
            )
            df = resp.get_data_frames()[0]
            print(f"   ✅ {season_type}: {len(df)} entries")
            return df
        except Exception as e:
            last_err = e
            print(f"   ⚠️ {season_type} fetch attempt {attempt}/{max_attempts} failed: {e}")
            if attempt < max_attempts:
                import time
                time.sleep(3 * attempt)
    raise last_err

def normalize_pick_date(val):
    s = str(val or "").strip()
    if not s:
        return ""
    if re.match(r'^\d{4}-\d{2}-\d{2}$', s):
        return s
    for fmt in ('%m/%d/%Y', '%m/%d/%y', '%Y/%m/%d'):
        try:
            return datetime.strptime(s, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    try:
        return pd.to_datetime(s).strftime('%Y-%m-%d')
    except Exception:
        return s

def normalize_person_name(name):
    text = unicodedata.normalize('NFKD', str(name or ''))
    text = ''.join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[’'`\\.]", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def find_box_score(box_lookup, player, date):
    box = box_lookup.get((player, date))
    if box is not None:
        return box
    player_norm = normalize_person_name(player)
    for (bn, bd), bv in box_lookup.items():
        if bd == date and normalize_person_name(bn) == player_norm:
            return bv
    return None

def grade_pick(actual, line_val, lean):
    if actual is None or line_val is None:
        return '', ''
    if actual == line_val:
        return 'PUSH', 'PUSH'
    if lean in ('UNDER', 'FADE'):
        return ('YES', 'HIT') if actual < line_val else ('NO', 'MISS')
    return ('YES', 'HIT') if actual > line_val else ('NO', 'MISS')

def combo_leg_label(row):
    return f"{row.get('player', '?')} {row.get('prop_type', '?')} {row.get('lean', '?')} {row.get('line', '?')}"

def print_winning_combo_tracker(df_all, dates_to_grade):
    if 'DATE' not in df_all.columns or 'HIT' not in df_all.columns:
        return
    hit_df = df_all[df_all['HIT'] == 'YES'].copy()
    if hit_df.empty:
        return
    hit_df['DATE'] = hit_df['DATE'].astype(str)
    target_dates = {str(d) for d in dates_to_grade}
    hit_df = hit_df[hit_df['DATE'].isin(target_dates)]
    if hit_df.empty:
        return
    hit_df['_run'] = pd.to_numeric(hit_df['RUN_NUMBER'], errors='coerce') if 'RUN_NUMBER' in hit_df.columns else np.nan
    print("\n   Winning Combo Tracker:")
    for date in sorted(hit_df['DATE'].unique()):
        date_df = hit_df[hit_df['DATE'] == date]
        run_vals = sorted(date_df['_run'].dropna().astype(int).unique()) if pd.Series(date_df['_run']).notna().any() else [None]
        for run_no in run_vals:
            grp = date_df if run_no is None else date_df[date_df['_run'] == run_no]
            labels = [combo_leg_label(row) for _, row in grp.iterrows()]
            if len(labels) < 2:
                continue
            combos2 = list(combinations(labels, 2))
            combos3 = list(combinations(labels, 3)) if len(labels) >= 3 else []
            header = f"   {date}" + (f" / Run {run_no}" if run_no is not None else "")
            print(f"{header}: {len(combos2)} winning 2-leg, {len(combos3)} winning 3-leg")
            if combos2:
                print(f"      2-leg ex: {' + '.join(combos2[0])}")
            if combos3:
                print(f"      3-leg ex: {' + '.join(combos3[0])}")

def print_clv_summary(df_all):
    needed = {'CLV_OPEN_LINE', 'CLV_LATEST_LINE', 'lean', 'HIT'}
    if not needed.issubset(df_all.columns):
        return
    clv_df = df_all[df_all['HIT'].isin(['YES', 'NO'])].copy()
    if clv_df.empty:
        return
    clv_df['open_line'] = pd.to_numeric(clv_df['CLV_OPEN_LINE'], errors='coerce')
    clv_df['latest_line'] = pd.to_numeric(clv_df['CLV_LATEST_LINE'], errors='coerce')
    clv_df = clv_df.dropna(subset=['open_line', 'latest_line'])
    if clv_df.empty:
        return
    clv_df['lean_norm'] = clv_df['lean'].fillna('').astype(str).str.upper().replace({'FADE': 'UNDER'})
    clv_df['clv_edge'] = np.where(clv_df['lean_norm'] == 'UNDER', clv_df['open_line'] - clv_df['latest_line'], clv_df['latest_line'] - clv_df['open_line'])
    print("\n   CLV Summary:")
    pos_df = clv_df[clv_df['clv_edge'] > 0]
    neg_df = clv_df[clv_df['clv_edge'] <= 0]
    if not pos_df.empty:
        pos_hits = len(pos_df[pos_df['HIT'] == 'YES'])
        print(f"   Positive CLV: {pos_hits}-{len(pos_df)-pos_hits} ({pos_hits/len(pos_df)*100:.0f}%) | Avg {pos_df['clv_edge'].mean():+.2f}")
    if not neg_df.empty:
        neg_hits = len(neg_df[neg_df['HIT'] == 'YES'])
        print(f"   Flat/Negative CLV: {neg_hits}-{len(neg_df)-neg_hits} ({neg_hits/len(neg_df)*100:.0f}%) | Avg {neg_df['clv_edge'].mean():+.2f}")

PICK_PERFORMANCE_COLUMNS = [
    'DIMENSION_TYPE', 'DIMENSION_VALUE', 'TIME_WINDOW',
    'N_PICKS', 'N_PICKS_DECISIVE', 'N_HITS', 'N_MISSES', 'N_PUSHES', 'N_DNP',
    'HIT_RATE', 'HIT_RATE_RAW', 'PUSH_RATE', 'DNP_RATE',
    'ROI_FLAT', 'ROI_PER_PICK',
    'AVG_CLV_EDGE', 'CLV_POSITIVE_RATE', 'CLV_POS_HIT_RATE', 'CLV_NEG_HIT_RATE',
    'WILSON_LOWER_95', 'MIN_SAMPLE_FLAG',
    'LAST_UPDATED',
]
PICK_PERFORMANCE_SNAPSHOT_COLUMNS = ['SNAPSHOT_DATE', 'METRIC_KEY', 'METRIC_VALUE', 'N_PICKS', 'TIME_WINDOW']

def normalize_prop_metric(metric):
    text = str(metric or '').strip().upper()
    text = re.sub(r"\s+", "", text)
    if text == 'BATTER_SO':
        return 'SO'
    return text

def normalize_confidence(val):
    conf = str(val or '').strip().upper()
    return conf if conf in {'SMASH', 'STRONG', 'LEAN'} else 'LEAN'

def pick_perf_clean_cell(val):
    if hasattr(val, 'item'):
        val = val.item()
    if val is None:
        return ''
    if isinstance(val, float) and (math.isnan(val) or math.isinf(val)):
        return ''
    return val

def pick_perf_safe_upload(spreadsheet, sheet_name, df):
    if df is None or df.empty:
        print(f"   ⏭️  {sheet_name}: No data — skipped")
        return False
    df_clean = df.copy().replace([np.inf, -np.inf], np.nan).fillna('')
    values = [df_clean.columns.tolist()] + [
        [pick_perf_clean_cell(v) for v in row]
        for row in df_clean.values.tolist()
    ]
    try:
        try:
            ws_out = spreadsheet.worksheet(sheet_name)
            ws_out.clear()
        except gspread.exceptions.WorksheetNotFound:
            ws_out = spreadsheet.add_worksheet(title=sheet_name, rows=max(len(values), 100), cols=max(len(df_clean.columns), 26))
        if ws_out.row_count < len(values) or ws_out.col_count < len(df_clean.columns):
            ws_out.resize(rows=max(len(values), ws_out.row_count), cols=max(len(df_clean.columns), ws_out.col_count))
        ws_out.update(values, value_input_option='RAW')
        print(f"   ✅ {sheet_name}: {len(df_clean)} rows × {len(df_clean.columns)} cols")
        return True
    except Exception as e:
        print(f"   ❌ {sheet_name}: {e}")
        return False

def pick_perf_append_upload(spreadsheet, sheet_name, df):
    if df is None or df.empty:
        print(f"   ⏭️  {sheet_name}: No snapshot rows — skipped")
        return False
    df_clean = df.copy().replace([np.inf, -np.inf], np.nan).fillna('')
    rows = [[pick_perf_clean_cell(v) for v in row] for row in df_clean.values.tolist()]
    try:
        try:
            ws_out = spreadsheet.worksheet(sheet_name)
            existing = ws_out.get_all_values()
        except gspread.exceptions.WorksheetNotFound:
            ws_out = spreadsheet.add_worksheet(title=sheet_name, rows=max(len(rows) + 1, 100), cols=max(len(df_clean.columns), 26))
            existing = []
        if not existing:
            ws_out.update([df_clean.columns.tolist()], value_input_option='RAW')
        if ws_out.col_count < len(df_clean.columns):
            ws_out.resize(rows=ws_out.row_count, cols=len(df_clean.columns))
        ws_out.append_rows(rows, value_input_option='RAW')
        print(f"   ✅ {sheet_name}: appended {len(rows)} rows")
        return True
    except Exception as e:
        print(f"   ❌ {sheet_name}: {e}")
        return False

def wilson_lower_bound(p, n, z=PICK_PERF_WILSON_Z):
    if n <= 0:
        return 0.0
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return max(0.0, (centre - margin) / denom)

def pick_perf_rate(hits, misses):
    denom = hits + misses
    return hits / denom if denom > 0 else np.nan

def pick_perf_prepare_df(df_all):
    if df_all is None or df_all.empty or 'HIT' not in df_all.columns:
        return pd.DataFrame()
    df = df_all[df_all['HIT'].isin(['YES', 'NO', 'PUSH', 'DNP'])].copy()
    if df.empty:
        return df
    idx = df.index
    df['player_norm'] = df.get('player', pd.Series('', index=idx)).map(normalize_person_name)
    df['prop_type_norm'] = df.get('prop_type', pd.Series('', index=idx)).map(normalize_prop_metric)
    df['lean_norm'] = df.get('lean', pd.Series('', index=idx)).fillna('').astype(str).str.upper().replace({'FADE': 'UNDER'})
    df['confidence_norm'] = df.get('confidence', pd.Series('', index=idx)).map(normalize_confidence)
    df['clv_open_f'] = pd.to_numeric(df.get('CLV_OPEN_LINE', pd.Series(np.nan, index=idx)), errors='coerce')
    df['clv_latest_f'] = pd.to_numeric(df.get('CLV_LATEST_LINE', pd.Series(np.nan, index=idx)), errors='coerce')
    df['clv_edge'] = np.where(df['lean_norm'] == 'UNDER', df['clv_open_f'] - df['clv_latest_f'], df['clv_latest_f'] - df['clv_open_f'])
    df['clv_edge'] = pd.to_numeric(df['clv_edge'], errors='coerce')
    df['clv_bucket'] = np.where(df['clv_edge'].isna(), 'unknown', np.where(df['clv_edge'] > 0, 'positive', np.where(df['clv_edge'] < 0, 'negative', 'flat')))
    df['consensus_bucket'] = pd.to_numeric(df.get('CONSENSUS_COUNT', pd.Series(1, index=idx)), errors='coerce').fillna(1).astype(int)
    df['has_lineup_risk'] = df.get('injury_context', pd.Series('', index=idx)).fillna('').astype(str).str.strip().str.startswith('LINEUP RISK')
    df['date_parsed'] = pd.to_datetime(df.get('DATE', pd.Series('', index=idx)), errors='coerce')
    bad_dates = int(df['date_parsed'].isna().sum())
    if bad_dates:
        print(f"   ⚠️ Pick_Performance: {bad_dates} graded rows have unparseable DATE and count only all_time")
    df['day_of_week'] = df['date_parsed'].dt.strftime('%a').fillna('unknown')
    if 'RUN_NUMBER' not in df.columns:
        df['RUN_NUMBER'] = 'unknown'
    else:
        df['RUN_NUMBER'] = df['RUN_NUMBER'].replace('', np.nan).fillna('unknown').astype(str)
    return df

def pick_perf_metrics_row(df_slice, dim_type, dim_value, window_name):
    n_picks = len(df_slice)
    n_hits = int((df_slice['HIT'] == 'YES').sum())
    n_misses = int((df_slice['HIT'] == 'NO').sum())
    n_pushes = int((df_slice['HIT'] == 'PUSH').sum())
    n_dnp = int((df_slice['HIT'] == 'DNP').sum())
    n_decisive = n_picks - n_dnp
    hit_rate = pick_perf_rate(n_hits, n_misses)
    hit_rate_raw = n_hits / n_decisive if n_decisive > 0 else np.nan
    roi_flat = (n_hits * (100 / abs(PICK_PERF_STANDARD_ODDS)) - n_misses) * 100
    roi_per_pick = roi_flat / n_decisive if n_decisive > 0 else np.nan
    clv_numeric = df_slice.dropna(subset=['clv_edge'])
    clv_pos = df_slice[df_slice['clv_edge'] > 0]
    clv_neg = df_slice[df_slice['clv_edge'].notna() & (df_slice['clv_edge'] <= 0)]
    pos_hits = int((clv_pos['HIT'] == 'YES').sum())
    pos_misses = int((clv_pos['HIT'] == 'NO').sum())
    neg_hits = int((clv_neg['HIT'] == 'YES').sum())
    neg_misses = int((clv_neg['HIT'] == 'NO').sum())
    wilson_n = n_hits + n_misses
    wilson_p = n_hits / wilson_n if wilson_n > 0 else 0
    return {
        'DIMENSION_TYPE': dim_type,
        'DIMENSION_VALUE': '' if dim_value is None else str(dim_value),
        'TIME_WINDOW': window_name,
        'N_PICKS': n_picks,
        'N_PICKS_DECISIVE': n_decisive,
        'N_HITS': n_hits,
        'N_MISSES': n_misses,
        'N_PUSHES': n_pushes,
        'N_DNP': n_dnp,
        'HIT_RATE': round(hit_rate, 3) if pd.notna(hit_rate) else np.nan,
        'HIT_RATE_RAW': round(hit_rate_raw, 3) if pd.notna(hit_rate_raw) else np.nan,
        'PUSH_RATE': round(n_pushes / n_picks, 3) if n_picks else 0,
        'DNP_RATE': round(n_dnp / n_picks, 3) if n_picks else 0,
        'ROI_FLAT': round(roi_flat, 3),
        'ROI_PER_PICK': round(roi_per_pick, 3) if pd.notna(roi_per_pick) else np.nan,
        'AVG_CLV_EDGE': round(clv_numeric['clv_edge'].mean(), 3) if not clv_numeric.empty else np.nan,
        'CLV_POSITIVE_RATE': round((clv_numeric['clv_edge'] > 0).mean(), 3) if not clv_numeric.empty else np.nan,
        'CLV_POS_HIT_RATE': round(pick_perf_rate(pos_hits, pos_misses), 3) if pd.notna(pick_perf_rate(pos_hits, pos_misses)) else np.nan,
        'CLV_NEG_HIT_RATE': round(pick_perf_rate(neg_hits, neg_misses), 3) if pd.notna(pick_perf_rate(neg_hits, neg_misses)) else np.nan,
        'WILSON_LOWER_95': round(wilson_lower_bound(wilson_p, wilson_n), 3),
        'MIN_SAMPLE_FLAG': bool(n_decisive >= PICK_PERF_MIN_SAMPLE),
        'LAST_UPDATED': timestamp_est,
    }

def pick_perf_window_df(df, window_name, days, today):
    if days is None:
        return df.copy()
    cutoff = pd.Timestamp(today - timedelta(days=days))
    return df[df['date_parsed'].notna() & (df['date_parsed'] >= cutoff)].copy()

def build_pick_performance_metrics(df_all):
    df = pick_perf_prepare_df(df_all)
    if df.empty:
        return pd.DataFrame(columns=PICK_PERFORMANCE_COLUMNS), df
    today = datetime.now(pytz.timezone('US/Eastern')).date()
    rows = []
    for window_name, days in PICK_PERF_TIME_WINDOWS.items():
        win_df = pick_perf_window_df(df, window_name, days, today)
        if win_df.empty:
            continue
        rows.append(pick_perf_metrics_row(win_df, 'overall', '', window_name))
        for dim in PICK_PERF_DIMENSIONS:
            if dim not in win_df.columns:
                continue
            for dim_value, grp in win_df.groupby(dim, dropna=False):
                rows.append(pick_perf_metrics_row(grp, dim, dim_value, window_name))
    metrics_df = pd.DataFrame(rows, columns=PICK_PERFORMANCE_COLUMNS)
    if metrics_df.empty:
        return metrics_df, df
    window_order = {name: i for i, name in enumerate(PICK_PERF_TIME_WINDOWS.keys())}
    metrics_df['_window_order'] = metrics_df['TIME_WINDOW'].map(window_order).fillna(99)
    metrics_df = metrics_df.sort_values(['_window_order', 'DIMENSION_TYPE', 'WILSON_LOWER_95'], ascending=[True, True, False])
    metrics_df = metrics_df.drop(columns=['_window_order']).reset_index(drop=True)
    return metrics_df, df

def build_snapshot_rows(metrics_df, snapshot_date):
    if metrics_df is None or metrics_df.empty:
        return []
    rows = []
    snap = metrics_df[metrics_df['TIME_WINDOW'].isin(PICK_PERF_SNAPSHOT_WINDOWS)].copy()
    for _, row in snap.iterrows():
        dim_type = row['DIMENSION_TYPE']
        dim_val = str(row['DIMENSION_VALUE'])
        key_suffix = 'overall' if dim_type == 'overall' else f"{dim_type.replace('_norm', '')}.{dim_val}"
        rows.append({'SNAPSHOT_DATE': snapshot_date, 'METRIC_KEY': f"hit_rate.{key_suffix}", 'METRIC_VALUE': row['HIT_RATE'], 'N_PICKS': row['N_PICKS_DECISIVE'], 'TIME_WINDOW': row['TIME_WINDOW']})
        if dim_type in {'overall', 'confidence_norm'}:
            rows.append({'SNAPSHOT_DATE': snapshot_date, 'METRIC_KEY': f"roi_per_pick.{key_suffix}", 'METRIC_VALUE': row['ROI_PER_PICK'], 'N_PICKS': row['N_PICKS_DECISIVE'], 'TIME_WINDOW': row['TIME_WINDOW']})
    return rows

def snapshot_already_exists(spreadsheet, snapshot_date):
    try:
        ws_snap = spreadsheet.worksheet('Pick_Performance_Snapshots')
        rows = ws_snap.get_all_records()
    except gspread.exceptions.WorksheetNotFound:
        return False
    except Exception as e:
        print(f"   ⚠️ Snapshot check failed: {e}")
        return False
    if not rows:
        return False
    df_snap = pd.DataFrame(rows)
    return 'SNAPSHOT_DATE' in df_snap.columns and str(snapshot_date) in set(df_snap['SNAPSHOT_DATE'].astype(str))

def print_pick_performance_summary(metrics_df, sport):
    print("\n" + "=" * 60)
    print(f"📊 PICK PERFORMANCE — {sport}")
    print("=" * 60)
    if metrics_df is None or metrics_df.empty:
        print("   No graded picks to analyze.")
        print("=" * 60)
        return
    overall_all = metrics_df[(metrics_df['DIMENSION_TYPE'] == 'overall') & (metrics_df['TIME_WINDOW'] == 'all_time')]
    overall_30 = metrics_df[(metrics_df['DIMENSION_TYPE'] == 'overall') & (metrics_df['TIME_WINDOW'] == 'last_30d')]
    def fmt_row(df_row):
        if df_row.empty:
            return "n/a"
        r = df_row.iloc[0]
        return f"{r['HIT_RATE'] * 100:.1f}% (n={int(r['N_PICKS_DECISIVE'])})" if pd.notna(r['HIT_RATE']) else f"n/a (n={int(r['N_PICKS_DECISIVE'])})"
    print(f"   Overall:       {fmt_row(overall_all)}  |  last 30d: {fmt_row(overall_30)}")
    conf = metrics_df[(metrics_df['DIMENSION_TYPE'] == 'confidence_norm') & (metrics_df['TIME_WINDOW'] == 'all_time')]
    for tier in ['SMASH', 'STRONG', 'LEAN']:
        row = conf[conf['DIMENSION_VALUE'] == tier]
        if not row.empty:
            print(f"   {tier:<14} {fmt_row(row)}")
    prop = metrics_df[(metrics_df['DIMENSION_TYPE'] == 'prop_type_norm') & (metrics_df['TIME_WINDOW'] == 'all_time') & (metrics_df['MIN_SAMPLE_FLAG'] == True)].copy()
    if not prop.empty:
        top = prop.sort_values('WILSON_LOWER_95', ascending=False).head(5)
        worst = prop.sort_values('WILSON_LOWER_95', ascending=True).head(5)
        print("\n   ✅ Top prop types (all-time, Wilson LB):")
        for _, r in top.iterrows():
            print(f"      {r['DIMENSION_VALUE']:<8} {r['HIT_RATE'] * 100:.1f}% (n={int(r['N_PICKS_DECISIVE'])})   LB={r['WILSON_LOWER_95']:.3f}")
        print("\n   🚨 Worst prop types (all-time, Wilson LB):")
        for _, r in worst.iterrows():
            print(f"      {r['DIMENSION_VALUE']:<8} {r['HIT_RATE'] * 100:.1f}% (n={int(r['N_PICKS_DECISIVE'])})   LB={r['WILSON_LOWER_95']:.3f}")
    alerts = []
    all_time = metrics_df[metrics_df['TIME_WINDOW'] == 'all_time']
    last_30 = metrics_df[metrics_df['TIME_WINDOW'] == 'last_30d']
    for _, r30 in last_30[last_30['MIN_SAMPLE_FLAG'] == True].iterrows():
        rall = all_time[
            (all_time['DIMENSION_TYPE'] == r30['DIMENSION_TYPE']) &
            (all_time['DIMENSION_VALUE'] == r30['DIMENSION_VALUE']) &
            (all_time['MIN_SAMPLE_FLAG'] == True)
        ]
        if rall.empty or pd.isna(r30['HIT_RATE']) or pd.isna(rall.iloc[0]['HIT_RATE']):
            continue
        delta = (r30['HIT_RATE'] - rall.iloc[0]['HIT_RATE']) * 100
        if abs(delta) >= PICK_PERF_DRIFT_ALERT_PP:
            label = r30['DIMENSION_TYPE'].replace('_norm', '')
            alerts.append(f"{label}.{r30['DIMENSION_VALUE']}: 30d={r30['HIT_RATE']*100:.1f}% vs all-time={rall.iloc[0]['HIT_RATE']*100:.1f}% (Δ={delta:+.1f}pp)")
    print("\n   ⚠️ Drift alerts:")
    if alerts:
        for alert in alerts[:8]:
            print(f"      {alert}")
    else:
        print("      none")
    print("=" * 60)

def run_pick_performance_section(df_all, sport):
    metrics_df, prepared_df = build_pick_performance_metrics(df_all)
    if prepared_df.empty:
        print("\n📊 Pick_Performance: no graded picks to analyze.")
        return
    wrote_perf = pick_perf_safe_upload(sh, 'Pick_Performance', metrics_df)
    snapshot_date = datetime.now(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d')
    if snapshot_already_exists(sh, snapshot_date):
        print(f"   ⏭️  Pick_Performance_Snapshots: snapshot already exists for {snapshot_date}")
    else:
        snapshot_df = pd.DataFrame(build_snapshot_rows(metrics_df, snapshot_date), columns=PICK_PERFORMANCE_SNAPSHOT_COLUMNS)
        pick_perf_append_upload(sh, 'Pick_Performance_Snapshots', snapshot_df)
    print_pick_performance_summary(metrics_df, sport)
    if wrote_perf:
        print("   📈 Pick_Performance written.")

# --- 3. FIND UNGRADED PICKS ---
hit_series = df_picks['HIT'].fillna('').astype(str).str.strip()
today_str = datetime.now(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d')
date_series = pd.to_datetime(df_picks['DATE'], errors='coerce')
today_ts = pd.to_datetime(today_str)
retry_cutoff = today_ts - pd.Timedelta(days=RETRY_DNP_LOOKBACK_DAYS)
retry_dnp_mask = (hit_series == 'DNP') & date_series.notna() & (date_series >= retry_cutoff) & (date_series <= today_ts)
blank_ungraded_mask = (hit_series == '') & date_series.notna() & (date_series < today_ts)
ungraded = df_picks[blank_ungraded_mask | retry_dnp_mask].copy()

if ungraded.empty:
    blanks_today = int(((hit_series == '') & date_series.notna() & (date_series >= today_ts)).sum())
    if blanks_today > 0:
        print(f"⏳ {blanks_today} ungraded picks from today ({today_str}) — games haven't finished yet. Run tomorrow.")
    else:
        print("✅ All picks are already graded! Nothing to do.")
    dates_to_grade = []
else:
    dates_to_grade = sorted(ungraded['DATE'].unique())
    retry_ct = int(retry_dnp_mask.sum())
    if retry_ct > 0:
        print(f"🎯 {len(ungraded)} gradeable picks from: {', '.join(dates_to_grade)} ({retry_ct} recent DNP retries)")
    else:
        print(f"🎯 {len(ungraded)} gradeable picks from: {', '.join(dates_to_grade)}")

if not dates_to_grade:
    print("\n⏭️ No gradeable NBA pick dates — skipping box score fetch.")
    raise SystemExit(0)

# --- 4. FETCH BOX SCORES ---
print("\nFetching box score data...")
box_lookup = {}
box_date_set = set()
try:
    print("   📊 Loading from Player_Stats sheet...")
    ws_logs = sh.worksheet('Player_Stats')
    log_rows = ws_logs.get_all_records()
    df_logs = pd.DataFrame(log_rows)
    if len(df_logs) > 0 and {'PLAYER_NAME', 'GAME_DATE'}.issubset(df_logs.columns):
        df_logs['GAME_DATE'] = df_logs['GAME_DATE'].map(normalize_pick_date)
        for _, row in df_logs.iterrows():
            key = (row['PLAYER_NAME'], row['GAME_DATE'])
            box_lookup[key] = {
                'PTS': safe_float(row.get('PTS'), 0),
                'REB': safe_float(row.get('REB'), 0),
                'AST': safe_float(row.get('AST'), 0),
                'PRA': safe_float(row.get('PRA'), 0),
                'PR': safe_float(row.get('PR'), 0),
                'PA': safe_float(row.get('PA'), 0),
                'RA': safe_float(row.get('RA'), 0),
                'FG3M': safe_float(row.get('FG3M'), 0),
                'BLK': safe_float(row.get('BLK'), 0),
                'STL': safe_float(row.get('STL'), 0),
                'STOCKS': safe_float(row.get('STOCKS'), 0),
                'DK_FP': round(safe_float(row.get('DK_FP'), 0), 1),
                'UD_FP': round(safe_float(row.get('UD_FP'), 0), 1),
                'MIN': safe_float(row.get('MIN'), 0),
            }
        box_date_set = set(df_logs['GAME_DATE'].astype(str).tolist())
        print(f"   ✅ Loaded {len(box_lookup)} player game entries from sheet")
except Exception as e:
    print(f"   ⚠️ Could not load Player_Stats sheet: {e}")

grade_dates_missing = [d for d in dates_to_grade if d not in box_date_set]
if (grade_dates_missing or not box_lookup) and os.environ.get('GITHUB_ACTIONS') == 'true':
    print(f"   ⚠️ Missing seeded Player_Stats dates: {', '.join(grade_dates_missing) if grade_dates_missing else 'all grade dates'}")
    print("   ⏭️ GitHub Actions mode — skipping stats.nba.com fallback; unavailable picks stay ungraded for retry.")
elif grade_dates_missing or not box_lookup:
    print("   🔄 Falling back to NBA API for missing dates...")
    season = current_nba_season()
    print(f"   Using NBA season: {season}")
    df_logs = pd.DataFrame()
    for season_type in ['Regular Season', 'Playoffs', 'PlayIn']:
        try:
            df_tmp = fetch_nba_gamelog_df(season, season_type)
            if len(df_tmp) > 0:
                df_logs = pd.concat([df_logs, df_tmp], ignore_index=True)
        except Exception as e:
            print(f"   ❌ {season_type} fetch failed after retries: {e}")
    if not df_logs.empty:
        df_logs['GAME_DATE'] = df_logs['GAME_DATE'].map(normalize_pick_date)
        df_logs['PRA'] = pd.to_numeric(df_logs['PTS']) + pd.to_numeric(df_logs['REB']) + pd.to_numeric(df_logs['AST'])
        df_logs['PR'] = pd.to_numeric(df_logs['PTS']) + pd.to_numeric(df_logs['REB'])
        df_logs['PA'] = pd.to_numeric(df_logs['PTS']) + pd.to_numeric(df_logs['AST'])
        df_logs['RA'] = pd.to_numeric(df_logs['REB']) + pd.to_numeric(df_logs['AST'])
        df_logs['STOCKS'] = pd.to_numeric(df_logs['STL']) + pd.to_numeric(df_logs['BLK'])
        df_logs['DD'] = (
            (pd.to_numeric(df_logs['PTS']) >= 10).astype(int) +
            (pd.to_numeric(df_logs['REB']) >= 10).astype(int) +
            (pd.to_numeric(df_logs['AST']) >= 10).astype(int) +
            (pd.to_numeric(df_logs['STL']) >= 10).astype(int) +
            (pd.to_numeric(df_logs['BLK']) >= 10).astype(int)
            >= 2
        ).astype(int)
        df_logs['DK_FP'] = (
            pd.to_numeric(df_logs['PTS']) +
            pd.to_numeric(df_logs['FG3M']) * 0.5 +
            pd.to_numeric(df_logs['REB']) * 1.25 +
            pd.to_numeric(df_logs['AST']) * 1.5 +
            pd.to_numeric(df_logs['STL']) * 2 +
            pd.to_numeric(df_logs['BLK']) * 2 -
            pd.to_numeric(df_logs['TOV']) * 0.5 +
            df_logs['DD'] * 1.5
        )
        df_logs['UD_FP'] = (
            pd.to_numeric(df_logs['PTS']) +
            pd.to_numeric(df_logs['REB']) * 1.2 +
            pd.to_numeric(df_logs['AST']) * 1.5 +
            pd.to_numeric(df_logs['STL']) * 3 +
            pd.to_numeric(df_logs['BLK']) * 3 -
            pd.to_numeric(df_logs['TOV'])
        )
        for _, row in df_logs.iterrows():
            key = (row['PLAYER_NAME'], row['GAME_DATE'])
            if key in box_lookup:
                continue
            box_lookup[key] = {
                'PTS': safe_float(row['PTS'], 0),
                'REB': safe_float(row['REB'], 0),
                'AST': safe_float(row['AST'], 0),
                'PRA': safe_float(row['PRA'], 0),
                'PR': safe_float(row['PR'], 0),
                'PA': safe_float(row['PA'], 0),
                'RA': safe_float(row['RA'], 0),
                'FG3M': safe_float(row['FG3M'], 0),
                'BLK': safe_float(row['BLK'], 0),
                'STL': safe_float(row['STL'], 0),
                'STOCKS': safe_float(row['STOCKS'], 0),
                'DK_FP': round(safe_float(row['DK_FP'], 0), 1),
                'UD_FP': round(safe_float(row.get('UD_FP'), 0), 1),
                'MIN': safe_float(row['MIN'], 0),
            }
        box_date_set.update(df_logs['GAME_DATE'].astype(str).tolist())
        print(f"   ✅ Total box score entries available: {len(box_lookup)}")

# --- 5. GRADE EACH PICK ---
print("\n" + "=" * 60)
print("📝 GRADING PICKS")
print("=" * 60)

graded = 0
hits = 0
misses = 0
pushes = 0
dnp = 0
not_found = 0

# Map column names to indices for direct cell updates
col_idx = {h: i for i, h in enumerate(headers)}
actual_col = col_idx.get('ACTUAL_STAT')
hit_col = col_idx.get('HIT')
result_col = col_idx.get('RESULT')

if actual_col is None or hit_col is None:
    print("❌ Missing ACTUAL_STAT or HIT columns in Daily_Picks")
    raise SystemExit

def col_letter(idx):
    if idx < 26:
        return chr(65 + idx)
    return chr(64 + idx // 26) + chr(65 + idx % 26)

# Collect cell updates for batch write
updates = []

for idx, pick in ungraded.iterrows():
    player = pick.get('player', '')
    date = pick.get('DATE', '')
    prop_type = pick.get('prop_type', 'PRA')
    line = pick.get('line', '')
    lean = (pick.get('lean', '') or '').upper()

    if not player or not date:
        continue

    line_val = safe_float(line)
    box = find_box_score(box_lookup, player, date)
    date_has_logs = date in box_date_set

    # Row in the sheet (1-indexed, +1 for header)
    sheet_row = int(idx) + 2  # idx is 0-based from data rows, +1 for header, +1 for 1-index

    if box is None:
        if not date_has_logs:
            print(f"   ⏳ {player} ({date}) — logs for this date are not available yet; leaving ungraded for retry")
            continue
        # Player didn't play (DNP, injury, etc.)
        updates.append({'range': f'{col_letter(actual_col)}{sheet_row}', 'value': 'DNP'})
        updates.append({'range': f'{col_letter(hit_col)}{sheet_row}', 'value': 'DNP'})
        if result_col is not None:
            updates.append({'range': f'{col_letter(result_col)}{sheet_row}', 'value': 'DNP'})
        dnp += 1
        print(f"   ⬜ {player} ({date}) — DNP / No box score")
        continue

    actual = box.get(prop_type)
    if actual is None:
        not_found += 1
        print(f"   ❓ {player} ({date}) — prop_type '{prop_type}' not in box score")
        continue

    actual = safe_float(actual)
    hit_str, result_str = grade_pick(actual, line_val, lean)
    if hit_str == 'PUSH':
        pushes += 1
    elif hit_str == 'YES':
        hits += 1
    elif hit_str == 'NO':
        misses += 1

    graded += 1

    updates.append({'range': f'{col_letter(actual_col)}{sheet_row}', 'value': str(actual)})
    updates.append({'range': f'{col_letter(hit_col)}{sheet_row}', 'value': hit_str})
    if result_col is not None:
        updates.append({'range': f'{col_letter(result_col)}{sheet_row}', 'value': result_str})

    icon = "✅" if hit_str == "YES" else "❌" if hit_str == "NO" else "➖"
    print(f"   {icon} {player} | {prop_type} {lean} {line} → Actual: {actual} → {hit_str}")

# --- 6. BATCH UPDATE GOOGLE SHEETS ---
if updates:
    print(f"\n📤 Writing {len(updates)} cell updates to Google Sheets...")
    cells = [{'range': u['range'], 'values': [[u['value']]]} for u in updates]
    ws.batch_update(cells)
    print("✅ Sheet updated!")
else:
    print("\n⚠️ No updates to write.")

# --- 7. SUMMARY ---
total_decided = hits + misses
hit_rate = (hits / total_decided * 100) if total_decided > 0 else 0
runlog.hits = hits
runlog.misses = misses
runlog.dnp_count = dnp
runlog.not_found_count = not_found
runlog.picks_graded = hits + misses

print("\n" + "=" * 60)
print("📊 GRADING COMPLETE")
print("=" * 60)
print(f"   ✅ Hits:      {hits}")
print(f"   ❌ Misses:    {misses}")
print(f"   ➖ Pushes:    {pushes}")
print(f"   ⬜ DNP:       {dnp}")
print(f"   ❓ Not found: {not_found}")
print(f"   📈 Hit Rate:  {hits}/{total_decided} ({hit_rate:.1f}%)")
print(f"   📋 Dates:     {', '.join(dates_to_grade)}")
print("=" * 60)

# --- 8. SHOW CUMULATIVE RECORD ---
print("\n📊 Cumulative Record (all graded picks):")
ws_fresh = sh.worksheet('Daily_Picks')
all_fresh = ws_fresh.get_all_records()
df_all = pd.DataFrame(all_fresh)

if 'HIT' in df_all.columns:
    total_yes = len(df_all[df_all['HIT'] == 'YES'])
    total_no = len(df_all[df_all['HIT'] == 'NO'])
    total_push = len(df_all[df_all['HIT'] == 'PUSH'])
    total_dnp = len(df_all[df_all['HIT'] == 'DNP'])
    total_dec = total_yes + total_no
    cum_rate = (total_yes / total_dec * 100) if total_dec > 0 else 0

    print(f"   Record: {total_yes}-{total_no} ({cum_rate:.1f}%)")
    print(f"   Pushes: {total_push} | DNPs: {total_dnp}")

    if 'lean' in df_all.columns:
        print("\n   By Side:")
        side_series = df_all['lean'].fillna('').astype(str).str.upper().replace({'FADE': 'UNDER'})
        for side in ['OVER', 'UNDER']:
            side_df = df_all[side_series == side]
            side_yes = len(side_df[side_df['HIT'] == 'YES'])
            side_no = len(side_df[side_df['HIT'] == 'NO'])
            side_dec = side_yes + side_no
            if side_dec > 0:
                print(f"   {side}: {side_yes}-{side_no} ({side_yes/side_dec*100:.0f}%)")

    print("\n   By Confidence:")
    for tier in ['SMASH', 'STRONG', 'LEAN']:
        tier_df = df_all[df_all['confidence'].fillna('').astype(str).str.upper() == tier]
        tier_yes = len(tier_df[tier_df['HIT'] == 'YES'])
        tier_no = len(tier_df[tier_df['HIT'] == 'NO'])
        tier_dec = tier_yes + tier_no
        if tier_dec > 0:
            print(f"   {tier}: {tier_yes}-{tier_no} ({tier_yes/tier_dec*100:.0f}%)")

    if 'prop_type' in df_all.columns:
        print("\n   By Prop Type:")
        for ptype in sorted(df_all['prop_type'].fillna('').astype(str).unique()):
            if not ptype:
                continue
            p_df = df_all[df_all['prop_type'] == ptype]
            p_yes = len(p_df[p_df['HIT'] == 'YES'])
            p_no = len(p_df[p_df['HIT'] == 'NO'])
            p_dec = p_yes + p_no
            if p_dec > 0:
                print(f"   {ptype}: {p_yes}-{p_no} ({p_yes/p_dec*100:.0f}%)")

    # By date
    print("\n   By Date:")
    for date in sorted(df_all['DATE'].unique()):
        d_df = df_all[df_all['DATE'] == date]
        d_yes = len(d_df[d_df['HIT'] == 'YES'])
        d_no = len(d_df[d_df['HIT'] == 'NO'])
        d_dec = d_yes + d_no
        if d_dec > 0:
            print(f"   {date}: {d_yes}-{d_no} ({d_yes/d_dec*100:.0f}%)")
        else:
            d_dnp = len(d_df[d_df['HIT'] == 'DNP'])
            d_empty = len(d_df[d_df['HIT'].isin(['', None])])
            print(f"   {date}: ungraded ({d_empty}) / DNP ({d_dnp})")

    if 'RUN_NUMBER' in df_all.columns:
        print("\n   By Run Number:")
        run_series = pd.to_numeric(df_all['RUN_NUMBER'], errors='coerce')
        for run_no in sorted(run_series.dropna().astype(int).unique()):
            r_df = df_all[run_series == run_no]
            r_yes = len(r_df[r_df['HIT'] == 'YES'])
            r_no = len(r_df[r_df['HIT'] == 'NO'])
            r_dec = r_yes + r_no
            if r_dec > 0:
                print(f"   Run {run_no}: {r_yes}-{r_no} ({r_yes/r_dec*100:.0f}%)")

    print_clv_summary(df_all)
    print_winning_combo_tracker(df_all, dates_to_grade)
    run_pick_performance_section(df_all, 'NBA')

print("\n🎯 Done! Run this every morning after games.")
