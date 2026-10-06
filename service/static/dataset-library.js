import {DarkSelect} from './dark-select.js';

const $=id=>document.getElementById(id);
const element=(tag,text,className)=>{const el=document.createElement(tag);if(text!=null)el.textContent=text;if(className)el.className=className;return el;};
export const intervalLabel=data=>data?.bar_minutes?data.bar_minutes+'m':'1D';
export const originLabel=data=>String(data?.provider||'').toLowerCase()==='questrade'?'Questrade history':data?.import_format==='tradingview'?'TradingView CSV':data?.origin==='imported'?'Imported CSV':'Synthetic demo';

export class DatasetLibrary {
  constructor({api,onCatalog,onSelect}){
    this.api=api;this.onCatalog=onCatalog;this.onSelect=onSelect;this.busy=false;this.version=0;this.highlight=null;
    this.menus=[new DarkSelect($('import-format'),{label:'CSV format'}),new DarkSelect($('import-interval'),{label:'Candle interval'}),new DarkSelect($('import-adjustment'),{label:'Price adjustment'})];
    $('import-form').addEventListener('submit',event=>this.submit(event));
    $('import-form').addEventListener('input',()=>{this.error('');this.clearValidation();$('import-status').textContent='';});
    $('import-interval').addEventListener('change',()=>{const daily=$('import-interval').value==='0';$('import-session').hidden=daily;for(const id of ['import-open','import-close'])$(id).disabled=daily;});
    $('import-format').addEventListener('change',()=>{this.updateFormatHelp();this.error('');this.clearValidation();$('import-status').textContent='';});
    $('refresh-datasets').addEventListener('click',()=>this.refresh().catch(e=>this.error(e.message,'library-error')));
    this.updateFormatHelp();
  }
  updateFormatHelp(){
    const tradingview=$('import-format').value==='tradingview';
    const source=$('import-source'),suggested=tradingview?'TradingView export (user-supplied)':'';
    if(!source.value.trim()||source.value===this.suggestedSource)source.value=suggested;
    this.suggestedSource=suggested;
    $('timezone-hint').textContent=tradingview?'Unix seconds and timestamps with a UTC offset are converted to this exchange time zone. Timestamps without an offset must already be local.':'Use the zone of your timestamps, such as America/New_York. Times must already be local; they are not converted.';
    const guide=$('csv-format-guide');guide.replaceChildren();
    if(tradingview){
      guide.append(element('p','Use TradingView’s Download chart data CSV for one symbol and the selected candle interval.'),element('pre','time,open,high,low,close,Volume'),element('p','These columns may be in any order. Extra indicator columns are listed as ignored and are not used by the strategy.'),element('p','Unix seconds and ISO timestamps with a UTC offset are converted to the declared exchange time zone. Date-only daily candles keep their calendar date.'));
    }else{
      guide.append(element('p','Use these six columns, with the oldest candle first.'),element('pre','timestamp,open,high,low,close,volume'),element('p','Intraday timestamps mark the start of each candle in exchange-local time:'),element('code','2026-01-05 09:30:00'),element('p','Daily candles use:'),element('code','2026-01-05'));
    }
    guide.append(element('p','Prices and volume must be numeric. Duplicate dates, invalid prices and candles that do not match the chosen interval are rejected.'));
  }
  error(message,id='import-error'){ $(id).textContent=message;$(id).hidden=!message; }
  clearValidation(){for(const el of $('import-form').querySelectorAll('[aria-invalid=true]')){el.removeAttribute('aria-invalid');el.removeAttribute('aria-describedby');}}
  async refresh(){
    const version=++this.version;const data=await this.api('/api/catalog');if(version!==this.version)return;
    this.error('','library-error');this.onCatalog(data.datasets);this.render(data.datasets);return data.datasets;
  }
  render(datasets){
    const library=$('dataset-library');library.replaceChildren();
    $('dataset-count').textContent=datasets.filter(d=>d.origin==='imported').length+' / 50 imports';
    for(const data of [...datasets].reverse()){
      const row=element('article',null,'dataset-row');if(data.id===this.highlight)row.classList.add('just-imported');
      const main=element('div',null,'dataset-info'),title=element('h3',data.name),meta=element('p',[data.symbol,intervalLabel(data),originLabel(data),data.currency].filter(Boolean).join(' · '),'dataset-meta');
      main.append(title,meta);
      if(data.first_timestamp)main.append(element('p',data.first_timestamp.slice(0,10)+' → '+data.last_timestamp.slice(0,10)+' · '+Number(data.row_count).toLocaleString()+' candles','dataset-meta'));
      if(data.source)main.append(element('p','Source: '+data.source,'dataset-meta'));
      if(data.warnings?.length){const details=element('details',null,'dataset-notes'),list=element('ul');details.append(element('summary',data.warnings.length+' data notes'));for(const text of data.warnings)list.append(element('li',text));details.append(list);details.open=data.id===this.highlight;main.append(details);}
      const use=element('button','Use dataset','secondary');use.type='button';use.setAttribute('aria-label','Use dataset '+data.name);use.addEventListener('click',()=>this.onSelect(data));
      row.append(main,use);library.append(row);
    }
  }
  async submit(event){
    event.preventDefault();if(this.busy)return;this.error('');this.clearValidation();$('import-status').textContent='';
    const invalid=$('import-form').querySelector(':invalid');
    if(invalid){this.error(invalid.validationMessage);invalid.setAttribute('aria-invalid','true');invalid.setAttribute('aria-describedby','import-error');invalid.focus();return;}
    const file=$('import-file').files[0];if(!file){this.error('Choose a CSV file first.');$('import-file').focus();return;}
    if(file.size>8*1024*1024){this.error('This file exceeds 8 MiB. Export a smaller date range and try again.');$('import-file').focus();return;}
    const minute=value=>{const [hour,min]=value.split(':').map(Number);return hour*60+min;};
    const payload={format:$('import-format').value,name:$('import-name').value.trim(),symbol:$('import-symbol').value.trim(),source:$('import-source').value.trim(),timezone:$('import-timezone').value.trim(),currency:$('import-currency').value.trim(),price_adjustment:$('import-adjustment').value,bar_minutes:Number($('import-interval').value),session_open_minute:minute($('import-open').value),session_close_minute:minute($('import-close').value)};
    if(!payload.bar_minutes){payload.session_open_minute=570;payload.session_close_minute=960;}
    this.busy=true;$('import-submit').disabled=true;$('import-submit').textContent='Validating…';
    // Freeze the submitted metadata while the file is being read and stored.
    const fields=[...$('import-form').querySelectorAll('input,select')],previous=fields.map(el=>el.disabled);
    fields.forEach(el=>el.disabled=true);this.menus.forEach(menu=>menu.sync());
    try{
      payload.csv=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(await file.arrayBuffer());
      const data=await this.api('/api/datasets/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
      this.highlight=data.dataset.id;
      $('import-status').textContent=data.created?'Saved locally. Review the data notes below, then choose Use dataset.':'Already saved. Choose Use dataset below.';
      // A refresh failure must not turn a successful import into an ambiguous failure.
      try{await this.refresh();}catch(e){this.error('Dataset saved. Refresh the library to load it: '+e.message,'library-error');}
      $('library-heading').scrollIntoView({block:'nearest'});
    }catch(e){this.error(e instanceof TypeError&&e.message.includes('encoded')?'The file must use UTF-8 text encoding.':e.message);}
    finally{this.busy=false;fields.forEach((el,i)=>el.disabled=previous[i]);this.menus.forEach(menu=>menu.sync());$('import-submit').disabled=false;$('import-submit').textContent='Validate & import';}
  }
}
