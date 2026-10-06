'use strict';
import {MarketChart} from './market-chart.js';
import {DarkSelect} from './dark-select.js';
import {DatasetLibrary,intervalLabel,originLabel} from './dataset-library.js';
import {ProviderConnection} from './provider-connection.js';
import {dateRangeError} from './date-fields.js';
const $ = id => document.getElementById(id);
const menuControls=[['session-select','Chart session',true],['strategy','Strategy model'],['dataset','Dataset'],['activity-filter','Show activity']].map(([id,label,searchable])=>new DarkSelect($(id),{label,searchable}));
const syncMenus=()=>menuControls.forEach(control=>control.sync());
let chartControlsEnabled=false, datasetCatalog=[], failedChartSession;
const names = {VWAP_OPENING:'Opening VWAP',SMA_CROSSOVER:'SMA crossover',EMA_CROSSOVER:'EMA crossover',RSI:'RSI recovery'};
const descriptions={VWAP_OPENING:'Strong opening candles. Higher-timeframe confirmation.',SMA_CROSSOVER:'Study the crossover between two moving averages.',EMA_CROSSOVER:'Track changes in direction with exponential averages.',RSI:'Study momentum as price leaves an extreme.'};
const parameterSets = {
  VWAP_OPENING:[['min_body_fraction','Minimum body / range',.6,.05,1,.05],['min_close_location','Close near candle edge',.75,.5,1,.05],['min_move_bps','Minimum candle move (bps)',10,0,1000,1],['min_vwap_slope_bps','Minimum VWAP slope (bps)',0,0,1000,1],['min_vwap_distance_bps','Minimum VWAP distance (bps)',0,0,1000,1],['fast_ema','Fast EMA',20,1,1000,1],['medium_ema','Medium EMA',50,2,1000,1],['slow_ema','Slow EMA',200,3,1000,1],['trend_ema_period','Weekly / monthly EMA period',2,1,120,1]],
  SMA_CROSSOVER:[['short_period','Fast SMA',10,1,1000,1],['long_period','Slow SMA',20,2,1000,1]],
  EMA_CROSSOVER:[['short_period','Fast EMA',12,1,1000,1],['long_period','Slow EMA',26,2,1000,1]],
  RSI:[['rsi_period','RSI period',14,1,1000,1],['oversold_threshold','Oversold threshold',30,1,99,1],['overbought_threshold','Overbought threshold',70,1,99,1]]
};
let report=null, selectedRun=null, page=0, pollVersion=0, pendingRequest=null, chartVersion=0, chartData=null, formRevision=0, loadingFormRevision=0, submitting=false;
const money = value => Number.isFinite(value) ? new Intl.NumberFormat('en-CA',{style:'currency',currency:selectedRun?.dataset_info?.currency||'USD',currencyDisplay:'narrowSymbol'}).format(value) : '—';
const pct = value => Number.isFinite(value) ? (value*100).toFixed(2)+'%' : '—';
const num = value => new Intl.NumberFormat('en-CA',{maximumFractionDigits:2}).format(value);
function node(tag, text, className) { const el=document.createElement(tag); if(text!=null)el.textContent=text;if(className)el.className=className;return el; }
function error(message, area='workspace-error') { $(area).textContent=message; $(area).hidden=!message; }
async function api(path, options) {
  const response=await fetch(path,options);
  const data=await response.json();
  if(!response.ok) throw new Error(typeof data.error==='string'?data.error:JSON.stringify(data.error||data));
  return data;
}

function workspaceFromHash(){return location.hash==='#saved-runs'?'history':location.hash==='#data'?'data':'workbench';}
function datasetInfo(id){return datasetCatalog.find(data=>data.id===id)||{id,name:id,symbol:'SIM',currency:'USD',origin:'synthetic',bar_minutes:id==='opening_demo'?2:0,session_open_minute:570,timezone:'exchange-local demo'};}
function clockLabel(minute){return String(Math.floor(minute/60)).padStart(2,'0')+':'+String(minute%60).padStart(2,'0');}
function runContext(run){const data=run.dataset_info||datasetInfo(run.dataset);return [originLabel(data),intervalLabel(data)+' candles',data.currency||'USD','Saved locally'].join(' · ');}
function renderInstrument(data){
  $('instrument-symbol').textContent=data.symbol||'SIM';$('instrument-source').textContent=originLabel(data);$('instrument-source').title=data.source||data.name;
  $('interval-label').textContent=intervalLabel(data);$('market-description').textContent=data.name+' · '+originLabel(data);
}
function updateDatasetNote(){
  const data=datasetInfo($('dataset').value);
  $('dataset-note').textContent=[originLabel(data),intervalLabel(data)+' candles',data.origin==='imported'?data.source:'Fictional prices for testing'].filter(Boolean).join(' · ');
  $('capital-label').textContent='Capital ('+(data.currency||'USD')+')';
  $('session-note').textContent='From '+clockLabel(data.session_open_minute??570)+' · '+(data.timezone||'exchange time');
}
function updateCatalog(datasets){
  datasetCatalog=datasets;const selected=$('dataset').value;const options=datasets.map(data=>{const option=node('option',data.name+' · '+intervalLabel(data));option.value=data.id;return option;});
  $('dataset').replaceChildren(...options);if(datasets.some(data=>data.id===selected))$('dataset').value=selected;
  updateDatasetNote();syncMenus();
}
function renderDatasetDetails(data){
  const rows=[['Dataset',data.name],['Origin',originLabel(data)],['Source',data.source||'Bundled synthetic fixture'],['Symbol / currency',(data.symbol||'SIM')+' / '+(data.currency||'USD')],['Candle interval',intervalLabel(data)],['Time zone',data.timezone||'exchange-local demo']];
  if(data.price_adjustment)rows.push(['Price adjustment',data.price_adjustment.replaceAll('_',' ')]);
  if(data.first_timestamp)rows.push(['Coverage',data.first_timestamp.slice(0,10)+' → '+data.last_timestamp.slice(0,10)]);
  $('data-summary').replaceChildren(...rows.map(([key,value])=>{const row=node('div');row.append(node('dt',key),node('dd',value));return row;}));
  $('data-warnings').replaceChildren(...(data.warnings||[]).map(text=>node('li',text)));
}
const datasetLibrary=new DatasetLibrary({api,onCatalog:updateCatalog,onSelect:data=>{
  $('dataset').value=data.id;updateDatasetNote();syncMenus();markEdited();showWorkspace('workbench');
  $('dataset-trigger').focus();
}});
const providerConnection=new ProviderConnection({api,onDataset:async data=>{
  if(data?.id)datasetLibrary.highlight=data.id;
  await datasetLibrary.refresh();
}});

function parameterInputs() {
  const strategy=$('strategy').value;
  $('vwap-settings').hidden=strategy!=='VWAP_OPENING';
  $('rule-summary').textContent={VWAP_OPENING:'Strong opening candle · rising VWAP · EMA alignment · completed weekly & monthly trends.',SMA_CROSSOVER:'Enter after a fast SMA crosses above a slow SMA. Exit on the bearish crossover.',EMA_CROSSOVER:'Enter after a fast EMA crosses above a slow EMA. Exit on the bearish crossover.',RSI:'Enter as RSI recovers above oversold. Exit as it falls below overbought.'}[strategy];
  const container=$('strategy-parameters');container.replaceChildren();
  for(const [key,label,value,min,max,step] of parameterSets[strategy]){
    const wrap=node('div'),l=node('label',label);l.htmlFor='param-'+key;
    const input=node('input');Object.assign(input,{id:'param-'+key,type:'number',value,min,max,step,required:true});
    wrap.append(l,input);container.append(wrap);
  }
  if(strategy==='VWAP_OPENING')container.append(node('p','The default trend period is a starting rule, not your mentor’s confirmed setting. It uses only completed weeks and months.','hint'));
  renderRuleSteps();
}
function renderRuleSteps(){
  const strategy=$('strategy').value;
  const value=key=>$('param-'+key)?.value||'—';
  const percent=key=>value(key)==='—'?'—':num(Number(value(key))*100)+'%';
  const periods=['fast_ema','medium_ema','slow_ema'].map(value).join(' / ');
  const rows={
    VWAP_OPENING:[['Candle body','≥ '+percent('min_body_fraction')+' · ≥ '+value('min_move_bps')+' bps'],['VWAP','Rising · close above'],['EMA periods',periods],['Trend filter','Weekly + monthly · EMA '+value('trend_ema_period')]],
    SMA_CROSSOVER:[['SMA periods',value('short_period')+' / '+value('long_period')],['Entry','Bullish crossover'],['Exit','Bearish crossover']],
    EMA_CROSSOVER:[['EMA periods',value('short_period')+' / '+value('long_period')],['Entry','Bullish crossover'],['Exit','Bearish crossover']],
    RSI:[['RSI period',value('rsi_period')],['Entry','Recover above '+value('oversold_threshold')],['Exit','Fall below '+value('overbought_threshold')]]
  };
  $('rule-steps').replaceChildren(...rows[strategy].map(([label,value])=>{const row=node('li');row.append(node('span',label,'rule-label'),node('span',value,'rule-value'));return row;}));
}
function syncWindow(){for(const button of document.querySelectorAll('[data-window]')){const selected=button.dataset.window===$('window').value;button.setAttribute('aria-checked',String(selected));button.tabIndex=selected?0:-1;}}
function clearValidation(){for(const input of $('run-form').querySelectorAll('[aria-invalid=true]')){input.removeAttribute('aria-invalid');if(['start-date','end-date'].includes(input.id))input.setAttribute('aria-describedby','evaluation-hint');else input.removeAttribute('aria-describedby');}}
function markEdited(){clearValidation();error('','form-error');formRevision++;$('setup-state').textContent='Edited · not run';$('setup-state').classList.add('edited');renderRuleSteps();}
function setSubmitting(active){submitting=active;$('run-button').disabled=active;document.querySelector('.inspector-run').disabled=active;$('run-button').textContent=active?'Saving run…':'Run backtest';}
function showWorkspace(name,updateHash=true){
  for(const [key,id] of [['workbench','workbench'],['history','saved-runs'],['data','data-workspace']])$(id).hidden=key!==name;
  for(const menu of menuControls)menu.close(false);
  for(const link of document.querySelectorAll('[data-workspace]')){if(link.dataset.workspace===name)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');}
  if(updateHash)history.replaceState(null,'',name==='history'?'#saved-runs':name==='data'?'#data':'#workbench');
}
function journalTime(timestamp){const date=timestamp.slice(0,10).split('-').map(Number);if(date.length!==3)return timestamp;return new Date(date[0],date[1]-1,date[2]).toLocaleDateString('en-CA',{month:'short',day:'numeric',year:'numeric'})+(timestamp.length>10?' · '+timestamp.slice(11,16):'');}
function requestPayload(){
  const strategy=$('strategy').value,params={};
  for(const [key] of parameterSets[strategy]) params[key]=Number($('param-'+key).value);
  if(strategy==='VWAP_OPENING')params.opening_window_minutes=Number($('window').value);
  const backtesting={initial_capital:Number($('capital').value),commission_rate:Number($('commission').value)/100,slippage:Number($('slippage').value)/10000};
  if($('start-date').value.trim())backtesting.start_date=$('start-date').value.trim();
  if($('end-date').value.trim())backtesting.end_date=$('end-date').value.trim();
  return {dataset:$('dataset').value,strategy,config:{backtesting,risk_management:{max_position_size:Number($('allocation').value)/100,stop_loss_pct:Number($('stop').value)/100,take_profit_pct:Number($('profit').value)/100},strategies:{[strategy]:params}}};
}
async function submit(event){
  event.preventDefault();if(submitting)return;error('','form-error');clearValidation();
  const invalid=$('run-form').querySelector(':invalid');
  if(invalid){const details=invalid.closest('details');if(details)details.open=true;error(invalid.validationMessage,'form-error');invalid.setAttribute('aria-invalid','true');invalid.setAttribute('aria-describedby','form-error');invalid.focus();return;}
  const dateIssue=dateRangeError($('start-date').value.trim(),$('end-date').value.trim());
  if(dateIssue){const input=$(dateIssue.field+'-date');input.closest('details').open=true;error(dateIssue.message,'form-error');input.setAttribute('aria-invalid','true');input.setAttribute('aria-describedby','evaluation-hint form-error');input.focus();return;}
  const payload=requestPayload();
  if(payload.strategy==='VWAP_OPENING'&&!datasetInfo(payload.dataset).bar_minutes){error('Opening VWAP needs intraday candles. Choose an intraday dataset, or use a crossover or RSI strategy for daily prices.','form-error');const trigger=$('dataset-trigger');trigger.setAttribute('aria-invalid','true');trigger.setAttribute('aria-describedby','form-error');trigger.focus();return;}
  const signature=JSON.stringify(payload);
  const submittedRevision=formRevision;
  if(!pendingRequest||pendingRequest.signature!==signature)pendingRequest={signature,request_id:crypto.randomUUID()};
  setSubmitting(true);
  try{
    const data=await api('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,request_id:pendingRequest.request_id})});
    pendingRequest=null;await selectRun(data.run,{setupRevision:submittedRevision});await refreshHistory();
  }catch(e){error(e.message+' You can retry this request.','form-error');}
  finally{setSubmitting(false);}
}
async function selectRun(run,{reveal=true,focus=false,setupRevision=formRevision}={}){
  if(reveal)showWorkspace('workbench');
  if(focus)$('results').focus();
  const version=++pollVersion;selectedRun=run;report=null;page=0;
  try{localStorage.setItem('opening-bell-selected-run',run.id);}catch{}
  loadingFormRevision=setupRevision;
  ++chartVersion;chartData=null;$('chart-data-note').hidden=true;$('retry-chart').hidden=true;failedChartSession=undefined;marketChart.setData(null);resetQuote();setChartControls(false);error('','market-error');
  $('market-chart').setAttribute('aria-busy','true');
  $('session-select').replaceChildren(node('option','Loading…'));
  $('opening-view').hidden=true;$('window-label').hidden=true;
  renderInstrument(run.dataset_info||datasetInfo(run.dataset));
  error('');$('result-content').hidden=true;$('download-button').disabled=true;
  $('result-title').textContent=names[run.strategy]||run.strategy;
  $('strategy-description').textContent=descriptions[run.strategy]||'';
  $('result-subtitle').textContent=runContext(run);
  $('result-subtitle').title='Run '+run.id;
  $('reconcile-result').textContent='';
  await showRunStatus(run,version);
}
async function showRunStatus(run,version){
  if(version!==pollVersion)return;
  selectedRun=run;$('run-status').textContent=run.status;
  $('run-status').dataset.status=run.status;
  const active=['pending','queued','running'].includes(run.status);
  $('cancel-button').hidden=!active;$('loading').hidden=!active;
  $('loading').textContent=run.status==='running'?'Running the simulation and saving its accounting history…':'Your simulation is queued. Results will appear here.';
  if(run.status==='completed'){
    $('loading').hidden=false;$('loading').textContent='Loading the saved result and accounting history…';
    try{const data=await api('/api/runs/'+run.id+'/result');if(version!==pollVersion)return;report=data;renderResult();await Promise.all([loadMarketChart(),refreshHistory()]);}
    catch(e){if(version===pollVersion){error(e.message);$('loading').hidden=true;$('market-chart').setAttribute('aria-busy','false');}}
  }else if(active){
    setTimeout(async()=>{if(version!==pollVersion)return;try{const data=await api('/api/runs/'+run.id);await showRunStatus(data.run,version);}catch(e){if(version===pollVersion){error(e.message+' Refresh saved simulations to reconnect.');$('loading').hidden=true;}}},900);
  }else{$('market-chart').setAttribute('aria-busy','false');error(run.error||('This simulation was '+run.status+'. Its partial results were not published.'));await refreshHistory();}
}
function renderResult(){
  const r=report.results;$('result-content').hidden=false;$('loading').hidden=true;$('download-button').disabled=false;
  if(formRevision===loadingFormRevision)loadSavedSetup();
  $('result-subtitle').textContent=runContext(selectedRun)+(report.strategy==='VWAP_OPENING'?' · '+report.effective_config.strategies.VWAP_OPENING.opening_window_minutes+' min entry window':'');
  renderDatasetDetails(selectedRun.dataset_info||datasetInfo(selectedRun.dataset));
  renderDiagnostics();
  $('equity').textContent=money(r.final_equity);
  const change=r.final_equity-r.initial_cash_cents/100;
  $('net-change').textContent=(change>=0?'+':'')+money(change)+' from start';
  $('return').textContent=pct(r.total_return);$('return').className=r.total_return>=0?'positive':'negative';
  $('drawdown').textContent=pct(r.max_drawdown);$('fills').textContent=num(r.total_trades);
  $('closed-count').textContent=num(r.closed_trades)+' closed exits';
  $('trade-count').textContent=num(r.total_trades);
  const balances=$('balances');balances.replaceChildren();
  for(const [label,value] of [['Available cash',money(r.final_cash)],['Open shares',num(r.final_quantity)],['Remaining cost basis',money(r.cost_basis)],['Realized P&L',money(r.realized_pnl)],['Unrealized P&L',money(r.unrealized_pnl)],['Winning exits',r.closed_trades?pct(r.win_rate):'—'],['Commission paid',money(r.total_fees)],['Slippage cost',money(r.total_slippage)]]){
    const row=node('div');row.append(node('dt',label),node('dd',value));balances.append(row);
  }
  drawCharts();renderActivity();
  $('provenance').textContent=JSON.stringify({run_id:selectedRun.id,engine_version:report.engine_version,engine_commit:report.engine_commit,engine_source_sha256:report.engine_source_sha256,dataset_fingerprint:report.dataset_fingerprint,dataset_sha256:selectedRun.dataset_sha256,dataset_info:selectedRun.dataset_info,effective_config:report.effective_config},null,2);
  $('assumptions').replaceChildren(...(report.assumptions||[]).map(value=>node('li',value)));
  if(report.benchmark)$('benchmark-note').textContent=report.benchmark.assumption;
}
function renderDiagnostics(){
  const section=$('strategy-diagnostics');section.hidden=report.strategy!=='VWAP_OPENING';if(section.hidden)return;
  const d=report.results.strategy_diagnostics,config=report.effective_config.backtesting;
  $('evaluation-range').textContent=(config.start_date||'First date')+' → '+(config.end_date||'Last date');
  $('diagnostics-counts').replaceChildren();$('diagnostics-holds').replaceChildren();$('diagnostics-reasons').hidden=!d;
  if(!d){$('diagnostics-summary').textContent='This saved run predates signal diagnostics. Run it again to review indicator readiness and held candles.';$('diagnostics-note').textContent='The saved result and accounting history are unchanged.';return;}
  let summary;
  if(!d.opening_bars)summary='No eligible opening candles were evaluated in this date range.';
  else if(!d.ready_opening_bars)summary='The opening candles did not have enough prior indicator history. Include more earlier sessions to warm up the intraday, weekly and monthly EMAs.';
  else if(!d.long_signals&&!d.short_signals)summary='Indicator history was ready for '+num(d.ready_opening_bars)+' opening candles. None matched all the entry rules.';
  else if(!d.long_signals)summary='The rules identified bearish setups only. These are recorded, but this simulator does not execute short sales.';
  else if(!report.results.total_trades)summary='The strategy produced buy signals, but no order filled. Review Unfilled signals in the Trades tab for the execution or risk reason.';
  else summary='The strategy produced '+num(d.long_signals)+' buy signals and '+num(d.short_signals)+' bearish signals. Review the Trades tab for fills and unfilled signals.';
  $('diagnostics-summary').textContent=summary;
  const counts=[['Opening candles',d.opening_bars],['History ready',d.ready_opening_bars],['Buy signals',d.long_signals],['Bearish signals',d.short_signals],['Prior warm-up bars',d.warmup_bars]];
  $('diagnostics-counts').replaceChildren(...counts.map(([label,value])=>{const row=node('div');row.append(node('dt',label),node('dd',num(value)));return row;}));
  const reasons=Object.entries(d.opening_hold_reasons||{}).sort((a,b)=>b[1]-a[1]);$('diagnostics-reasons').hidden=!reasons.length;
  $('diagnostics-holds').replaceChildren(...reasons.map(([label,value])=>{const row=node('div');row.append(node('dt',label),node('dd',num(value)));return row;}));
  const last=d.last_diagnostics;
  $('diagnostics-note').textContent='History ready means enough indicator history; it does not mean the entry rules matched. Signal counts are before risk and execution checks.'+(last?' Last regular-session bar: '+(d.last_regular_timestamp||'none')+' · completed weeks '+num(last.weekly_completed_bars)+' / months '+num(last.monthly_completed_bars)+'.':'');
}
function svgNode(tag,attrs={},text){const el=document.createElementNS('http://www.w3.org/2000/svg',tag);for(const [key,value] of Object.entries(attrs))el.setAttribute(key,String(value));if(text!=null)el.textContent=text;return el;}
// Preserve bucket extrema when reducing long curves for rendering; exports contain every bar.
function indices(values,buckets=450){
  if(values.length<=buckets*2)return values.map((_,i)=>i);
  const output=[0],size=values.length/buckets;
  for(let b=0;b<buckets;b++){const first=Math.floor(b*size),last=Math.min(values.length,Math.ceil((b+1)*size));let lo=first,hi=first;
    for(let i=first+1;i<last;i++){if(values[i]<values[lo])lo=i;if(values[i]>values[hi])hi=i;}
    output.push(...(lo<hi?[lo,hi]:[hi,lo]));}
  output.push(values.length-1);return [...new Set(output)].sort((a,b)=>a-b);
}
function chart(svg,series,width,height,format){
  svg.replaceChildren();const pad={l:62,r:18,t:16,b:32};let low=Infinity,high=-Infinity;
  for(const s of series)for(const value of s.values){low=Math.min(low,value);high=Math.max(high,value);}
  const spread=high-low||Math.max(Math.abs(high)*.01,1);low-=spread*.08;high+=spread*.08;
  pad.l=Math.max(62,Math.min(170,Math.max(format(low).length,format(high).length)*8+14));
  const x=(i,n)=>pad.l+(width-pad.l-pad.r)*i/Math.max(1,n-1);
  const y=value=>height-pad.b-(value-low)/(high-low)*(height-pad.t-pad.b);
  for(let i=0;i<5;i++){const value=low+(high-low)*i/4,py=y(value);svg.append(svgNode('line',{x1:pad.l,x2:width-pad.r,y1:py,y2:py,stroke:'#2b3035','stroke-width':1}),svgNode('text',{x:pad.l-10,y:py+4,'text-anchor':'end'},format(value)));}
  for(const s of series){const points=indices(s.values).map(i=>x(i,s.values.length).toFixed(2)+','+y(s.values[i]).toFixed(2)).join(' ');
    svg.append(svgNode('polyline',{points,fill:'none',stroke:s.color,'stroke-width':2,'stroke-linejoin':'round','stroke-dasharray':s.dashed?'5 5':'none'}));}
  const times=report.results.equity_timestamps;svg.append(svgNode('text',{x:pad.l,y:height-7},(times[1]||'Start').slice(0,10)),svgNode('text',{x:width-pad.r,y:height-7,'text-anchor':'end'},times.at(-1).slice(0,10)));
}
function drawCharts(){
  const r=report.results,series=[{values:r.equity_curve,color:'#69bda3'}];
  if(report.benchmark)series.push({values:report.benchmark.equity_curve,color:'#919aa4',dashed:true});
  chart($('equity-chart'),series,900,290,value=>num(value));
  let peak=r.equity_curve[0];const dd=r.equity_curve.map(value=>{peak=Math.max(peak,value);return peak?100*(value-peak)/peak:0;});
  chart($('drawdown-chart'),[{values:dd,color:'#d8828d'}],600,150,value=>value.toFixed(2)+'%');
  $('chart-position').max=r.equity_curve.length-1;$('chart-position').value=r.equity_curve.length-1;inspectChart();
}
function inspectChart(){if(!report)return;const i=Number($('chart-position').value),r=report.results;$('chart-value').textContent=r.equity_timestamps[i]+' · '+money(r.equity_curve[i]);}
function renderActivity(){
  if(!report)return;
  const rejected=$('activity-filter').value==='rejections',rows=rejected?report.results.rejections:report.results.trades;
  const headers=rejected?['Signal time','Outcome time','Action','Reason']:['Executed at','Action','Shares','Fill price','Fee','Realized P&L','Reason'];
  const head=node('tr');for(const text of headers)head.append(node('th',text));$('activity-head').replaceChildren(head);
  const body=$('activity-body');body.replaceChildren();
  page=Math.min(page,Math.max(0,Math.ceil(rows.length/20)-1));
  for(const row of rows.slice(page*20,(page+1)*20)){
    const tr=node('tr');
    if(rejected){tr.append(node('td',journalTime(row.signal_timestamp||row.timestamp),'time'),node('td',journalTime(row.timestamp),'time'),node('td',row.action),node('td',row.reason,'reason'));}
    else{
      const action=node('td'),tag=node('span',row.action,'side-tag'+(row.action==='SELL'?' sell':''));action.append(tag);
      tr.append(node('td',journalTime(row.timestamp),'time'),action,node('td',num(row.quantity),'numeric'),node('td',money(row.price),'numeric'),node('td',money(row.commission),'numeric'),node('td',row.action==='BUY'?'—':(row.pnl>0?'+':'')+money(row.pnl),'numeric '+(row.action==='BUY'?'':row.pnl>=0?'positive':'negative')),node('td',row.reason,'reason'));
    }body.append(tr);
  }
  if(!rows.length){const tr=node('tr'),td=node('td',rejected?'No unfilled signals in this simulation.':'No orders filled. The rules may need more warm-up or a different dataset.','empty-cell');td.colSpan=headers.length;tr.append(td);body.append(tr);}
  $('activity-summary').textContent=report.results.trades.length+' fills · '+report.results.rejections.length+' unfilled signals. Buys add to cost basis; exits realize P&L.';
  $('page-count').textContent=rows.length?`${page*20+1}–${Math.min((page+1)*20,rows.length)} of ${rows.length}`:'0 entries';
  $('previous-page').disabled=page===0;$('next-page').disabled=(page+1)*20>=rows.length;
}
async function refreshHistory(){
  const data=await api('/api/runs');const container=$('run-history');container.replaceChildren();
  $('history-error')?.remove();
  $('history-count').textContent=data.runs.length?String(data.runs.length):'';
  for(const run of data.runs){
    const button=node('button',null,'history-item');button.type='button';button.setAttribute('aria-current',String(run.id===selectedRun?.id));
    const description=node('span');description.append(node('span',names[run.strategy]||run.strategy,'history-name'),node('span',(run.dataset_info?.name||datasetInfo(run.dataset).name)+' · '+new Date(typeof run.created_at==='number'?run.created_at*1000:run.created_at).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}),'history-description'));button.title='Run '+run.id;
    const status=node('span',run.status,'badge');status.dataset.status=run.status;
    button.append(description,status);button.addEventListener('click',()=>selectRun(run,{focus:true}).catch(e=>error(e.message)));container.append(button);
  }
  if(!data.runs.length)container.append(node('p','No saved simulations yet.','muted'));
  return data.runs;
}
async function boot(){
  parameterInputs();
  providerConnection.refresh();
  const initialFormRevision=formRevision;
  showWorkspace(workspaceFromHash(),false);
  try{
    await datasetLibrary.refresh();
    const runs=await refreshHistory();
    let savedId;try{savedId=localStorage.getItem('opening-bell-selected-run');}catch{}
    if(runs.length)await selectRun(runs.find(run=>run.id===savedId)||runs[0],{reveal:false,setupRevision:initialFormRevision});
    else{const data=await api('/api/runs',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({request_id:'opening-bell-sample-v2',dataset:'opening_demo',strategy:'VWAP_OPENING',config:{}})});await selectRun(data.run,{reveal:false,setupRevision:initialFormRevision});}
  }catch(e){$('loading').hidden=true;$('run-status').textContent='Unavailable';error(e.message+' Start the local service, then refresh this page.');}
}
function resetQuote(){
  for(const id of ['market-price','market-change','quote-open','quote-high','quote-low','quote-close','quote-volume','value-vwap','value-ema_fast','value-ema_medium','value-ema_slow'])$(id).textContent='—';
  $('market-time').textContent='Exchange time';
}
function loadSavedSetup(){
  clearValidation();error('','form-error');
  const config=report.effective_config;
  $('strategy').value=report.strategy;$('dataset').value=selectedRun.dataset;parameterInputs();
  const parameters=config.strategies[report.strategy];
  for(const [key] of parameterSets[report.strategy])if(parameters[key]!=null)$('param-'+key).value=parameters[key];
  if(report.strategy==='VWAP_OPENING')$('window').value=parameters.opening_window_minutes;
  syncWindow();renderRuleSteps();$('setup-state').textContent='Saved settings';$('setup-state').classList.remove('edited');
  const amounts={capital:config.backtesting.initial_capital,commission:config.backtesting.commission_rate*100,slippage:config.backtesting.slippage*10000,allocation:config.risk_management.max_position_size*100,stop:config.risk_management.stop_loss_pct*100,profit:config.risk_management.take_profit_pct*100};
  for(const [id,value] of Object.entries(amounts))$(id).value=Number(value.toFixed(8));
  $('start-date').value=config.backtesting.start_date||'';$('end-date').value=config.backtesting.end_date||'';
  $('dataset').dispatchEvent(new Event('change'));syncMenus();
}
function inspectPrice({bar,change,changePercent,source}){
  if(!bar)return;
  const price=value=>Number.isFinite(value)?value.toFixed(2):'—';
  $('market-price').textContent=price(bar.close);
  $('market-change').textContent=Number.isFinite(change)?`${change>=0?'+':''}${price(change)} (${change>=0?'+':''}${Number.isFinite(changePercent)?changePercent.toFixed(2):'0.00'}%)`:'—';
  $('market-change').className=change>=0?'positive':'negative';
  $('market-change').title='Change from the previous candle close';
  $('market-time').textContent=bar.timestamp.replace('T',' · ').slice(0,18)+' · '+(chartData?.timezone||selectedRun?.dataset_info?.timezone||'exchange time');
  for(const key of ['open','high','low','close'])$('quote-'+key).textContent=price(bar[key]);
  $('quote-volume').textContent=num(bar.volume);
  for(const key of ['vwap','ema_fast','ema_medium','ema_slow'])$('value-'+key).textContent=price(bar[key]);
  if(source==='keyboard')inspectionStatus.textContent=`${bar.timestamp.replace('T',' ')}. Open ${price(bar.open)}, high ${price(bar.high)}, low ${price(bar.low)}, close ${price(bar.close)}, volume ${num(bar.volume)}.`;
}
const inspectionStatus=node('output',null,'sr-only');inspectionStatus.setAttribute('aria-live','polite');inspectionStatus.setAttribute('aria-atomic','true');$('market-chart').after(inspectionStatus);
const marketChart=new MarketChart($('market-chart'),inspectPrice);
function syncRangeControls(state){
  $('opening-view').setAttribute('aria-pressed',String(state.opening));$('reset-view').setAttribute('aria-pressed',String(state.full));
  for(const [id,key] of [['pan-left','canPanLeft'],['pan-right','canPanRight'],['zoom-in','canZoomIn'],['zoom-out','canZoomOut']])$(id).disabled=!chartControlsEnabled||!state[key];
}
$('market-chart').addEventListener('chartviewchange',event=>syncRangeControls(event.detail));
function setChartControls(enabled){
  chartControlsEnabled=enabled;$('market-chart').inert=!enabled;
  if(!enabled&&document.activeElement===$('market-chart'))$('market-chart').blur();
  for(const id of ['view-candles','view-line','opening-view','reset-view','zoom-in','zoom-out','pan-left','pan-right'])$(id).disabled=!enabled;
  $('session-select').disabled=!enabled||!chartData?.sessions?.length;
  for(const input of document.querySelectorAll('[data-indicator]'))input.disabled=!enabled||(input.dataset.indicator==='vwap'&&chartData?.interval_minutes==null);
  syncRangeControls(marketChart.viewState());syncMenus();
}
async function loadMarketChart(session){
  if(!selectedRun||!report)return;
  const run=selectedRun,version=++chartVersion;setChartControls(false);error('','market-error');$('retry-chart').hidden=true;
  $('market-chart').setAttribute('aria-busy','true');
  $('loading').hidden=false;$('loading').textContent='Loading saved candles and indicators…';
  try{
    const payload=await api('/api/runs/'+run.id+'/chart'+(session?'?session='+encodeURIComponent(session):''));
    if(version!==chartVersion||selectedRun.id!==run.id)return;
    chartData=payload;
    const chartNotes=[];
    if(payload.truncated)chartNotes.push('Showing the last '+num(payload.bars.length)+' of '+num(payload.total_bars)+' candles. Exports retain the complete run.');
    if(payload.outside_session_bar_count)chartNotes.push('Includes extended hours. VWAP covers the regular session only.');
    $('chart-data-note').textContent=chartNotes.join(' ');$('chart-data-note').hidden=!chartNotes.length;
    const selector=$('session-select');selector.replaceChildren();
    for(const date of payload.sessions){const option=node('option',journalTime(date));option.value=date;selector.append(option);}
    if(payload.session)selector.value=payload.session;else selector.append(node('option','Full history'));
    $('interval-label').textContent=payload.interval_minutes?payload.interval_minutes+'m':'1D';
    renderInstrument(payload.dataset_info||run.dataset_info||datasetInfo(run.dataset));
    $('reset-view').textContent=payload.interval_minutes?'Full session':'Full history';
    const showOpening=payload.interval_minutes!=null&&report.strategy==='VWAP_OPENING';
    $('opening-view').hidden=!showOpening;
    $('window-label').hidden=!showOpening;
    $('window-label').textContent=payload.opening_window_minutes+' min entry window';
    for(const [key,value] of Object.entries(payload.periods))$('label-ema_'+key).textContent='EMA '+value;
    marketChart.setData({...payload,opening_window_minutes:showOpening?payload.opening_window_minutes:0});
    if(showOpening)marketChart.fitToOpeningWindow();
    setChartControls(true);
  }catch(e){if(version===chartVersion){error('Price chart unavailable: '+e.message,'market-error');failedChartSession=session;$('retry-chart').hidden=false;$('session-select').replaceChildren(node('option','Unavailable'));marketChart.setData(null);resetQuote();}}
  finally{if(version===chartVersion){$('market-chart').setAttribute('aria-busy','false');$('loading').hidden=true;}}
}
function selectPane(name,focus=false){
  for(const button of document.querySelectorAll('[data-pane]')){
    const active=button.dataset.pane===name;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1;$('pane-'+button.dataset.pane).hidden=!active;
    if(active&&focus)button.focus();
  }
}
for(const button of document.querySelectorAll('[data-pane]')){
  button.addEventListener('click',()=>selectPane(button.dataset.pane));
  button.addEventListener('keydown',event=>{
    const tabs=[...document.querySelectorAll('[data-pane]')],i=tabs.indexOf(button);let target;
    if(event.key==='ArrowRight')target=(i+1)%tabs.length;
    if(event.key==='ArrowLeft')target=(i+tabs.length-1)%tabs.length;
    if(event.key==='Home')target=0;if(event.key==='End')target=tabs.length-1;
    if(target!==undefined){event.preventDefault();selectPane(tabs[target].dataset.pane,true);}
  });
}
for(const view of ['candles','line'])$('view-'+view).addEventListener('click',()=>{marketChart.setView(view);for(const name of ['candles','line'])$('view-'+name).setAttribute('aria-pressed',String(name===view));});
for(const input of document.querySelectorAll('[data-indicator]'))input.addEventListener('change',()=>marketChart.setIndicator(input.dataset.indicator,input.checked));
$('session-select').addEventListener('change',()=>loadMarketChart($('session-select').value));
$('retry-chart').addEventListener('click',()=>loadMarketChart(failedChartSession));
$('opening-view').addEventListener('click',()=>marketChart.fitToOpeningWindow());
$('reset-view').addEventListener('click',()=>marketChart.resetView());
$('zoom-in').addEventListener('click',()=>marketChart.zoomIn());
$('zoom-out').addEventListener('click',()=>marketChart.zoomOut());
$('pan-left').addEventListener('click',()=>marketChart.panLeft());$('pan-right').addEventListener('click',()=>marketChart.panRight());
for(const button of document.querySelectorAll('[data-window]')){
  button.addEventListener('click',()=>{if($('window').value===button.dataset.window)return;$('window').value=button.dataset.window;syncWindow();markEdited();});
  button.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key)){event.preventDefault();const value=event.key==='Home'?'15':event.key==='End'?'30':$('window').value==='15'?'30':'15',changed=value!==$('window').value;$('window').value=value;syncWindow();document.querySelector('[data-window="'+value+'"]').focus();if(changed)markEdited();}});
}
for(const link of document.querySelectorAll('[data-workspace]'))link.addEventListener('click',()=>showWorkspace(link.dataset.workspace,false));
document.querySelector('.wordmark').addEventListener('click',()=>showWorkspace('workbench',false));
window.addEventListener('hashchange',()=>showWorkspace(workspaceFromHash(),false));
setChartControls(false);
$('run-form').addEventListener('submit',submit);
$('run-form').addEventListener('input',markEdited);
$('strategy').addEventListener('change',parameterInputs);
$('dataset').addEventListener('change',updateDatasetNote);
$('activity-filter').addEventListener('change',()=>{page=0;renderActivity();});
$('previous-page').addEventListener('click',()=>{page--;renderActivity();});
$('next-page').addEventListener('click',()=>{page++;renderActivity();});
$('chart-position').addEventListener('input',inspectChart);
$('refresh-runs').addEventListener('click',()=>refreshHistory().catch(e=>{let message=$('history-error');if(!message){message=node('p',null,'error');message.id='history-error';message.setAttribute('role','alert');$('run-history').before(message);}message.textContent=e.message+' Try refreshing again.';}));
$('download-button').addEventListener('click',()=>{if(!report)return;const url=URL.createObjectURL(new Blob([JSON.stringify(report,null,2)],{type:'application/json'})),a=node('a');a.href=url;a.download='opening-bell-'+selectedRun.id+'.json';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);});
$('cancel-button').addEventListener('click',async()=>{try{const data=await api('/api/runs/'+selectedRun.id+'/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});await selectRun(data.run);}catch(e){error(e.message);}});
$('reconcile-button').addEventListener('click',async()=>{const id=selectedRun.id;$('reconcile-button').disabled=true;$('reconcile-result').textContent='Checking the saved accounting history…';try{const data=await api('/api/runs/'+id+'/reconcile');if(selectedRun.id===id)$('reconcile-result').textContent=data.ok?'Verified: saved balances match the accounting history.':JSON.stringify(data);}catch(e){if(selectedRun.id===id)$('reconcile-result').textContent=e.message;}finally{$('reconcile-button').disabled=false;}});
boot();
