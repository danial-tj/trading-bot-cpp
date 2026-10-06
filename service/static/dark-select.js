// App-owned menus avoid platform select popups with incompatible light/dark colours.
let openControl = null;
const make = (tag, className, text) => { const el=document.createElement(tag); if(className)el.className=className;if(text!=null)el.textContent=text;return el; };
const chevron = () => { const el=document.createElementNS('http://www.w3.org/2000/svg','svg');el.setAttribute('viewBox','0 0 16 16');el.setAttribute('aria-hidden','true');const path=document.createElementNS(el.namespaceURI,'path');path.setAttribute('d','m4 6 4 4 4-4');el.append(path);return el; };
export class DarkSelect {
  constructor(select, {label, searchable=false}={}) {
    this.select=select;this.label=label;this.searchable=searchable;this.opened=false;this.active=-1;this.typeBuffer='';
    this.wrapper=make('div','dark-select');this.wrapper.id=select.id+'-control';
    this.button=make('button','select-trigger');this.button.type='button';this.button.id=select.id+'-trigger';
    this.value=make('span','select-value');this.button.append(this.value,chevron());
    this.button.setAttribute('aria-expanded','false');this.button.setAttribute('aria-haspopup',searchable?'dialog':'listbox');
    if(!searchable)this.button.setAttribute('role','combobox');
    for(const l of [...select.labels])l.htmlFor=this.button.id;
    select.before(this.wrapper);this.wrapper.append(select,this.button);select.hidden=true;
    this.panel=make('div','select-popover');this.panel.id=select.id+'-popup';this.panel.hidden=true;
    if(searchable){this.panel.setAttribute('role','dialog');this.panel.setAttribute('aria-label',label);}
    this.heading=make('div','select-menu-heading',label);this.panel.append(this.heading);
    this.list=make('div','select-options');this.list.id=select.id+'-options';this.list.setAttribute('role','listbox');this.list.setAttribute('aria-label',label);
    this.button.setAttribute('aria-controls',searchable?this.panel.id:this.list.id);
    if(searchable){
      this.search=make('input','select-search');this.search.type='text';this.search.placeholder='Search date, e.g. May 1 or 2024-05';this.search.autocomplete='off';this.search.spellcheck=false;
      this.search.setAttribute('role','combobox');this.search.setAttribute('aria-label','Search chart sessions');this.search.setAttribute('aria-autocomplete','list');this.search.setAttribute('aria-controls',this.list.id);this.search.setAttribute('aria-expanded','false');
      const wrap=make('div','select-search-wrap');wrap.append(this.search);this.panel.append(wrap);
      this.search.addEventListener('input',()=>{this.renderOptions();this.place();});
      this.search.addEventListener('keydown',e=>this.keydown(e));
    }
    this.panel.append(this.list);this.empty=make('p','select-empty','No matching sessions. Try a different date.');this.empty.setAttribute('role','status');this.panel.append(this.empty);
    this.panel.append(make('div','select-menu-hint','↑ ↓ Navigate   ·   Enter Select   ·   Esc Close'));document.body.append(this.panel);
    this.button.addEventListener('click',()=>this.opened?this.close(true):this.open());
    this.button.addEventListener('keydown',e=>this.keydown(e));
    this.list.addEventListener('pointerdown',e=>e.preventDefault());
    select.addEventListener('change',()=>this.sync());
    this.observer=new MutationObserver(()=>this.sync());this.observer.observe(select,{childList:true,subtree:true,attributes:true,attributeFilter:['disabled','selected','label']});
    this.sync();
  }
  sync(){
    const option=this.select.selectedOptions[0];this.value.textContent=option?.textContent||'Unavailable';this.button.disabled=this.select.disabled;
    this.button.setAttribute('aria-label',this.label+': '+this.value.textContent);
    if(this.opened){if(this.button.disabled||!this.button.getClientRects().length)this.close(false);else{this.renderOptions();this.place();}}
  }
  renderOptions(){
    const query=(this.search?.value||'').trim().toLowerCase();
    this.items=[...this.select.options].filter(o=>!o.disabled&&(!query||(o.textContent+' '+o.value).toLowerCase().includes(query)));
    this.list.replaceChildren();this.empty.hidden=this.items.length>0;this.list.hidden=!this.items.length;
    for(const [index,option] of this.items.entries()){
      const row=make('div','select-option');row.id=this.select.id+'-option-'+index;row.setAttribute('role','option');row.setAttribute('aria-selected',String(option.value===this.select.value));
      row.append(make('span','',option.textContent));const mark=make('span','select-check',option.value===this.select.value?'✓':'');mark.setAttribute('aria-hidden','true');row.append(mark);
      row.addEventListener('click',()=>this.choose(index));row.addEventListener('pointermove',()=>this.activate(index,false));this.list.append(row);
    }
    const selected=this.items.findIndex(o=>o.value===this.select.value);this.activate(selected>=0?selected:this.items.length?0:-1);
  }
  activate(index,scroll=true){
    this.active=index;const control=this.search||this.button;
    for(const [i,row] of [...this.list.children].entries())row.classList.toggle('is-active',i===index);
    const row=this.list.children[index];
    if(row){control.setAttribute('aria-activedescendant',row.id);if(scroll){const top=row.offsetTop,bottom=top+row.offsetHeight;if(top<this.list.scrollTop)this.list.scrollTop=top;else if(bottom>this.list.scrollTop+this.list.clientHeight)this.list.scrollTop=bottom-this.list.clientHeight;}}else control.removeAttribute('aria-activedescendant');
  }
  open(){
    if(this.button.disabled||this.opened)return;if(openControl)openControl.close(false);openControl=this;this.opened=true;
    if(this.search)this.search.value='';this.panel.hidden=false;this.button.setAttribute('aria-expanded','true');this.search?.setAttribute('aria-expanded','true');
    this.renderOptions();this.place();this.activate(this.active);(this.search||this.button).focus({preventScroll:true});
  }
  close(returnFocus=false){
    if(!this.opened)return;this.opened=false;this.panel.hidden=true;this.button.setAttribute('aria-expanded','false');this.search?.setAttribute('aria-expanded','false');
    (this.search||this.button).removeAttribute('aria-activedescendant');if(openControl===this)openControl=null;
    if(returnFocus&&!this.button.disabled)this.button.focus({preventScroll:true});
  }
  choose(index){
    const option=this.items[index];if(!option)return;const changed=this.select.value!==option.value;
    this.select.value=option.value;this.close(true);this.sync();
    if(changed){this.select.dispatchEvent(new Event('input',{bubbles:true}));this.select.dispatchEvent(new Event('change',{bubbles:true}));}
  }
  keydown(event){
    const key=event.key;
    if(key==='Escape'&&this.opened){event.preventDefault();event.stopPropagation();this.close(true);return;}
    if(key==='Tab'&&this.opened){
      // The searchable popup is portalled; continue tab order from its original trigger.
      if(this.search){this.button.focus({preventScroll:true});}this.close(false);return;
    }
    if(!this.opened){
      if(['ArrowDown','ArrowUp','Enter',' '].includes(key)){event.preventDefault();this.open();return;}
      if(key.length===1&&!event.ctrlKey&&!event.metaKey&&!event.altKey){event.preventDefault();this.open();if(this.search){this.search.value=key;this.renderOptions();}else this.typeahead(key);}return;
    }
    if(key==='Enter'||(key===' '&&!this.search)){event.preventDefault();this.choose(this.active);return;}
    if(key==='ArrowDown'||key==='ArrowUp'){event.preventDefault();this.activate(Math.max(0,Math.min(this.items.length-1,this.active+(key==='ArrowDown'?1:-1))));return;}
    if(!this.search&&(key==='Home'||key==='End')){event.preventDefault();this.activate(key==='Home'?0:this.items.length-1);return;}
    if(!this.search&&key.length===1&&!event.ctrlKey&&!event.metaKey&&!event.altKey){event.preventDefault();this.typeahead(key);}
  }
  typeahead(key){
    clearTimeout(this.typeTimer);this.typeBuffer+=key.toLowerCase();this.typeTimer=setTimeout(()=>this.typeBuffer='',650);
    const index=this.items.findIndex(o=>o.textContent.toLowerCase().startsWith(this.typeBuffer));if(index>=0)this.activate(index);
  }
  place(){
    if(!this.opened)return;const rect=this.button.getBoundingClientRect(),view=window.visualViewport;
    const width=view?.width||window.innerWidth,height=view?.height||window.innerHeight,top=view?.offsetTop||0,left=view?.offsetLeft||0;
    if(rect.bottom<=top||rect.top>=top+height||rect.right<=left||rect.left>=left+width){this.close(false);return;}
    const menuWidth=Math.min(Math.max(rect.width,this.searchable?300:240),width-16);
    this.panel.style.width=menuWidth+'px';this.panel.style.left=Math.max(left+8,Math.min(rect.right-menuWidth,left+width-menuWidth-8))+'px';
    const below=top+height-rect.bottom-13,above=rect.top-top-13,up=below<220&&above>below;
    const space=Math.max(0,up?above:below);this.panel.classList.toggle('is-cramped',space<180);this.panel.style.maxHeight=Math.min(420,space)+'px';
    const panelHeight=this.panel.getBoundingClientRect().height;
    this.panel.style.top=(up?Math.max(top+8,rect.top-panelHeight-5):Math.max(top+8,rect.bottom+5))+'px';
  }
}
document.addEventListener('pointerdown',event=>{if(openControl&&!openControl.wrapper.contains(event.target)&&!openControl.panel.contains(event.target))openControl.close(false);});
document.addEventListener('focusin',event=>{if(openControl&&!openControl.wrapper.contains(event.target)&&!openControl.panel.contains(event.target))openControl.close(false);});
window.addEventListener('resize',()=>openControl?.place());window.visualViewport?.addEventListener('resize',()=>openControl?.place());window.visualViewport?.addEventListener('scroll',()=>openControl?.place());
document.addEventListener('scroll',event=>{if(openControl&&!openControl.panel.contains(event.target)){if(!openControl.button.getClientRects().length)openControl.close(false);else openControl.place();}},true);
