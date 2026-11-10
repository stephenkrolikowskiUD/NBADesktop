"""Public NBA schedule/roster/final-box fallback. Independent of the dashboard UI."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from pathlib import Path
import hashlib
import json
import os
import math
import requests
from nba_api.stats.static import teams
from nba_injuries import norm

BASE='https://site.api.espn.com/apis/site/v2/sports/basketball/nba'
# ESPN numeric team IDs verified against its public NBA team directory.
ESPN_TEAM_IDS={'ATL':'1','BOS':'2','NOP':'3','CHI':'4','CLE':'5','DAL':'6','DEN':'7','DET':'8','GSW':'9','HOU':'10','IND':'11','LAC':'12','LAL':'13','MIA':'14','MIL':'15','MIN':'16','BKN':'17','NYK':'18','ORL':'19','PHI':'20','PHX':'21','POR':'22','SAC':'23','SAS':'24','OKC':'25','UTA':'26','WAS':'27','TOR':'28','MEM':'29','CHA':'30'}
ALIASES={'GS':'GSW','NY':'NYK','NO':'NOP','SA':'SAS','UTAH':'UTA','WSH':'WAS','PHO':'PHX'}


def _abbr(text):return ALIASES.get(str(text).upper(),str(text).upper())


def _get(path,params=None,ttl=120):
    cache=Path(os.getenv('NBA_CACHE_DIR',str(Path(__file__).parent/'nba_cache')))
    cache.mkdir(parents=True,exist_ok=True)
    key=hashlib.sha256((path+json.dumps(params or {},sort_keys=True)).encode()).hexdigest()
    file=cache/(key+'.json')
    if file.exists() and datetime.now().timestamp()-file.stat().st_mtime<ttl:
        return json.loads(file.read_text())
    response=requests.get(BASE+path,params=params,timeout=20)
    response.raise_for_status();payload=response.json()
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w',dir=cache,delete=False) as stream:
        json.dump(payload,stream);temporary=stream.name
    os.replace(temporary,file)
    return payload


def schedule(date):
    from zoneinfo import ZoneInfo
    raw=_get('/scoreboard',{'dates':date.replace('-',''),'limit':100})
    if 'events' not in raw:raise ValueError('ESPN schedule has no events key')
    out=[]
    for event in raw['events']:
        start=datetime.fromisoformat(event['date'].replace('Z','+00:00'))
        if start.astimezone(ZoneInfo('America/New_York')).date().isoformat()!=date:continue
        if event.get('season',{}).get('type')==1:continue
        competition=event['competitions'][0]
        sides={x['homeAway']:x for x in competition['competitors']}
        if set(sides)!={'home','away'}:raise ValueError('Incomplete ESPN competition')
        status=event.get('status',competition.get('status',{})).get('type',{})
        out.append({'homeTeam':{'teamTricode':_abbr(sides['home']['team']['abbreviation'])},
                    'awayTeam':{'teamTricode':_abbr(sides['away']['team']['abbreviation'])},
                    'gameTimeUTC':start.isoformat(),'ESPN_EVENT_ID':str(event['id']),
                    'gameStatus':3 if status.get('completed') else 2 if status.get('state')=='in' else 1})
    return out


def current_roster(games):
    import pandas as pd
    abbreviations=sorted({g[s]['teamTricode'] for g in games for s in ('homeTeam','awayTeam')})
    def fetch(team):
        team_id=ESPN_TEAM_IDS.get(team)
        if not team_id:raise ValueError(f'No verified ESPN team ID for {team}')
        payload=_get(f'/teams/{team_id}/roster',ttl=3600)
        if _abbr(payload.get('team',{}).get('abbreviation'))!=team or not payload.get('athletes'):
            raise ValueError(f'Roster mismatch or missing athletes for {team} (ESPN team {team_id})')
        return [{'DISPLAY_FIRST_LAST':p['displayName'],'TEAM_ABBREVIATION':team,
                 'ESPN_PLAYER_ID':str(p['id'])} for p in payload['athletes']]
    with ThreadPoolExecutor(max_workers=4) as pool:return pd.DataFrame([r for part in pool.map(fetch,abbreviations) for r in part])


def align_roster_names(roster, history):
    out=roster.copy()
    known=history.drop_duplicates('PLAYER_NAME').set_index('PLAYER_NAME').index
    lookup={norm(name):name for name in known}
    out['DISPLAY_FIRST_LAST']=out['DISPLAY_FIRST_LAST'].map(lambda name:lookup.get(norm(name),name))
    return out


def _played_number(value,minutes=False):
    try:
        if minutes and ':' in str(value):
            whole,seconds=str(value).split(':')
            if not 0<=float(seconds)<60:return None
            number=float(whole)+float(seconds)/60
        else:number=float(value)
        return number if math.isfinite(number) and number>=0 else None
    except (ValueError,TypeError):return None


def _final_game_rows(game,date,on_error=None):
    event=game['ESPN_EVENT_ID'];payload=_get('/summary',{'event':event},ttl=300)
    competitions=payload.get('header',{}).get('competitions',[])
    comp=competitions[0] if competitions else {}
    boxes=payload.get('boxscore',{}).get('players',[])
    if str(comp.get('id'))!=event or not comp.get('status',{}).get('type',{}).get('completed') or len(boxes)!=2:
        raise ValueError('Final box is incomplete')
    expected={game['homeTeam']['teamTricode'],game['awayTeam']['teamTricode']}
    if {_abbr(side.get('team',{}).get('abbreviation')) for side in boxes}!=expected:
        raise ValueError('Final box team identities do not match schedule')
    game_rows=[]
    for side in boxes:
        team=_abbr(side['team']['abbreviation']);home=team==game['homeTeam']['teamTricode']
        opponent=game['awayTeam' if home else 'homeTeam']['teamTricode']
        blocks=side.get('statistics',[]);block=blocks[0] if blocks else {}
        athletes=block.get('athletes',[])
        if len(athletes)<5:raise ValueError('Incomplete final player list')
        for person_row in athletes:
            person=person_row.get('athlete',{})
            stats=dict(zip(block.get('labels',[]),person_row.get('stats',[])))
            dnp=person_row.get('didNotPlay') is True
            row={'PLAYER_NAME':person.get('displayName',''),'ESPN_PLAYER_ID':str(person.get('id','')),
                 'GAME_ID':'ESPN:'+event,'ESPN_EVENT_ID':event,'TEAM_ABBREVIATION':team,'GAME_DATE':date,
                 'MATCHUP':f'{team} vs. {opponent}' if home else f'{team} @ {opponent}',
                 'GAME_OPP':opponent,'FINAL':True,'DNP':dnp,'SOURCE':'ESPN final box','WL':''}
            for dest,label in {'MIN':'MIN','PTS':'PTS','REB':'REB','AST':'AST','STL':'STL','BLK':'BLK','TOV':'TO'}.items():
                row[dest]=_played_number(stats.get(label),minutes=dest=='MIN')
            for label,made,attempt in [('FG','FGM','FGA'),('3PT','FG3M','FG3A'),('FT','FTM','FTA')]:
                pieces=str(stats.get(label,'')).split('-')
                row[made]=_played_number(pieces[0]) if len(pieces)==2 else None
                row[attempt]=_played_number(pieces[1]) if len(pieces)==2 else None
                if row[made] is not None and row[attempt] is not None and row[made]>row[attempt]:row[made]=row[attempt]=None
            required=('MIN','PTS','REB','AST','STL','BLK','TOV','FG3M','FG3A','FGM','FGA','FTM','FTA')
            missing=[key for key in required if row.get(key) is None] if not dnp else []
            if not row['PLAYER_NAME'] or not row['ESPN_PLAYER_ID']:missing.append('player identity')
            if missing:
                reason=f"Final box contains incomplete played statistics for {row['PLAYER_NAME']}: {', '.join(missing)}"
                if on_error:
                    on_error({'date':date,'event':event,'team':team,'player':row['PLAYER_NAME'],'reason':reason})
                    continue
                raise ValueError(reason)
            game_rows.append(row)
    return game_rows


def final_boxes(date,on_error=None):
    """Strict for grading; refresh callers can retain valid rows and report gaps."""
    output=[]
    for game in schedule(date):
        if game['gameStatus']!=3:continue
        try:output.extend(_final_game_rows(game,date,on_error))
        except (ValueError,requests.RequestException,KeyError,TypeError,IndexError) as error:
            if not on_error:raise
            on_error({'date':date,'event':game.get('ESPN_EVENT_ID',''),'reason':str(error)})
    return output


def refresh_seed(existing,date,season):
    """Re-fetch corrections and catch up from the seed, retaining historical raw rows."""
    import pandas as pd
    existing = reconcile_history(existing)
    current=existing[pd.to_datetime(existing['GAME_DATE'],errors='coerce').dt.year>=int(season[:4])].copy()
    # Season boundaries use September, so exclude spring rows from the previous season.
    current=current[pd.to_datetime(current['GAME_DATE'],errors='coerce')>=pd.Timestamp(int(season[:4]),9,1)]
    latest=pd.to_datetime(current['GAME_DATE'],errors='coerce').max()
    season_start=datetime(int(season[:4]),10,1).date()
    start=max(season_start,latest.date()-timedelta(days=7)) if pd.notna(latest) else season_start
    end=datetime.fromisoformat(date).date()
    dates=[(start+timedelta(days=i)).isoformat() for i in range(max(0,(end-start).days))]
    issues=[]
    def fetch(day):
        try:return [row for row in final_boxes(day,on_error=issues.append) if not row['DNP']]
        except (ValueError,requests.RequestException,KeyError,TypeError,IndexError) as error:
            issues.append({'date':day,'event':'','reason':str(error)})
            return []
    with ThreadPoolExecutor(max_workers=4) as pool:parts=list(pool.map(fetch,dates))
    fresh=pd.DataFrame([r for part in parts for r in part])
    if fresh.empty:
        result=existing.copy();result.attrs['REFRESH_WARNINGS']=issues
        return result
    # Resolve NBA IDs from the existing seed; ESPN IDs remain separate.
    known=existing.dropna(subset=['PLAYER_ID']).copy()
    known['PERSON_KEY']=known['PLAYER_NAME'].map(norm)
    if (known.groupby('PERSON_KEY')['PLAYER_ID'].nunique()>1).any():
        raise ValueError('Ambiguous normalized player identity in historical seed')
    known=known.drop_duplicates('PERSON_KEY',keep='last').set_index('PERSON_KEY')
    fresh['PERSON_KEY']=fresh['PLAYER_NAME'].map(norm)
    fresh['PLAYER_ID']=fresh['PERSON_KEY'].map(known['PLAYER_ID'])
    fresh['PLAYER_NAME']=fresh['PERSON_KEY'].map(known['PLAYER_NAME']).fillna(fresh['PLAYER_NAME'])
    fresh['PLAYER_ID']=fresh['PLAYER_ID'].fillna(-pd.to_numeric(fresh['ESPN_PLAYER_ID'],errors='coerce'))
    fresh=fresh.drop(columns='PERSON_KEY')
    result=reconcile_history(pd.concat([existing,fresh],ignore_index=True))
    result.attrs['REFRESH_WARNINGS']=issues
    return result


def reconcile_history(frame):
    """Bridge synthetic ESPN IDs only with an overlapping NBA game/team record."""
    import pandas as pd
    if frame.empty:
        return frame.copy()
    out=frame.copy()
    out['_person']=out['PLAYER_NAME'].map(norm)
    out['_day']=pd.to_datetime(out['GAME_DATE'],errors='coerce').dt.strftime('%Y-%m-%d')
    out['_team']=out['TEAM_ABBREVIATION'].map(_abbr)
    out['PLAYER_ID']=pd.to_numeric(out['PLAYER_ID'],errors='coerce')
    for person, group in out.groupby('_person'):
        ids=set(group['PLAYER_ID'].dropna())
        if len(ids)<=1:
            continue
        positive={value for value in ids if value>0}
        synthetic={value for value in ids if value<0}
        if not person or len(positive)!=1 or not synthetic or 0 in ids:
            raise ValueError('Ambiguous normalized player identity in historical seed')
        canonical=next(iter(positive))
        nba=group[group['PLAYER_ID']==canonical]
        nba_games=set(zip(nba['_day'].fillna(''),nba['_team']))
        for source_id in synthetic:
            espn=group[group['PLAYER_ID']==source_id]
            overlap=nba_games & set(zip(espn['_day'].fillna(''),espn['_team']))
            if not any(day and team and team!='NAN' for day,team in overlap):
                raise ValueError('Unverified ESPN/NBA identity bridge in historical seed')
        out.loc[group.index,'PLAYER_ID']=canonical
        out.loc[group.index,'PLAYER_NAME']=nba.iloc[-1]['PLAYER_NAME']
    out=out.drop_duplicates(['PLAYER_ID','_person','_day','_team'],keep='last')
    return out.drop(columns=['_person','_day','_team'])
