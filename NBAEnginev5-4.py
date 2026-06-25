# @title 🏀 NBA Dashboard Engine (v8.5 - EV%, Line Movement, Gemini AI Picks) — 5-4 Baseline
import pandas as pd
import numpy as np
import requests
import json
import time
import re
import os
import atexit
import sys
import subprocess
from datetime import datetime
import pytz
import gspread
from google.auth import default
from google.oauth2.service_account import Credentials
try:
    from nba_api.stats.endpoints import leaguegamelog, leaguedashteamstats, scoreboardv3
    from nba_api.stats.static import teams
except ImportError:
    print("📦 Installing missing nba_api package...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "nba_api"])
    from nba_api.stats.endpoints import leaguegamelog, leaguedashteamstats, scoreboardv3
    from nba_api.stats.static import teams
try:
    from run_logger import RunLogger
except ImportError:
    class RunLogger:
        def __init__(self, *args, **kwargs):
            self.picks_generated = 0
        def record_write(self, *args, **kwargs):
            pass
        def warn(self, *args, **kwargs):
            pass
        def fail(self, *args, **kwargs):
            pass
        def finalize_and_write(self):
            pass

# --- 1. AUTHENTICATION & SETUP ---
print("Authenticating with Google...")
SHEET_NAME = 'NBA_Dashboard_Data'
SHEET_ID = '12gBgVx_RCsIytjZHjfZWgLtG-R-zcPbYYE-CVFd4EDw'
SNAPSHOT_DATE = "2026-05-04"
SPORT_LABEL = "NBA"
ENABLE_FANDUEL_FALLBACK = os.getenv("ENABLE_FANDUEL_FALLBACK", "false").lower() == "true"
_last_odds_credits_remaining = None

# --- Odds API quota guard ---
QUOTA_FLOOR_GLOBAL = 2000
QUOTA_FLOOR_THIS_SPORT = {
    "MLB": 1000,
    "NBA": 800,
    "NHL": 600,
    "WNBA": 500,
    "WC": 600,
}[SPORT_LABEL]
CACHE_DIR = os.path.expanduser("~/.dfs_engines_cache")
os.makedirs(CACHE_DIR, exist_ok=True)
CACHE_TTL_SECONDS = {
    "MLB": 900,
    "NBA": 900,
    "NHL": 900,
    "WNBA": 1800,
    "WC": 1800,
}[SPORT_LABEL]


def check_quota_or_abort(resp, context: str) -> None:
    """Read x-requests-remaining from response and abort run if below floor."""
    global _last_odds_credits_remaining
    try:
        remaining = int(resp.headers.get('x-requests-remaining', '99999'))
    except (AttributeError, TypeError, ValueError):
        return
    _last_odds_credits_remaining = remaining
    try:
        runlog.odds_credits_remaining = remaining
    except Exception:
        pass
    floor = QUOTA_FLOOR_THIS_SPORT
    if remaining < floor:
        print(
            f"🛑 QUOTA GUARD: {remaining} remaining < {SPORT_LABEL} floor {floor} "
            f"({context}). Aborting run."
        )
        sys.exit(0)


def cached_odds_fetch(cache_key: str, fetch_fn):
    """Return cached payload if fresh, else fetch and cache."""
    path = os.path.join(CACHE_DIR, f"{SPORT_LABEL}_{cache_key}.json")
    if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < CACHE_TTL_SECONDS:
        age = int(time.time() - os.path.getmtime(path))
        with open(path) as f:
            print(f"💾 Cache hit: {cache_key} (age {age}s)")
            return json.load(f)
    data = fetch_fn()
    with open(path, 'w') as f:
        json.dump(data, f)
    return data

SHEET_SCHEMAS = {
    'Tonights_Opponent': {
        'required': ['PLAYER_NAME', 'TEAM_ABBREVIATION', 'CURRENT_TEAM', 'TONIGHT_OPP',
                     'L5_GAMES_PLAYED', 'GAMES_LAST_7D', 'LIMITED_SAMPLE', 'RETURNING'],
        'recommended': ['OPP_DEF_RTG', 'OPP_PACE', 'B2B'],
    },
    'Daily_Picks': {
        'required': ['DATE', 'RUN_NUMBER', 'rank', 'player', 'team', 'opponent',
                     'prop_type', 'line', 'lean', 'confidence', 'rationale', 'HIT'],
        'recommended': ['CONSENSUS_COUNT', 'CONSENSUS_RUNS'],
    },
    'DK_Player_Props': {
        'required': ['PLAYER_NAME', 'METRIC', 'DK_LINE', 'OVER_ODDS', 'UNDER_ODDS'],
        'recommended': ['BOOK', 'REFERENCE_BOOK', 'BEST_OVER_BOOK', 'BEST_OVER_ODDS', 'BEST_OVER_DELTA_PP',
                        'BEST_UNDER_BOOK', 'BEST_UNDER_ODDS', 'BEST_UNDER_DELTA_PP',
                        'ALT_LINE_AVAILABLE', 'ALT_LINE_BOOKS', 'LAST_UPDATED'],
    },
    'All_Books_Props': {
        'required': ['PLAYER_NAME', 'METRIC', 'LINE', 'BOOK', 'OVER_ODDS', 'UNDER_ODDS',
                     'OVER_IMPLIED', 'UNDER_IMPLIED', 'LAST_UPDATED'],
        'recommended': [],
    },
    'Player_Stats': {
        'required': ['PLAYER_ID', 'PLAYER_NAME', 'TEAM_ABBREVIATION', 'GAME_DATE',
                     'PTS', 'REB', 'AST', 'STL', 'BLK', 'UD_FP', 'DK_FP'],
        'recommended': ['L5_GAMES_PLAYED', 'GAMES_LAST_7D', 'LIMITED_SAMPLE', 'RETURNING'],
    },
}

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
        colab_auth.authenticate_user()
        creds, _ = default(scopes=scopes)
        print("✅ Google auth via Colab")
        return gspread.authorize(creds)
    except Exception as e:
        raise RuntimeError("Google auth unavailable. Set GOOGLE_SERVICE_ACCOUNT_JSON or run in Colab.") from e

def load_secret(name, prompt_text=None, allow_missing=False):
    env_val = os.environ.get(name)
    if env_val:
        print(f"🔐 Loaded {name} from environment!")
        return env_val
    try:
        from google.colab import userdata
        colab_val = userdata.get(name)
        if colab_val:
            print(f"🔐 Loaded {name} from Colab userdata!")
            return colab_val
    except Exception:
        pass
    if allow_missing:
        return None
    import getpass
    return getpass.getpass(prompt_text or f"Paste your {name}: ")

gc = get_gspread_client()
try:
    sh = gc.open_by_key(SHEET_ID)
    print(f"✅ Connected to Google Sheet: {SHEET_ID}")
    runlog = RunLogger(gc, SHEET_ID, sport='NBA', kind='engine')
    atexit.register(runlog.finalize_and_write)
except Exception as e:
    print(f"❌ Error: Could not find Google Sheet with ID '{SHEET_ID}'.")

ODDS_API_KEY = load_secret('ODDS_API_KEY', '🔑 Paste your Odds API Key: ')
GEMINI_API_KEY = load_secret('GEMINI_API_KEY', allow_missing=True)
if GEMINI_API_KEY:
    print("🔐 Gemini API key ready!")
else:
    print("⚠️ No Gemini API key found — AI picks will be skipped.")

# --- SHARED UTILITY ---
def clean_cell(val):
    if not isinstance(val, (str, int, float, type(None))):
        return str(val)
    if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
        return None
    if isinstance(val, str):
        return val.encode('utf-8', 'ignore').decode('utf-8')
    return val

def get_nba_season(now=None):
    now = now or datetime.now(pytz.timezone('US/Eastern'))
    start_year = now.year if now.month >= 9 else now.year - 1
    return f"{start_year}-{str(start_year + 1)[-2:]}"

def get_nba_season_phase(today_str: str, games_list: list) -> str:
    phase_priority = {
        'Offseason': 0,
        'Regular Season': 1,
        'Play-In Tournament': 2,
        'Playoffs': 3,
    }
    phase_counts = {}
    for game in games_list or []:
        raw_values = [
            game.get('seasonType'),
            game.get('season_type'),
            game.get('seasonTypeText'),
            game.get('gameLabel'),
            game.get('gameSubtype'),
            game.get('seriesText'),
            game.get('gameStatusText'),
        ]
        nested = game.get('gameEt', {}) if isinstance(game.get('gameEt'), dict) else {}
        raw_values.extend([nested.get('seasonType'), nested.get('seasonTypeText')])
        text = ' '.join(str(v or '') for v in raw_values).lower()
        phase = None
        if 'playoff' in text or 'postseason' in text:
            phase = 'Playoffs'
        elif 'play-in' in text or 'play in' in text or 'playin' in text:
            phase = 'Play-In Tournament'
        elif 'regular' in text:
            phase = 'Regular Season'
        if phase:
            phase_counts[phase] = phase_counts.get(phase, 0) + 1
    if phase_counts:
        return sorted(
            phase_counts.items(),
            key=lambda kv: (kv[1], phase_priority.get(kv[0], 0)),
            reverse=True,
        )[0][0]

    try:
        dt = pd.to_datetime(today_str).date()
    except Exception:
        print("⚠️ NBA season phase unavailable — defaulting to Regular Season")
        return 'Regular Season'
    if dt.month in (5, 6):
        return 'Playoffs'
    if dt.month == 4 and dt.day >= 15:
        return 'Play-In Tournament'
    if dt.month in (10, 11, 12, 1, 2, 3) or (dt.month == 4 and dt.day < 15):
        return 'Regular Season'
    return 'Offseason'

def clean_name(val):
    return str(val or "").strip()

def normalizePlayerName(name):
    return clean_name(name)\
        .lower()\
        .replace("’", "")\
        .replace("'", "")\
        .replace(".", "")\
        .replace("`", "")

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

def fetch_league_gamelog_df(season, season_type, max_attempts=3):
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
            print(f"   ✅ {season_type}: {len(df)} rows")
            return df
        except Exception as e:
            last_err = e
            print(f"   ⚠️ {season_type} fetch attempt {attempt}/{max_attempts} failed: {e}")
            if attempt < max_attempts:
                time.sleep(3 * attempt)
    raise last_err

def fetch_league_gamelog_for_date(season, season_type, game_date, max_attempts=1):
    game_date_param = pd.to_datetime(game_date).strftime('%m/%d/%Y')
    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = leaguegamelog.LeagueGameLog(
                player_or_team_abbreviation='P',
                league_id='00',
                season=season,
                season_type_all_star=season_type,
                date_from_nullable=game_date_param,
                date_to_nullable=game_date_param,
                timeout=45,
            )
            df = resp.get_data_frames()[0]
            print(f"   ✅ {season_type} {game_date}: {len(df)} rows")
            return df
        except Exception as e:
            last_err = e
            print(f"   ⚠️ {season_type} {game_date} fetch attempt {attempt}/{max_attempts} failed: {e}")
            if attempt < max_attempts:
                time.sleep(3 * attempt)
    raise last_err

def fetch_live_boxscore_logs_for_date(game_date):
    date_key = pd.to_datetime(game_date).strftime('%Y%m%d')
    scoreboard_url = f'https://cdn.nba.com/static/json/liveData/scoreboard/scoreboard_{date_key}.json'
    resp = requests.get(scoreboard_url, timeout=30)
    resp.raise_for_status()
    games = resp.json().get('scoreboard', {}).get('games', [])
    rows = []
    for game in games:
        game_id = game.get('gameId')
        if not game_id:
            continue
        home = game.get('homeTeam', {}) or {}
        away = game.get('awayTeam', {}) or {}
        home_tri = home.get('teamTricode', '')
        away_tri = away.get('teamTricode', '')
        home_pts = int(home.get('score') or 0)
        away_pts = int(away.get('score') or 0)
        box_url = f'https://cdn.nba.com/static/json/liveData/boxscore/boxscore_{game_id}.json'
        try:
            box_resp = requests.get(box_url, timeout=30)
            box_resp.raise_for_status()
            game_payload = box_resp.json().get('game', {})
        except Exception as e:
            print(f"   ⚠️ CDN boxscore {game_id} failed: {e}")
            continue
        for side_key, opp_tri, team_pts, opp_pts, matchup_prefix in [
            ('homeTeam', away_tri, home_pts, away_pts, 'vs.'),
            ('awayTeam', home_tri, away_pts, home_pts, '@'),
        ]:
            team_payload = game_payload.get(side_key, {}) or {}
            team_tri = team_payload.get('teamTricode') or (home_tri if side_key == 'homeTeam' else away_tri)
            wl = 'W' if team_pts > opp_pts else 'L' if team_pts < opp_pts else ''
            for player in team_payload.get('players', []) or []:
                stats = player.get('statistics', {}) or {}
                if not stats:
                    continue
                rows.append({
                    'PLAYER_ID': player.get('personId'),
                    'PLAYER_NAME': player.get('name'),
                    'TEAM_ABBREVIATION': team_tri,
                    'GAME_DATE': pd.to_datetime(game_date).strftime('%Y-%m-%d'),
                    'MATCHUP': f"{team_tri} {matchup_prefix} {opp_tri}",
                    'GAME_OPP': opp_tri,
                    'WL': wl,
                    'MIN': stats.get('minutes') or '0:00',
                    'PTS': stats.get('points', 0),
                    'REB': stats.get('reboundsTotal', 0),
                    'AST': stats.get('assists', 0),
                    'STL': stats.get('steals', 0),
                    'BLK': stats.get('blocks', 0),
                    'TOV': stats.get('turnovers', 0),
                    'FG3M': stats.get('threePointersMade', 0),
                    'FG3A': stats.get('threePointersAttempted', 0),
                    'FGA': stats.get('fieldGoalsAttempted', 0),
                    'FTA': stats.get('freeThrowsAttempted', 0),
                    'FGM': stats.get('fieldGoalsMade', 0),
                })
    df = pd.DataFrame(rows)
    print(f"   ✅ CDN boxscores {game_date}: {len(df)} player rows")
    return df

def fetch_odds_api_schedule_games(game_date):
    try:
        resp = requests.get(
            'https://api.the-odds-api.com/v4/sports/basketball_nba/events',
            params={'apiKey': ODDS_API_KEY},
            timeout=30,
        )
        check_quota_or_abort(resp, "NBA schedule events fallback")
        if resp.status_code != 200:
            print(f"   ⚠️ Odds API schedule fallback {resp.status_code}: {resp.text[:100]}")
            return None
        raw_games = resp.json()
        full_to_abbr = {t['full_name']: t['abbreviation'] for t in teams.get_teams()}
        normalized_games = []
        for game in raw_games:
            commence_raw = str(game.get('commence_time', '') or '').strip()
            if not commence_raw:
                continue
            try:
                commence_ts = datetime.fromisoformat(commence_raw.replace('Z', '+00:00'))
                commence_date_est = commence_ts.astimezone(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d')
            except Exception:
                commence_date_est = commence_raw[:10]
            if commence_date_est != game_date:
                continue
            home = full_to_abbr.get(game.get('home_team'))
            away = full_to_abbr.get(game.get('away_team'))
            if home and away:
                normalized_games.append({
                    'homeTeam': {'teamTricode': home},
                    'awayTeam': {'teamTricode': away},
                })
        print(f"   ✅ Fallback schedule fetch: {len(normalized_games)} games from Odds API events")
        return normalized_games
    except Exception as e:
        print(f"   ⚠️ Odds API schedule fallback failed: {e}")
        return None

def fetch_scoreboard_games(game_date, max_attempts=3):
    if os.environ.get('GITHUB_ACTIONS', '').lower() == 'true':
        print("   ⚠️ GitHub Actions mode — using Odds API as primary NBA schedule source")
        odds_games = fetch_odds_api_schedule_games(game_date)
        if odds_games is not None:
            return odds_games, 'fallback'

    last_err = None
    for attempt in range(1, max_attempts + 1):
        try:
            board = scoreboardv3.ScoreboardV3(
                game_date=game_date,
                league_id='00',
                timeout=90,
            )
            games = board.get_dict().get('scoreboard', {}).get('games', [])
            print(f"   ✅ Schedule fetch: {len(games)} games")
            return games, 'stats'
        except Exception as e:
            last_err = e
            print(f"   ⚠️ Schedule fetch attempt {attempt}/{max_attempts} failed: {e}")
            if attempt < max_attempts:
                time.sleep(3 * attempt)
    print(f"   ❌ Schedule fetch failed after retries: {last_err}")

    # Fallback: use the NBA's public scoreboard JSON instead of stats.nba.com.
    date_compact = game_date.replace('-', '')
    fallback_urls = [
        f"https://data.nba.net/prod/v2/{date_compact}/scoreboard.json",
        "https://cdn.nba.com/static/json/liveData/scoreboard/todaysScoreboard_00.json",
    ]
    for url in fallback_urls:
        try:
            resp = requests.get(url, timeout=30)
            if resp.status_code != 200:
                print(f"   ⚠️ Fallback schedule API {resp.status_code}: {url}")
                continue
            payload = resp.json()
            raw_games = payload.get('games') or payload.get('scoreboard', {}).get('games') or []
            normalized_games = []
            for game in raw_games:
                home = (
                    game.get('hTeam', {}).get('triCode')
                    or game.get('homeTeam', {}).get('teamTricode')
                    or game.get('homeTeam', {}).get('teamTricode')
                )
                away = (
                    game.get('vTeam', {}).get('triCode')
                    or game.get('awayTeam', {}).get('teamTricode')
                    or game.get('awayTeam', {}).get('teamTricode')
                )
                if home and away:
                    normalized_games.append({
                        'homeTeam': {'teamTricode': home},
                        'awayTeam': {'teamTricode': away},
                    })
            print(f"   ✅ Fallback schedule fetch: {len(normalized_games)} games from {url}")
            return normalized_games, 'fallback'
        except Exception as e:
            print(f"   ⚠️ Fallback schedule fetch failed from {url}: {e}")

    odds_games = fetch_odds_api_schedule_games(game_date)
    if odds_games is not None:
        return odds_games, 'fallback'
    return [], 'unavailable'

def pick_player_name(row):
    for col in ('player', 'PLAYER', 'Player', 'PLAYER_NAME'):
        if col in row and pd.notna(row[col]) and clean_name(row[col]):
            return clean_name(row[col])
    return ""

def normalize_status(val):
    return str(val or "").strip().upper()

def first_present(row, cols, default=""):
    for col in cols:
        if col in row and pd.notna(row[col]):
            val = row[col]
            if isinstance(val, str):
                val = val.strip()
            if val != "":
                return val
    return default

def normalize_confidence(val):
    conf = str(val or "").strip().upper()
    return conf if conf in {"SMASH", "STRONG", "LEAN"} else "LEAN"

def parse_gemini_json_array(raw):
    cleaned = str(raw or "").strip()
    json_match = re.search(r'\[[\s\S]*\]', cleaned)
    if json_match:
        cleaned = json_match.group(0)
    elif cleaned.startswith('```'):
        cleaned = cleaned.split('\n', 1)[1] if '\n' in cleaned else cleaned[3:]
        cleaned = cleaned.rsplit('```', 1)[0]
    cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        lc = cleaned.rfind('}')
        if lc > 0:
            return json.loads(cleaned[:lc + 1] + ']')
        raise

def promote_consensus_confidence(confidence, consensus_count):
    conf = normalize_confidence(confidence)
    if consensus_count < 2:
        return conf
    if conf == 'LEAN':
        return 'STRONG'
    if conf == 'STRONG':
        return 'SMASH'
    return conf

def build_consensus_pick_pool(pick_lists):
    grouped = {}
    for run_idx, picks in enumerate(pick_lists, start=1):
        for pick in picks or []:
            player_key = normalizePlayerName(pick.get('player', ''))
            prop_key = str(pick.get('prop_type', '') or '').strip().upper()
            lean_key = str(pick.get('lean', '') or '').strip().upper()
            if not player_key or not prop_key or not lean_key:
                continue
            key = (player_key, prop_key, lean_key)
            entry = grouped.setdefault(key, {'pick': dict(pick), 'count': 0, 'runs': [], 'best_rank': 999})
            if run_idx not in entry['runs']:
                entry['runs'].append(run_idx)
                entry['count'] += 1
            try:
                rank_val = int(float(pick.get('rank', 999)))
            except (TypeError, ValueError):
                rank_val = 999
            if rank_val < entry['best_rank']:
                entry['pick'] = dict(pick)
                entry['best_rank'] = rank_val
    merged = []
    for entry in grouped.values():
        pick = dict(entry['pick'])
        pick['CONSENSUS_COUNT'] = entry['count']
        pick['CONSENSUS_RUNS'] = ','.join(str(r) for r in entry['runs'])
        pick['CONSENSUS_TAG'] = f"CONSENSUS {entry['count']}/3" if entry['count'] >= 2 else ""
        pick['confidence'] = promote_consensus_confidence(pick.get('confidence'), entry['count'])
        merged.append(pick)
    merged.sort(key=lambda pk: (-int(pk.get('CONSENSUS_COUNT', 1)), float(pk.get('rank', 999) or 999)))
    for idx, pick in enumerate(merged, start=1):
        pick['rank'] = idx
    return merged

def normalize_game_date(val):
    s = str(val or "").strip()
    if not s:
        return ""
    try:
        return pd.to_datetime(s).strftime('%Y-%m-%d')
    except Exception:
        return s[:10]

def load_existing_player_logs(sheet, keep_cols, numeric_cols):
    try:
        ws = sh.worksheet(sheet)
        rows = ws.get_all_records()
    except Exception:
        return pd.DataFrame(columns=keep_cols)
    if not rows:
        return pd.DataFrame(columns=keep_cols)
    df = pd.DataFrame(rows)
    for col in keep_cols:
        if col not in df.columns:
            df[col] = np.nan
    df = df[keep_cols].copy()
    if 'GAME_DATE' in df.columns:
        df['GAME_DATE'] = df['GAME_DATE'].map(normalize_game_date)
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    return df

def load_existing_daily_picks(sheet, target_date):
    try:
        ws = sheet.worksheet('Daily_Picks')
        rows = ws.get_all_records()
    except Exception:
        return pd.DataFrame()
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if 'DATE' not in df.columns:
        return pd.DataFrame()
    df['DATE'] = df['DATE'].map(normalize_pick_date)
    return df[df['DATE'] == target_date].copy()

def find_missing_player_stat_dates(sheet, existing_dates, today_str, lookback_days=7):
    try:
        ws = sheet.worksheet('Daily_Picks')
        rows = ws.get_all_records()
    except Exception as e:
        print(f"   ⚠️ Could not inspect Daily_Picks for stat backfill: {e}")
        return []
    if not rows:
        return []
    df = pd.DataFrame(rows)
    if 'DATE' not in df.columns or 'HIT' not in df.columns:
        return []
    df['DATE'] = df['DATE'].map(normalize_pick_date)
    hit_series = df['HIT'].fillna('').astype(str).str.strip()
    date_series = pd.to_datetime(df['DATE'], errors='coerce')
    today_ts = pd.to_datetime(today_str)
    retry_cutoff = today_ts - pd.Timedelta(days=lookback_days)
    needs_grade = (
        ((hit_series == '') & date_series.notna() & (date_series < today_ts)) |
        ((hit_series == 'DNP') & date_series.notna() & (date_series >= retry_cutoff) & (date_series <= today_ts))
    )
    pick_dates = sorted(set(df.loc[needs_grade, 'DATE'].dropna().astype(str)))
    return [d for d in pick_dates if d not in existing_dates]

def refresh_clv_daily_picks(sheet, target_date, props_df, timestamp_label):
    if props_df is None or props_df.empty:
        return
    try:
        ws = sheet.worksheet('Daily_Picks')
        values = ws.get_all_values()
    except Exception:
        return
    if not values:
        return
    rows = [list(r) for r in values]
    headers = rows[0]
    clv_cols = ['CLV_OPEN_LINE', 'CLV_LATEST_LINE', 'CLV_DELTA', 'CLV_LAST_UPDATE']
    changed = False
    for col in clv_cols:
        if col not in headers:
            headers.append(col)
            for r in rows[1:]:
                r.append('')
            changed = True
    rows[0] = headers
    col_idx = {h: i for i, h in enumerate(headers)}
    line_map = {}
    for _, prop in props_df.iterrows():
        try:
            latest_line = float(prop.get('DK_LINE'))
        except (TypeError, ValueError):
            continue
        key = (
            normalizePlayerName(prop.get('PLAYER_NAME', '')),
            str(prop.get('METRIC', '')).strip().upper(),
        )
        line_map[key] = latest_line
    for r in rows[1:]:
        while len(r) < len(headers):
            r.append('')
        if normalize_pick_date(r[col_idx['DATE']]) != target_date:
            continue
        key = (
            normalizePlayerName(r[col_idx.get('player', 0)]),
            str(r[col_idx.get('prop_type', 0)]).strip().upper(),
        )
        if key not in line_map:
            continue
        latest_line = line_map[key]
        open_raw = r[col_idx['CLV_OPEN_LINE']] or r[col_idx.get('line', 0)]
        try:
            open_line = float(open_raw)
        except (TypeError, ValueError):
            open_line = None
        new_latest = f"{latest_line:g}"
        new_delta = f"{(latest_line - open_line):+.1f}" if open_line is not None else ''
        if r[col_idx['CLV_LATEST_LINE']] != new_latest:
            r[col_idx['CLV_LATEST_LINE']] = new_latest
            changed = True
        if r[col_idx['CLV_DELTA']] != new_delta:
            r[col_idx['CLV_DELTA']] = new_delta
            changed = True
        if r[col_idx['CLV_LAST_UPDATE']] != timestamp_label:
            r[col_idx['CLV_LAST_UPDATE']] = timestamp_label
            changed = True
        if open_line is not None and r[col_idx['CLV_OPEN_LINE']] != f"{open_line:g}":
            r[col_idx['CLV_OPEN_LINE']] = f"{open_line:g}"
            changed = True
    if changed:
        ws.clear()
        ws.update(rows, value_input_option='RAW')
        print("🔁 CLV latest-line tracker refreshed for today's existing picks.")

def build_sample_flag_frame(log_df, ref_date=None):
    cols = ['PLAYER_ID', 'PLAYER_NAME', 'L5_GAMES_PLAYED', 'GAMES_LAST_7D', 'LIMITED_SAMPLE', 'RETURNING']
    if log_df is None or log_df.empty:
        return pd.DataFrame(columns=cols)
    ref_ts = pd.to_datetime(ref_date or datetime.now(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d'))
    last7_cutoff = ref_ts - pd.Timedelta(days=6)
    rows = []
    for pid, group in log_df.groupby('PLAYER_ID'):
        grp = group.sort_values('GAME_DATE').copy()
        if grp.empty:
            continue
        ud_vals = pd.to_numeric(grp['UD_FP'], errors='coerce').dropna()
        l5_games = int(min(5, len(grp)))
        season_avg = float(ud_vals.mean()) if len(ud_vals) else 0.0
        l5_avg = float(ud_vals.tail(5).mean()) if len(ud_vals) else 0.0
        game_dates = pd.to_datetime(grp['GAME_DATE'], errors='coerce')
        games_last_7d = int(((game_dates >= last7_cutoff) & (game_dates <= ref_ts)).sum())
        limited_sample = l5_games < 3
        returning = bool(season_avg > 0 and l5_avg < (0.7 * season_avg) and games_last_7d < 4)
        rows.append({
            'PLAYER_ID': pid,
            'PLAYER_NAME': grp['PLAYER_NAME'].iloc[-1],
            'L5_GAMES_PLAYED': l5_games,
            'GAMES_LAST_7D': games_last_7d,
            'LIMITED_SAMPLE': limited_sample,
            'RETURNING': returning,
        })
    return pd.DataFrame(rows, columns=cols)

NBA_SEASON = get_nba_season()

# --- 2. FETCH PLAYER DATA ---
print(f"Fetching Player Game Logs ({NBA_SEASON} Season + Play-In + Playoffs)...")
PLAYER_LOG_BASE_COLS = [
    'PLAYER_ID', 'PLAYER_NAME', 'TEAM_ABBREVIATION', 'GAME_DATE', 'MATCHUP', 'GAME_OPP', 'WL',
    'MIN', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'TOV', 'FG3M', 'FG3A', 'FGA', 'FTA', 'FGM'
]
PLAYER_LOG_NUMERIC_COLS = ['PLAYER_ID', 'MIN', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'TOV', 'FG3M', 'FG3A', 'FGA', 'FTA', 'FGM']
existing_player_logs = load_existing_player_logs('Player_Stats', PLAYER_LOG_BASE_COLS, PLAYER_LOG_NUMERIC_COLS)
is_github_actions = os.environ.get('GITHUB_ACTIONS', '').lower() == 'true'
df_log_parts = []
if is_github_actions and len(existing_player_logs) > 0:
    print("   ⚠️ GitHub Actions mode — using seeded Player_Stats and checking missing pick dates")
    backfill_today = datetime.now(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d')
    existing_dates = set(existing_player_logs['GAME_DATE'].map(normalize_game_date).astype(str))
    missing_dates = find_missing_player_stat_dates(sh, existing_dates, backfill_today)
    for missing_date in missing_dates[:3]:
        print(f"   🔎 Backfilling missing Player_Stats date: {missing_date}")
        date_parts = []
        for season_type in ['Regular Season', 'Playoffs', 'PlayIn']:
            try:
                df_tmp = fetch_league_gamelog_for_date(NBA_SEASON, season_type, missing_date)
                if len(df_tmp) > 0:
                    date_parts.append(df_tmp)
            except Exception as e:
                print(f"   ⚠️ Could not backfill {missing_date} {season_type}: {e}")
        if date_parts:
            df_log_parts.extend(date_parts)
            continue
        try:
            df_tmp = fetch_live_boxscore_logs_for_date(missing_date)
            if len(df_tmp) > 0:
                df_log_parts.append(df_tmp)
        except Exception as e:
            print(f"   ⚠️ Could not backfill {missing_date} from NBA CDN boxscores: {e}")
    if df_log_parts:
        df_logs_api = pd.concat([existing_player_logs[PLAYER_LOG_BASE_COLS]] + df_log_parts, ignore_index=True)
    else:
        df_logs_api = existing_player_logs[PLAYER_LOG_BASE_COLS].copy()
else:
    for season_type in ['Regular Season', 'PlayIn', 'Playoffs']:
        try:
            df_log_parts.append(fetch_league_gamelog_df(NBA_SEASON, season_type))
        except Exception as e:
            print(f"   ❌ {season_type} fetch failed after retries: {e}")
    if not df_log_parts:
        if len(existing_player_logs) > 0:
            print("   ⚠️ NBA API unavailable — using seeded Player_Stats only")
            df_logs_api = existing_player_logs[PLAYER_LOG_BASE_COLS].copy()
        else:
            raise RuntimeError("NBA API unavailable and no seeded Player_Stats exist to fall back on.")
    else:
        df_logs_api = pd.concat(df_log_parts, ignore_index=True)
if 'GAME_OPP' not in df_logs_api.columns:
    df_logs_api['GAME_OPP'] = df_logs_api['MATCHUP'].astype(str).str[-3:]

player_id_by_name = df_logs_api[['PLAYER_NAME', 'PLAYER_ID']].dropna().drop_duplicates(subset=['PLAYER_NAME'])
if len(existing_player_logs) > 0:
    existing_player_logs = existing_player_logs.merge(player_id_by_name, on='PLAYER_NAME', how='left', suffixes=('', '_API'))
    existing_player_logs['PLAYER_ID'] = existing_player_logs['PLAYER_ID'].fillna(existing_player_logs['PLAYER_ID_API'])
    existing_player_logs = existing_player_logs.drop(columns=['PLAYER_ID_API'])

latest_date_by_pid = {}
latest_date_by_name = {}
if len(existing_player_logs) > 0:
    existing_player_logs['GAME_DATE'] = existing_player_logs['GAME_DATE'].map(normalize_game_date)
    latest_date_by_name = existing_player_logs.groupby('PLAYER_NAME')['GAME_DATE'].max().to_dict()
    pid_frame = existing_player_logs.dropna(subset=['PLAYER_ID']).copy()
    if not pid_frame.empty:
        pid_frame['PLAYER_ID'] = pd.to_numeric(pid_frame['PLAYER_ID'], errors='coerce')
        pid_frame = pid_frame.dropna(subset=['PLAYER_ID'])
        latest_date_by_pid = pid_frame.groupby('PLAYER_ID')['GAME_DATE'].max().to_dict()
    latest_seed_date = max(latest_date_by_name.values()) if latest_date_by_name else ''
    if latest_seed_date:
        print(f"♻️ Seeded Player_Stats through {latest_seed_date} ({len(existing_player_logs)} existing rows)")
else:
    print("🆕 No existing Player_Stats seed found — full log fetch")

df_logs_api['GAME_DATE'] = df_logs_api['GAME_DATE'].map(normalize_game_date)
keep_mask = []
for _, row in df_logs_api.iterrows():
    pid = row.get('PLAYER_ID')
    name = row.get('PLAYER_NAME')
    game_date = row.get('GAME_DATE')
    cutoff = latest_date_by_pid.get(pid)
    if not cutoff:
        cutoff = latest_date_by_name.get(name)
    keep_mask.append(not cutoff or game_date > cutoff)
new_player_logs = df_logs_api.loc[keep_mask, PLAYER_LOG_BASE_COLS].copy()
combined_logs = pd.concat([existing_player_logs[PLAYER_LOG_BASE_COLS], new_player_logs], ignore_index=True)
combined_logs = combined_logs.drop_duplicates(subset=['PLAYER_ID', 'PLAYER_NAME', 'GAME_DATE', 'MATCHUP', 'TEAM_ABBREVIATION'], keep='last')
df_logs = combined_logs.copy()


# --- 3. CLEANING & FORMATTING ---
print("Cleaning Data Types...")
df_logs['GAME_DATE'] = pd.to_datetime(df_logs['GAME_DATE'])
df_logs['GAME_OPP'] = df_logs['MATCHUP'].str[-3:]
for col in ['MIN', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'TOV', 'FG3M', 'FG3A', 'FGA', 'FTA', 'FGM']:
    df_logs[col] = pd.to_numeric(df_logs[col])
df_logs = df_logs.sort_values(by=['PLAYER_ID', 'GAME_DATE'], ascending=[True, True]).reset_index(drop=True)
print(f"✅ Fetched {len(new_player_logs)} new player logs; {len(df_logs)} combined logs across {df_logs['PLAYER_NAME'].nunique()} players")

# --- 4. CALCULATE METRICS ---
print("Calculating Custom Metrics, Combos & FPPM...")
df_logs['PRA'] = df_logs['PTS'] + df_logs['REB'] + df_logs['AST']
df_logs['PR'] = df_logs['PTS'] + df_logs['REB']
df_logs['PA'] = df_logs['PTS'] + df_logs['AST']
df_logs['RA'] = df_logs['REB'] + df_logs['AST']
df_logs['STOCKS'] = df_logs['STL'] + df_logs['BLK']
df_logs['DD'] = (
    (df_logs['PTS'] >= 10).astype(int) +
    (df_logs['REB'] >= 10).astype(int) +
    (df_logs['AST'] >= 10).astype(int) +
    (df_logs['STL'] >= 10).astype(int) +
    (df_logs['BLK'] >= 10).astype(int) >= 2
).astype(int)
df_logs['DK_FP'] = (
    df_logs['PTS'] + (df_logs['FG3M'] * 0.5) + (df_logs['REB'] * 1.25) +
    (df_logs['AST'] * 1.5) + (df_logs['STL'] * 2) + (df_logs['BLK'] * 2) -
    (df_logs['TOV'] * 0.5) + (df_logs['DD'] * 1.5)
)
df_logs['UD_FP'] = (
    df_logs['PTS'] + (df_logs['REB'] * 1.2) + (df_logs['AST'] * 1.5) +
    (df_logs['STL'] * 3) + (df_logs['BLK'] * 3) - (df_logs['TOV'] * 1)
)
df_logs['FPPM'] = np.where(df_logs['MIN'] > 0, df_logs['DK_FP'] / df_logs['MIN'], 0).round(2)
df_logs['UD_FPPM'] = np.where(df_logs['MIN'] > 0, df_logs['UD_FP'] / df_logs['MIN'], 0).round(2)
df_logs['USAGE_PPM'] = np.where(df_logs['MIN'] > 0, (df_logs['FGA'] + (0.44 * df_logs['FTA']) + df_logs['TOV']) / df_logs['MIN'], 0).round(2)

metrics = ['MIN', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'TOV', 'FG3M', 'FG3A', 'PRA', 'PR', 'PA', 'RA', 'STOCKS', 'DD', 'DK_FP', 'UD_FP', 'FPPM', 'UD_FPPM', 'USAGE_PPM']
windows = [3, 5, 10]
grouped = df_logs.groupby('PLAYER_ID')
for m in metrics:
    df_logs[f'Seas_{m}'] = grouped[m].transform(lambda x: x.expanding().mean()).round(2)
    for w in windows:
        df_logs[f'L{w}_{m}'] = grouped[m].transform(lambda x: x.rolling(window=w, min_periods=1).mean()).round(2)

df_sample_flags = build_sample_flag_frame(df_logs)
if not df_sample_flags.empty:
    df_logs = df_logs.merge(df_sample_flags, on=['PLAYER_ID', 'PLAYER_NAME'], how='left')
    df_logs['LIMITED_SAMPLE'] = df_logs['LIMITED_SAMPLE'].fillna(False)
    df_logs['RETURNING'] = df_logs['RETURNING'].fillna(False)
    limited_ct = int(df_sample_flags['LIMITED_SAMPLE'].sum())
    returning_ct = int(df_sample_flags['RETURNING'].sum())
    print(f"✅ Sample flags built — {limited_ct} LIMITED_SAMPLE, {returning_ct} RETURNING")
else:
    df_logs['L5_GAMES_PLAYED'] = 0
    df_logs['GAMES_LAST_7D'] = 0
    df_logs['LIMITED_SAMPLE'] = False
    df_logs['RETURNING'] = False

final_columns = ['PLAYER_ID', 'PLAYER_NAME', 'TEAM_ABBREVIATION', 'GAME_DATE', 'MATCHUP', 'GAME_OPP', 'WL', 'FGA', 'FTA', 'FGM']
for m in metrics:
    final_columns.append(m)
    final_columns.append(f'Seas_{m}')
    for w in windows:
        final_columns.append(f'L{w}_{m}')
final_columns.extend(['L5_GAMES_PLAYED', 'GAMES_LAST_7D', 'LIMITED_SAMPLE', 'RETURNING'])

df_player_final = df_logs[final_columns].copy()
df_player_final = df_player_final.sort_values(by='GAME_DATE', ascending=False)
df_player_final['GAME_DATE'] = df_player_final['GAME_DATE'].dt.strftime('%Y-%m-%d')

# --- 4.5 ACTIVE TONIGHT FILTER ---
print("Fetching tonight's schedule...")
today_str = datetime.now(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d')
opp_map = {}
games_list, schedule_source = fetch_scoreboard_games(today_str)
for game in games_list:
    home = game['homeTeam']['teamTricode']
    away = game['awayTeam']['teamTricode']
    opp_map[home] = away
    opp_map[away] = home

current_teams = df_player_final.sort_values('GAME_DATE').groupby('PLAYER_NAME').tail(1)[['PLAYER_NAME', 'TEAM_ABBREVIATION']]
current_teams.rename(columns={'TEAM_ABBREVIATION': 'CURRENT_TEAM'}, inplace=True)
df_player_final = df_player_final.merge(current_teams, on='PLAYER_NAME', how='left')
df_player_upload = df_player_final.copy()

active_players = current_teams[current_teams['CURRENT_TEAM'].isin(opp_map.keys())].copy()
active_players['TONIGHT_OPP'] = active_players['CURRENT_TEAM'].map(opp_map)
active_player_names = active_players['PLAYER_NAME'].tolist()

df_player_final = df_player_final[df_player_final['CURRENT_TEAM'].isin(opp_map.keys())]
print(f"🔥 Filtered dashboard for {len(games_list)} games on {today_str}!")

timestamp_pst = datetime.now(pytz.timezone('US/Eastern')).strftime('%Y-%m-%d %I:%M:%S %p EST')
df_player_final['LAST_UPDATED'] = timestamp_pst
df_player_upload['LAST_UPDATED'] = timestamp_pst

if len(games_list) == 0 and os.environ.get('GITHUB_ACTIONS', '').lower() == 'true':
    if schedule_source == 'unavailable':
        print("⚠️ NBA schedule unavailable in GitHub Actions — skipping slate-specific work.")
    else:
        print("⚠️ No NBA games tonight — skipping slate-specific work in GitHub Actions.")
    print("\n" + "=" * 60)
    print("🏀 NBA ENGINE v8.5 — RUN COMPLETE")
    print("=" * 60)
    print(f"📅 Date:             {today_str}")
    print(f"📆 Season:           {NBA_SEASON}")
    print(f"🗂️  Snapshot:         {SNAPSHOT_DATE}")
    print("🏟️  Games tonight:    0")
    print(f"🏀 Active players:    {len(df_player_final['PLAYER_NAME'].unique())}")
    print("🎲 Player props:      Skipped")
    print("📈 +EV props:         Skipped")
    print("🔄 Line movers:       Skipped")
    print("🤖 AI Picks:          Skipped")
    print(f"📝 Google Sheet:      {SHEET_ID}")
    print(f"🕐 Last updated:      {timestamp_pst}")
    print("=" * 60)
    raise SystemExit(0)

# --- 5. FETCH TEAM ADVANCED STATS ---
print("Fetching Team Advanced Stats...")
try:
    df_adv = leaguedashteamstats.LeagueDashTeamStats(
        measure_type_detailed_defense='Advanced',
        season=NBA_SEASON
    ).get_data_frames()[0]
    df_opp = leaguedashteamstats.LeagueDashTeamStats(
        measure_type_detailed_defense='Opponent',
        season=NBA_SEASON
    ).get_data_frames()[0]
    df_team_final = df_adv[['TEAM_ID', 'TEAM_NAME', 'PACE', 'DEF_RATING']].merge(
        df_opp[['TEAM_ID', 'OPP_FG3A']], on='TEAM_ID'
    )
    df_team_final.rename(columns={'OPP_FG3A': 'OPP_3PA'}, inplace=True)

    nba_teams = teams.get_teams()
    team_dict = {t['id']: t['abbreviation'] for t in nba_teams}
    full_to_abbr = {t['full_name']: t['abbreviation'] for t in nba_teams}
    df_team_final['TEAM_ABBREVIATION'] = df_team_final['TEAM_ID'].map(team_dict)
except Exception as e:
    print(f"⚠️ Team advanced stats unavailable — using minimal team fallback: {e}")
    nba_teams = teams.get_teams()
    full_to_abbr = {t['full_name']: t['abbreviation'] for t in nba_teams}
    tonight_abbrs = sorted(set(opp_map.keys()))
    df_team_final = pd.DataFrame({'TEAM_ABBREVIATION': tonight_abbrs})
    df_team_final['TEAM_ID'] = pd.NA
    df_team_final['TEAM_NAME'] = df_team_final['TEAM_ABBREVIATION']
    df_team_final['PACE'] = np.nan
    df_team_final['DEF_RATING'] = np.nan
    df_team_final['OPP_3PA'] = np.nan

# --- 5.1 FETCH LIVE VEGAS ODDS ---
print("Fetching Live Vegas Odds...")
df_odds = pd.DataFrame()
try:
    def _fetch_game_odds():
        response = requests.get('https://api.the-odds-api.com/v4/sports/basketball_nba/odds',
            params={'apiKey': ODDS_API_KEY, 'regions': 'us', 'markets': 'spreads,totals', 'oddsFormat': 'american', 'bookmakers': 'draftkings'})
        check_quota_or_abort(response, "NBA game odds")
        if response.status_code != 200:
            print(f"❌ Odds API Error: {response.status_code} — {response.text[:200]}")
            return []
        return response.json()

    odds_games = cached_odds_fetch("game_odds", _fetch_game_odds)
    if odds_games:
        odds_list = []
        for game in odds_games:
            if not game.get('bookmakers'):
                print(f"⚠️ No DK line for: {game['away_team']} @ {game['home_team']}")
                continue
            home_team = game['home_team']
            away_team = game['away_team']
            mkts = game['bookmakers'][0]['markets']
            spread_home, total_ou = 0, 0
            for mkt in mkts:
                if mkt['key'] == 'spreads':
                    for o in mkt['outcomes']:
                        if o['name'] == home_team:
                            spread_home = o['point']
                elif mkt['key'] == 'totals':
                    total_ou = mkt['outcomes'][0]['point']
            if total_ou > 0:
                hi = round((total_ou / 2) - (spread_home / 2), 2)
                ai = round(total_ou - hi, 2)
                odds_list.append({'TEAM_NAME': home_team, 'IMPLIED_TOTAL': hi, 'SPREAD': spread_home, 'GAME_TOTAL': total_ou})
                odds_list.append({'TEAM_NAME': away_team, 'IMPLIED_TOTAL': ai, 'SPREAD': -spread_home, 'GAME_TOTAL': total_ou})
        df_odds = pd.DataFrame(odds_list)
        df_odds['TEAM_NAME'] = df_odds['TEAM_NAME'].replace({'Los Angeles Clippers': 'LA Clippers'})
        full_to_abbr['LA Clippers'] = 'LAC'
        df_odds['TEAM_ABBREVIATION'] = df_odds['TEAM_NAME'].map(full_to_abbr)
        tonight_team_set = set(opp_map.keys())
        if tonight_team_set:
            before_games = len(df_odds) // 2
            df_odds = df_odds[df_odds['TEAM_ABBREVIATION'].isin(tonight_team_set)].copy()
            print(f"🎯 Odds aligned to tonight's slate: {before_games} -> {len(df_odds) // 2} games")
        print(f"🎰 Vegas odds pulled for {len(df_odds) // 2} games!")
        unmapped = df_odds[df_odds['TEAM_ABBREVIATION'].isna()]['TEAM_NAME'].unique()
        if len(unmapped) > 0:
            print(f"⚠️ UNMAPPED: {unmapped}")
        df_team_final = df_team_final.merge(
            df_odds[['TEAM_ABBREVIATION', 'IMPLIED_TOTAL', 'SPREAD', 'GAME_TOTAL']],
            on='TEAM_ABBREVIATION', how='left')
except Exception as e:
    print(f"❌ Failed to fetch odds: {e}")

# --- 5.2 MERGE MATCHUP & DEFENSIVE STATS ---
df_player_final['TONIGHT_OPP'] = df_player_final['CURRENT_TEAM'].map(opp_map)
df_dm = df_team_final[['TEAM_ABBREVIATION', 'DEF_RATING', 'PACE', 'OPP_3PA']].copy()
df_dm.rename(columns={'TEAM_ABBREVIATION': 'TONIGHT_OPP', 'DEF_RATING': 'OPP_DEF_RTG', 'PACE': 'OPP_PACE', 'OPP_3PA': 'OPP_3PA_ALLOWED'}, inplace=True)
df_player_final = df_player_final.merge(df_dm, on='TONIGHT_OPP', how='left')

# --- 5.5 CALCULATE H2H ---
print("Calculating Head-to-Head Previous Performances...")
df_h2h_base = df_logs.merge(active_players[['PLAYER_NAME', 'TONIGHT_OPP']], on='PLAYER_NAME', how='inner')
df_h2h_matches = df_h2h_base[df_h2h_base['GAME_OPP'] == df_h2h_base['TONIGHT_OPP']]
h2h_cols = ['MIN', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'FG3M', 'FG3A', 'PRA', 'PR', 'PA', 'RA', 'STOCKS', 'DD', 'DK_FP', 'UD_FP', 'FPPM', 'USAGE_PPM']
h2h_agg = df_h2h_matches.groupby('PLAYER_NAME')[h2h_cols].mean().round(2).reset_index()
h2h_agg.rename(columns={c: f'H2H_{c}' for c in h2h_cols}, inplace=True)

# --- 5.6 BUILD TONIGHT'S SHEET ---
tonight_cols = ['PLAYER_NAME', 'CURRENT_TEAM', 'TONIGHT_OPP', 'OPP_DEF_RTG', 'OPP_PACE', 'OPP_3PA_ALLOWED',
                'L5_GAMES_PLAYED', 'GAMES_LAST_7D', 'LIMITED_SAMPLE', 'RETURNING']
df_tonight_sheet = df_player_final[[c for c in tonight_cols if c in df_player_final.columns]].drop_duplicates(subset=['PLAYER_NAME'])
df_tonight_sheet.rename(columns={'CURRENT_TEAM': 'TEAM_ABBREVIATION'}, inplace=True)
df_tonight_sheet['CURRENT_TEAM'] = df_tonight_sheet['TEAM_ABBREVIATION']
df_tonight_sheet = df_tonight_sheet.merge(h2h_agg, on='PLAYER_NAME', how='left')

if not df_odds.empty:
    df_tonight_sheet = df_tonight_sheet.merge(
        df_odds[['TEAM_ABBREVIATION', 'IMPLIED_TOTAL', 'SPREAD', 'GAME_TOTAL']],
        on='TEAM_ABBREVIATION', how='left')
    df_oo = df_odds[['TEAM_ABBREVIATION', 'IMPLIED_TOTAL']].copy()
    df_oo.rename(columns={'TEAM_ABBREVIATION': 'TONIGHT_OPP', 'IMPLIED_TOTAL': 'OPP_IMPLIED_TOTAL'}, inplace=True)
    df_tonight_sheet = df_tonight_sheet.merge(df_oo, on='TONIGHT_OPP', how='left')

now_est = datetime.now(pytz.timezone('US/Eastern'))
yesterday_str = (pd.Timestamp(now_est.date()) - pd.Timedelta(days=1)).strftime('%Y-%m-%d')
team_last_game = df_logs.sort_values('GAME_DATE').groupby('TEAM_ABBREVIATION')['GAME_DATE'].max().astype(str).to_dict() if not df_logs.empty else {}
df_tonight_sheet['B2B'] = df_tonight_sheet['TEAM_ABBREVIATION'].map(lambda t: str(team_last_game.get(t, '')) == yesterday_str)

df_tonight_sheet['LAST_UPDATED'] = timestamp_pst
df_tonight_sheet = df_tonight_sheet.drop_duplicates(subset=['PLAYER_NAME'])

# --- 5.7 TEAMMATE CORRELATION MATRIX ---
print("Calculating Teammate Correlation Matrix...")
df_corr_base = df_logs[df_logs['PLAYER_NAME'].isin(active_player_names)][['PLAYER_NAME', 'TEAM_ABBREVIATION', 'GAME_DATE', 'DK_FP']].copy()
MIN_GAMES = 10
corr_rows = []
for team, group in df_corr_base.groupby('TEAM_ABBREVIATION'):
    pivot = group.pivot_table(index='GAME_DATE', columns='PLAYER_NAME', values='DK_FP')
    pivot = pivot.dropna(thresh=MIN_GAMES, axis=1)
    if pivot.shape[1] < 2:
        continue
    cm = pivot.corr(min_periods=MIN_GAMES)
    cm.index.name = 'PLAYER_A'
    cm.columns.name = 'PLAYER_B'
    corr_df = cm.unstack().reset_index()
    corr_df.columns = ['PLAYER_A', 'PLAYER_B', 'CORRELATION']
    corr_df = corr_df[corr_df['PLAYER_A'] != corr_df['PLAYER_B']].dropna(subset=['CORRELATION'])
    if corr_df.empty:
        continue
    games_played = {}
    for player_a, player_b in corr_df[['PLAYER_A', 'PLAYER_B']].itertuples(index=False):
        games_played[(player_a, player_b)] = int(pivot[[player_a, player_b]].dropna().shape[0])
    corr_df['TEAM'] = team
    corr_df['CORRELATION'] = corr_df['CORRELATION'].round(3)
    corr_df['GAMES_PLAYED'] = corr_df.apply(lambda r: games_played.get((r['PLAYER_A'], r['PLAYER_B']), 0), axis=1)
    corr_rows.extend(corr_df[['TEAM', 'PLAYER_A', 'PLAYER_B', 'CORRELATION', 'GAMES_PLAYED']].to_dict('records'))

df_correlation = pd.DataFrame(corr_rows)

def corr_label(c):
    if c >= 0.5: return 'Strong Stack'
    if c >= 0.25: return 'Lean Stack'
    if c >= -0.25: return 'Neutral'
    if c >= -0.5: return 'Lean Fade'
    return 'Strong Fade'

if df_correlation.empty:
    df_correlation = pd.DataFrame(columns=['TEAM', 'PLAYER_A', 'PLAYER_B', 'CORRELATION', 'GAMES_PLAYED', 'STACK_LABEL', 'LAST_UPDATED'])
    print("⚠️ No correlation data — no games tonight or not enough shared games.")
else:
    df_correlation['STACK_LABEL'] = df_correlation['CORRELATION'].apply(corr_label)
    df_correlation = df_correlation.sort_values('CORRELATION', ascending=False).reset_index(drop=True)
    df_correlation['LAST_UPDATED'] = timestamp_pst
    print(f"✅ Correlation matrix built — {len(df_correlation)} player pairs across {df_correlation['TEAM'].nunique()} teams")

# --- 5.8 HOME/AWAY SPLITS ---
print("Calculating Home/Away Splits...")
df_logs['HOME_AWAY'] = np.where(df_logs['MATCHUP'].str.contains('vs.'), 'Home', 'Away')
avg_metrics = ['MIN', 'PTS', 'REB', 'AST', 'STL', 'BLK', 'FG3M', 'FG3A', 'PRA', 'PR', 'PA', 'RA', 'STOCKS', 'DK_FP', 'UD_FP', 'FPPM', 'USAGE_PPM']
sum_metrics = ['DD']
all_metrics = avg_metrics + sum_metrics
df_splits_base = df_logs[df_logs['PLAYER_NAME'].isin(active_player_names)].copy()

if df_splits_base.empty:
    ha_pivot = pd.DataFrame(columns=['PLAYER_NAME', 'LAST_UPDATED'])
    print("⚠️ No splits data — no active players tonight.")
else:
    ha_mean = df_splits_base.groupby(['PLAYER_NAME', 'HOME_AWAY'])[avg_metrics].mean()
    ha_sum = df_splits_base.groupby(['PLAYER_NAME', 'HOME_AWAY'])[sum_metrics].sum()
    ha_count = df_splits_base.groupby(['PLAYER_NAME', 'HOME_AWAY'])['MIN'].count().rename('GAMES')
    df_home_away = ha_mean.join(ha_sum).join(ha_count).reset_index()
    ha_pivot = df_home_away.pivot(index='PLAYER_NAME', columns='HOME_AWAY', values=all_metrics)
    ha_pivot.columns = [f'{s}_{l}' for s, l in ha_pivot.columns]
    hcp = df_home_away.pivot(index='PLAYER_NAME', columns='HOME_AWAY', values='GAMES')
    hcp.columns = ['Away_GAMES', 'Home_GAMES']
    ha_pivot = ha_pivot.join(hcp)
    for m in all_metrics:
        hc, ac = f'{m}_Home', f'{m}_Away'
        if hc in ha_pivot.columns and ac in ha_pivot.columns:
            ha_pivot[f'{m}_SPLIT_DIFF'] = (ha_pivot[hc] - ha_pivot[ac]).where(
                ha_pivot[hc].notna() & ha_pivot[ac].notna(), other=np.nan).round(2)
    ha_pivot = ha_pivot.reset_index()
    ha_pivot = ha_pivot.reindex(sorted(ha_pivot.columns), axis=1)
    ha_pivot = ha_pivot[['PLAYER_NAME'] + [c for c in ha_pivot.columns if c != 'PLAYER_NAME']]
    ha_pivot['LAST_UPDATED'] = timestamp_pst
    print(f"✅ Home/Away splits built for {ha_pivot['PLAYER_NAME'].nunique()} players")

# --- MULTI-BOOK PLAYER PROPS ---
print("Fetching Live Multi-Book Player Props...")
SPORT = 'basketball_nba'
BOOKMAKER = 'draftkings'
PROP_BOOKMAKER = 'draftkings'
FALLBACK_BOOKMAKER = 'fanduel'
THIN_MARKET_THRESHOLD = 5
# Caesars was dropped on 2026-05-27 — returned 0/0 best-book wins in production verification.
# May be worth re-adding after 6/1 reset to re-test (could have been a one-day API issue).
SUPPORTED_BOOKMAKERS = ['draftkings', 'fanduel', 'betmgm', 'espnbet']
ACTIVE_PROP_BOOKMAKERS = SUPPORTED_BOOKMAKERS if ENABLE_FANDUEL_FALLBACK else [
    b for b in SUPPORTED_BOOKMAKERS if b != FALLBACK_BOOKMAKER
]
REFERENCE_BOOKMAKER = 'draftkings'
BEST_BOOK_TIE_BREAK = 'alpha'

MARKET_BATCHES = [
    'player_points,player_rebounds,player_assists,player_threes,player_points_rebounds_assists',
    'player_points_rebounds,player_points_assists,player_rebounds_assists,player_blocks,player_steals'
]

market_mapping = {
    'player_points': 'PTS', 'player_rebounds': 'REB', 'player_assists': 'AST',
    'player_threes': 'FG3M', 'player_points_rebounds_assists': 'PRA',
    'player_points_rebounds': 'PR', 'player_points_assists': 'PA',
    'player_rebounds_assists': 'RA', 'player_blocks': 'BLK', 'player_steals': 'STL'
}

BINARY_PROP_MARKETS = {}
name_fixes = {
    'Luka Doncic': 'Luka Dončić', 'Nikola Jokic': 'Nikola Jokić',
    'Nikola Vucevic': 'Nikola Vučević', 'Bogdan Bogdanovic': 'Bogdan Bogdanović',
    'Bojan Bogdanovic': 'Bojan Bogdanović', 'Dario Saric': 'Dario Šarić',
    'Goran Dragic': 'Goran Dragić', 'Jonas Valanciunas': 'Jonas Valančiūnas',
    'Kristaps Porzingis': 'Kristaps Porziņģis', 'Dennis Schroder': 'Dennis Schröder',
    'Derrick Jones': 'Derrick Jones Jr.', 'G.G. Jackson': 'GG Jackson',
    'Kelly Oubre Jr': 'Kelly Oubre Jr.', 'Nicolas Claxton': 'Nic Claxton',
    'R.J. Barrett': 'RJ Barrett',
}

DK_PLAYER_PROPS_COLUMNS = [
    'PLAYER_NAME', 'METRIC', 'DK_LINE', 'OVER_ODDS', 'UNDER_ODDS', 'BOOK',
    'REFERENCE_BOOK', 'BEST_OVER_BOOK', 'BEST_OVER_ODDS', 'BEST_OVER_DELTA_PP',
    'BEST_UNDER_BOOK', 'BEST_UNDER_ODDS', 'BEST_UNDER_DELTA_PP',
    'ALT_LINE_AVAILABLE', 'ALT_LINE_BOOKS', 'LAST_UPDATED'
]
ALL_BOOKS_PROPS_COLUMNS = [
    'PLAYER_NAME', 'METRIC', 'LINE', 'BOOK', 'OVER_ODDS', 'UNDER_ODDS',
    'OVER_IMPLIED', 'UNDER_IMPLIED', 'LAST_UPDATED'
]


def american_to_implied(odds):
    try:
        if odds is None or pd.isna(odds):
            return np.nan
        if isinstance(odds, str) and odds.strip().lower() in {'', 'nan', 'none'}:
            return np.nan
        odds = float(odds)
        if odds == 0:
            return np.nan
        return (-odds / (-odds + 100)) if odds < 0 else (100 / (odds + 100))
    except (TypeError, ValueError):
        return np.nan


def implied_to_american(prob):
    try:
        prob = float(prob)
        if prob <= 0 or prob >= 1:
            return None
        return int(round(-100 * prob / (1 - prob))) if prob >= 0.5 else int(round(100 * (1 - prob) / prob))
    except (TypeError, ValueError):
        return None


def apply_multi_book_name_fixes(df, name_fixes):
    if df is None or df.empty or 'PLAYER_NAME' not in df.columns:
        return df
    out = df.copy()
    out['PLAYER_NAME'] = out['PLAYER_NAME'].replace(name_fixes or {})
    return out


def parse_multi_book_market(mkt, metric_name, book_key, binary_prop_markets=None):
    rows_by_key = {}
    market_key = mkt.get('key', '')
    binary_prop_markets = binary_prop_markets or {}
    for oc in mkt.get('outcomes', []):
        player_name = oc.get('description') or oc.get('participant') or oc.get('player') or ''
        bet_type = str(oc.get('name', '')).strip()
        line_val = oc.get('point')
        odds_val = oc.get('price')
        if not player_name or odds_val is None:
            continue
        if line_val is None and market_key in binary_prop_markets:
            line_val = binary_prop_markets[market_key]
        if line_val is None:
            continue
        try:
            line_val = float(line_val)
        except (TypeError, ValueError):
            continue
        key = (player_name, metric_name, line_val, book_key)
        if key not in rows_by_key:
            rows_by_key[key] = {
                'PLAYER_NAME': player_name,
                'METRIC': metric_name,
                'LINE': line_val,
                'BOOK': book_key,
                'OVER_ODDS': np.nan,
                'UNDER_ODDS': np.nan,
            }
        if bet_type in {'Over', 'Yes'}:
            rows_by_key[key]['OVER_ODDS'] = odds_val
        elif bet_type in {'Under', 'No'}:
            rows_by_key[key]['UNDER_ODDS'] = odds_val
    return list(rows_by_key.values())


def finalize_all_books_frame(rows, timestamp_value, name_fixes=None):
    if not rows:
        return pd.DataFrame(columns=ALL_BOOKS_PROPS_COLUMNS)
    df = pd.DataFrame(rows)
    df = df[df['BOOK'].isin(ACTIVE_PROP_BOOKMAKERS)].copy()
    df = apply_multi_book_name_fixes(df, name_fixes or {})
    df['LINE'] = pd.to_numeric(df['LINE'], errors='coerce')
    df['OVER_ODDS'] = pd.to_numeric(df['OVER_ODDS'], errors='coerce')
    df['UNDER_ODDS'] = pd.to_numeric(df['UNDER_ODDS'], errors='coerce')
    df = df.dropna(subset=['PLAYER_NAME', 'METRIC', 'LINE', 'BOOK'])
    df['OVER_IMPLIED'] = df['OVER_ODDS'].map(american_to_implied).round(4)
    df['UNDER_IMPLIED'] = df['UNDER_ODDS'].map(american_to_implied).round(4)
    df['LAST_UPDATED'] = timestamp_value
    df = df.drop_duplicates(subset=['PLAYER_NAME', 'METRIC', 'LINE', 'BOOK'], keep='first')
    return df.reindex(columns=ALL_BOOKS_PROPS_COLUMNS).sort_values(['METRIC', 'PLAYER_NAME', 'LINE', 'BOOK']).reset_index(drop=True)


def _select_best_book(same_line, odds_col):
    available = same_line.dropna(subset=[odds_col]).copy()
    if available.empty:
        return None, np.nan, np.nan, []
    available[odds_col] = pd.to_numeric(available[odds_col], errors='coerce')
    available = available.dropna(subset=[odds_col])
    if available.empty:
        return None, np.nan, np.nan, []
    best_odds = available[odds_col].max()
    tied = sorted(available[available[odds_col] == best_odds]['BOOK'].astype(str).unique())
    best_book = tied[0] if tied else None
    best_implied = american_to_implied(best_odds)
    return best_book, best_odds, best_implied, tied


def compute_best_book_columns(df_long, timestamp_value):
    if df_long is None or df_long.empty:
        return pd.DataFrame(columns=DK_PLAYER_PROPS_COLUMNS), []
    df = df_long.copy()
    df['LINE'] = pd.to_numeric(df['LINE'], errors='coerce')
    df['OVER_ODDS'] = pd.to_numeric(df['OVER_ODDS'], errors='coerce')
    df['UNDER_ODDS'] = pd.to_numeric(df['UNDER_ODDS'], errors='coerce')
    df = df.dropna(subset=['PLAYER_NAME', 'METRIC', 'LINE', 'BOOK'])
    if df.empty:
        return pd.DataFrame(columns=DK_PLAYER_PROPS_COLUMNS), []

    metric_book_coverage = (
        df.groupby(['METRIC', 'BOOK'])['PLAYER_NAME']
        .nunique()
        .reset_index(name='coverage')
        .sort_values(['METRIC', 'coverage', 'BOOK'], ascending=[True, False, True])
    )
    coverage_lookup = {
        metric: grp.iloc[0]['BOOK']
        for metric, grp in metric_book_coverage.groupby('METRIC')
        if not grp.empty
    }

    rows = []
    tie_notes = []
    for (player, metric), grp in df.groupby(['PLAYER_NAME', 'METRIC'], sort=True):
        dk_rows = grp[grp['BOOK'] == REFERENCE_BOOKMAKER].sort_values(['LINE', 'BOOK'])
        if not dk_rows.empty:
            ref = dk_rows.iloc[0]
            reference_book = REFERENCE_BOOKMAKER
        else:
            reference_book = coverage_lookup.get(metric) or sorted(grp['BOOK'].astype(str).unique())[0]
            ref_rows = grp[grp['BOOK'] == reference_book].sort_values(['LINE', 'BOOK'])
            if ref_rows.empty:
                ref_rows = grp.sort_values(['BOOK', 'LINE'])
                reference_book = ref_rows.iloc[0]['BOOK']
            ref = ref_rows.iloc[0]

        ref_line = float(ref['LINE'])
        same_line = grp[grp['LINE'].sub(ref_line).abs() < 1e-9].copy()
        alt_line_books = sorted(grp[grp['LINE'].sub(ref_line).abs() >= 1e-9]['BOOK'].astype(str).unique())
        best_over_book, best_over_odds, best_over_implied, over_ties = _select_best_book(same_line, 'OVER_ODDS')
        best_under_book, best_under_odds, best_under_implied, under_ties = _select_best_book(same_line, 'UNDER_ODDS')
        if len(over_ties) > 1:
            tie_notes.append(f"{player} {metric} OVER tied: {', '.join(over_ties)}")
        if len(under_ties) > 1:
            tie_notes.append(f"{player} {metric} UNDER tied: {', '.join(under_ties)}")

        ref_over_implied = american_to_implied(ref.get('OVER_ODDS'))
        ref_under_implied = american_to_implied(ref.get('UNDER_ODDS'))
        over_delta = (ref_over_implied - best_over_implied) * 100 if pd.notna(ref_over_implied) and pd.notna(best_over_implied) else np.nan
        under_delta = (ref_under_implied - best_under_implied) * 100 if pd.notna(ref_under_implied) and pd.notna(best_under_implied) else np.nan

        rows.append({
            'PLAYER_NAME': player,
            'METRIC': metric,
            'DK_LINE': ref_line,
            'OVER_ODDS': ref.get('OVER_ODDS'),
            'UNDER_ODDS': ref.get('UNDER_ODDS'),
            'BOOK': ref.get('BOOK'),
            'REFERENCE_BOOK': reference_book,
            'BEST_OVER_BOOK': best_over_book,
            'BEST_OVER_ODDS': best_over_odds,
            'BEST_OVER_DELTA_PP': round(over_delta, 3) if pd.notna(over_delta) else np.nan,
            'BEST_UNDER_BOOK': best_under_book,
            'BEST_UNDER_ODDS': best_under_odds,
            'BEST_UNDER_DELTA_PP': round(under_delta, 3) if pd.notna(under_delta) else np.nan,
            'ALT_LINE_AVAILABLE': bool(alt_line_books),
            'ALT_LINE_BOOKS': ','.join(alt_line_books),
            'LAST_UPDATED': timestamp_value,
        })

    df_props_out = pd.DataFrame(rows, columns=DK_PLAYER_PROPS_COLUMNS)
    if not df_props_out.empty:
        df_props_out = df_props_out.sort_values(['METRIC', 'PLAYER_NAME']).reset_index(drop=True)
    return df_props_out, tie_notes


def print_best_book_summary(df_props, df_all_books):
    print("\n" + "=" * 60)
    print("BEST-BOOK ROUTING SUMMARY")
    print("=" * 60)
    print(f"   Books queried:    {', '.join(ACTIVE_PROP_BOOKMAKERS)}")
    if not ENABLE_FANDUEL_FALLBACK:
        print("   ⏭️  FanDuel fallback DISABLED (ENABLE_FANDUEL_FALLBACK=false) — FanDuel skipped")
    if df_all_books is None or df_all_books.empty or df_props is None or df_props.empty:
        print("   Props covered:    0 unique (player, metric) pairs")
        print("=" * 60)
        return
    covered = len(df_props)
    dk_ref = int((df_props['REFERENCE_BOOK'] == REFERENCE_BOOKMAKER).sum()) if 'REFERENCE_BOOK' in df_props.columns else 0
    dk_pct = (dk_ref / covered * 100) if covered else 0
    print(f"   Props covered:    {covered} unique (player, metric) pairs")
    print(f"   DK reference:     {dk_ref} / {covered} ({dk_pct:.1f}%)")
    print("   Best-book wins by:")
    for book in ACTIVE_PROP_BOOKMAKERS:
        over_ct = int((df_props.get('BEST_OVER_BOOK') == book).sum()) if 'BEST_OVER_BOOK' in df_props.columns else 0
        under_ct = int((df_props.get('BEST_UNDER_BOOK') == book).sum()) if 'BEST_UNDER_BOOK' in df_props.columns else 0
        print(f"      {book:<12} {over_ct:>4} OVER  / {under_ct:>4} UNDER")
    over_edge = pd.to_numeric(df_props.get('BEST_OVER_DELTA_PP'), errors='coerce').dropna()
    under_edge = pd.to_numeric(df_props.get('BEST_UNDER_DELTA_PP'), errors='coerce').dropna()
    over_avg = over_edge.mean() if not over_edge.empty else 0
    under_avg = under_edge.mean() if not under_edge.empty else 0
    alt_ct = int(df_props.get('ALT_LINE_AVAILABLE', pd.Series(dtype=bool)).fillna(False).astype(bool).sum()) if 'ALT_LINE_AVAILABLE' in df_props.columns else 0
    print(f"   Avg edge captured: +{over_avg:.1f}pp OVER, +{under_avg:.1f}pp UNDER (vs reference)")
    print(f"   Alt lines available: {alt_ct} props")
    print("=" * 60)


df_props = pd.DataFrame(columns=DK_PLAYER_PROPS_COLUMNS)
df_all_books = pd.DataFrame(columns=ALL_BOOKS_PROPS_COLUMNS)
try:
    ev_resp = requests.get(f'https://api.the-odds-api.com/v4/sports/{SPORT}/events', params={'apiKey': ODDS_API_KEY}, timeout=15)
    check_quota_or_abort(ev_resp, "NBA events")
    if ev_resp.status_code != 200:
        print(f"❌ Failed to fetch events: {ev_resp.status_code} — {ev_resp.text[:200]}")
    else:
        ev_data = ev_resp.json()
        active_team_names = set()
        for t in nba_teams:
            if t['abbreviation'] in opp_map:
                active_team_names.add(t['full_name'].lower())
            if t['abbreviation'] == 'LAC':
                active_team_names.add('la clippers')
        tonight_ids = [
            e['id'] for e in ev_data
            if e.get('home_team', '').lower() in active_team_names
            or e.get('away_team', '').lower() in active_team_names
        ]
        if not tonight_ids:
            print(f"⏭️  No {SPORT_LABEL} games scheduled — skipping props pull.")
            sys.exit(0)
        print(f"🏟️ Found {len(tonight_ids)} events — fetching props from {len(ACTIVE_PROP_BOOKMAKERS)} books...")

        all_book_rows = []
        api_errors = 0
        last_resp = None
        for eid in tonight_ids:
            for batch in MARKET_BATCHES:
                markets_param = batch if isinstance(batch, str) else ','.join(batch)
                pr = requests.get(
                    f'https://api.the-odds-api.com/v4/sports/{SPORT}/events/{eid}/odds',
                    params={
                        'apiKey': ODDS_API_KEY,
                        'regions': 'us',
                        'markets': markets_param,
                        'bookmakers': ','.join(ACTIVE_PROP_BOOKMAKERS),
                        'oddsFormat': 'american'
                    },
                    timeout=15
                )
                check_quota_or_abort(pr, f"NBA event props {eid}")
                last_resp = pr
                if pr.status_code != 200:
                    if api_errors < 3:
                        print(f"   ⚠️ Props API {pr.status_code} for event {eid}: {pr.text[:100]}")
                    api_errors += 1
                    if api_errors > 5:
                        print("   ⚠️ More than 5 props API errors — continuing with partial data")
                    continue
                data = pr.json()
                for bk in data.get('bookmakers', []):
                    book_key = bk.get('key', '')
                    if book_key not in ACTIVE_PROP_BOOKMAKERS:
                        continue
                    for mkt in bk.get('markets', []):
                        mn = market_mapping.get(mkt.get('key'))
                        if not mn:
                            continue
                        all_book_rows.extend(parse_multi_book_market(mkt, mn, book_key, BINARY_PROP_MARKETS))
            time.sleep(1)

        df_all_books = finalize_all_books_frame(all_book_rows, timestamp_pst, name_fixes)
        if last_resp is not None and hasattr(last_resp, 'headers'):
            print(f"   📊 API quota remaining: {last_resp.headers.get('x-requests-remaining', '?')}")
        if api_errors:
            print(f"   ⚠️ Total props API errors: {api_errors}")
        for book in ACTIVE_PROP_BOOKMAKERS:
            book_ct = 0 if df_all_books.empty else int((df_all_books['BOOK'] == book).sum())
            if book_ct == 0:
                print(f"   {book}: 0 props")
        df_props, tie_notes = compute_best_book_columns(df_all_books, timestamp_pst)
        for note in tie_notes[:10]:
            print(f"   ℹ️ Best-book tie: {note}")
        if len(tie_notes) > 10:
            print(f"   ℹ️ Best-book ties suppressed: {len(tie_notes) - 10} more")
        if not df_props.empty:
            metric_counts = df_props['METRIC'].value_counts().to_dict()
            thin_metrics = sorted([metric for metric in set(market_mapping.values()) if metric_counts.get(metric, 0) < THIN_MARKET_THRESHOLD])
            if thin_metrics:
                print(f"   ⚠️ Thin/missing markets after multi-book fetch: {', '.join(thin_metrics)}")
            print(f"✅ Fetched {len(df_props)} reference props across {df_props['METRIC'].nunique()} markets")
            print(f"✅ All_Books_Props rows: {len(df_all_books)} across {df_all_books['BOOK'].nunique()} books")
        else:
            print("⚠️ No player props returned.")
        print_best_book_summary(df_props, df_all_books)
except Exception as e:
    print(f"❌ Failed to fetch player props: {e}")

# --- 5.92 CALCULATE EV% FOR EVERY PROP ---
print("\n" + "=" * 60)
print("📊 CALCULATING EV% & LINE MOVEMENT")
print("=" * 60)

df_ev = pd.DataFrame()
df_movers = pd.DataFrame()

if not df_props.empty and len(df_logs) > 0:
    logs_by_player = {name: group.sort_values('GAME_DATE', ascending=False) for name, group in df_logs.groupby('PLAYER_NAME')}

    def calc_implied(odds):
        if odds is None or odds == 0: return None
        odds = float(odds)
        return abs(odds) / (abs(odds) + 100) if odds < 0 else 100 / (odds + 100)

    def calc_dollar_ev(hit_rate, odds):
        if odds is None or hit_rate is None: return None
        odds = float(odds)
        payout = 100 / abs(odds) if odds < 0 else odds / 100
        return round((hit_rate * payout - ((1 - hit_rate) * 1)) * 100, 2)

    ev_rows = []
    for _, prop in df_props.iterrows():
        player = prop['PLAYER_NAME']
        metric = prop['METRIC']
        dk_line = float(prop['DK_LINE'])
        over_odds = prop.get('OVER_ODDS')
        under_odds = prop.get('UNDER_ODDS')

        p_logs = logs_by_player.get(player)
        if p_logs is None or len(p_logs) < 5:
            continue

        sample_row = df_sample_flags[df_sample_flags['PLAYER_NAME'] == player]
        limited_sample = bool(sample_row['LIMITED_SAMPLE'].iloc[0]) if not sample_row.empty else False
        returning = bool(sample_row['RETURNING'].iloc[0]) if not sample_row.empty else False
        l5_games_played = int(sample_row['L5_GAMES_PLAYED'].iloc[0]) if not sample_row.empty else int(min(5, len(p_logs)))
        games_last_7d = int(sample_row['GAMES_LAST_7D'].iloc[0]) if not sample_row.empty else 0

        hits_all = (p_logs[metric] > dk_line).sum()
        games_all = len(p_logs)
        pushes_all = (p_logs[metric] == dk_line).sum()
        non_push = games_all - pushes_all
        hits_l10 = (p_logs.head(10)[metric] > dk_line).sum()
        hits_l5 = (p_logs.head(5)[metric] > dk_line).sum()

        season_rate = hits_all / non_push if non_push > 0 else 0
        l10_rate = hits_l10 / min(10, len(p_logs)) if len(p_logs) >= 5 else None
        l5_rate = hits_l5 / min(5, len(p_logs)) if len(p_logs) >= 5 else None

        over_impl = calc_implied(over_odds)
        under_impl = calc_implied(under_odds)
        edge_over = round((season_rate - over_impl) * 100, 1) if over_impl else None
        edge_under = round(((1 - season_rate) - under_impl) * 100, 1) if under_impl else None
        edge_multiplier = 0.5 if returning else 1.0
        if edge_over is not None:
            edge_over = round(edge_over * edge_multiplier, 1)
        if edge_under is not None:
            edge_under = round(edge_under * edge_multiplier, 1)

        ev_rows.append({
            'PLAYER_NAME': player, 'METRIC': metric, 'DK_LINE': dk_line,
            'OVER_ODDS': over_odds, 'UNDER_ODDS': under_odds,
            'HITS_SEASON': int(hits_all), 'GAMES': int(games_all), 'PUSHES': int(pushes_all),
            'HIT_RATE_SEASON': round(season_rate, 3),
            'HIT_RATE_L10': round(l10_rate, 3) if l10_rate else None,
            'HIT_RATE_L5': round(l5_rate, 3) if l5_rate else None,
            'L5_GAMES_PLAYED': l5_games_played,
            'GAMES_LAST_7D': games_last_7d,
            'LIMITED_SAMPLE': limited_sample,
            'RETURNING': returning,
            'OVER_IMPLIED': round(over_impl, 3) if over_impl else None,
            'UNDER_IMPLIED': round(under_impl, 3) if under_impl else None,
            'EDGE_OVER': edge_over, 'EDGE_UNDER': edge_under,
            'EDGE_MULTIPLIER': edge_multiplier,
            'EV_OVER_$100': round(calc_dollar_ev(season_rate, over_odds) * edge_multiplier, 2) if calc_dollar_ev(season_rate, over_odds) is not None else None,
            'EV_UNDER_$100': round(calc_dollar_ev(1 - season_rate, under_odds) * edge_multiplier, 2) if calc_dollar_ev(1 - season_rate, under_odds) is not None else None,
            'BEST_BET': 'OVER' if (edge_over or 0) > 5 else ('UNDER' if (edge_under or 0) > 5 else ''),
            'LAST_UPDATED': timestamp_pst
        })

    df_ev = pd.DataFrame(ev_rows)
    if not df_ev.empty:
        df_ev = df_ev.sort_values('EDGE_OVER', ascending=False, na_position='last').reset_index(drop=True)
        plus_ev_over = len(df_ev[df_ev['EDGE_OVER'] > 0])
        smash_plays = len(df_ev[df_ev['EDGE_OVER'] > 10])
        print(f"✅ EV calculated for {len(df_ev)} props")
        print(f"   📈 {plus_ev_over} +EV overs | {smash_plays} SMASH plays (10%+ edge)")
        for _, r in df_ev.head(5).iterrows():
            print(f"   🔥 {r['PLAYER_NAME']} {r['METRIC']} > {r['DK_LINE']}: {r['HIT_RATE_SEASON']*100:.0f}% hit, {r['EDGE_OVER']}% edge, ${r['EV_OVER_$100']}/100")
    else:
        print("⚠️ No EV data — not enough game logs to calculate.")
else:
    print("⚠️ Skipping EV — no props or logs.")

# --- 5.93 LINE MOVEMENT TRACKER ---
print("\nChecking for line movement...")
if not df_props.empty:
    try:
        try:
            ws_snap = sh.worksheet('Props_Snapshot')
            snap_data = ws_snap.get_all_records()
            df_snapshot = pd.DataFrame(snap_data)
        except Exception:
            df_snapshot = pd.DataFrame()

        if not df_snapshot.empty and 'DK_LINE' in df_snapshot.columns:
            snap_lookup = {}
            for _, row in df_snapshot.iterrows():
                key = f"{row.get('PLAYER_NAME', '')}|{row.get('METRIC', '')}"
                snap_lookup[key] = {
                    'OLD_LINE': float(row.get('DK_LINE', 0)),
                    'OLD_OVER': row.get('OVER_ODDS'),
                    'OLD_UNDER': row.get('UNDER_ODDS'),
                    'SNAP_TIME': row.get('LAST_UPDATED', '')
                }

            mover_rows = []
            for _, prop in df_props.iterrows():
                key = f"{prop['PLAYER_NAME']}|{prop['METRIC']}"
                if key in snap_lookup:
                    old = snap_lookup[key]
                    old_line = old['OLD_LINE']
                    new_line = float(prop['DK_LINE'])
                    diff = round(new_line - old_line, 1)
                    if abs(diff) >= 0.5:
                        mover_rows.append({
                            'PLAYER_NAME': prop['PLAYER_NAME'], 'METRIC': prop['METRIC'],
                            'OLD_LINE': old_line, 'NEW_LINE': new_line, 'MOVE': diff,
                            'DIRECTION': '📈 UP' if diff > 0 else '📉 DOWN',
                            'OLD_OVER_ODDS': old['OLD_OVER'], 'NEW_OVER_ODDS': prop.get('OVER_ODDS'),
                            'OLD_UNDER_ODDS': old['OLD_UNDER'], 'NEW_UNDER_ODDS': prop.get('UNDER_ODDS'),
                            'SNAP_TIME': old['SNAP_TIME'], 'CURRENT_TIME': timestamp_pst
                        })

            df_movers = pd.DataFrame(mover_rows)
            if not df_movers.empty:
                df_movers = df_movers.sort_values('MOVE', key=abs, ascending=False).reset_index(drop=True)
                print(f"🔄 {len(df_movers)} lines moved since last run!")
                for _, m in df_movers.head(5).iterrows():
                    print(f"   {m['DIRECTION']} {m['PLAYER_NAME']} {m['METRIC']}: {m['OLD_LINE']} → {m['NEW_LINE']} ({'+' if m['MOVE'] > 0 else ''}{m['MOVE']})")
            else:
                print("✅ No significant line movement detected.")
        else:
            print("📸 No previous snapshot — storing first one now.")

        # Save snapshot
        try:
            ws_snap = sh.worksheet('Props_Snapshot')
        except gspread.exceptions.WorksheetNotFound:
            ws_snap = sh.add_worksheet(title='Props_Snapshot', rows=1000, cols=20)
        ws_snap.clear()
        ws_snap.update([df_props.columns.tolist()] + [[clean_cell(v) for v in row] for row in df_props.values.tolist()])
        print("📸 Props snapshot saved for next run.")

    except Exception as e:
        print(f"⚠️ Line movement error: {e}")
else:
    print("⚠️ Skipping line movement — no props.")

# --- 5.95 GEMINI AI DAILY PICKS GENERATOR ---
print("\n" + "=" * 60)
print("🤖 GEMINI AI DAILY PICKS GENERATOR")
print("=" * 60)

df_picks = pd.DataFrame()
existing_daily_picks = load_existing_daily_picks(sh, today_str)
seen_pick_keys = set()
if len(existing_daily_picks) > 0:
    for _, row in existing_daily_picks.iterrows():
        key = (
            normalizePlayerName(str(row.get('player', ''))),
            str(row.get('prop_type', '')).strip().upper(),
            str(row.get('lean', '')).strip().upper(),
        )
        seen_pick_keys.add(key)
existing_run_numbers = pd.to_numeric(existing_daily_picks.get('RUN_NUMBER', pd.Series(dtype=float)), errors='coerce').dropna().astype(int)
today_run_number = int(existing_run_numbers.max()) + 1 if not existing_run_numbers.empty else 1
refresh_clv_daily_picks(sh, today_str, df_props, timestamp_pst)

if GEMINI_API_KEY and len(games_list) > 0:
    try:
        from google import genai
        from google.genai import types
        client = genai.Client(api_key=GEMINI_API_KEY)

        # Build game context
        games_context = []
        for ta, oa in opp_map.items():
            row = df_team_final[df_team_final['TEAM_ABBREVIATION'] == ta]
            if row.empty:
                continue
            r = row.iloc[0]
            games_context.append({
                'team': ta, 'opponent': oa,
                'spread': float(r['SPREAD']) if pd.notna(r.get('SPREAD')) else 'N/A',
                'total': float(r['GAME_TOTAL']) if pd.notna(r.get('GAME_TOTAL')) else 'N/A',
                'implied_total': float(r['IMPLIED_TOTAL']) if pd.notna(r.get('IMPLIED_TOTAL')) else 'N/A',
                'pace': round(float(r.get('PACE', 0)), 1),
                'def_rating': round(float(r.get('DEF_RATING', 0)), 1)
            })
        seen = set()
        unique_games = []
        for g in games_context:
            key = tuple(sorted([g['team'], g['opponent']]))
            if key not in seen:
                seen.add(key)
                unique_games.append(g)

        # Build player context
        player_pool = df_tonight_sheet.copy()
        props_fallback_used = False
        if player_pool.empty and not df_props.empty:
            print("WARNING: tonight sheet empty; building fallback player pool from live props")
            props_fallback_used = True
            player_pool = pd.DataFrame({'PLAYER_NAME': sorted(df_props['PLAYER_NAME'].dropna().astype(str).map(clean_name).unique())})
            latest_fallback = df_player_final.copy()
            latest_fallback['PLAYER_NAME'] = latest_fallback['PLAYER_NAME'].map(clean_name)
            latest_fallback = latest_fallback.sort_values('GAME_DATE', ascending=False).drop_duplicates('PLAYER_NAME')
            fallback_cols = [c for c in [
                'PLAYER_NAME', 'TEAM_ABBREVIATION', 'CURRENT_TEAM', 'TONIGHT_OPP',
                'OPP_DEF_RTG', 'OPP_PACE', 'OPP_3PA_ALLOWED',
                'IMPLIED_TOTAL', 'SPREAD', 'GAME_TOTAL', 'OPP_IMPLIED_TOTAL', 'B2B'
            ] if c in latest_fallback.columns]
            if fallback_cols:
                player_pool = player_pool.merge(latest_fallback[fallback_cols], on='PLAYER_NAME', how='left')
            if 'TEAM_ABBREVIATION' not in player_pool.columns and 'CURRENT_TEAM' in player_pool.columns:
                player_pool['TEAM_ABBREVIATION'] = player_pool['CURRENT_TEAM']
        print(f"Total tonight players: {len(player_pool)}")
        print(f"Players before dedupe: {len(player_pool)}")
        player_pool['PLAYER_NAME'] = player_pool['PLAYER_NAME'].map(clean_name)
        player_pool = player_pool[player_pool['PLAYER_NAME'] != ""].drop_duplicates(subset=['PLAYER_NAME']).copy()
        print(f"Players after dedupe: {len(player_pool)}")

        latest = df_player_final.copy()
        latest['PLAYER_NAME'] = latest['PLAYER_NAME'].map(clean_name)
        latest = latest.sort_values('GAME_DATE', ascending=False).drop_duplicates('PLAYER_NAME')
        stats_cols = ['PLAYER_NAME', 'Seas_PTS', 'Seas_REB', 'Seas_AST', 'Seas_PRA',
              'L5_PTS', 'L5_REB', 'L5_AST', 'L5_PRA', 'L5_DK_FP',
              'Seas_FG3M', 'L5_FG3M', 'Seas_STL', 'L5_STL', 'Seas_BLK', 'L5_BLK',
              'Seas_MIN', 'L5_MIN', 'Seas_UD_FP', 'L5_UD_FP',
              'L5_GAMES_PLAYED', 'GAMES_LAST_7D', 'LIMITED_SAMPLE', 'RETURNING']
        available = [c for c in stats_cols if c in latest.columns]
        player_pool = player_pool.merge(latest[available], on='PLAYER_NAME', how='left')

        if not df_props.empty:
            pp = df_props.pivot_table(index='PLAYER_NAME', columns='METRIC', values='DK_LINE', aggfunc='first').reset_index()
            pp.columns = [f'DK_{c}' if c != 'PLAYER_NAME' else c for c in pp.columns]
            pp['PLAYER_NAME'] = pp['PLAYER_NAME'].map(clean_name)
            player_pool = player_pool.merge(pp, on='PLAYER_NAME', how='left')

        if 'Seas_PRA' in player_pool.columns:
            player_pool = player_pool.dropna(subset=['Seas_PRA'])

        if not ha_pivot.empty:
            split_cols = ['PLAYER_NAME', 'Home_GAMES', 'Away_GAMES',
                          'PTS_Home', 'PTS_Away', 'REB_Home', 'REB_Away',
                          'AST_Home', 'AST_Away', 'PRA_Home', 'PRA_Away',
                          'FG3M_Home', 'FG3M_Away', 'STL_Home', 'STL_Away',
                          'BLK_Home', 'BLK_Away', 'UD_FP_Home', 'UD_FP_Away']
            split_cols = [c for c in split_cols if c in ha_pivot.columns]
            split_frame = ha_pivot[split_cols].copy()
            split_frame['PLAYER_NAME'] = split_frame['PLAYER_NAME'].map(clean_name)
            player_pool = player_pool.merge(split_frame, on='PLAYER_NAME', how='left')

        print(f"Players before injury filter: {len(player_pool)}")
        HARD_OUT = {"OUT", "O", "DOUBTFUL", "D", "INACTIVE", "SUSPENDED"}
        status_cols = [c for c in ['STATUS', 'PLAYER_STATUS', 'INJURY_STATUS', 'REPORT_STATUS', 'AVAILABILITY', 'ACTIVE_STATUS'] if c in player_pool.columns]
        removed_debug = []
        keep_mask = []
        for _, row in player_pool.iterrows():
            raw_status = first_present(row, status_cols, default="")
            status_clean = normalize_status(raw_status)
            remove_player = status_clean in HARD_OUT
            keep_mask.append(not remove_player)
            if remove_player and len(removed_debug) < 20:
                removed_debug.append({
                    'player_name': row.get('PLAYER_NAME', ''),
                    'raw_status': raw_status,
                    'normalized_status': status_clean,
                    'reason': f"excluded_hard_out_status:{status_clean}"
                })
        filtered_pool = player_pool[pd.Series(keep_mask, index=player_pool.index)].copy()
        if filtered_pool.empty and props_fallback_used and not player_pool.empty:
            print("WARNING: injury/status filter emptied props fallback pool; restoring unfiltered fallback pool")
            filtered_pool = player_pool.copy()
        print(f"Players after injury filter: {len(filtered_pool)}")
        if removed_debug:
            print("First removed players:")
            for item in removed_debug:
                print(f" - {item['player_name']} | raw='{item['raw_status']}' | normalized='{item['normalized_status']}' | reason={item['reason']}")

        prop_pool = filtered_pool.copy()
        dk_cols = [c for c in prop_pool.columns if str(c).startswith('DK_')]
        if dk_cols:
            prop_pool = prop_pool[prop_pool[dk_cols].notna().any(axis=1)].copy()
        if 'Seas_MIN' in prop_pool.columns:
            min_qualified_pool = prop_pool[pd.to_numeric(prop_pool['Seas_MIN'], errors='coerce').fillna(0) >= 25].copy()
            print(f"Players after 25+ minute filter: {len(min_qualified_pool)}")
            if not min_qualified_pool.empty:
                prop_pool = min_qualified_pool
        if prop_pool.empty and props_fallback_used and not filtered_pool.empty:
            print("WARNING: downstream filters emptied props fallback pool; restoring filtered fallback pool")
            prop_pool = filtered_pool.copy()
        print(f"Players after props filter: {len(prop_pool)}")

        active_players_for_gemini = prop_pool.copy()
        if len(active_players_for_gemini) == 0:
            print("WARNING: active player pool empty after filtering; falling back to tonight player pool")
            active_players_for_gemini = player_pool.copy()
        if len(active_players_for_gemini) == 0 and not df_props.empty:
            print("WARNING: tonight player pool still empty; building direct Gemini pool from live props")
            active_players_for_gemini = pd.DataFrame({
                'PLAYER_NAME': sorted(df_props['PLAYER_NAME'].dropna().astype(str).map(clean_name).unique())
            })
            latest_fallback = df_player_final.copy()
            latest_fallback['PLAYER_NAME'] = latest_fallback['PLAYER_NAME'].map(clean_name)
            latest_fallback = latest_fallback.sort_values('GAME_DATE', ascending=False).drop_duplicates('PLAYER_NAME')
            fallback_cols = [c for c in [
                'PLAYER_NAME', 'TEAM_ABBREVIATION', 'CURRENT_TEAM', 'TONIGHT_OPP',
                'Seas_PTS', 'Seas_REB', 'Seas_AST', 'Seas_PRA', 'Seas_PR', 'Seas_PA', 'Seas_RA',
                'L5_PTS', 'L5_REB', 'L5_AST', 'L5_PRA', 'L5_PR', 'L5_PA', 'L5_RA', 'L5_DK_FP',
                'PTS_Home', 'PTS_Away', 'REB_Home', 'REB_Away', 'AST_Home', 'AST_Away',
                'PRA_Home', 'PRA_Away', 'UD_FP_Home', 'UD_FP_Away',
                'RETURNING', 'B2B'
            ] if c in latest_fallback.columns]
            if fallback_cols:
                active_players_for_gemini = active_players_for_gemini.merge(
                    latest_fallback[fallback_cols], on='PLAYER_NAME', how='left'
                )
            if 'TEAM_ABBREVIATION' not in active_players_for_gemini.columns:
                active_players_for_gemini['TEAM_ABBREVIATION'] = ""
            if 'TONIGHT_OPP' not in active_players_for_gemini.columns:
                active_players_for_gemini['TONIGHT_OPP'] = "?"
        sort_col = 'Seas_UD_FP' if 'Seas_UD_FP' in active_players_for_gemini.columns else 'Seas_PRA'
        active_players_for_gemini = active_players_for_gemini.sort_values(sort_col, ascending=False).copy()
        star_source = prop_pool.copy() if len(prop_pool) else active_players_for_gemini.copy()
        if sort_col in star_source.columns:
            star_source = star_source.sort_values(sort_col, ascending=False).copy()
        guaranteed_stars = star_source.head(15).copy()
        star_top20_names = set(star_source.head(20)['PLAYER_NAME'].tolist())
        rest_pool = active_players_for_gemini[~active_players_for_gemini['PLAYER_NAME'].isin(guaranteed_stars['PLAYER_NAME'])].copy()
        active_players_for_gemini = pd.concat([guaranteed_stars, rest_pool], ignore_index=True).drop_duplicates(subset=['PLAYER_NAME']).head(80).copy()
        active_players_for_gemini['STAR'] = active_players_for_gemini['PLAYER_NAME'].isin(star_top20_names)
        print(f"Sending {len(active_players_for_gemini)} active players to Gemini")
        returning_player_map = {
            normalizePlayerName(n): bool(r)
            for n, r in zip(active_players_for_gemini['PLAYER_NAME'], active_players_for_gemini.get('RETURNING', pd.Series(False, index=active_players_for_gemini.index)))
        }

        home_teams = {g['homeTeam']['teamTricode'] for g in games_list if g.get('homeTeam')}
        active_players_for_gemini['HOME_AWAY_TONIGHT'] = np.where(
            active_players_for_gemini['TEAM_ABBREVIATION'].isin(home_teams), 'Home', 'Away'
        )

        player_ev_map = {}
        metric_ev_map = {}
        if not df_ev.empty:
            ev_pool = df_ev.copy()
            ev_pool['PLAYER_NAME'] = ev_pool['PLAYER_NAME'].map(clean_name)
            ev_pool['PLAYER_NAME_NORM'] = ev_pool['PLAYER_NAME'].map(normalizePlayerName)
            ev_pool['METRIC_NORM'] = ev_pool['METRIC'].fillna('').astype(str).str.upper().str.strip()
            active_name_map = {normalizePlayerName(n): n for n in active_players_for_gemini['PLAYER_NAME'].tolist() if clean_name(n)}
            ev_pool = ev_pool[ev_pool['PLAYER_NAME_NORM'].isin(active_name_map.keys())].copy()
            for name_norm, grp in ev_pool.groupby('PLAYER_NAME_NORM'):
                sigs = []
                for _, r in grp.sort_values('EDGE_OVER', ascending=False).head(3).iterrows():
                    hr = pd.to_numeric(r.get('HIT_RATE_SEASON'), errors='coerce')
                    edge = pd.to_numeric(r.get('EDGE_OVER'), errors='coerce')
                    if pd.isna(hr) or pd.isna(edge):
                        continue
                    sigs.append(f"{r.get('METRIC')} {r.get('DK_LINE')} HR={hr*100:.0f}% EV={edge:.0f}%")
                if sigs:
                    player_ev_map[name_norm] = '; '.join(sigs)
            for (name_norm, metric_norm), grp in ev_pool.groupby(['PLAYER_NAME_NORM', 'METRIC_NORM']):
                metric_ev_map[(name_norm, metric_norm)] = {
                    'edge': pd.to_numeric(grp.get('EDGE_OVER'), errors='coerce').max(),
                    'hit_rate': pd.to_numeric(grp.get('HIT_RATE_SEASON'), errors='coerce').max(),
                }

        streak_ctx = ""
        player_streak_map = {}
        try:
            streaks = get_streaks()
            streak_lines = [f"{s['player']} — {s['stat']} streak: {s['streak']} games" for s in streaks if s['streak'] >= 3]
            streak_ctx = "\n".join(streak_lines) if streak_lines else "No active streaks tonight."
            for s in streaks:
                if s.get('streak', 0) >= 3:
                    player_streak_map.setdefault(normalizePlayerName(s['player']), []).append(f"{s['stat']} x{s['streak']}")
        except:
            streak_ctx = "Streak data unavailable."

        # Build player lines
        player_lines = []
        for _, p in active_players_for_gemini.iterrows():
            ln = f"{p.get('PLAYER_NAME', '?')} ({p.get('TEAM_ABBREVIATION', '?')} vs {p.get('TONIGHT_OPP', '?')})"
            if bool(p.get('STAR', False)):
                ln += " | STAR"
            ln += f" | Seas: {p.get('Seas_PTS', '')}/{p.get('Seas_REB', '')}/{p.get('Seas_AST', '')} PRA={p.get('Seas_PRA', '')} UD_FP={p.get('Seas_UD_FP', '')}"
            ln += f" | L5: {p.get('L5_PTS', '')}/{p.get('L5_REB', '')}/{p.get('L5_AST', '')} PRA={p.get('L5_PRA', '')} UD_FP={p.get('L5_UD_FP', '')}"
            ln += f" | Seas MIN={p.get('Seas_MIN', '')}"
            ln += f" | OPP DEF_RTG={p.get('OPP_DEF_RTG', '')} PACE={p.get('OPP_PACE', '')}"
            if bool(p.get('B2B', False)):
                ln += " | SCHEDULE FLAG: B2B"
            if bool(p.get('RETURNING', False)):
                ln += f" | SAMPLE FLAG: RETURNING (L5 games={int(p.get('L5_GAMES_PLAYED', 0) or 0)}, last7={int(p.get('GAMES_LAST_7D', 0) or 0)})"
            elif bool(p.get('LIMITED_SAMPLE', False)):
                ln += f" | SAMPLE FLAG: LIMITED_SAMPLE (L5 games={int(p.get('L5_GAMES_PLAYED', 0) or 0)})"
            if pd.notna(p.get('H2H_PRA')):
                ln += f" | H2H PRA={p.get('H2H_PRA', '')}"
            loc = p.get('HOME_AWAY_TONIGHT', '')
            if loc in ('Home', 'Away'):
                split_bits = []
                for stat in ['PTS', 'REB', 'AST', 'PRA', 'UD_FP']:
                    val = p.get(f'{stat}_{loc}')
                    if pd.notna(val):
                        split_bits.append(f"{stat}={val}")
                if split_bits:
                    ln += f" | Tonight {loc} split: {' '.join(split_bits[:5])}"
            streak_bits = player_streak_map.get(normalizePlayerName(p.get('PLAYER_NAME')))
            if streak_bits:
                ln += f" | Streaks: {', '.join(streak_bits[:3])}"
            ev_bits = player_ev_map.get(normalizePlayerName(p.get('PLAYER_NAME')))
            if ev_bits:
                ln += f" | Best prop signals: {ev_bits}"
            dk_cols = [c for c in p.index if str(c).startswith('DK_') and pd.notna(p[c])]
            if dk_cols:
                ln += f" | DK Lines: {', '.join(f'{str(c)[3:]}={p[c]}' for c in dk_cols)}"
            player_lines.append(ln)

        player_ctx = '\n'.join(player_lines[:60])
        games_str = json.dumps(unique_games, indent=2, default=str)
        valid_player_name_map = {normalizePlayerName(n): n for n in active_players_for_gemini['PLAYER_NAME'].tolist() if clean_name(n)}

        season_phase = get_nba_season_phase(today_str, games_list)
        prompt = f"""You are an expert NBA props analyst. Today is {today_str}. NBA {season_phase}.

TONIGHT'S GAMES:
{games_str}

PLAYER DATA (season averages, L5 averages, home/away splits, opponent defensive stats, H2H, best prop EV/hit-rate signals, Underdog fantasy points, and live DK prop lines):
{player_ctx}

ACTIVE PROP STREAKS:
{streak_ctx}

RULES:
- CRITICAL: ONLY pick players from the PLAYER DATA list above. Do NOT include any player not in the list.
- Return EXACTLY 10 ranked picks as a JSON array
- Confidence tiers: SMASH (top 3 highest conviction only), STRONG (next 3-4), LEAN (rest)
- Players flagged RETURNING have depressed lines due to injury/absence. Their season averages are NOT reliable short-term predictors. Treat with extreme caution — do NOT SMASH these players.
- Players flagged B2B are on the second night of a back-to-back. Deprioritize them unless the edge is exceptional.
- STAR players are the top 20 by season UD fantasy points in tonight's valid prop pool.
- At least 5 of your 10 picks should come from STAR players. Bench players can fill the remaining slots only when they have exceptional edges.
- Available prop types: PTS, REB, PRA, PR, PA, RA, FG3M, STL, BLK, UD_FP
- Do NOT pick AST — observed ~29% hit rate during NBA playoffs (slower pace, more isolation offense). Hard blacklist for playoffs and play-in. Revisit when regular season returns.
- DIVERSIFY prop types: max 3 picks of the same prop type per slate. Mix in PTS, REB, FG3M, and the cleanest combo props.
- Prefer PR over PRA when both are available for the same player.
- PRA requires a stronger edge threshold than other props. If the PRA edge is only marginal, use LEAN or skip it.
- STRONG should require multiple confirming signals: positive EV, strong hit rate, and supportive matchup/split context. If only one signal is strong, use LEAN instead.
- Prefer live DK lines when available; only fall back to L5 average if no live line exists.
- Favor cleaner one-stat edges over forcing combo props unless the combo edge is clearly stronger.
- Be much more selective on UNDER picks. Only use UNDER when the edge is clearly stronger than the comparable OVER case and the player context supports downside.
- Cap UNDER picks at 2 per slate. Avoid LEAN UNDER picks entirely.
- Use DK lines when available; otherwise use L5 average. NEVER return null for line.
- Use the listed best prop signals when present: higher EV% and higher hit rate should drive conviction.

ANALYSIS FACTORS:
- Active prop streaks (3+ games on a prop = strong lean to continue)
- Hit rate: players hitting a prop 80%+ over L10 = high reliability
- EV% matters: props with strong positive EV% vs the book deserve more weight
- Opponent DEF_RTG: high DEF_RTG = weak defense = good for overs
- PACE factor: high pace games produce more stats
- High game totals (O/U 230+) favor offensive props
- Home/away splits matter — compare tonight's location to the player's split before making a pick
- L5 trend vs season average: rising L5 = hot player, falling L5 = cold player
- BLOWOUT RISK: Games with spreads of 12+ points — FAVOR props for players on the favored team (more blowout production). AVOID props for players on the underdog (starters may sit early if down big).

For each pick provide:
- rank (1-10)
- player (exact name from data)
- team (abbreviation)
- game (e.g. "TOR @ BOS")
- prop_type (from list above)
- line (DK line or L5 average)
- lean (OVER or UNDER)
- confidence (SMASH, STRONG, or LEAN)
- rationale (1 sentence, under 15 words)
- injury_context (under 10 words)

Example format:
[{{"rank":1,"player":"PLAYER_NAME","team":"TEAM","game":"AWAY @ HOME","prop_type":"PTS","line":28.5,"lean":"OVER","confidence":"SMASH","rationale":"L5 averaging 32 vs weak perimeter D.","injury_context":"All healthy."}}]

IMPORTANT: Return ONLY the JSON array. No markdown, no preamble."""

        consensus_pick_lists = []
        consensus_temps = [0.35, 0.55, 0.75]
        for run_idx, temp in enumerate(consensus_temps, start=1):
            gen_config = types.GenerateContentConfig(temperature=temp, max_output_tokens=8192)
            print(f"🤖 Calling Gemini API run {run_idx}/3 (temp={temp:.2f})...")
            try:
                raw = client.models.generate_content(
                    model='gemini-2.5-flash-lite',
                    contents=prompt,
                    config=gen_config
                ).text.strip()
                run_picks = parse_gemini_json_array(raw)
                print(f"   ↳ {len(run_picks)} picks returned")
                consensus_pick_lists.append(run_picks)
            except json.JSONDecodeError:
                print(f"   ⚠️ Run {run_idx} returned malformed JSON — ignoring that pass")
            except Exception as e:
                print(f"   ⚠️ Run {run_idx} failed: {e}")
        if not consensus_pick_lists:
            raise RuntimeError("All Gemini passes failed")
        picks_data = build_consensus_pick_pool(consensus_pick_lists)
        consensus_hits = sum(1 for pk in picks_data if int(pk.get('CONSENSUS_COUNT', 1) or 1) >= 2)
        print(f"🤝 Consensus merge: {len(picks_data)} unique picks, {consensus_hits} appearing in 2+ runs")

        df_picks = pd.DataFrame(picks_data)

        # Post-filter hallucinated players
        bf = len(df_picks)
        df_picks['player'] = df_picks['player'].map(clean_name)
        df_picks = df_picks[df_picks['player'].map(normalizePlayerName).isin(valid_player_name_map.keys())].copy()
        if not df_picks.empty:
            df_picks['player'] = df_picks['player'].map(lambda n: valid_player_name_map.get(normalizePlayerName(n), n))
        dropped = bf - len(df_picks)
        if dropped > 0:
            print(f"🚫 Post-filter removed {dropped} hallucinated picks")
        if not df_picks.empty and 'prop_type' in df_picks.columns:
            player_norm_series = df_picks['player'].map(normalizePlayerName)
            players_with_pr = set(player_norm_series[df_picks['prop_type'].fillna('').astype(str).str.upper() == 'PR'])
            prop_type_counts = {}
            under_count = 0
            kept_rows = []
            dropped_prop_caps = []
            for _, row in df_picks.iterrows():
                prop_type = str(row.get('prop_type', '') or '').strip().upper()
                lean = str(row.get('lean', '') or '').strip().upper()
                conf = normalize_confidence(row.get('confidence'))
                player_norm = normalizePlayerName(row.get('player', ''))
                if prop_type == 'AST':
                    dropped_prop_caps.append(f"{row.get('player', '?')} AST — blacklisted prop")
                    continue
                if prop_type == 'PRA' and player_norm in players_with_pr:
                    dropped_prop_caps.append(f"{row.get('player', '?')} PRA — PR preferred for same player")
                    continue
                if prop_type == 'PRA':
                    ev_meta = metric_ev_map.get((player_norm, 'PRA'), {})
                    ev_edge = pd.to_numeric(ev_meta.get('edge'), errors='coerce')
                    if pd.isna(ev_edge) or float(ev_edge) < 8:
                        dropped_prop_caps.append(f"{row.get('player', '?')} PRA — edge below threshold")
                        continue
                if lean in {'UNDER', 'FADE'}:
                    if conf == 'LEAN':
                        dropped_prop_caps.append(f"{row.get('player', '?')} {prop_type} UNDER — lean under removed")
                        continue
                    if under_count >= 2:
                        dropped_prop_caps.append(f"{row.get('player', '?')} {prop_type} UNDER — under cap")
                        continue
                if prop_type_counts.get(prop_type, 0) >= 3:
                    dropped_prop_caps.append(f"{row.get('player', '?')} {prop_type} — per-type cap")
                    continue
                prop_type_counts[prop_type] = prop_type_counts.get(prop_type, 0) + 1
                if lean in {'UNDER', 'FADE'}:
                    under_count += 1
                kept_rows.append(row.to_dict())
            if dropped_prop_caps:
                print(f"🚫 Post-filter removed {len(dropped_prop_caps)} extra prop-type picks")
                for reason in dropped_prop_caps[:20]:
                    print(f"   - {reason}")
            df_picks = pd.DataFrame(kept_rows)
        dropped_prop_caps = dropped_prop_caps if 'dropped_prop_caps' in locals() else []
        if not df_picks.empty:
            df_picks['confidence'] = df_picks['confidence'].map(normalize_confidence)
            returning_mask = df_picks['player'].map(lambda n: returning_player_map.get(normalizePlayerName(n), False))
            if returning_mask.any():
                df_picks.loc[returning_mask & (df_picks['confidence'] == 'SMASH'), 'confidence'] = 'STRONG'
            for i, (_, row) in enumerate(df_picks.iterrows(), start=1):
                df_picks.at[row.name, 'rank'] = i
            df_picks = df_picks.reset_index(drop=True)
            df_picks['rank'] = range(1, len(df_picks) + 1)
        if bf > 0 and df_picks.empty:
            print("WARNING: All Gemini picks were filtered out because none matched the normalized valid-player pool")
        if not df_picks.empty:
            df_picks['confidence'] = df_picks['confidence'].map(normalize_confidence)
            smash_idx = df_picks.index[df_picks['confidence'] == 'SMASH'].tolist()
            max_smash = min(3, max(1, len(df_picks) // 4 + (1 if len(df_picks) >= 8 else 0)))
            for idx in smash_idx[max_smash:]:
                df_picks.at[idx, 'confidence'] = 'STRONG'
        print("Gemini pool summary:")
        print(f" - total tonight players: {len(df_tonight_sheet)}")
        print(f" - after dedupe: {len(player_pool)}")
        print(f" - after status filter: {len(filtered_pool)}")
        print(f" - after props filter: {len(prop_pool)}")
        print(f" - fallback used: {'tonight player pool' if len(prop_pool) == 0 else 'props + stats'}")
        print(f" - final sent to Gemini: {len(active_players_for_gemini)}")
        print(f" - picks before post-filter: {bf}")
        print(f" - picks after post-filter: {len(df_picks)}")
        # Fill null lines with L5 average
        for i, row in df_picks.iterrows():
            if pd.isna(row.get('line')) or row.get('line') is None:
                pd2 = active_players_for_gemini[active_players_for_gemini['PLAYER_NAME'] == row['player']]
                if not pd2.empty:
                    fb = pd2.iloc[0].get(f"L5_{row.get('prop_type', 'PRA')}")
                    if pd.notna(fb):
                        df_picks.at[i, 'line'] = round(float(fb), 1)
                        print(f"   📎 Filled null line for {row['player']}: {round(float(fb), 1)}")

        df_picks['DATE'] = today_str
        df_picks['RUN_TIME'] = timestamp_pst
        df_picks['RUN_NUMBER'] = today_run_number
        df_picks['LAST_UPDATED'] = timestamp_pst
        df_picks['RESULT'] = ''
        df_picks['ACTUAL_STAT'] = np.nan
        df_picks['HIT'] = ''
        df_picks['CLV_OPEN_LINE'] = df_picks['line']
        df_picks['CLV_LATEST_LINE'] = df_picks['line']
        df_picks['CLV_DELTA'] = 0.0
        df_picks['CLV_LAST_UPDATE'] = timestamp_pst
        df_picks['DATA_SOURCE'] = 'props_validated' if len(prop_pool) > 0 else 'stats_fallback'
        df_picks['matchup'] = df_picks['game']
        if 'opponent' not in df_picks.columns:
            df_picks['opponent'] = ''
        df_picks['opponent'] = df_picks.apply(
            lambda r: next((part.strip() for part in re.split(r'\s+@\s+|\s+vs\.?\s+', str(r.get('game', ''))) if part.strip() and part.strip() != str(r.get('team', '')).strip()), r.get('opponent', '')),
            axis=1
        )
        df_picks['reasoning'] = df_picks['rationale']
        df_picks['source'] = df_picks['DATA_SOURCE']
        if 'CONSENSUS_COUNT' not in df_picks.columns:
            df_picks['CONSENSUS_COUNT'] = 1
        if 'CONSENSUS_RUNS' not in df_picks.columns:
            df_picks['CONSENSUS_RUNS'] = '1'
        if 'CONSENSUS_TAG' not in df_picks.columns:
            df_picks['CONSENSUS_TAG'] = ''
        dedup_keep = []
        duplicate_drop_msgs = []
        for _, row in df_picks.iterrows():
            pick_key = (
                normalizePlayerName(row.get('player', '')),
                str(row.get('prop_type', '')).strip().upper(),
                str(row.get('lean', '')).strip().upper(),
            )
            if pick_key in seen_pick_keys:
                duplicate_drop_msgs.append(f"{row.get('player')} {row.get('prop_type')} {row.get('lean')} — duplicate prior run")
                print(f"🔁 Skipping duplicate pick: {row.get('player')} {row.get('prop_type')} {row.get('lean')}")
                continue
            seen_pick_keys.add(pick_key)
            dedup_keep.append(row.to_dict())
        if duplicate_drop_msgs:
            dropped_prop_caps.extend(duplicate_drop_msgs)
        df_picks = pd.DataFrame(dedup_keep)

        col_order = ['DATE', 'RUN_NUMBER', 'RUN_TIME', 'rank', 'game', 'matchup', 'game_time', 'player', 'team',
                     'opponent', 'prop_type', 'line', 'lean', 'confidence',
                     'rationale', 'reasoning', 'injury_context', 'spread', 'total', 'DATA_SOURCE', 'source',
                     'CONSENSUS_COUNT', 'CONSENSUS_RUNS', 'CONSENSUS_TAG',
                     'CLV_OPEN_LINE', 'CLV_LATEST_LINE', 'CLV_DELTA', 'CLV_LAST_UPDATE',
                     'RESULT', 'ACTUAL_STAT', 'HIT', 'LAST_UPDATED']
        df_picks = df_picks[[c for c in col_order if c in df_picks.columns]]
        if not df_picks.empty:
            df_picks = df_picks.reset_index(drop=True)
            df_picks['rank'] = range(1, len(df_picks) + 1)
        lean_series = df_picks['lean'].fillna('').astype(str).str.upper().replace({'FADE': 'UNDER'}) if not df_picks.empty else pd.Series(dtype=str)
        conf_series = df_picks['confidence'].fillna('').astype(str).str.upper() if not df_picks.empty else pd.Series(dtype=str)
        prop_dist = df_picks['prop_type'].fillna('').astype(str).str.upper().value_counts().to_dict() if not df_picks.empty else {}
        lean_over = int((lean_series == 'OVER').sum()) if not df_picks.empty else 0
        lean_under = int((lean_series == 'UNDER').sum()) if not df_picks.empty else 0
        star_ct = int(df_picks['player'].isin(star_top20_names).sum()) if not df_picks.empty else 0
        returning_ct = int(df_picks['player'].map(lambda n: returning_player_map.get(normalizePlayerName(n), False)).sum()) if not df_picks.empty else 0
        dropped_total = dropped + len(dropped_prop_caps)
        dropped_reasons = dropped_prop_caps if dropped_prop_caps else (["hallucinated picks removed"] if dropped else [])
        print(f"📊 Post-filter lean mix: {{'OVER': {lean_over}, 'UNDER': {lean_under}}}")
        print("📊 Final pick distribution:")
        print(f"   Prop types: {prop_dist}")
        print(f"   Lean: {lean_over} OVER / {lean_under} UNDER")
        print(f"   Confidence: {int((conf_series == 'SMASH').sum())} SMASH / {int((conf_series == 'STRONG').sum())} STRONG / {int((conf_series == 'LEAN').sum())} LEAN")
        print(f"   Stars: {star_ct}")
        print(f"   Returning: {returning_ct}")
        print(f"   Dropped: {dropped_total} — {', '.join(dropped_reasons[:10]) if dropped_reasons else 'none'}")

        if len(df_picks) > 0:
            print(f"✅ Generated {len(df_picks)} picks across {df_picks['game'].nunique()} games!")
            print(f"🏆 #1: {df_picks.iloc[0]['player']} — {df_picks.iloc[0]['prop_type']} {df_picks.iloc[0]['lean']} {df_picks.iloc[0]['line']} ({df_picks.iloc[0]['confidence']})")
            sc = len(df_picks[df_picks['confidence'] == 'SMASH'])
            print(f"💪 {sc} SMASH | {len(df_picks) - sc} standard")
        else:
            print("⚠️ All picks filtered out.")

    except json.JSONDecodeError as e:
        print(f"❌ JSON parse failed: {e}")
        print(f"Raw: {raw[:500]}")
    except Exception as e:
        print(f"❌ AI Picks failed: {e}")
else:
    if not GEMINI_API_KEY:
        print("⚠️ No Gemini API key — skipping.")
    if len(games_list) == 0:
        print("⚠️ No games tonight — skipping.")

# --- 6. SCRUB DATA ---
print("\nScrubbing data for upload...")
for df in [df_player_final, df_team_final, df_tonight_sheet, df_correlation, ha_pivot]:
    df.replace([np.inf, -np.inf], np.nan, inplace=True)
if not df_props.empty:
    df_props.replace([np.inf, -np.inf], np.nan, inplace=True)
if not df_picks.empty:
    df_picks.replace([np.inf, -np.inf], np.nan, inplace=True)
if not df_ev.empty:
    df_ev.replace([np.inf, -np.inf], np.nan, inplace=True)
if not df_movers.empty:
    df_movers.replace([np.inf, -np.inf], np.nan, inplace=True)
df_team_final['LAST_UPDATED'] = timestamp_pst

# --- 7. UPLOAD ---
print("Uploading to Google Sheets...")

def validate_sheet_schema(sheet_name, df):
    schema = SHEET_SCHEMAS.get(sheet_name) if 'SHEET_SCHEMAS' in globals() else None
    if schema:
        actual_cols = set(df.columns)
        missing_required = [c for c in schema['required'] if c not in actual_cols]
        missing_recommended = [c for c in schema['recommended'] if c not in actual_cols]
        if missing_required:
            msg = f"{sheet_name} missing REQUIRED columns: {missing_required}"
            print(f"   ❌ SCHEMA VIOLATION: {msg}")
            try:
                runlog.warn(msg)
            except Exception:
                pass
            raise RuntimeError(f"Schema validation failed for {sheet_name}: missing required {missing_required}")
        if missing_recommended:
            msg = f"{sheet_name} missing recommended columns: {missing_recommended}"
            print(f"   ⚠️ SCHEMA WARNING: {msg}")
            try:
                runlog.warn(msg)
            except Exception:
                pass

def safe_upload(sheet_name, df):
    if df is None or len(df) == 0:
        print(f"⏭️ Skipping '{sheet_name}' — no data.")
        return
    validate_sheet_schema(sheet_name, df)
    try:
        try:
            ws = sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=sheet_name, rows=1000, cols=150)
        ws.clear()
        ws.update([df.columns.tolist()] + [[clean_cell(v) for v in row] for row in df.values.tolist()])
        print(f"✅ Successfully updated '{sheet_name}'")
        try:
            runlog.record_write(sheet_name, len(df))
        except Exception:
            pass
        time.sleep(1)
    except Exception as e:
        print(f"❌ FAILED '{sheet_name}': {e}")

def normalize_date(val):
    """Convert any date format to ISO for consistent comparison.
    Handles: '2026-04-14', '4/14/2026', '04/14/2026'"""
    if not val:
        return ""
    val = str(val).strip()
    if re.match(r'^\d{4}-\d{2}-\d{2}', val):
        return val[:10]  # already ISO, trim any time portion
    for fmt in ('%m/%d/%Y', '%m/%d/%y'):
        try:
            return datetime.strptime(val, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return val
    
def append_upload(sheet_name, df):
    if df is None or len(df) == 0:
        print(f"⏭️ Skipping append '{sheet_name}' — no data.")
        return
    validate_sheet_schema(sheet_name, df)
    try:
        try:
            ws = sh.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            ws = sh.add_worksheet(title=sheet_name, rows=5000, cols=30)
            ws.update([df.columns.tolist()])

        existing = ws.get_all_values()

        if len(existing) <= 1:
            cleaned = [[clean_cell(v) for v in row] for row in df.values.tolist()]
            ws.update([df.columns.tolist()] + cleaned)
        else:
            headers = existing[0]
            all_headers = headers + [c for c in df.columns.tolist() if c not in headers]
            df_aligned = df.copy()
            for col in all_headers:
                if col not in df_aligned.columns:
                    df_aligned[col] = ''
            df_aligned = df_aligned[all_headers]
            cleaned = [[clean_cell(v) for v in row] for row in df_aligned.values.tolist()]
            if all_headers != headers:
                final_rows = [all_headers]
                for row in existing[1:]:
                    row_map = {headers[i]: row[i] for i in range(min(len(headers), len(row)))}
                    final_rows.append([row_map.get(h, "") for h in all_headers])
                final_rows.extend(cleaned)
                ws.clear()
                ws.update(final_rows, value_input_option='RAW')
            else:
                ws.append_rows(cleaned, value_input_option='RAW')
            print(f"✅ Appended {len(df)} rows to '{sheet_name}'")
            try:
                runlog.record_write(sheet_name, len(df))
            except Exception:
                pass
            return

        print(f"✅ Appended {len(df)} rows to '{sheet_name}'")
        try:
            runlog.record_write(sheet_name, len(df))
        except Exception:
            pass
        time.sleep(1)
    except Exception as e:
        print(f"❌ FAILED append '{sheet_name}': {e}")

safe_upload('Player_Stats', df_player_upload)
safe_upload('Team_Advanced', df_team_final)
safe_upload('Tonights_Opponent', df_tonight_sheet)
safe_upload('Teammate_Correlations', df_correlation)
safe_upload('Home_Away_Splits', ha_pivot)

if not df_props.empty:
    safe_upload('DK_Player_Props', df_props)
else:
    print("⚠️ Skipping DK_Player_Props — no data.")

if not df_all_books.empty:
    safe_upload('All_Books_Props', df_all_books)
else:
    print("⚠️ Skipping All_Books_Props — no data.")

if not df_ev.empty:
    safe_upload('Prop_EV', df_ev)
else:
    print("⚠️ Skipping Prop_EV — no data.")

if not df_movers.empty:
    safe_upload('Line_Movers', df_movers)
else:
    print("ℹ️ No line movers to upload.")

if not df_picks.empty:
    try:
        runlog.picks_generated = len(df_picks)
    except Exception:
        pass
    append_upload('Daily_Picks', df_picks)
else:
    print("⚠️ Skipping Daily_Picks — no picks.")

print("\n" + "=" * 60)
print("🏀 NBA ENGINE v8.5 — RUN COMPLETE")
print("=" * 60)
print(f"📅 Date:             {today_str}")
print(f"📆 Season:           {NBA_SEASON}")
print(f"🗂️  Snapshot:         {SNAPSHOT_DATE}")
print(f"🏟️  Games tonight:    {len(games_list)}")
print(f"🏀 Active players:    {len(df_player_final['PLAYER_NAME'].unique())}")
if not df_props.empty:
    print(f"🎲 Player props:      {len(df_props)}")
    print(f"🏪 All Books Props:   {len(df_all_books)} rows across {df_all_books['BOOK'].nunique() if not df_all_books.empty else 0} books")
else:
    print("🎲 Player props:      Skipped")
if not df_ev.empty:
    print(f"📈 +EV props:         {len(df_ev[df_ev['EDGE_OVER'] > 0])}")
else:
    print("📈 +EV props:         Skipped")
if not df_movers.empty:
    print(f"🔄 Line movers:       {len(df_movers)}")
else:
    print("🔄 Line movers:       None")
if not df_picks.empty:
    print(f"🤖 AI Picks:          {len(df_picks)} picks (Top: {df_picks.iloc[0]['player']} {df_picks.iloc[0]['confidence']})")
else:
    print("🤖 AI Picks:          Skipped")
print(f"📝 Google Sheet:      {SHEET_NAME}")
print(f"🕐 Last updated:      {timestamp_pst}")
print("=" * 60)
