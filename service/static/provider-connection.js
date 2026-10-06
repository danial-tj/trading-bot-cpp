import {DarkSelect} from './dark-select.js';
import {dateRangeError} from './date-fields.js';

const $=id=>document.getElementById(id);
const active=job=>['queued','running'].includes(job?.status);
const el=(tag,text,className)=>{const node=document.createElement(tag);if(text!=null)node.textContent=text;if(className)node.className=className;return node;};
const base='/api/providers/questrade';

export class ProviderConnection {
  constructor({api,onDataset}){
    this.api=api;this.onDataset=onDataset;this.connected=false;this.job=null;this.symbol=null;this.busy=false;this.searching=false;this.version=0;this.searchVersion=0;this.pending=null;this.saved=new Set();
    this.menu=new DarkSelect($('provider-interval'),{label:'Historical candle interval'});
    $('provider-connect-form').addEventListener('submit',event=>this.connect(event));
    $('provider-disconnect').addEventListener('click',()=>this.disconnect());
    $('provider-search-form').addEventListener('submit',event=>this.search(event));
    $('provider-symbol').addEventListener('input',()=>{this.searchVersion++;this.symbol=null;$('provider-symbols').replaceChildren();$('provider-selection').textContent='Search, then choose the exact symbol.';this.renderControls();});
    $('provider-import-form').addEventListener('submit',event=>this.download(event));
    $('provider-cancel').addEventListener('click',()=>this.cancel());
    $('provider-retry-status').addEventListener('click',()=>this.refresh());
    for(const form of ['provider-connect-form','provider-search-form','provider-import-form'])$(form).addEventListener('input',()=>{this.error('');this.clearValidation();});
  }
  error(message,statusError=false){this.statusError=statusError;$('provider-error').textContent=message;$('provider-error').hidden=!message;}
  clearValidation(){for(const input of document.querySelectorAll('.provider-section [aria-invalid=true]')){input.removeAttribute('aria-invalid');if(input.id==='provider-token')input.setAttribute('aria-describedby','token-hint');else input.removeAttribute('aria-describedby');}}
  invalid(id,message){this.error(message);$(id).setAttribute('aria-invalid','true');$(id).setAttribute('aria-describedby',(id==='provider-token'?'token-hint ':'')+'provider-error');$(id).focus();}
  post(path,payload){return this.api(base+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});}
  renderControls(){
    const downloading=active(this.job),locked=this.busy||downloading;
    $('provider-connect-form').hidden=this.connected;$('provider-connected').hidden=!this.connected;
    $('provider-state').textContent=this.connected?'Connected · historical data':'Disconnected';
    $('provider-state').classList.toggle('positive',this.connected);
    for(const id of ['provider-token','provider-connect'])$(id).disabled=locked;
    $('provider-disconnect').disabled=this.busy;
    for(const id of ['provider-symbol','provider-start','provider-end','provider-interval'])$(id).disabled=!this.connected||locked;
    $('provider-search').disabled=!this.connected||locked||this.searching;
    for(const button of $('provider-symbols').querySelectorAll('button'))button.disabled=!this.connected||locked;
    $('provider-download').disabled=!this.connected||locked||!this.symbol;
    $('provider-download').textContent=downloading?'Downloading…':'Download & save';
    $('provider-cancel').hidden=!downloading;$('provider-cancel').disabled=this.busy;
    $('provider-import-form').setAttribute('aria-busy',String(downloading));this.menu.sync();
  }
  async applyStatus(data){
    this.connected=Boolean(data.connection?.connected||data.connection?.can_refresh);this.job=data.job??null;
    if(this.pending&&this.job?.request_id===this.pending.request_id)this.pending=null;
    if(!this.connected){this.symbol=null;this.searchVersion++;$('provider-symbols').replaceChildren();$('provider-selection').textContent='Connect to choose a symbol.';}
    this.renderControls();
    const job=this.job;let message='';
    if(active(job)){
      const progress=data.connection?.history_progress||data.connection?.history;
      message=job.status==='queued'?'Download queued…':'Downloading historical candles…';
      if(progress?.total_chunks)message+=' '+progress.completed_chunks+' / '+progress.total_chunks+' requests complete.';
    }else if(job?.status==='completed')message='Saved '+(job.dataset?.name||'historical prices')+'. Review the dataset notes below before running a backtest.';
    else if(job?.status==='cancelled')message='Download cancelled. No dataset was saved.';
    else if(job?.status==='failed')message='Download failed: '+(job.error||'Refresh the connection and try again.');
    $('provider-job-status').textContent=message;
    if(job?.status==='completed'&&!this.saved.has(job.id)){
      this.saved.add(job.id);
      try{await this.onDataset(job.dataset);}catch{this.saved.delete(job.id);this.error('Prices were saved. Refresh the dataset library to load them.');}
    }
  }
  schedule(){clearTimeout(this.timer);if(this.connected||active(this.job))this.timer=setTimeout(()=>this.refresh(),active(this.job)?1200:30000);}
  async refresh(){
    if(this.busy){this.schedule();return;}
    const version=++this.version;clearTimeout(this.timer);
    try{const data=await this.api(base+'/status');if(version!==this.version)return;if(this.statusError)this.error('');await this.applyStatus(data);$('provider-retry-status').hidden=true;this.schedule();}
    catch{if(version!==this.version)return;$('provider-state').textContent='Connection status unavailable';$('provider-retry-status').hidden=false;this.error('Could not reach the local data service. Refresh the connection to check the download status.',true);}
  }
  async connect(event){
    event.preventDefault();if(this.busy||active(this.job))return;
    if(!$('provider-token').value.trim()){this.invalid('provider-token','Enter a new manual authorization token from Questrade.');return;}
    this.busy=true;++this.version;clearTimeout(this.timer);this.error('');this.renderControls();$('provider-connect').textContent='Connecting…';
    try{const data=await this.post('/connect',{refresh_token:$('provider-token').value.trim()});this.connected=Boolean(data.connection?.connected||data.connection?.can_refresh);$('provider-selection').textContent='Search, then choose the exact symbol.';}
    catch(e){this.error(e.message);}
    finally{$('provider-token').value='';this.busy=false;$('provider-connect').textContent='Connect';this.renderControls();await this.refresh();}
  }
  async disconnect(){
    if(this.busy)return;this.busy=true;++this.version;++this.searchVersion;clearTimeout(this.timer);this.error('');this.renderControls();
    try{await this.post('/disconnect',{});this.connected=false;this.symbol=null;this.pending=null;}
    catch(e){this.error(e.message);}
    finally{this.busy=false;await this.refresh();this.renderControls();}
  }
  async search(event){
    event.preventDefault();if(this.busy||this.searching||active(this.job)||!this.connected)return;
    const prefix=$('provider-symbol').value.trim();if(!prefix){this.invalid('provider-symbol','Enter a symbol to search for.');return;}
    const version=++this.searchVersion;this.searching=true;this.symbol=null;this.error('');$('provider-symbols').replaceChildren();this.renderControls();$('provider-search').textContent='Searching…';
    try{
      const data=await this.api(base+'/symbols?prefix='+encodeURIComponent(prefix));if(version!==this.searchVersion)return;
      for(const symbol of data.symbols){
        const button=el('button',null,'symbol-result');button.type='button';button.append(el('strong',symbol.symbol),el('span',symbol.description||'', 'symbol-description'),el('span',symbol.currency||'','symbol-currency'));
        button.addEventListener('click',()=>{this.symbol={...symbol,id:symbol.id??symbol.symbol_id??symbol.symbolId};$('provider-selection').textContent=symbol.symbol+' · '+(symbol.description||'Selected symbol');$('provider-symbols').replaceChildren();this.error('');this.renderControls();$('provider-start').focus();});
        $('provider-symbols').append(button);
      }
      $('provider-selection').textContent=data.symbols.length?'Choose a symbol from the results.':'No matching symbols. Try a shorter ticker.';
    }catch(e){if(version===this.searchVersion)this.error(e.message);}
    finally{this.searching=false;$('provider-search').textContent='Search';this.renderControls();}
  }
  async download(event){
    event.preventDefault();if(this.busy||active(this.job)||!this.connected||!this.symbol)return;
    const start=$('provider-start').value.trim(),end=$('provider-end').value.trim();
    const issue=dateRangeError(start,end,{required:true,maxDays:366});if(issue){this.invalid('provider-'+issue.field,issue.message);return;}
    const payload={symbol_id:this.symbol.id,start_date:start,end_date:end,bar_minutes:Number($('provider-interval').value)};
    const signature=JSON.stringify(payload);if(this.pending?.signature!==signature)this.pending={signature,request_id:crypto.randomUUID()};
    this.busy=true;++this.version;clearTimeout(this.timer);this.error('');this.renderControls();$('provider-job-status').textContent='Starting download…';
    try{const data=await this.post('/import',{...payload,request_id:this.pending.request_id});this.pending=null;this.job=data.job;}
    catch(e){this.error(e.message+' You can retry this request.');}
    finally{this.busy=false;this.renderControls();await this.refresh();}
  }
  async cancel(){
    if(this.busy||!active(this.job))return;this.busy=true;++this.version;clearTimeout(this.timer);this.error('');this.renderControls();
    try{const data=await this.post('/cancel',{job_id:this.job.id});this.job=data.job;}
    catch(e){this.error(e.message);}
    finally{this.busy=false;await this.refresh();this.renderControls();}
  }
}
