// Run the actual dashboard script against isolated sheet responses and a minimal DOM.
const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const html=fs.readFileSync('index.html','utf8');
const code=html.match(/<script>([\s\S]*?)<\/script>/)[1].replace(/loadAllData\(\);\s*setInterval[\s\S]*$/,'');
const app={innerHTML:''};
let fixtures={};
const context=vm.createContext({
  console:{log(){},warn(){},error(){}}, Date, AbortSignal,
  localStorage:{getItem(){return null},setItem(){}},
  document:{getElementById:id=>id==='app'?app:null,querySelectorAll:()=>[],body:{classList:{toggle(){}}}},
  fetch:async url=>({ok:true,text:async()=>JSON.stringify(fixtures[new URL(url).searchParams.get('sheet')]||[])}),
  Papa:{parse:(text,options)=>options.complete({data:JSON.parse(text),errors:[],meta:{fields:[]}})},
});
vm.runInContext(code+'\nglobalThis.api={evaluateFeed,loadAllData,getState:()=>st,easternTodayISO,seasonForDate,normalizePlayerName,getConvergenceBoard,aiAgreesWithBet};',context);
const api=context.api;
const today=api.easternTodayISO();
const stamp=new Date().toString();
const base={SLATE_DATE:today,SEASON:api.seasonForDate(today),LAST_UPDATED:stamp};
const player={...base,PLAYER_NAME:'Example Player',STATUS:'AVAILABLE',TEAM_ABBREVIATION:'LAL',CURRENT_TEAM:'LAL',TONIGHT_OPP:'NYK'};
function seed(status='READY'){
  fixtures={
    NBA_Status:[{...base,STATUS:status}],
    Tonights_Opponent:[player],
    Player_Stats:[{...player,GAME_DATE:today,PTS:20,MIN:30},{...player,SEASON:'2025-26',GAME_DATE:'2026-04-20',PTS:99}],
    DK_Player_Props:[{...player,METRIC:'PTS',DK_LINE:20.5,OVER_ODDS:-110}],
    Daily_Picks:[{DATE:'2026-04-20',player:player.PLAYER_NAME,prop_type:'PTS',line:20.5,lean:'OVER'}],
  };
}
(async()=>{
  assert.equal(api.normalizePlayerName('Nikola Jokić'),'nikola jokic');
  assert.equal(api.evaluateFeed([]).usable,false);
  assert.equal(api.evaluateFeed([{...base,STATUS:'READY',SLATE_DATE:'2026-01-01'}]).usable,false);
  assert.equal(api.evaluateFeed([{...base,STATUS:'READY',LAST_UPDATED:new Date(Date.now()-5*3600000).toString()}]).usable,false);
  seed();await api.loadAllData();
  assert.equal(api.getState().error,null);
  assert.equal(api.getState().tonight.length,1);
  assert.equal(api.getState().playerStats.length,1);
  assert.equal(api.getState().aiPicks.length,0,'old picks must never masquerade as current');
  assert.match(app.innerHTML,/NBA feed is current/);
  fixtures.Daily_Picks=[{DATE:today,player:player.PLAYER_NAME,prop_type:'PTS',line:20.5,lean:'OVER'},
    {DATE:today,player:player.PLAYER_NAME,prop_type:'REB',line:4.5,lean:'OVER'}];
  await api.loadAllData();assert.equal(api.getState().aiPicks.length,2,'different markets must survive deduplication');
  fixtures.NBA_Status=[];await api.loadAllData();
  assert.equal(api.getState().tonight.length,0);assert.equal(api.getState().props.length,0);
  assert.equal(api.getState().player,'');assert.equal(api.getState().error,null);
  seed('NO_GAMES');await api.loadAllData();assert.match(app.innerHTML,/No NBA games scheduled/);
  assert.equal(api.getState().tonight.length,0);
  seed('WARMUP');await api.loadAllData();assert.match(app.innerHTML,/history is still building/);
  seed('UPDATING');await api.loadAllData();assert.equal(api.getState().props.length,0);
  seed();fixtures.NBA_Status[0].RUN_ID='new-run';await api.loadAllData();
  assert.notEqual(api.getState().error,null,'mixed snapshots must fail closed');
  seed();fixtures.Daily_Picks=[{DATE:today,RUN_NUMBER:1,player:player.PLAYER_NAME,prop_type:'REB',line:4.5,lean:'OVER'},
    {DATE:today,RUN_NUMBER:2,player:player.PLAYER_NAME,prop_type:'PTS',line:20.5,lean:'OVER'}];
  await api.loadAllData();
  vm.runInContext(`getBestBets=()=>[{name:'Example Player',metric:'REB',dkLine:4.5,lean:'OVER',edge:.1},{name:'Example Player',metric:'PTS',dkLine:20.5,lean:'OVER',edge:.1}];getStreaks=()=>[{player:'Example Player',stat:'PTS',streak:5},{player:'Example Player',stat:'REB',streak:5}];getDueBoard=()=>[];getHeaters=()=>[];resetDerived();`,context);
  const board=api.getConvergenceBoard();assert.equal(board.length,1);assert.equal(board[0].sourceCount,3);
  assert.equal(api.aiAgreesWithBet({player:'Example Player',prop_type:'PTS',lean:'OVER',line:21.5},{name:'Example Player',metric:'PTS',lean:'OVER',dkLine:20.5}),false);
  assert.equal(fs.readFileSync('index.html','utf8'),html,'HTML and compatibility source must stay in sync');
  console.log('Dashboard regression checks passed: current/stale/missing/updating/off-day/warmup feeds, season isolation, pick dates, deduplication, and empty-state rendering.');
})().catch(e=>{console.error(e);process.exitCode=1});
