"""Public NBA schedule/roster/final-box fallback. Independent of the dashboard UI."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from pathlib import Path
import hashlib
import json
import os
import requests
from nba_api.stats.static import teams
from nba_injuries import norm

BASE='https://site.api.espn.com/apis/site/v2/sports/basketball/nba'
# ESPN numeric team IDs verified against its public NBA team directory.
ESPN_TEAM_IDS={'ATL':'1','BOS':'2','NOP':'3','CHI':'4','CLE':'5','DAL':'6','DEN':'7','DET':'8','GSW':'9','HOU':'10','IND':'11','LAC':'12','LAL':'13','MIA':'14','MIL':'15','MIN':'16','BKN':'17','NYK':'18','ORL':'19','PHI':'20','PHX':'21','POR':'22','SAC':'23','SAS':'24','TOR':'25','UTA':'26','WAS':'27','MEM':'28','OKC':'29','DEN':'7','NJN':'17','CHA':'30','MIA':'14'}
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


def final_boxes(date):
    """Return only complete final boxes, including explicitly listed DNPs."""
    output=[]
    for game in schedule(date):
        if game['gameStatus']!=3:continue
        event=game['ESPN_EVENT_ID']
        try:
            payload=_get('/summary',{'event':event},ttl=300)
            comp=payload.get('header',{}).get('competitions',[{}])[0]
            boxes=payload.get('boxscore',{}).get('players',[])
            if str(comp.get('id'))!=event or not comp.get('status',{}).get('type',{}).get('completed') or len(boxes)!=2:
                raise ValueError('Final box is incomplete')
        except ValueError as exc:
            print(f"Skipping malformed ESPN final box for event {event} on {date}: {exc}")
            continue
        game_rows=[]
        for side in boxes:
            try:
                team=_abbr(side['team']['abbreviation']);home=team==game['homeTeam']['teamTricode']
                opponent=game['awayTeam' if home else 'homeTeam']['teamTricode']
                block=side.get('statistics',[{}])[0];athletes=block.get('athletes',[])
                if len(athletes)<5:
                    raise ValueError('Incomplete final player list')
                for p in athletes:
                    person=p.get('athlete',{});stats=dict(zip(block.get('labels',[]),p.get('stats',[])))
                    dnp=bool(p.get('didNotPlay'))
                    row={'PLAYER_NAME':person.get('displayName',''),'ESPN_PLAYER_ID':str(person.get('id','')),
                         'GAME_ID':'ESPN:'+event,'ESPN_EVENT_ID':event,'TEAM_ABBREVIATION':team,'GAME_DATE':date,
                         'MATCHUP':f'{team} vs. {opponent}' if home else f'{team} @ {opponent}',
                         'GAME_OPP':opponent,'FINAL':True,'DNP':dnp,'SOURCE':'ESPN final box','WL':''}
                    for dest,label in {'MIN':'MIN','PTS':'PTS','REB':'REB','AST':'AST','STL':'STL','BLK':'BLK','TOV':'TO'}.items():
                        value=stats.get(label)
                        try:row[dest]=float(value)
                        except (TypeError,ValueError):row[dest]=None
                    for label,made,attempt in [('FG','FGM','FGA'),('3PT','FG3M','FG3A'),('FT','FTM','FTA')]:
                        pieces=str(stats.get(label,'')).split('-')
                        try:row[made],row[attempt]=map(float,pieces)
                        except (ValueError,TypeError):row[made]=row[attempt]=None
                    if not dnp:
                        required=('MIN','PTS','REB','AST','STL','BLK','TOV','FG3M')
                        missing=[k for k in required if row.get(k) is None]
                        if missing:
                            print(f"Skipping incomplete ESPN final-box row for {row.get('PLAYER_NAME')} on {date}; missing {missing}")
                            continue
                    game_rows.append(row)
            except ValueError as exc:
                print(f"Skipping malformed ESPN side for event {event} on {date}: {exc}")
                continue
        output.extend(game_rows)
    return output


def refresh_seed(existing,date,season):
    """Re-fetch corrections and catch up from the seed, retaining historical raw rows."""
    import pandas as pd
    current=existing[pd.to_datetime(existing['GAME_DATE'],errors='coerce').dt.year>=int(season[:4])].copy()
    # Season boundaries use September, so exclude spring rows from the previous season.
    current=current[pd.to_datetime(current['GAME_DATE'],errors='coerce')>=pd.Timestamp(int(season[:4]),9,1)]
    latest=pd.to_datetime(current['GAME_DATE'],errors='coerce').max()
    start=(latest.date()-timedelta(days=2)) if pd.notna(latest) else datetime(int(season[:4]),10,1).date()
    end=datetime.fromisoformat(date).date()
    dates=[(start+timedelta(days=i)).isoformat() for i in range(max(0,(end-start).days))]
    def fetch(day):return [r for r in final_boxes(day) if not r['DNP']]
    with ThreadPoolExecutor(max_workers=4) as pool:parts=list(pool.map(fetch,dates))
    fresh=pd.DataFrame([r for part in parts for r in part])
    if fresh.empty:return existing.copy()
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
    return pd.concat([existing,fresh],ignore_index=True).drop_duplicates(['PLAYER_NAME','GAME_DATE','TEAM_ABBREVIATION'],keep='last')
