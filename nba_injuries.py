"""Official NBA injury reports and conservative availability checks."""
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import csv,io,re,unicodedata
import requests
EASTERN=ZoneInfo('America/New_York')
def iso_time(value):
    try:
        parsed=datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return parsed if parsed.tzinfo else None
    except (ValueError,TypeError):return None
def norm(value):
    text=unicodedata.normalize('NFKD',str(value or ''))
    text=''.join(c for c in text if not unicodedata.combining(c))
    text=re.sub(r"['’`.]",'',text.lower())
    text=re.sub(r'\b(jr|sr|ii|iii|iv)\b','',text)
    return re.sub(r'[^a-z0-9]+',' ',text).strip()
class FeedError(RuntimeError):pass
class ReportHTTP:
    def get(self,url):
        try:
            response=requests.get(url,timeout=10)
            if response.status_code!=200:raise FeedError('Official report unavailable')
            if not response.content.startswith(b'%PDF'):raise FeedError('Official report was not a PDF')
            return response.content
        except requests.RequestException as exc:raise FeedError('Official report unavailable') from exc

STATUSES={'OUT':'OUT','DOUBTFUL':'DOUBTFUL','QUESTIONABLE':'QUESTIONABLE','PROBABLE':'PROBABLE',
          'AVAILABLE':'AVAILABLE','NOT YET SUBMITTED':'UNKNOWN','UNKNOWN':'UNKNOWN','NOT_LISTED':'UNKNOWN'}


def apply_availability(roster,report,date,now):
    """An explicit report record or a submitted official team report is required."""
    timestamp=iso_time(report.get('reported_at'))
    fresh=bool(timestamp and timestamp<=now and (now-timestamp).total_seconds()<=6*3600 and report.get('slate_date')==date)
    lookup={(norm(p.get('player')),p.get('team')):p for p in report.get('players',[])}
    submitted=set(report.get('submitted_teams',[])) if fresh and report.get('official') else set()
    roster_keys={(norm(person['PLAYER_NAME']),person['CURRENT_TEAM']) for person in roster}
    uncertain_teams={team for player,team in lookup if (player,team) not in roster_keys}
    submitted-=uncertain_teams
    out=[]
    for person in roster:
        row=dict(person);team=row['CURRENT_TEAM'];item=lookup.get((norm(row['PLAYER_NAME']),team))
        status=STATUSES.get(str(item.get('status','')).upper(),'UNKNOWN') if item and fresh else ('AVAILABLE' if team in submitted else 'UNKNOWN')
        # A supplied record must identify a source, timestamp, date, and team.
        if not report.get('source'):status='UNKNOWN'
        detail=item.get('detail','') if item else ''
        row.update(AVAILABILITY=status,INJURY_SOURCE=report.get('source',''),
                   INJURY_REPORT_TIME=report.get('reported_at',''),INJURY_DETAIL=detail,
                   MINUTES_RESTRICTED=bool(item and item.get('minutes_restricted')) or bool(re.search(r'minute.? (?:limit|restrict)|minutes restriction',detail,re.I)),
                   INJURY_FRESH=fresh)
        out.append(row)
    return out


def report_from_csv(text):
    rows=list(csv.DictReader(io.StringIO(text)))
    if not rows:return {}
    timestamps={r.get('REPORTED_AT') for r in rows};dates={r.get('SLATE_DATE') for r in rows};sources={r.get('SOURCE') for r in rows}
    if len(timestamps)!=1 or len(dates)!=1 or len(sources)!=1:raise ValueError('Injury CSV must describe one dated source report')
    return {'reported_at':rows[0]['REPORTED_AT'],'slate_date':rows[0]['SLATE_DATE'],'source':rows[0]['SOURCE'],'official':False,
            'submitted_teams':[],'players':[{'player':r['PLAYER_NAME'],'team':r['TEAM_ABBREVIATION'],
            'status':r['STATUS'],'detail':r.get('DETAIL',''),'minutes_restricted':str(r.get('MINUTES_RESTRICTED','')).upper()=='TRUE'} for r in rows]}


def report_from_pdf(data,date,team_names,url):
    """Parse aligned NBA report text conservatively; unsupported layouts fail closed."""
    from pypdf import PdfReader
    reader=PdfReader(io.BytesIO(data))
    text='\n'.join(page.extract_text(extraction_mode='layout') or '' for page in reader.pages)
    stamp=re.search(r'Injury Report:\s*(\d\d/\d\d/\d\d)\s+(\d\d:\d\d\s*[AP]M)',text)
    if not stamp:raise ValueError('NBA injury report timestamp not found')
    reported=datetime.strptime(stamp[1]+' '+stamp[2].replace(' ',''),'%m/%d/%y %I:%M%p').replace(tzinfo=EASTERN)
    report={'source':url,'reported_at':reported.isoformat(),'slate_date':date,'official':True,'submitted_teams':[],'players':[]}
    active_date='';team='';seen=set();unsubmitted=set();verified=set();last_player=None
    for line in text.splitlines():
        date_match=re.search(r'\b(\d\d/\d\d/\d{4})\b',line)
        if date_match:active_date=datetime.strptime(date_match[1],'%m/%d/%Y').date().isoformat();team=''
        if active_date!=date:continue
        for full,abbr in team_names.items():
            if full in line:team=abbr;seen.add(team)
        if not team:continue
        if 'NOT YET SUBMITTED' in line:unsubmitted.add(team);continue
        status=re.search(r'\b(Out|Doubtful|Questionable|Probable|Available)\b',line)
        if not status:
            if re.search(r'\bNone\b',line):verified.add(team)
            elif last_player and line.strip() and not date_match and not any(full in line for full in team_names):
                last_player['detail']+=' '+line.strip()
            continue
        prefix=line[:status.start()].strip()
        # NBA report prints Last, First in the player column.
        person=re.search(r"([\wÀ-ž'’. -]+),\s*([\wÀ-ž'’. -]+)\s*$",prefix)
        if not person:raise ValueError('Unsupported injury player layout; refusing healthy-by-absence inference')
        last=person[1].strip();first=person[2].strip()
        # Strip any left-aligned team/date columns from the surname.
        for full in team_names:last=last.split(full)[-1].strip()
        last=re.split(r'\s{2,}',last)[-1].strip()
        last_player={'player':first+' '+last,'team':team,'status':status[1].upper(),'detail':line[status.end():].strip()}
        report['players'].append(last_player);verified.add(team)
    report['submitted_teams']=sorted(verified-unsubmitted)
    if not seen:raise ValueError('No teams matched requested slate date in NBA report')
    return report


def fetch_official_report(http,date,teams,now):
    # Official report filenames are quarter-hour Eastern timestamps. Try only a bounded window.
    eastern=now.astimezone(EASTERN)
    rounded=eastern.replace(minute=eastern.minute//15*15,second=0,microsecond=0)
    errors=[]
    for minutes in (0,15,30,45,60,90,120):
        stamp=rounded-timedelta(minutes=minutes)
        url='https://ak-static.cms.nba.com/referee/injury/Injury-Report_'+stamp.strftime('%Y-%m-%d_%I_%M%p')+'.pdf'
        try:return report_from_pdf(http.get(url),date,teams,url)
        except (FeedError,ValueError) as exc:errors.append(type(exc).__name__)
    return {'source':'NBA official report unavailable','slate_date':date,'reported_at':'',
            'official':False,'players':[],'submitted_teams':[],'errors':errors}
