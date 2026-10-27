import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {createRequire} from 'node:module';
import {mkdtemp,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const require=createRequire(import.meta.url);
const WS=require('ws');
export const pause=ms=>new Promise(r=>setTimeout(r,ms));
export async function until(fn,label,ms=20000) {
 const end=Date.now()+ms;
 while(Date.now()<end){if(await fn())return;await pause(25);}
 throw Error('Timed out: '+label);
}
export class BrowserDriver {
 constructor({cleanupDeadline=5000}={}){this.cleanupDeadline=cleanupDeadline;this.pending=new Map();this.active=new Map();this.responses=[];this.tasks=new Set();this.sequence=0;this.errors=[];this.apiFailures=[];this.tearingDown=false;this.networkEvents=[];}
 async start(){
  this.profile=await mkdtemp(join(tmpdir(),'piano-release-owned-chrome-'));
  this.chrome=spawn('/usr/bin/google-chrome',['--headless=new','--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--remote-debugging-port=0',`--user-data-dir=${this.profile}`,'about:blank']);
  this.stderr='';this.chrome.stderr.on('data',x=>this.stderr+=x);
  this.exited=new Promise(resolve=>this.chrome.once('exit',(code,signal)=>{this.exit={code,signal};resolve(this.exit);}));
  await until(()=>this.stderr.includes('DevTools listening'),'owned Chrome endpoint',10000);
  const port=this.stderr.match(/ws:\/\/127.0.0.1:(\d+)/)[1];
  const tabs=await(await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  this.ws=new WS(tabs.find(t=>t.type==='page').webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{this.ws.once('open',resolve);this.ws.once('error',reject);});
  this.ws.on('close',()=>{for(const p of this.pending.values())p.reject(Error('CDP closed'));this.pending.clear();});
  this.ws.on('message',raw=>this.message(JSON.parse(raw)));
  await this.send('Runtime.enable');await this.send('Page.enable');await this.send('Network.enable');
  return this;
 }
 track(promise){this.tasks.add(promise);promise.finally(()=>this.tasks.delete(promise)).catch(()=>{});return promise;}
 message(m){
  if(m.id&&this.pending.has(m.id)){const p=this.pending.get(m.id);this.pending.delete(m.id);m.error?p.reject(Error(JSON.stringify(m.error))):p.resolve(m.result);}
  const p=m.params;
  if(m.method==='Network.requestWillBeSent')this.active.set(p.requestId,{url:p.request.url,type:p.type,sequence:++this.sequence});
  if(m.method==='Network.responseReceived'){
   const request=this.active.get(p.requestId);if(request)request.response=p.response;
   if(p.response.url.includes('/api/v1/')&&p.response.status>=400&&!this.tearingDown)this.apiFailures.push({url:p.response.url,status:p.response.status});
  }
  if(m.method==='Network.loadingFinished'||m.method==='Network.loadingFailed'){
   const request=this.active.get(p.requestId);this.active.delete(p.requestId);
   this.networkEvents.push({method:m.method,requestId:p.requestId,url:request?.url,error:p.errorText,teardown:this.tearingDown});
   if(m.method==='Network.loadingFinished'&&request?.response&&/\/api\/v1\/learn\/lessons(?:\/|\?)/.test(request.url)){
    const sequence=request.sequence;
    const task=this.send('Network.getResponseBody',{requestId:p.requestId}).then(r=>{
     this.responses.push({sequence,url:request.url,status:request.response.status,body:JSON.parse(r.base64Encoded?Buffer.from(r.body,'base64').toString():r.body)});
    }).catch(e=>{if(!this.tearingDown)this.errors.push(String(e));});this.track(task);
   }
  }
  if(m.method==='Runtime.exceptionThrown'&&!this.tearingDown)this.errors.push(p.exceptionDetails);
  this.onEvent?.(m);
 }
 send(method,params={}){
  return new Promise((resolve,reject)=>{
   const id=(this.id=(this.id||0)+1);
   const timer=setTimeout(()=>{this.pending.delete(id);reject(Error('CDP deadline: '+method));},10000);
   this.pending.set(id,{resolve:x=>{clearTimeout(timer);resolve(x);},reject:e=>{clearTimeout(timer);reject(e);}});
   this.ws.send(JSON.stringify({id,method,params}),e=>{if(e){this.pending.get(id)?.reject(e);this.pending.delete(id);}});
  });
 }
 async ev(expression){const r=await this.send('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value;}
 wait(expression){return until(()=>this.ev(expression),expression);}
 async painted(){await this.ev('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');}
 async click(selector){
  await this.wait(`(()=>{const e=document.querySelector(${JSON.stringify(selector)});return !!e&&!e.disabled&&!e.hidden&&e.getBoundingClientRect().width>0})()`);
  await this.ev(`document.querySelector(${JSON.stringify(selector)}).scrollIntoView({block:'center'})`);await this.painted();
  const point=await this.ev(`(()=>{const e=document.querySelector(${JSON.stringify(selector)}),r=e.getBoundingClientRect(),x=r.x+r.width/2,y=r.y+r.height/2;return {x,y,hit:e.contains(document.elementFromPoint(x,y))}})()`);
  assert(point.hit,'visible user hit target: '+selector);delete point.hit;
  await this.send('Input.dispatchMouseEvent',{type:'mousePressed',button:'left',clickCount:1,...point});
  await this.send('Input.dispatchMouseEvent',{type:'mouseReleased',button:'left',clickCount:1,...point});
 }
 async key(key,code,extra={}){await this.send('Input.dispatchKeyEvent',{type:'keyDown',key,code,...extra});await this.send('Input.dispatchKeyEvent',{type:'keyUp',key,code,...extra});}
 async text(selector,value){await this.click(selector);await this.key('a','KeyA',{modifiers:2,windowsVirtualKeyCode:65});await this.send('Input.insertText',{text:value});}
 async choose(selector,value){
  const index=await this.ev(`Array.from(document.querySelector(${JSON.stringify(selector)}).options).findIndex(o=>o.value===${JSON.stringify(value)})`);assert(index>=0);
  await this.click(selector);await this.key('Home','Home',{windowsVirtualKeyCode:36});
  for(let i=0;i<index;i++)await this.key('ArrowDown','ArrowDown',{windowsVirtualKeyCode:40});
  await this.key('Enter','Enter',{windowsVirtualKeyCode:13});
  await this.wait(`document.querySelector(${JSON.stringify(selector)}).value===${JSON.stringify(value)}`);
 }
 async close(){
  const receipt={status:'RUNNING',requests_before:[...this.active.entries()].map(([requestId,request])=>({requestId,...request})),started:Date.now(),forced_termination:false};
  const deadline=receipt.started+this.cleanupDeadline,gracefulDeadline=deadline-250;
  async function bounded(promise,label,end=gracefulDeadline){
   const remaining=end-Date.now();if(remaining<=0)throw Error('Cleanup absolute deadline: '+label);
   let timer;try{return await Promise.race([promise,new Promise((_,reject)=>{timer=setTimeout(()=>reject(Error('Cleanup absolute deadline: '+label)),remaining);})]);}finally{clearTimeout(timer);}
  }
  this.tearingDown=true;
  try{
   assert.equal(this.ws?.readyState,WS.OPEN,'owned CDP available for cleanup barrier');
   await bounded(this.send('Page.navigate',{url:'about:blank'}),'document navigation');
   await bounded(this.wait("location.href==='about:blank'&&document.readyState==='complete'"),'blank document complete');
   await bounded(until(()=>this.tasks.size===0,'owned receipt/body tasks settled',this.cleanupDeadline),'receipt/body tasks');
   receipt.document_destroyed=true;receipt.receipt_tasks_drained=true;
   // CDP may disconnect before replying. Normal process exit is authoritative.
   this.send('Browser.close').catch(()=>{});
   await bounded(until(()=>!!this.exit,'owned Chrome process exit',this.cleanupDeadline),'normal browser exit');
   receipt.process_exit=this.exit;
   // Preserve absent terminal Network events without inventing HTTP success.
   receipt.requests_retired_by_process_exit=[...this.active.entries()].map(([requestId,request])=>({requestId,...request,disposition:'owner_process_destroyed'}));
   assert.equal(this.tasks.size,0);assert.equal(this.exit.code,0);
   this.active.clear();receipt.requests_after=[];receipt.network_drained=true;receipt.status='PASS';
  }catch(e){
   receipt.status='FAIL';receipt.error=String(e);receipt.forced_termination=true;
   this.chrome?.kill('SIGTERM');
   try{await bounded(until(()=>!!this.exit,'emergency termination',this.cleanupDeadline),'emergency termination',deadline);}
   catch(e){this.chrome?.kill('SIGKILL');receipt.emergency_exit_error=String(e);}
  }finally{
   this.ws?.terminate();receipt.process_exit=this.exit;receipt.network_events=this.networkEvents;
   try{await bounded(rm(this.profile,{recursive:true,force:true,maxRetries:2}),'owned profile removal',deadline);}
   catch(e){receipt.status='FAIL';receipt.profile_cleanup_error=String(e);}
   receipt.finished=Date.now();receipt.absolute_deadline=deadline;
  }
  return receipt;
 }
}
