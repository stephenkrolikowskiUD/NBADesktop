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
import gspread
from google.auth import default
from google.oauth2.service_account import Credentials
from nba_api.stats.endpoints import leaguegamelog

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
RETRY_DNP_LOOKBACK_DAYS = 7

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
    print("✅ All picks are already graded! Nothing to do.")
    dates_to_grade = []

if ungraded.empty:
    print(f"⏳ All ungraded picks are from today ({today_str}) — games haven't finished yet. Run tomorrow.")
    dates_to_grade = []
else:
    dates_to_grade = sorted(ungraded['DATE'].unique())
    retry_ct = int(retry_dnp_mask.sum())
    if retry_ct > 0:
        print(f"🎯 {len(ungraded)} gradeable picks from: {', '.join(dates_to_grade)} ({retry_ct} recent DNP retries)")
    else:
        print(f"🎯 {len(ungraded)} gradeable picks from: {', '.join(dates_to_grade)}")

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

dates_to_grade = sorted(ungraded['DATE'].unique())
grade_dates_missing = [d for d in dates_to_grade if d not in box_date_set]
if grade_dates_missing or not box_lookup:
    print("   🔄 Falling back to NBA API for missing dates...")
    season = current_nba_season()
    print(f"   Using NBA season: {season}")
    df_logs = pd.DataFrame()
    for season_type in ['Regular Season', 'Playoffs', 'PlayIn']:
        try:
            log = leaguegamelog.LeagueGameLog(
                player_or_team_abbreviation='P',
                league_id='00',
                season=season,
                season_type_all_star=season_type
            )
            df_tmp = log.get_data_frames()[0]
            if len(df_tmp) > 0:
                df_logs = pd.concat([df_logs, df_tmp], ignore_index=True)
                print(f"   ✅ {season_type}: {len(df_tmp)} entries")
        except Exception as e:
            print(f"   ⚠️ {season_type}: {e}")
    if not df_logs.empty:
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

print("\n🎯 Done! Run this every morning after games.")
