import assert from 'node:assert/strict';

// A small DOM seam isolates recovery transitions without credentials or a broker.
const nodes=new Map();
globalThis.window={addEventListener(){}};
globalThis.document={addEventListener(){},getElementById(id){
  if(!nodes.has(id))nodes.set(id,{textContent:'',hidden:false,disabled:false,classList:{toggle(){}},setAttribute(){},replaceChildren(){},querySelectorAll(){return [];}});
  return nodes.get(id);
}};
const {ProviderConnection}=await import('../service/static/provider-connection.js');
const controller=Object.assign(Object.create(ProviderConnection.prototype),{
  connected:true,job:null,symbol:{id:123},busy:false,searching:false,searchVersion:0,
  saved:new Set(),menu:{sync(){}},version:0,onDataset:async()=>{},schedule(){}
});
const refreshable={connected:false,can_refresh:true,state:'expired'};
await controller.applyStatus({connection:refreshable,job:null});
assert.equal(controller.connected,true,'retained refresh credentials keep the session usable');
assert.equal(controller.symbol.id,123,'token expiry must not erase the selected symbol');
assert.equal(document.getElementById('provider-download').disabled,false);
assert.equal(document.getElementById('provider-connected').hidden,false,'Disconnect stays available');

controller.pending={request_id:'accepted-but-response-lost'};
await controller.applyStatus({connection:refreshable,job:null});
assert.ok(controller.pending,'unknown outcome keeps the idempotent retry ID');
await controller.applyStatus({connection:refreshable,job:{id:'other',request_id:'someone-else',status:'cancelled'}});
assert.ok(controller.pending,'an unrelated job cannot resolve this submission');
await controller.applyStatus({connection:refreshable,job:{id:'confirmed',request_id:'accepted-but-response-lost',status:'completed',dataset:{id:'data'}}});
assert.equal(controller.pending,null,'confirmed acceptance releases the key for a future intentional download');

controller.api=async()=>({connection:refreshable,job:null});
controller.error('Temporary connection failure',true);
await controller.refresh();
assert.equal(document.getElementById('provider-error').hidden,true,'successful status refresh clears its old error');
controller.error('Symbol search failed');
await controller.refresh();
assert.equal(document.getElementById('provider-error').textContent,'Symbol search failed','status refresh preserves action errors');
await controller.applyStatus({connection:{connected:false,can_refresh:false},job:null});
assert.equal(controller.symbol,null);
assert.equal(document.getElementById('provider-download').disabled,true);
console.log('Provider UI checks passed: refreshable sessions, lost-response recovery, scoped errors and disconnect controls.');
