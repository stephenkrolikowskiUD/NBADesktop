"""Side-effect-free safety rules for the NBA launch pipeline."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import math

EASTERN = ZoneInfo('America/New_York')


def resolve_slate(requested, now, fetch_schedule, lookahead=7):
    today=now.astimezone(EASTERN).date()
    mode=str(requested or 'next').strip().lower()
    if mode in {'next','auto'}:
        days=[today+timedelta(days=offset) for offset in range(lookahead+1)]
    else:
        target=today if mode=='today' else datetime.strptime(mode,'%Y-%m-%d').date()
        if target<today or target>today+timedelta(days=lookahead):
            raise ValueError('NBA_SLATE_DATE must be today or within the next seven days')
        days=[target]
    first=None
    for day in days:
        games,source=fetch_schedule(day.isoformat())
        if source=='unavailable':raise RuntimeError(f'Schedule unavailable for {day}; cannot select a verified slate')
        first=first or (day.isoformat(),games,source)
        if mode not in {'next','auto'}:return day.isoformat(),games,source
        upcoming=[]
        for game in games:
            try:
                start=datetime.fromisoformat(str(game.get('gameTimeUTC','')).replace('Z','+00:00'))
                eligible=start.tzinfo is not None and start>now
            except (ValueError,TypeError):
                eligible=game.get('gameStatus')!=3
            if eligible:upcoming.append(game)
        if upcoming:return day.isoformat(),games,source
    return first


def season_for_date(value):
    date = str(value)[:10]
    parsed = datetime.strptime(date, '%Y-%m-%d')
    year = parsed.year if parsed.month >= 9 else parsed.year - 1
    return f'{year}-{str(year + 1)[-2:]}'


def event_is_on_date(event, target_date):
    """Unknown start times must never qualify as tonight's event."""
    try:
        start = datetime.fromisoformat(str(event.get('commence_time', '')).replace('Z', '+00:00'))
        return start.tzinfo is not None and start.astimezone(EASTERN).date().isoformat() == target_date
    except (ValueError, TypeError):
        return False


def slate_market_events(events, target_date, opponents, team_names):
    selected=[]
    for event in events:
        home=team_names.get(event.get('home_team'))
        away=team_names.get(event.get('away_team'))
        if event_is_on_date(event,target_date) and home and away and opponents.get(home)==away and opponents.get(away)==home:
            selected.append(event)
    return selected


def is_actionable_player(row, now):
    status=str(row.get('STATUS') or row.get('AVAILABILITY') or 'UNKNOWN').strip().upper()
    restricted=str(row.get('MINUTES_RESTRICTED', '')).strip().lower() in {'true','1'}
    try:
        start=datetime.fromisoformat(str(row.get('COMMENCE_TIME') or '').replace('Z','+00:00'))
        return status=='AVAILABLE' and not restricted and start.tzinfo is not None and start>now
    except (ValueError,TypeError):
        return False


PERFORMANCE_POLICY = 'nba-market-provenance-v1'


def performance_eligibility(row, season):
    try:
        if season_for_date(str(row.get('DATE',''))[:10]) != season:return False, 'outside season'
    except ValueError:
        return False, 'invalid date'
    if not event_is_on_date({'commence_time':row.get('COMMENCE_TIME')}, str(row.get('DATE',''))[:10]):
        return False, 'event date mismatch'
    if str(row.get('MARKET_VALIDATED','')).lower() not in {'true','1'}:
        return False, 'market not validated'
    if not row.get('BOOK') or not row.get('EVENT_ID') or finite_odds_probability(row.get('ODDS')) is None:
        return False, 'missing market identity or valid odds'
    try:
        quote,published,start=[datetime.fromisoformat(str(row.get(field,'')).replace('Z','+00:00')) for field in ['QUOTE_TIME','PUBLISHED_AT_UTC','COMMENCE_TIME']]
        if any(value.tzinfo is None for value in [quote,published,start]) or not quote<=published<start:
            return False, 'unverified pregame timing'
        if (published-quote).total_seconds()>4*3600:
            return False, 'stale market quote'
    except (ValueError,TypeError):
        return False, 'missing timestamp'
    if row.get('lean') not in {'OVER','UNDER'}:
        return False, 'invalid side'
    try:
        if not math.isfinite(float(row.get('line'))):return False, 'invalid line'
    except (ValueError,TypeError):
        return False, 'invalid line'
    return True, ''


def dashboard_stats(history, tonight):
    import pandas as pd
    from nba_injuries import norm
    metrics=['MIN','PTS','REB','AST','STL','BLK','TOV','FG3M','FG3A','PRA','PR','PA','RA','STOCKS','DD','DK_FP','UD_FP','FPPM','UD_FPPM','USAGE_PPM']
    names=['PLAYER_NAME','SEASON','TEAM_ABBREVIATION','GAME_DATE','MATCHUP','GAME_OPP','WL','FGA','FTA','FGM','LAST_UPDATED']
    names += metrics + [prefix+metric for prefix in ['Seas_','L3_','L5_','L10_'] for metric in metrics]
    columns=[name for name in dict.fromkeys(names) if name in history.columns]
    if tonight.empty:return pd.DataFrame(columns=columns)
    scopes={norm(row['PLAYER_NAME']):row['ANALYSIS_SEASON'] for row in tonight.to_dict('records')}
    frame=history.copy()
    keep=[scopes.get(norm(row['PLAYER_NAME']))==row['SEASON'] for row in frame.to_dict('records')]
    return frame.loc[keep,columns].copy()


def validate_market_picks(picks, props, normalize_name):
    """Only real, priced markets can become published picks; never invent a line."""
    markets = {}
    for prop in props:
        try:
            line = float(prop['DK_LINE'])
            if math.isfinite(line):
                markets[(normalize_name(prop['PLAYER_NAME']), str(prop['METRIC']).upper(), line)] = prop
        except (ValueError, TypeError, KeyError):
            continue
    valid = []
    for pick in picks:
        try:
            line = float(pick.get('line'))
            lean = str(pick.get('lean', '')).upper().replace('FADE', 'UNDER')
            market = markets.get((normalize_name(pick.get('player', '')), str(pick.get('prop_type', '')).upper(), line))
            if market is None or lean not in {'OVER', 'UNDER'}:
                continue
            odds = float(market.get(f'{lean}_ODDS'))
            if not math.isfinite(odds) or abs(odds) < 100:
                continue
            valid.append({**pick, 'line': line, 'lean': lean, 'BOOK': market.get('BOOK', 'draftkings'), 'ODDS': odds, **{k:market.get(k,'') for k in ['EVENT_ID','COMMENCE_TIME','QUOTE_TIME']}})
        except (ValueError, TypeError):
            continue
    return valid


def replace_sheet_values(ws, values):
    """Write first, then erase obsolete tail cells. Failed writes preserve old data."""
    if not values or not values[0]:
        raise ValueError('Replacement requires an explicit header')
    old = ws.get_all_values()
    backup_values(old,getattr(ws,'title','worksheet'))
    width = max(len(row) for row in values)
    if len(values) > ws.row_count or width > ws.col_count:
        ws.resize(rows=max(ws.row_count, len(values)), cols=max(ws.col_count, width))
    ws.update(values=values, range_name='A1', value_input_option='RAW')
    def column(number):
        label = ''
        while number:
            number, rem = divmod(number - 1, 26)
            label = chr(65 + rem) + label
        return label
    ranges = []
    old_width = max((len(row) for row in old), default=0)
    if len(old) > len(values):
        ranges.append(f'A{len(values) + 1}:{column(max(width, old_width))}{len(old)}')
    if old_width > width:
        ranges.append(f'{column(width + 1)}1:{column(old_width)}{len(values)}')
    if ranges:
        ws.batch_clear(ranges)


def select_analysis_history(frame, season):
    """Use current-season rows when present; otherwise label one prior-season baseline."""
    import pandas as pd
    year=int(season[:4])-1
    prior=f'{year}-{str(year+1)[-2:]}'
    selected=[];metadata=[]
    for name,group in frame.groupby('PLAYER_NAME'):
        current=group[group['SEASON']==season]
        previous=group[group['SEASON']==prior]
        scope=season if not current.empty else prior
        rows=current if not current.empty else previous
        if rows.empty:continue
        selected.append(rows)
        metadata.append({'PLAYER_NAME':name,'ANALYSIS_SEASON':scope,
                         'BASELINE_TYPE':'CURRENT' if not current.empty else 'PRIOR_SEASON',
                         'CURRENT_GAMES':len(current),'PRIOR_GAMES':len(previous)})
    data=pd.concat(selected,ignore_index=True) if selected else frame.iloc[:0].copy()
    info=pd.DataFrame(metadata,columns=['PLAYER_NAME','ANALYSIS_SEASON','BASELINE_TYPE','CURRENT_GAMES','PRIOR_GAMES'])
    return data,info


def finite_odds_probability(value):
    try:value=float(value)
    except (TypeError,ValueError):return None
    if not math.isfinite(value) or abs(value)<100:return None
    return abs(value)/(abs(value)+100) if value<0 else 100/(value+100)


def non_push_rate(values,line):
    usable=[]
    for value in values:
        try:
            value=float(value)
            if math.isfinite(value):usable.append(value)
        except (TypeError,ValueError):pass
    over=sum(v>line for v in usable);under=sum(v<line for v in usable)
    return over/(over+under) if over+under else None


def box_metrics(row):
    """Fantasy and combination stats shared by the final-box grading path."""
    result=dict(row)
    required=['PTS','REB','AST','STL','BLK','TOV','FG3M']
    vals={}
    for key in required:
        try:
            value=float(row.get(key))
            vals[key]=value if math.isfinite(value) else None
        except (TypeError,ValueError):vals[key]=None
    if any(v is None for v in vals.values()):return result
    p,r,a,s,b,t,th=(vals[k] for k in required)
    count=sum(x>=10 for x in (p,r,a,s,b))
    result.update(PRA=p+r+a,PR=p+r,PA=p+a,RA=r+a,STOCKS=s+b,
        DK_FP=round(p+.5*th+1.25*r+1.5*a+2*s+2*b-.5*t+1.5*(count>=2)+3*(count>=3),3),
        UD_FP=round(p+1.2*r+1.5*a+3*s+3*b-t,3))
    return result


def selection_key(row,normalize_name):
    try:line=f"{float(row.get('line')):g}"
    except (TypeError,ValueError):line=''
    return (str(row.get('DATE',''))[:10],normalize_name(row.get('player','')),
            str(row.get('prop_type','')).upper(),str(row.get('lean','')).upper().replace('FADE','UNDER'),
            line,str(row.get('BOOK') or 'draftkings'),str(row.get('EVENT_ID') or ''))


def backup_values(values,title='worksheet'):
    import os,json,uuid
    from pathlib import Path
    from datetime import timezone
    directory=Path(os.getenv('NBA_BACKUP_DIR',str(Path(__file__).parent/'nba_backups')))
    directory.mkdir(parents=True,exist_ok=True)
    target=directory/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')+'-'+uuid.uuid4().hex[:6]+'-'+title+'.json')
    target.write_text(json.dumps(values,ensure_ascii=False,default=str))
    return target
