"use strict";
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {DemoCallController, browserTimers} = require('../static/demo.js');
const flush = async () => { for (let i=0; i<8; i++) await new Promise(resolve => setImmediate(resolve)); };
const deferred = () => { let resolve; const promise = new Promise(r => resolve=r); return {promise, resolve}; };
function harness(overrides={}) {
  const requests=[], views=[], sent=[], values=new Map();
  const track={stopped:0,stop(){this.stopped++;}};
  const stream={getTracks:()=>[track]};
  const channel={readyState:'open',closed:0,send:x=>sent.push(JSON.parse(x)),close(){this.closed++;}};
  const peer={closed:0,createDataChannel:()=>channel,addTrack(){},createOffer:async()=>({sdp:'offer'}),
    setLocalDescription:async()=>{},setRemoteDescription:async()=>{},close(){this.closed++;},connectionState:'connected'};
  const snapshot=(revision=0,id='call-one')=>({call_id:id,revision,call_status:'active',order:{items:[],total:0,status:'building'},pending_action:{action:null}});
  const api=async(path,body)=>{
    requests.push({path,body});
    if(path==='/demo/calls')return snapshot();
    if(path==='/realtime-session')return {value:'ephemeral'};
    if(path==='/realtime-tool')return {...snapshot(1),result:{status:'added'}};
    return {...snapshot(),call_status:path.endsWith('/end')?'ended':'active'};
  };
  let interval;const timeouts=new Map();
  const controller=new DemoCallController({api,view:x=>views.push(x),storage:{getItem:k=>values.get(k),setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)},
    media:{getUserMedia:async()=>stream},makePeer:()=>peer,play(){},sdp:async()=> 'answer',
    every:f=>{interval=f;return 1;},cancelEvery(){interval=null;},after:(f,delay)=>{timeouts.set(delay,f);return delay;},cancelAfter:id=>timeouts.delete(id),
    keepalive:path=>requests.push({path,keepalive:true}),...overrides});
  return {controller,requests,views,sent,track,channel,peer,snapshot,values,heartbeat:()=>interval?.(),deadline:()=>timeouts.get(600000)?.(),readiness:()=>timeouts.get(15000)?.()};
}
test('microphone denial does not create or mint a call',async()=>{
  const h=harness({media:{getUserMedia:async()=>{const e=new Error('denied');e.name='NotAllowedError';throw e;}}});
  await h.controller.start('code');assert.equal(h.controller.state,'error');assert.equal(h.requests.length,0);
  assert.match(h.views.at(-1).error,/permission was denied/);
});
test('start uses permission first and repeated starts are ignored',async()=>{
  const permission=deferred();const h=harness({media:{getUserMedia:()=>permission.promise}});
  const start=h.controller.start('code');await h.controller.start('code');assert.equal(h.requests.length,0);
  permission.resolve({getTracks:()=>[h.track]});await start;
  assert.equal(h.requests.filter(x=>x.path==='/demo/calls').length,1);
  h.controller.event({type:'session.created'},h.controller.generation);assert.equal(h.controller.state,'listening');
});
test('end cleans stream peer and channel and retains snapshot',async()=>{
  const h=harness();await h.controller.start('code');await h.controller.end();
  assert.equal(h.track.stopped,1);assert.equal(h.peer.closed,1);assert.equal(h.channel.closed,1);
  assert.equal(h.controller.state,'ended');assert(h.requests.some(x=>x.path.endsWith('/end')));
});
test('cancel pending permission stops late stream without backend call',async()=>{
  const permission=deferred();const h=harness({media:{getUserMedia:()=>permission.promise}});
  const pending=h.controller.start('code');await h.controller.end();permission.resolve({getTracks:()=>[h.track]});await pending;
  assert.equal(h.track.stopped,1);assert.equal(h.requests.length,0);assert.equal(h.controller.state,'ended');
});
test('late call creation after cancellation is closed',async()=>{
  const start=deferred();const h=harness();const original=h.controller.api;
  h.controller.api=(path,body)=>path==='/demo/calls'?start.promise:original(path,body);
  const pending=h.controller.start('code');await flush();await h.controller.end();start.resolve(h.snapshot());await pending;
  assert(h.requests.some(x=>x.path==='/demo/calls/call-one/end'));assert.equal(h.peer.closed,0);
});
test('SDP connection failure shuts down resources and backend call',async()=>{
  const h=harness({sdp:async()=>{throw new Error('SDP failed');}});await h.controller.start('code');
  assert.equal(h.controller.state,'error');assert.equal(h.track.stopped,1);assert.equal(h.peer.closed,1);
  assert(h.requests.some(x=>x.path.endsWith('/end')));
});
test('credential failure closes backend call and microphone',async()=>{
  const h=harness();const original=h.controller.api;
  h.controller.api=(path,body)=>path==='/realtime-session'?Promise.reject(new Error('Unavailable')):original(path,body);
  await h.controller.start('code');assert.equal(h.controller.state,'error');assert.equal(h.track.stopped,1);
  assert(h.requests.some(x=>x.path.endsWith('/end')));
});
test('restore retrieves and ends active call without microphone or credential',async()=>{
  let microphone=0;const h=harness({media:{getUserMedia:async()=>{microphone++;throw new Error('must not connect');}}});
  h.values.set('orderai-demo-call','call-one');await h.controller.restore();
  assert.equal(microphone,0);assert.equal(h.controller.state,'ended');assert.equal(h.requests.length,2);
  assert(!h.requests.some(x=>x.path==='/realtime-session'));
});
test('lost refresh reference reports explicit error',async()=>{
  const h=harness({api:async()=>{throw new Error('Session expired');}});h.values.set('orderai-demo-call','lost');
  await h.controller.restore();assert.equal(h.controller.state,'error');assert.equal(h.controller.callId,null);
  assert.equal(h.values.has('orderai-demo-call'),false);
});
test('older revisions and previous call snapshots never replace cart',async()=>{
  const h=harness();await h.controller.start('code');h.controller.applySnapshot(h.snapshot(3));
  const count=h.views.filter(x=>x.snapshot).length;
  h.controller.applySnapshot(h.snapshot(2));h.controller.applySnapshot(h.snapshot(4,'previous'));
  assert.equal(h.views.filter(x=>x.snapshot).length,count);assert.equal(h.controller.revision,3);
});
test('duplicate function event executes once and continues once after response done',async()=>{
  const h=harness();await h.controller.start('code');const generation=h.controller.generation;
  const message={type:'response.output_item.done',response_id:'response-one',item:{type:'function_call',call_id:'tool-one',name:'add_item_to_order',arguments:'{"search_term":"Firecracker","quantity":1}'}};
  h.controller.event(message,generation);h.controller.event(message,generation);await h.controller.queue;
  assert.equal(h.requests.filter(x=>x.path==='/realtime-tool').length,1);assert.equal(h.sent.filter(x=>x.type==='response.create').length,0);
  h.controller.event({type:'response.done',response:{id:'response-one'}},generation);
  h.controller.event({type:'response.done',response:{id:'response-one'}},generation);
  assert.equal(h.sent.filter(x=>x.type==='response.create').length,1);
});
test('multiple functions are serial and continuation waits for all outputs',async()=>{
  const h=harness();await h.controller.start('code');let running=0,max=0;const original=h.controller.api;
  h.controller.api=async(path,body)=>{if(path==='/realtime-tool'){running++;max=Math.max(max,running);await flush();const r=await original(path,body);running--;return r;}return original(path,body);};
  for(const id of ['one','two'])h.controller.event({type:'response.output_item.done',response_id:'batch',item:{type:'function_call',call_id:id,name:'get_order',arguments:'{}'}},h.controller.generation);
  h.controller.event({type:'response.done',response:{id:'batch'}},h.controller.generation);
  await h.controller.queue;assert.equal(max,1);assert.equal(h.sent.filter(x=>x.type==='response.create').length,1);
});
test('late tool response after end does not render or send output',async()=>{
  const pending=deferred();const h=harness();await h.controller.start('code');const original=h.controller.api;
  h.controller.api=(path,body)=>path==='/realtime-tool'?pending.promise:original(path,body);
  h.controller.event({type:'response.output_item.done',response_id:'batch',item:{type:'function_call',call_id:'late',name:'get_order',arguments:'{}'}},h.controller.generation);
  await flush();await h.controller.end();const count=h.views.filter(x=>x.snapshot).length;
  pending.resolve({...h.snapshot(10),result:{status:'added'}});await h.controller.queue;
  assert.equal(h.views.filter(x=>x.snapshot).length,count);assert.equal(h.sent.length,0);
});
test('disconnect heartbeat error and time limit all clean up',async()=>{
  for(const mode of ['disconnect','heartbeat','deadline']){
    const h=harness();await h.controller.start('code');
    if(mode==='disconnect'){h.peer.connectionState='disconnected';h.peer.onconnectionstatechange();}
    if(mode==='heartbeat'){const original=h.controller.api;h.controller.api=(path,body)=>path.endsWith('/heartbeat')?Promise.reject(new Error('Expired')):original(path,body);await h.heartbeat();}
    if(mode==='deadline')h.deadline();
    await flush();assert.equal(h.controller.state,'error');assert.equal(h.track.stopped,1);
  }
});
test('pagehide shuts microphone and sends keepalive end',async()=>{
  const h=harness();await h.controller.start('code');h.controller.unload();
  assert.equal(h.track.stopped,1);assert(h.requests.some(x=>x.keepalive));
});
test('autoplay failure cleans up, but old playback failure cannot end a new call',async()=>{
  const h=harness({play:stream=>stream?Promise.reject(new Error('Blocked')):undefined});
  await h.controller.start('code');h.peer.ontrack({streams:[{}]});await flush();
  assert.equal(h.controller.state,'error');assert.equal(h.track.stopped,1);
  let reject;const late=new Promise((resolve,r)=>reject=r);
  const other=harness({play:stream=>stream?late:undefined});await other.controller.start('code');
  other.peer.ontrack({streams:[{}]});await other.controller.end();await other.controller.start('code');
  reject(new Error('Old playback'));await flush();assert.equal(other.controller.state,'connecting');
});
test('missing session readiness times out and releases microphone',async()=>{
  const h=harness();await h.controller.start('code');h.readiness();await flush();
  assert.equal(h.controller.state,'error');assert.equal(h.track.stopped,1);
  const ready=harness();await ready.controller.start('code');
  ready.controller.event({type:'session.created'},ready.controller.generation);ready.readiness();
  assert.equal(ready.controller.state,'listening');
});

test('browser timer wrappers preserve the native receiver through controller methods', async()=>{
  const host = {};
  for (const name of ['setInterval','clearInterval','setTimeout','clearTimeout']) {
    host[name] = function() {
      if (this !== host) throw new TypeError('Illegal invocation');
      return 5;
    };
  }
  assert.throws(()=>{ const detached = {after: host.setTimeout}; detached.after(()=>{},1); }, /Illegal invocation/);
  const h = harness(browserTimers(host));
  await h.controller.start('code');
  assert.equal(h.controller.state,'connecting');
  await h.controller.end();
  assert.equal(h.controller.state,'ended');
  assert.equal(h.track.stopped,1);
});
