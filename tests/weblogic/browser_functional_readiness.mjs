// Real Application integration; MIDI hardware adapters are synthetic.
// Own child server and Chromium only. No API response fulfillment or demo fallback.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {mkdtemp,writeFile,readFile,rm,mkdir} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {isValidScorePayload,isValidFingeringPayload} from '../../src/piano_core/web/static/app.js';
const require=createRequire(import.meta.url), WS=require('ws');
const ROOT=resolve(import.meta.dirname,'../..');
const OUT=process.env.FUNCTIONAL_EVIDENCE_DIR || '/var/tmp/functional-readiness-evidence';
await mkdir(OUT,{recursive:true});
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,label){const end=Date.now()+20000;while(Date.now()<end){if(await fn())return;await pause(50);}throw new Error(`Timed out: ${label}`);}
const server=spawn(process.env.PIANO_TEST_PYTHON||'/home/dev/.venvs/piano-canonical/bin/python',[join(ROOT,'tests/weblogic/real_backend_fixture.py')],{cwd:ROOT,env:{...process.env,PYTHONPATH:process.env.PIANO_TEST_PACKAGE_ROOT||'src'}});
let inputId=0,ready;const inputPending=new Map();let lines='';server.stdout.on('data',chunk=>{lines+=chunk;while(lines.includes('\n')){const i=lines.indexOf('\n'),line=lines.slice(0,i);lines=lines.slice(i+1);const data=JSON.parse(line);if(data.ready)ready=data;else if(inputPending.has(data.id)){inputPending.get(data.id)(data);inputPending.delete(data.id);}}});
const fixture=(action,extra={})=>new Promise(r=>{const id=++inputId;inputPending.set(id,r);server.stdin.write(JSON.stringify({id,action,...extra})+'\n');});
const raw=(type,note)=>fixture('midi',{message:{type,note,velocity:type==='note_on'?80:0}});
let output='';server.stdout.on('data',x=>output+=x);server.stderr.on('data',x=>output+=x);
const profile=await mkdtemp('/var/tmp/astra-adaptive-chrome-');
let chrome,ws,interceptPlay;let passes=0, failures=0;const metrics=[],errors=[],requests=[],responses=[],flows=[];let apiTrace=[];
async function test(name,fn){try{await fn();passes++;console.log(`ok - ${name}`);flows.push({name,status:'PASS',backend:'real',io:'synthetic MIDI adapters'});}catch(e){failures++;console.error(`FAIL - ${name}`,e);flows.push({name,status:'FAIL',error:String(e)});throw e;}}
try {
await until(()=>ready,'real Application startup');
const base=ready.origin;console.log(JSON.stringify(ready));
chrome=spawn(process.env.CHROME||'/usr/bin/google-chrome',['--headless=new','--no-sandbox','--disable-dev-shm-usage','--use-angle=swiftshader','--enable-unsafe-swiftshader','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank']);
let stderr='';chrome.stderr.on('data',x=>stderr+=x);await until(()=>stderr.includes('DevTools listening'),'chrome');
const debug=stderr.match(/ws:\/\/127.0.0.1:(\d+)/)[1];const targets=await(await fetch(`http://127.0.0.1:${debug}/json/list`)).json();
ws=new WS(targets.find(x=>x.type==='page').webSocketDebuggerUrl);await new Promise(r=>ws.once('open',r));let next=0;const pending=new Map();
ws.on('message',raw=>{const m=JSON.parse(raw);if(pending.has(m.id)){const [r,j]=pending.get(m.id);pending.delete(m.id);m.error?j(m.error):r(m.result);}if(m.method==='Fetch.requestPaused'&&interceptPlay)interceptPlay(m.params);if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails);if(m.method==='Network.requestWillBeSent')requests.push(m.params.request);if(m.method==='Network.responseReceived')responses.push(m.params.response);});
const cdp=(method,params={})=>new Promise((r,j)=>{const id=++next;pending.set(id,[r,j]);ws.send(JSON.stringify({id,method,params}));});
const ev=async expression=>{const r=await cdp('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw r.exceptionDetails;return r.result.value;};
const wait=expr=>until(()=>ev(expr),expr);
async function viewport(w,h){await cdp('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:false});await ev('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');}
async function route(r){await ev(`location.hash='#/${r}'`);await wait(`document.body.dataset.ui008Route==='${r}'`);}
async function view(v,r){await ev(`{let e=document.querySelector('#ui008-view-${r}');e.value='${v}';e.dispatchEvent(new Event('change'));}`);await wait(`document.querySelector('.workspace-scene').dataset.view==='${v}'`);await ev('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');}
async function click(sel){await wait(`!document.querySelector('${sel}').disabled`);await ev(`document.querySelector('${sel}').click()`);if(sel==='#btn-stop')await wait(`document.querySelector('#player-state').textContent==='Stopped'`);}
async function shot(name){await pause(400);await wait(`document.querySelector('#toast').hidden`);await pause(200);const s=await cdp('Page.captureScreenshot',{format:'png'});await writeFile(join(OUT,name+'.png'),Buffer.from(s.data,'base64'));}
async function control(sel,scroll=false){if(scroll)await ev(`document.querySelector('${sel}').scrollIntoView({block:'center'})`);return ev(`(()=>{const e=document.querySelector('${sel}'),r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {w:r.width,h:r.height,visible:r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth,hit:e===hit||e.contains(hit),hitId:hit?.id,hitClass:hit?.className,top:r.top,bottom:r.bottom}})()`);}
await cdp('Page.enable');await cdp('Runtime.enable');await cdp('Network.enable');
await cdp('Page.addScriptToEvaluateOnNewDocument',{source:`window.__life={observers:0,paint:{}};window.__listeners=[];Object.defineProperty(window.__life,"listeners",{enumerable:true,get(){return window.__listeners.filter(x=>x===window||x===document||x.isConnected).length}});
const add=EventTarget.prototype.addEventListener;EventTarget.prototype.addEventListener=function(...a){if(!(this instanceof AbortSignal))window.__listeners.push(this);return add.apply(this,a)};
const RO=ResizeObserver;window.ResizeObserver=class extends RO{constructor(cb){super(cb);window.__life.observers++}};
for(const C of [WebGLRenderingContext,WebGL2RenderingContext])for(const method of ['drawArrays','drawElements']){const original=C.prototype[method];C.prototype[method]=function(...args){const id=this.canvas.id;window.__life.paint[id]=(window.__life.paint[id]||0)+1;return original.apply(this,args)}};
const clear=CanvasRenderingContext2D.prototype.clearRect;CanvasRenderingContext2D.prototype.clearRect=function(...a){const id=this.canvas.id;window.__life.paint[id]=(window.__life.paint[id]||0)+1;return clear.apply(this,a)};`});

const api=async(path,body,method='POST')=>{const r=await fetch(base+'/api/v1'+path,body===undefined?{}:{method,headers:{'content-type':'application/json'},body:JSON.stringify(body)});const data=await r.json();apiTrace.push({path,body,status:r.status,result:data});return {status:r.status,...data};};
const learning=()=>api('/learning');
const count=path=>requests.filter(r=>r.method==='POST'&&r.url.endsWith(path)).length;
const catalog=await api('/library/songs?limit=100');
const songs=catalog.songs||catalog.items;
assert(songs,'real library response: '+JSON.stringify(catalog));
const id=name=>songs.find(s=>s.filename===name+'.mid'||s.relpath===name+'.mid').song_id;
const row=name=>`.song-item[data-song-id="${id(name)}"]`;
const select=async(name,action='details')=>{await route('library');await click(`${row(name)} [data-action="${action}"]`);};
const field=async(sel,value)=>ev(`{const e=document.querySelector(${JSON.stringify(sel)});e.value=${JSON.stringify(value)};e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));}`);
const search=async q=>{await field('#search',q);await pause(400);await wait(`document.querySelector('#search').value===${JSON.stringify(q)}&&!document.querySelector('#library-skeleton').hidden===false`);};
const waitLearning=async predicate=>{let s;await until(async()=>{s=await learning();return predicate(s)},'authoritative learning');return s;};
const release=async notes=>{for(const n of notes)await raw('note_off',n);await pause(70);};
await viewport(1024,768);await cdp('Page.navigate',{url:base+'/ui/#/library'});await wait(`document.querySelectorAll('.song-item').length===50`);
await test('Real Library pagination, relpath search, clear and ready filter',async()=>{
 await click('#btn-more');await wait(`document.querySelectorAll('.song-item').length===65`);
 await search('chords');await wait(`document.querySelectorAll('.song-item').length===1`);assert.equal(await ev(`document.querySelector('.song-item').dataset.songId`),id('03-chords'));
 await click('#btn-clear');await wait(`document.querySelectorAll('.song-item').length===50`);
 await click('#library-filter-ready');await wait(`!document.querySelector('${row('05-invalid')}')`);await click('#library-filter-ready');await wait(`!!document.querySelector('${row('05-invalid')}')`);
});
await test('Late real search response cannot replace newer results',async()=>{
 let held;interceptPlay=p=>{if(p.request.url.includes('q=repeated'))held=p;else void cdp('Fetch.continueRequest',{requestId:p.requestId});};
 await cdp('Fetch.enable',{patterns:[{urlPattern:'*/api/v1/library/songs*',requestStage:'Response'}]});
 await field('#search','repeated');await until(()=>held,'held real search response');
 await field('#search','chords');await wait(`document.querySelectorAll('.song-item').length===1&&document.querySelector('.song-item').dataset.songId==='${id('03-chords')}'`);
 await cdp('Fetch.continueResponse',{requestId:held.requestId}).catch(()=>{});await cdp('Fetch.disable');interceptPlay=null;await pause(300);
 assert.equal(await ev(`document.querySelector('.song-item').dataset.songId`),id('03-chords'));await click('#btn-clear');await wait(`document.querySelectorAll('.song-item').length===50`);
});
await test('Play correct identity once; authoritative title survives selecting another row',async()=>{
 const before=count('/playback/play');await select('02-repeated','play');await wait(`document.body.dataset.ui008Route==='player'&&document.querySelector('#player-state').textContent==='Playing'`);
 assert.equal(count('/playback/play'),before+1);assert.equal(JSON.parse(requests.filter(r=>r.url.endsWith('/playback/play')).at(-1).postData).song_id,id('02-repeated'));
 await until(async()=>(await fixture('output')).sent.some(x=>x[0]==='note_on'),'scheduled MIDI output');await click('#btn-pause');await wait(`document.querySelector('#player-state').textContent==='Paused'`);
 await shot('real-player-paused');const truth=await api('/playback');assert.equal(truth.song_id,id('02-repeated'));assert(truth.position_seconds>=0);assert(truth.duration_seconds>10);
 const title=await ev(`document.querySelector('#player-title').textContent`);await select('04-other');await route('player');assert.equal(await ev(`document.querySelector('#player-title').textContent`),title);
 assert.equal(await ev(`document.querySelector('#player-duration').textContent`),'0:11');
 const n=count('/playback/play');for(let i=0;i<4;i++){await route('piano');await route('learn');await route('player');}assert.equal(count('/playback/play'),n);
 await click('#btn-resume');await wait(`document.querySelector('#player-state').textContent==='Playing'`);await click('#btn-stop');await wait(`document.querySelector('#player-state').textContent==='Stopped'`);assert.equal((await api('/playback')).song_id,null);
 for(const endpoint of ['pause','resume','stop'])assert.equal(count('/playback/'+endpoint),1);
 assert((await fixture('output')).sent.some(x=>x[0]==='note_on'),'real playback sends into synthetic output');
});
await test('Invalid MIDI gives actionable feedback and recovers without optimistic success',async()=>{
 await select('05-invalid','play');await pause(500);assert.equal(await ev('document.body.dataset.ui008Route'),'library');
 assert(await ev(`!document.querySelector('#library-feedback').hidden&&document.querySelector('#library-feedback').textContent.length>0`));
 assert.notEqual((await api('/playback')).state,'PLAYING');await select('02-repeated','play');await wait(`document.body.dataset.ui008Route==='player'`);await click('#btn-stop');
});
await test('Duplicate suppression and real mutex denial reconcile without navigating',async()=>{
 let held;interceptPlay=p=>held=p;await cdp('Fetch.enable',{patterns:[{urlPattern:'*/api/v1/playback/play',requestStage:'Request'}]});
 await select('02-repeated','play');await until(()=>held,'held play');const n=count('/playback/play');await ev(`document.querySelector('${row('02-repeated')} .song-play').click()`);assert.equal(count('/playback/play'),n);
 const session=await api('/learning/start',{song_id:id('03-chords')});assert.equal(session.status,200);
 await cdp('Fetch.continueRequest',{requestId:held.requestId});await cdp('Fetch.disable');interceptPlay=null;
 await wait(`!document.querySelector('${row('02-repeated')} .song-play').disabled`);assert.equal(await ev('document.body.dataset.ui008Route'),'library');assert(await ev(`!document.querySelector('#library-feedback').hidden`));assert.notEqual((await api('/playback')).state,'PLAYING');
 await route('learn');await click('#btn-practice-stop');await waitLearning(s=>s.state==='IDLE');
});
await test('Real Learn wrong note, repeated note release/fresh press, progress and no playback',async()=>{
 await select('02-repeated','learn');await wait(`document.body.dataset.ui008Route==='learn'`);const n=count('/playback/play');const sent=(await fixture('output')).sent.length;
 await click('#btn-practice');let s=await waitLearning(s=>s.state==='WAITING');assert.equal(s.song_id,id('02-repeated'));assert.equal(s.mode,'WAIT_FOR_NOTE');assert.equal(s.total_steps,3);
 await raw('note_on',61);await pause(120);s=await learning();assert.equal(s.completed_steps,0);assert(s.incorrect_attempts>=1);await release([61]);
 await raw('note_on',60);s=await waitLearning(s=>s.completed_steps===1);await raw('note_on',60);await pause(120);assert.equal((await learning()).completed_steps,1);await release([60]);await raw('note_on',60);s=await waitLearning(s=>s.completed_steps===2);await release([60]);
 await wait(`document.querySelector('#practice-step').textContent.includes('3')`);assert.equal(count('/playback/play'),n);assert.equal((await fixture('output')).sent.length,sent);
 await shot('real-learn-target');const before=await learning();for(const v of ['falling','piano','hands','sheet'])await view(v,'learn');for(const r of ['player','piano','settings','learn'])await route(r);const after=await learning();assert.equal(after.session_id,before.session_id);assert.equal(after.version,before.version);assert.deepEqual(after.target,before.target);
 await click('#ui008-learn-options');await click('#btn-learning-restart-target');await until(()=>count('/learning/restart-target')===1,'restart target');await pause(150);assert.equal((await learning()).completed_steps,2);
 await click('#btn-learning-restart-section');await waitLearning(s=>s.completed_steps===0);await click('#ui008-learn-sheet-close');await click('#btn-practice-stop');await waitLearning(s=>s.state==='IDLE');
});
await test('Chord partial input never advances; full chord follows canonical target',async()=>{
 await select('03-chords','learn');await click('#btn-practice');let s=await waitLearning(s=>s.state==='WAITING');assert.deepEqual(s.target.notes,[60,64,67]);
 await raw('note_on',60);await raw('note_on',64);await pause(120);assert.equal((await learning()).completed_steps,0);await raw('note_on',67);await waitLearning(s=>s.completed_steps===1);await release([60,64,67]);await wait(`document.querySelector('#practice-target').textContent.includes('D4')`);await click('#btn-practice-stop');await waitLearning(s=>s.state==='IDLE');
});
await test('Section/repetition parameters and guarded commands use real contract',async()=>{
 await select('02-repeated','learn');await click('#ui008-learn-options');await field('#learning-mode','SECTION_PRACTICE');await field('#learning-section-start','0.5');await field('#learning-section-end','1');await click('#ui008-learn-sheet-close');await click('#btn-practice');let s=await waitLearning(s=>s.state==='WAITING');assert.equal(s.mode,'SECTION_PRACTICE');assert.deepEqual(s.section,{start_seconds:.5,end_seconds:1});assert.equal(s.total_steps,1);await click('#btn-practice-stop');await waitLearning(s=>s.state==='IDLE');
 await click('#ui008-learn-options');await field('#learning-mode','REPEAT_UNTIL_CORRECT');await field('#learning-repetitions','2');await click('#ui008-learn-sheet-close');await click('#btn-practice');s=await waitLearning(s=>s.state==='WAITING');assert.equal(s.repetition.required,2);assert.equal(s.mode,'REPEAT_UNTIL_CORRECT');
 const denied=await api('/learning/restart-target',{expected_session_id:s.session_id,expected_version:s.version+100});assert.equal(denied.status,409);assert.equal((await learning()).version,s.version);
 await click('#btn-practice-stop');await waitLearning(s=>s.state==='IDLE');await click('#ui008-learn-options');await field('#learning-mode','WAIT_FOR_NOTE');await click('#ui008-learn-sheet-close');
});
await test('Playback owns mutex against real Learn; natural playback completion',async()=>{
 await select('02-repeated','play');await wait(`document.body.dataset.ui008Route==='player'`);const denied=await api('/learning/start',{song_id:id('03-chords')});assert.equal(denied.status,409);await route('learn');assert(await ev(`document.querySelector('#btn-practice').disabled`));await route('player');await click('#btn-stop');
 await select('01-short','play');await until(async()=>(await api('/playback')).state==='COMPLETED','natural completion');await wait(`document.querySelector('#player-state').textContent==='Completed'`);
});
await test('Canonical score/fingering payloads and packaged Hands asset load',async()=>{
 await select('03-chords','learn');const score=await api('/score/'+id('03-chords'));const fingering=await api('/fingering/'+id('03-chords'));assert(isValidScorePayload(score));assert(isValidFingeringPayload(fingering));assert.equal(score.note_count,6);assert.equal(fingering.source_version,score.source_version);
 await view('hands','learn');await wait(`window.__pianoHands?.info()?.assetState==='ready'`);await view('falling','learn');
});
await test('Physical adapter input/disconnect/reconnect reflected by real state and Piano UI',async()=>{
 await route('piano');await raw('note_on',65);await wait(`document.querySelector('#piano-notes').textContent.includes('F4')`);await release([65]);
 await fixture('connection',{connected:false});await wait(`document.querySelector('#piano-state').textContent.toLowerCase().includes('disconnect')`);
 await fixture('connection',{connected:true});await wait(`document.querySelector('#piano-state').textContent==='Piano connected'`);
});
await test('LED exactly-once valid updates and invalid real command is atomic',async()=>{
 await route('piano');await click('#ui008-piano-options');const puts=()=>requests.filter(r=>r.method==='PUT'&&r.url.endsWith('/led')).length;let n=puts();await field('#led-brightness','42');await until(()=>puts()===n+1,'LED brightness request');await until(async()=>(await api('/led')).brightness===42,'LED truth');
 n=puts();await field('#led-mode','physical');await until(()=>puts()===n+1,'LED mode request');await until(async()=>(await api('/led')).mode==='physical','LED truth');
 n=puts();await click('#led-enabled');await until(()=>puts()===n+1,'LED enabled request');const prior=await api('/led');const denied=await api('/led',{brightness:101},'PUT');assert.equal(denied.status,400);assert.equal((await api('/led')).brightness,prior.brightness);
 await click('#led-enabled');await until(async()=>(await api('/led')).enabled===true,'LED enabled again');const clears=count('/led/clear');await click('#btn-led-clear');await until(()=>count('/led/clear')===clears+1,'LED clear');await click('#ui008-piano-sheet-close');
});
await test('Real LED denial restores authoritative UI after invalid request fault',async()=>{
 await route('piano');await click('#ui008-piano-options');
 let held;interceptPlay=p=>held=p;await cdp('Fetch.enable',{patterns:[{urlPattern:'*/api/v1/led',requestStage:'Request'}]});
 await field('#led-brightness','55');await until(()=>held,'held LED change');
 // Transport fault only: server receives invalid data and generates its own 400.
 await cdp('Fetch.continueRequest',{requestId:held.requestId,postData:Buffer.from(JSON.stringify({brightness:101})).toString('base64')});await cdp('Fetch.disable');interceptPlay=null;
 await wait(`!document.querySelector('#led-error').hidden`);await wait(`document.querySelector('#led-brightness').value==='42'`);assert.equal((await api('/led')).brightness,42);
 await click('#ui008-piano-sheet-close');
});
await test('Offline truth clears connected state and recovers without demo fallback',async()=>{
 await cdp('Network.emulateNetworkConditions',{offline:true,latency:0,downloadThroughput:-1,uploadThroughput:-1});await wait(`document.querySelector('#piano-state').textContent==='Reconnecting'`);assert(!await ev(`document.querySelector('#piano-state').textContent==='Piano connected'`));
 await cdp('Network.emulateNetworkConditions',{offline:false,latency:0,downloadThroughput:-1,uploadThroughput:-1});await wait(`document.querySelector('#piano-state').textContent==='Piano connected'`);
});
await test('Preferences persist locally and route cycles do not multiply renderers/listeners',async()=>{
 await route('settings');await click('#settings-hands-camera-follow');const stored=await ev('JSON.stringify(localStorage)');const life=await ev('JSON.stringify({listeners:__life.listeners,observers:__life.observers})');const n=requests.filter(r=>['POST','PUT'].includes(r.method)).length;
 for(let i=0;i<8;i++)for(const r of ['library','player','learn','piano','settings'])await route(r);assert.equal(await ev('JSON.stringify({listeners:__life.listeners,observers:__life.observers})'),life);assert.equal(requests.filter(r=>['POST','PUT'].includes(r.method)).length,n);assert.equal(await ev('JSON.stringify(localStorage)'),stored);
 const time=await ev('performance.timeOrigin');await cdp('Page.reload');await wait(`performance.timeOrigin!==${time}&&document.documentElement?.dataset.ui008ShellReady==='true'`);assert.equal(await ev(`document.querySelector('#settings-hands-camera-follow').getAttribute('aria-pressed')`),'true');
});
await test('No browser exceptions, fixture/demo URLs or unexpected HTTP errors',async()=>{
 assert.deepEqual(errors,[]);assert(!output.includes('Traceback'));assert(!requests.some(r=>/immersive_preview|demo-study/.test(r.url)));
 const bad=responses.filter(r=>r.status>=400&&!([400,409,422].includes(r.status)&&/\/api\/v1\/(playback|learning|led)/.test(r.url)));assert.deepEqual(bad,[]);
});
console.log(`PASS ${passes}; FAIL ${failures}`);process.exitCode=failures?1:0;
} catch(e){process.exitCode=1;console.error(e);}
finally {
 await writeFile(join(OUT,'real-backend-flows.json'),JSON.stringify({revision:'f8e3594d81c5a5558daaf5f3650d51ede2af2da3 + task worktree',origin:ready?.origin,flows,requests,responses,apiTrace,errors,passes,failures,serverOutput:output},null,2)+'\n');
 if(ws)ws.close();if(chrome){chrome.kill('SIGTERM');await Promise.race([new Promise(r=>chrome.once('exit',r)),pause(2000)]);}server.stdin.end();await Promise.race([new Promise(r=>server.once('exit',r)),pause(5000)]);if(server.exitCode===null)server.kill('SIGTERM');await rm(profile,{recursive:true,force:true,maxRetries:5});
}
