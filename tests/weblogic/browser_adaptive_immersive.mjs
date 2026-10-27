// Adaptive composition against the actual LOCAL DEMO server and canonical
// fixture. No hardware or external server. Chromium and server PIDs owned here.
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {spawn} from 'node:child_process';
import {mkdtemp,writeFile,readFile,rm,mkdir} from 'node:fs/promises';
import {resolve,join} from 'node:path';
import {isValidScorePayload,isValidFingeringPayload} from '../../src/piano_core/web/static/app.js';
const require=createRequire(import.meta.url), WS=require('ws');
const ROOT=resolve(import.meta.dirname,'../..');
const OUT=process.env.ADAPTIVE_EVIDENCE_DIR || '/var/tmp/piano-core-astra-evidence/adaptive';
await mkdir(OUT,{recursive:true});
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,label){const end=Date.now()+20000;while(Date.now()<end){if(await fn())return;await pause(50);}throw new Error(`Timed out: ${label}`);}
const server=spawn(process.execPath,[join(ROOT,'tests/weblogic/immersive_preview.mjs'),'0'],{cwd:ROOT});
let output='';server.stdout.on('data',x=>output+=x);server.stderr.on('data',x=>output+=x);
const profile=await mkdtemp('/var/tmp/astra-adaptive-chrome-');
let chrome,ws;let passes=0, failures=0;const metrics=[],errors=[],requests=[];
async function test(name,fn){try{await fn();passes++;console.log(`ok - ${name}`);}catch(e){failures++;console.error(`FAIL - ${name}`,e);}}
try {
await until(()=>output.includes('http://'),'demo startup');
const port=Number(output.match(/0\.0\.0\.0:(\d+)/)[1]);const base=`http://127.0.0.1:${port}`;
chrome=spawn(process.env.CHROME||'/usr/bin/google-chrome',['--headless=new','--no-sandbox','--disable-dev-shm-usage','--use-angle=swiftshader','--enable-unsafe-swiftshader','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank']);
let stderr='';chrome.stderr.on('data',x=>stderr+=x);await until(()=>stderr.includes('DevTools listening'),'chrome');
const debug=stderr.match(/ws:\/\/127.0.0.1:(\d+)/)[1];const targets=await(await fetch(`http://127.0.0.1:${debug}/json/list`)).json();
ws=new WS(targets.find(x=>x.type==='page').webSocketDebuggerUrl);await new Promise(r=>ws.once('open',r));let next=0;const pending=new Map();
ws.on('message',raw=>{const m=JSON.parse(raw);if(pending.has(m.id)){const [r,j]=pending.get(m.id);pending.delete(m.id);m.error?j(m.error):r(m.result);}if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails);if(m.method==='Network.requestWillBeSent')requests.push(m.params.request);if(m.method==='Network.responseReceived'&&m.params.response.status>=400)errors.push(m.params.response);});
const cdp=(method,params={})=>new Promise((r,j)=>{const id=++next;pending.set(id,[r,j]);ws.send(JSON.stringify({id,method,params}));});
const ev=async expression=>{const r=await cdp('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw r.exceptionDetails;return r.result.value;};
const wait=expr=>until(()=>ev(expr),expr);
async function viewport(w,h){await cdp('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:false});await ev('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');}
async function route(r){await ev(`location.hash='#/${r}'`);await wait(`document.body.dataset.ui008Route==='${r}'`);}
async function view(v,r){await ev(`{let e=document.querySelector('#ui008-view-${r}');e.value='${v}';e.dispatchEvent(new Event('change'));}`);await wait(`document.querySelector('.workspace-scene').dataset.view==='${v}'`);await ev('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');}
async function click(sel){await wait(`!document.querySelector('${sel}').disabled`);await ev(`document.querySelector('${sel}').click()`);}
async function shot(name){await wait(`document.querySelector('#toast').hidden`);await pause(200);const s=await cdp('Page.captureScreenshot',{format:'png'});await writeFile(join(OUT,name+'.png'),Buffer.from(s.data,'base64'));}
async function control(sel,scroll=false){if(scroll)await ev(`document.querySelector('${sel}').scrollIntoView({block:'center'})`);return ev(`(()=>{const e=document.querySelector('${sel}'),r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {w:r.width,h:r.height,visible:r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth,hit:e===hit||e.contains(hit),hitId:hit?.id,hitClass:hit?.className,top:r.top,bottom:r.bottom}})()`);}
await cdp('Page.enable');await cdp('Runtime.enable');await cdp('Network.enable');
await cdp('Page.addScriptToEvaluateOnNewDocument',{source:`window.__life={observers:0,paint:{}};window.__listeners=[];Object.defineProperty(window.__life,"listeners",{enumerable:true,get(){return window.__listeners.filter(x=>x===window||x===document||x.isConnected).length}});
const add=EventTarget.prototype.addEventListener;EventTarget.prototype.addEventListener=function(...a){if(!(this instanceof AbortSignal))window.__listeners.push(this);return add.apply(this,a)};
const RO=ResizeObserver;window.ResizeObserver=class extends RO{constructor(cb){super(cb);window.__life.observers++}};
const clear=CanvasRenderingContext2D.prototype.clearRect;CanvasRenderingContext2D.prototype.clearRect=function(...a){const id=this.canvas.id;window.__life.paint[id]=(window.__life.paint[id]||0)+1;return clear.apply(this,a)};`});
await viewport(1024,768);await cdp('Page.navigate',{url:base+'/ui/#/library'});await wait(`!!document.querySelector('.btn-song')`);await click('.btn-song');
await test('actual preview uses canonical score, plan and candidate bytes',async()=>{const fixture=JSON.parse(await readFile(join(ROOT,'tests/weblogic/fixtures/immersive_demo.json')));assert(isValidScorePayload(fixture.score));assert(isValidFingeringPayload(fixture.fingering,fixture.score));assert.deepEqual(await(await fetch(base+'/api/v1/score/demo-study')).json(),fixture.score);assert.deepEqual(await(await fetch(base+'/api/v1/fingering/demo-study')).json(),fixture.fingering);assert.equal(await(await fetch(base+'/ui/styles.css')).text(),await readFile(join(ROOT,'src/piano_core/web/static/styles.css'),'utf8'));assert(await ev(`document.querySelector('.demo-banner').textContent.includes('MOCK')`));});
await test('preview playback and live Piano use the same canonical timeline',async()=>{
 await route('player');await click('#btn-play');await route('learn');await view('piano','learn');
 await wait(`document.querySelector('[data-midi="60"]')?.dataset.source==='playback'`);
 const snap=await(await fetch(base+'/api/v1/live-notes')).json();assert(snap.playback.notes.includes(60));assert.deepEqual(snap.physical.notes,[]);
 await click('#btn-stop');await wait(`!document.querySelector('#btn-play').disabled`);
});
await route('learn');await view('falling','learn');await click('#btn-practice');await wait(`document.querySelector('#visual-stage').dataset.status==='ready'`);
await view('hands','learn');await wait(`window.__pianoHands?.info()?.assetState==='ready'`);await wait(`window.__pianoHands?.info()?.identity?.startsWith('demo-study|')`);
const MATRIX=[[320,568],[338,654],[390,844],[430,932],[768,1024],[1024,768],[1280,720],[1440,900],[1920,1080],[2560,1440],[899,900],[900,900]];
for(const r of ['learn']){
 if(r==='player'){await click('#btn-practice-stop');await route('player');await click('#btn-play');await click('#btn-pause');}
 for(const [w,h] of MATRIX)await test(`${r} ${w}x${h}: four equal full-width dominant scenes and touch controls`,async()=>{
  await viewport(w,h);await route(r);let first;
  for(const v of ['falling','piano','hands','sheet']){
   await view(v,r);
   const m=await ev(`(()=>{const scene=document.querySelector('.workspace-scene'),r=scene.getBoundingClientRect(),main=document.querySelector('.layout'),c=getComputedStyle(main),canvas=document.querySelector('.scene-view:not([hidden]) canvas:not([hidden])'),cr=canvas?.getBoundingClientRect();return {w:r.width,h:r.height,x:r.x,y:r.y,available:main.clientWidth-parseFloat(c.paddingLeft)-parseFloat(c.paddingRight),overflow:main.scrollHeight-main.clientHeight,doc:document.documentElement.scrollWidth-innerWidth,views:[...document.querySelectorAll('.scene-view')].filter(e=>!e.hidden).length,canvas:cr?{w:cr.width,h:cr.height,bw:canvas.width,bh:canvas.height}:null}})()`);
   metrics.push({route:r,view:v,viewport:[w,h],...m});assert.equal(m.views,1);assert(m.doc<=1);assert(m.overflow<=1,JSON.stringify(m));assert(Math.abs(m.w-m.available)<=1,'full main width');assert(m.h/h>=(w<600?.50:.65),`dominance ${m.h/h}`);
   if(first){assert(Math.abs(first.w-m.w)<=1);assert(Math.abs(first.h-m.h)<=1);}else first=m;
   const primary=r==='learn'?'#btn-practice-stop':'#btn-resume';for(const sel of [primary,`#ui008-view-${r}`,`#ui008-${r}-options`,...(r==='player'?['#btn-stop']:[])]){const c=await control(sel);assert(c.visible&&c.hit,`${sel}: ${JSON.stringify(c)}`);assert(c.w>=44&&c.h>=44);if(sel===primary)assert(c.w>=56&&c.h>=56);}
   if(v==='piano')assert(await ev(`(()=>{const k=document.querySelector('#live-keyboard').getBoundingClientRect();return [...document.querySelectorAll('.piano-key')].length===88&&k.width>0&&k.right<=innerWidth})()`));
   if(v==='hands'){await wait(`(()=>{let c=document.querySelector('#hands-canvas'),r=c.getBoundingClientRect();return Math.abs(c.width/c.height-r.width/r.height)<.04})()`);assert(m.canvas.h>m.h*.55,'hands occupies the scene');}
   if(r==='learn'&&w===338&&v==='falling')await shot('learn-falling-338');if(r==='learn'&&w===1024&&v==='falling')await shot('learn-falling-1024');if(r==='learn'&&w===1440&&v==='piano')await shot('learn-piano-1440');if(r==='learn'&&w===1024&&v==='hands')await shot('learn-hands-1024');if(r==='learn'&&w===1024&&v==='sheet')await shot('learn-sheet-1024');if(r==='player'&&w===338&&v==='falling')await shot('player-falling-338');if(r==='player'&&w===1440&&v==='falling')await shot('player-falling-1440');
  }
 });
}
await test('20 view switches and route cycles preserve state, instances, observers and listeners',async()=>{
 await click('#btn-practice-stop');await route('player');await click('#btn-play');await click('#btn-pause');await route('learn');await viewport(1024,768);await view('falling','learn');await pause(500);
 const before=await ev(`({...window.__life,canvases:document.querySelectorAll('canvas').length})`), play=await(await fetch(base+'/api/v1/playback')).json(),n=requests.filter(x=>x.method==='POST'||x.method==='PUT').length;
 for(let i=0;i<20;i++){await view(['piano','hands','sheet','falling'][i%4],'learn');if(i%4===0){await route('player');await route('learn');}}
 const after=await ev(`({...window.__life,canvases:document.querySelectorAll('canvas').length})`);assert.equal(after.observers,before.observers);assert.equal(after.listeners,before.listeners);assert.equal(after.canvases,before.canvases);assert.equal(requests.filter(x=>x.method==='POST'||x.method==='PUT').length,n);assert.deepEqual(await(await fetch(base+'/api/v1/playback')).json(),play);
 await view('piano','learn');const paints=await ev('JSON.stringify(window.__life.paint)');await pause(1200);assert.equal(await ev('JSON.stringify(window.__life.paint)'),paints,'inactive 2D canvases do not paint');
});
await test('options, focus return and orientation use existing layers and resize',async()=>{
 await click('#btn-stop');await route('learn');await click('#btn-practice');await viewport(1024,768);await view('hands','learn');await click('#ui008-learn-options');await wait(`!document.querySelector('#ui008-learn-sheet').hidden`);assert(await ev(`document.querySelector('#ui008-learn-sheet').contains(document.activeElement)`));assert((await control('#btn-learning-restart-target')).hit);await shot('lesson-options');await ev(`document.activeElement.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}))`);await wait(`document.querySelector('#ui008-learn-sheet').hidden`);assert(await ev(`document.activeElement.id==='ui008-learn-options'`));await viewport(768,1024);await wait(`(()=>{let c=document.querySelector('#hands-canvas'),r=c.getBoundingClientRect();return Math.abs(c.width/c.height-r.width/r.height)<.04})()`);await shot('hands-after-orientation');
 const before=await(await fetch(base+'/api/v1/learning')).json();for(let i=0;i<20;i++)await view(['piano','hands','sheet','falling'][i%4],'learn');assert.deepEqual(await(await fetch(base+'/api/v1/learning')).json(),before,'target/session/progress frozen until physical input');
});
await test('125/150/200 percent text plus short landscape keep controls reachable',async()=>{
 for(const r of ['learn']){await route(r);for(const scale of [125,150,200])for(const [w,h] of [[338,654],[844,390]]){
  await viewport(w,h);await ev(`document.documentElement.style.fontSize='${scale}%'`);
  for(const v of ['falling','piano','hands','sheet']){await view(v,r);for(const sel of [r==='learn'?'#btn-practice-stop':'#btn-play',`#ui008-${r}-options`]){const c=await control(sel,true);assert(c.visible&&c.hit,`${r}/${v} ${scale}% ${w}: ${JSON.stringify(c)}`);}assert(await ev('document.documentElement.scrollWidth<=innerWidth+1'));}
 }}await ev(`document.documentElement.style.fontSize=''`);
});
await test('browser zoom equivalent: CSS viewport shrinks and DPR increases at 125/150/200%',async()=>{
 for(const zoom of [1.25,1.5,2]){
  await cdp('Emulation.setDeviceMetricsOverride',{width:Math.floor(1024/zoom),height:Math.floor(768/zoom),deviceScaleFactor:zoom,mobile:false});
  for(const r of ['learn']){await route(r);for(const v of ['falling','piano','hands','sheet']){
   await view(v,r);const sel=r==='learn'?'#btn-practice-stop':'#btn-play';const c=await control(sel,true);assert(c.visible&&c.hit,`${zoom} ${r}/${v}`);
   assert(await ev('document.documentElement.scrollWidth<=innerWidth+1'));
  }}
 }
});
await test('no browser exceptions or failing network responses',async()=>{assert.deepEqual(errors,[]);});
await writeFile(join(OUT,'dimensions.json'),JSON.stringify(metrics,null,2)+'\n');
console.log(`PASS ${passes}; FAIL ${failures}`);process.exitCode=failures?1:0;
} finally {if(ws)ws.close();if(chrome){chrome.kill('SIGTERM');await Promise.race([new Promise(r=>chrome.once('exit',r)),pause(2000)]);}server.kill('SIGTERM');await rm(profile,{recursive:true,force:true,maxRetries:5});}
