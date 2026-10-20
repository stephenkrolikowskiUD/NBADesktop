// Run the actual dashboard script against isolated sheet responses and a minimal DOM.
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const html=fs.readFileSync('index.html','utf8');
const code=html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/loadAllData\(\);\s*setInterval[\s\S]*$/,'');
const app={innerHTML:''};
let fixtures={};
let requests=[];let transientFailures=0;
const context=vm.createContext({
  URL,
  console:{log(){},warn(){},error(){}}, Date, AbortSignal,
  localStorage:{getItem(){return null},setItem(){}},
  document:{getElementById:id=>id==='app'?app:null,querySelectorAll:()=>[],body:{classList:{toggle(){}}}},
  fetch:async url=>{
    const parsed=new URL(url);const sheet=parsed.searchParams.get('sheet');requests.push({sheet,query:parsed.searchParams.get('tq')});
    if(transientFailures>0){transientFailures--;throw new Error('Transient network failure')}
    if((fixtures.__failSheets||[]).includes(sheet))throw new Error('Fixture network failure');
    return {ok:true,text:async()=>JSON.stringify(fixtures[sheet]||[])};
  },
  Papa:{parse:(text,options)=>{
    const data=JSON.parse(text);
    // Match PapaParse's ISO timestamp coercion when dynamic typing is enabled.
    for(const row of data)for(const [field,value] of Object.entries(row)){
      const typed=typeof options.dynamicTyping==='function'?options.dynamicTyping(field):options.dynamicTyping;
      if(typed&&typeof value==='string'&&/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(value))row[field]=new Date(value);
    }
    options.complete({data,errors:[],meta:{fields:[]}});
  }},
});
vm.runInContext(code+'\nglobalThis.api={evaluateFeed,loadAllData,searchDashPlayer,getPlayerLogs,getStreaks,render,teamBadge,matchupBadges,getLockInfo,refreshGameLocks,getState:()=>st,easternTodayISO,seasonForDate,normalizePlayerName,getConvergenceBoard,aiAgreesWithBet,buildCandidatePool,entryFindProp,playerAvailability,getBestBets,scoreCandidate,currentPerformanceRows,performanceTabName,entryLatestPicks};',context);
const api=context.api;
const today=api.easternTodayISO();
const stamp=new Date().toISOString();
const base={SLATE_DATE:today,SEASON:api.seasonForDate(today),LAST_UPDATED:'2026-10-19 11:00:00 AM EDT',UPDATED_AT_UTC:stamp};
const player={...base,PLAYER_NAME:'Example Player',STATUS:'AVAILABLE',TEAM_ABBREVIATION:'LAL',CURRENT_TEAM:'LAL',TONIGHT_OPP:'NYK'};
function seed(status='READY'){
  fixtures={
    NBA_Status:[{...base,STATUS:status}],
    Tonights_Opponent:[{...player}],
    Dashboard_Player_Stats:[{...player,GAME_DATE:today,PTS:20,MIN:30},{...player,SEASON:'2025-26',GAME_DATE:'2026-04-20',PTS:99}],
    DK_Player_Props:[{...player,METRIC:'PTS',DK_LINE:20.5,OVER_ODDS:-110}],
    Daily_Picks:[{DATE:'2026-04-20',player:player.PLAYER_NAME,prop_type:'PTS',line:20.5,lean:'OVER'}],
  };
}
(async()=>{
  if(process.env.NBA_CONTRACT_FIXTURE){
    const contract=JSON.parse(fs.readFileSync(process.env.NBA_CONTRACT_FIXTURE,'utf8'));
    fixtures=contract.ready;await api.loadAllData();assert.equal(api.getState().error,null);
    assert.equal(api.getState().tonight.length,1);assert.equal(api.getState().playerStats.length,1);
    fixtures=contract.offDay;await api.loadAllData();assert.equal(api.getState().error,null);assert.equal(api.getState().tonight.length,0);
    assert.match(app.innerHTML,/No NBA games scheduled/);
    if(contract.future){
      fixtures=contract.future;await api.loadAllData();assert.equal(api.getState().error,null);
      assert.equal(api.getState().aiPicks.length,1);assert.equal(api.entryLatestPicks().length,1);
      assert.equal(api.getState().aiPicks[0].DATE,contract.future.NBA_Status[0].SLATE_DATE);
    }
  }
  assert.match(api.teamBadge('BOS'),/assets\/team-logos\/BOS.svg/);assert.match(api.teamBadge('BRK'),/BKN.svg/);
  assert.equal(api.teamBadge('<bad>'),'&lt;BAD&gt;');assert.match(api.matchupBadges('BOS @ DET'),/DET.svg/);
  const legacy={SEASON:'2025-26',PERFORMANCE_POLICY:'nba-market-provenance-v1',ROI_METHOD:'EQUAL_UNIT_RECORDED_ODDS'};
  const current={...legacy,SEASON:base.SEASON};
  assert.equal(api.currentPerformanceRows([legacy,current]).length,1);
  assert.equal(api.currentPerformanceRows([{...current,PERFORMANCE_POLICY:'legacy'}]).length,0);
  assert.equal(api.scoreCandidate({ai_tier:'SMASH',prop_type:'AST'}).leg_score,api.scoreCandidate({ai_tier:'LEAN',prop_type:'REB'}).leg_score);
  assert.equal(api.normalizePlayerName('Nikola Jokić'),'nikola jokic');
  assert.equal(api.evaluateFeed([]).usable,false);
  assert.equal(api.evaluateFeed([{...base,STATUS:'READY',SLATE_DATE:'2026-01-01'}]).usable,false);
  assert.equal(api.evaluateFeed([{...base,STATUS:'READY',UPDATED_AT_UTC:new Date(Date.now()-5*3600000).toISOString()}]).usable,true);
  seed();transientFailures=1;requests=[];await api.loadAllData();
  assert.equal(api.getState().error,null,'one transient request failure is retried');
  assert.equal(requests[0].sheet,'NBA_Status','status loads before bulk feeds');
  assert.ok(requests.some(r=>r.sheet.startsWith('Pick_Performance_')&&r.query==='select * limit 0'));
  assert.equal(requests.filter(r=>r.sheet.startsWith('Pick_Performance_')&&!r.query).length,0,'missing optional tabs never download fallback history');
  assert.equal(api.getState().error,null);
  assert.equal(api.getState().tonight.length,1);
  assert.equal(api.getState().playerStats.length,1);
  assert.equal(api.getState().aiPicks.length,0,'old picks must never masquerade as current');
  assert.match(app.innerHTML,/Last engine update/);
  seed();
  fixtures.NBA_Status[0].RUN_ID='research-run';
  fixtures.NBA_Status[0].UPDATED_AT_UTC=new Date(Date.now()-5*3600000).toISOString();
  fixtures.Tonights_Opponent[0].ANALYSIS_SEASON='2025-26';
  fixtures.Tonights_Opponent[0].COMMENCE_TIME=new Date(Date.now()+3600000).toISOString();
  fixtures.Daily_Picks=[{DATE:today,player:player.PLAYER_NAME,prop_type:'PTS',line:20.5,lean:'OVER'}];
  for(const rows of [fixtures.Tonights_Opponent,fixtures.Dashboard_Player_Stats,fixtures.DK_Player_Props])for(const row of rows)row.RUN_ID='research-run';
  await api.loadAllData();
  assert.equal(api.getState().tonight.length,1);assert.equal(api.getState().props.length,1);
  assert.equal(api.getState().aiPicks.length,1);assert.ok(api.buildCandidatePool('LAL|NYK').candidates.length);
  assert.equal(api.getState().feedStatus.aging,true);
  assert.match(app.innerHTML,/Saved prices and injury statuses may have changed/);assert.match(app.innerHTML,/<option value="Example Player">/);
  assert.equal(api.searchDashPlayer('example',true),true);assert.equal(api.getState().player,'Example Player');
  assert.equal(api.getPlayerLogs('Example Player')[0].PTS,99,'prior-season history survives stale feed gating');
  assert.equal(api.searchDashPlayer('no such player',true),false);assert.equal(api.getState().player,'Example Player');
  api.getState().researchPlayers.push({PLAYER_NAME:'Example Other'});
  assert.equal(api.searchDashPlayer('example',true),false,'ambiguous searches must not select arbitrary players');
  assert.equal(api.searchDashPlayer('example player'),true);
  fixtures.Dashboard_Player_Stats=[0,1,2,3].map(i=>({...fixtures.Dashboard_Player_Stats[1],PTS:30,Seas_PTS:25,GAME_DATE:`2026-04-${20-i}`}));
  await api.loadAllData();
  assert.ok(api.getStreaks().length,'stale markets must not erase historical streaks');
  assert.equal(api.getStreaks()[0].dkLine,20.5,'saved market prices stay visible with the age warning');
  assert.equal(api.getStreaks()[0].opp,'NYK');assert.equal(api.getStreaks()[0].analysisSeason,'2025-26');
  api.getState().activeTab='picks';api.getState().picksView='streaks';api.render();
  assert.match(app.innerHTML,/Active Streaks/);assert.match(app.innerHTML,/Last game 2026-04-20/);
  assert.match(app.innerHTML,/rerun the engine/);assert.equal(api.getState().props.length,1);

  seed();
  const tomorrow=new Date(Date.parse(today+'T12:00:00Z')+86400000).toISOString().slice(0,10);
  for(const name of ['NBA_Status','Tonights_Opponent','Dashboard_Player_Stats','DK_Player_Props'])for(const row of fixtures[name])row.SLATE_DATE=tomorrow;
  fixtures.Tonights_Opponent[0].COMMENCE_TIME=tomorrow+'T23:59:59-04:00';
  fixtures.Daily_Picks=[{DATE:tomorrow,player:player.PLAYER_NAME,prop_type:'PTS',line:20.5,lean:'OVER'}];
  await api.loadAllData();assert.equal(api.getState().error,null);assert.equal(api.getState().tonight.length,1);
  assert.equal(api.getState().aiPicks.length,1);assert.equal(api.entryLatestPicks().length,1);
  assert.ok(api.buildCandidatePool('LAL|NYK').candidates.length);assert.match(app.innerHTML,new RegExp(tomorrow));
  assert.equal(api.getState().pickGuard,null,'future slate picks are not stale merely because they are tomorrow');
  seed();await api.loadAllData();
  fixtures.Daily_Picks=[{DATE:today,player:player.PLAYER_NAME,prop_type:'PTS',line:20.5,lean:'OVER'},
    {DATE:today,player:player.PLAYER_NAME,prop_type:'REB',line:4.5,lean:'OVER'}];
  await api.loadAllData();assert.equal(api.getState().aiPicks.length,2,'different markets must survive deduplication');
  fixtures.NBA_Status=[];await api.loadAllData();
  assert.equal(api.getState().tonight.length,0);assert.equal(api.getState().props.length,0);
  assert.ok(api.getState().researchPlayers.length,'historical players remain searchable without a live slate');assert.equal(api.getState().error,null);
  seed('NO_GAMES');await api.loadAllData();assert.match(app.innerHTML,/No NBA games scheduled/);
  assert.equal(api.getState().tonight.length,0);
  seed('NO_GAMES');fixtures.NBA_Status[0].RUN_ID='off-day-run';fixtures.Dashboard_Player_Stats[0].RUN_ID='old-run';
  await api.loadAllData();assert.equal(api.getState().error,null);assert.match(app.innerHTML,/No NBA games scheduled/);
  fixtures.Tonights_Opponent=[];await api.loadAllData();assert.equal(api.getState().error,null);
  fixtures.__failSheets=['Dashboard_Player_Stats','Home_Away_Splits','Teammate_Correlations'];
  await api.loadAllData();assert.equal(api.getState().error,null);assert.match(app.innerHTML,/No NBA games scheduled/);
  seed();fixtures.__failSheets=['Dashboard_Player_Stats'];await api.loadAllData();
  assert.match(api.getState().error,/Required slate feed/);assert.equal(api.getState().tonight.length,0);
  assert.doesNotMatch(app.innerHTML,/Check Google Sheet sharing settings/);
  assert.equal(api.evaluateFeed([{...base,STATUS:'READY',UPDATED_AT_UTC:''}]).usable,false,'locale timestamp cannot establish freshness');
  seed('WARMUP');await api.loadAllData();assert.match(app.innerHTML,/history is still building/);
  seed('UPDATING');await api.loadAllData();assert.equal(api.getState().props.length,0);
  seed();fixtures.NBA_Status[0].RUN_ID='new-run';await api.loadAllData();
  assert.notEqual(api.getState().error,null,'mixed snapshots must fail closed');
  seed();
  const future=new Date(Date.now()+3600000).toISOString();
  fixtures.Tonights_Opponent[0].COMMENCE_TIME=future;
  fixtures.Daily_Picks=[{DATE:today,player:player.PLAYER_NAME,prop_type:'PTS',line:24.5,lean:'OVER',confidence:'SMASH'}];
  fixtures.DK_Player_Props[0].DK_LINE=25.5;fixtures.DK_Player_Props[0].OVER_ODDS=150;
  await api.loadAllData();
  assert.equal(api.entryFindProp(player.PLAYER_NAME,'PTS',24.5),null);
  assert.equal(api.buildCandidatePool('LAL|NYK').candidates.some(row=>row.source==='AI'),false,'AI price cannot come from another line');
  assert.ok(api.buildCandidatePool('LAL|NYK').candidates.length);
  for(const status of ['OUT','UNKNOWN']){
    fixtures.Tonights_Opponent[0].STATUS=status;await api.loadAllData();
    assert.equal(api.buildCandidatePool('LAL|NYK').candidates.length,0,status+' must be excluded');
  }
  fixtures.Tonights_Opponent[0].STATUS='AVAILABLE';fixtures.Tonights_Opponent[0].MINUTES_RESTRICTED=true;
  await api.loadAllData();assert.equal(api.getState().aiPicks.length,1);assert.equal(api.buildCandidatePool('LAL|NYK').candidates.length,0);
  assert.equal(api.playerAvailability(player.PLAYER_NAME),'MINUTES RESTRICTED');assert.equal(api.getBestBets().length,0);
  fixtures.Tonights_Opponent[0].MINUTES_RESTRICTED=false;fixtures.Tonights_Opponent[0].COMMENCE_TIME=new Date(Date.now()-3600000).toISOString();
  await api.loadAllData();assert.equal(api.buildCandidatePool('LAL|NYK').candidates.length,0,'started games must be excluded');
  assert.equal(api.getState().aiPicks.length,1,'published picks remain visible after tipoff');assert.equal(api.getLockInfo(player.PLAYER_NAME).started,true);
  api.getState().activeTab='picks';api.getState().picksView='ai';api.render();assert.match(app.innerHTML,/LOCKED/);
  fixtures.Tonights_Opponent[0].COMMENCE_TIME=future;fixtures.DK_Player_Props[0].OVER_ODDS=50;
  await api.loadAllData();assert.equal(api.buildCandidatePool('LAL|NYK').candidates.length,0,'invalid odds must be excluded');
  seed();fixtures.Daily_Picks=[{DATE:today,RUN_NUMBER:1,player:player.PLAYER_NAME,prop_type:'REB',line:4.5,lean:'OVER'},
    {DATE:today,RUN_NUMBER:2,player:player.PLAYER_NAME,prop_type:'PTS',line:20.5,lean:'OVER'}];
  await api.loadAllData();
  vm.runInContext(`getBestBets=()=>[{name:'Example Player',metric:'REB',dkLine:4.5,lean:'OVER',edge:.1},{name:'Example Player',metric:'PTS',dkLine:20.5,lean:'OVER',edge:.1}];getStreaks=()=>[{player:'Example Player',stat:'PTS',streak:5},{player:'Example Player',stat:'REB',streak:5}];getDueBoard=()=>[];getHeaters=()=>[];resetDerived();`,context);
  const board=api.getConvergenceBoard();assert.equal(board.length,1);assert.equal(board[0].sourceCount,3);
  assert.equal(api.aiAgreesWithBet({player:'Example Player',prop_type:'PTS',lean:'OVER',line:21.5},{name:'Example Player',metric:'PTS',lean:'OVER',dkLine:20.5}),false);
  assert.equal(fs.readFileSync('index.html','utf8'),html,'HTML and compatibility source must stay in sync');
  console.log('Dashboard regression checks passed: current/stale/missing/updating/off-day/warmup feeds, season isolation, pick dates, deduplication, and empty-state rendering.');
})().catch(e=>{console.error(e);process.exitCode=1});
