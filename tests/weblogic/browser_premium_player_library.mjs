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
const OUT=process.env.PREMIUM_EVIDENCE_DIR || '/var/tmp/premium-player-evidence';
await mkdir(OUT,{recursive:true});
const pause=ms=>new Promise(r=>setTimeout(r,ms));
async function until(fn,label){const end=Date.now()+20000;while(Date.now()<end){if(await fn())return;await pause(50);}throw new Error(`Timed out: ${label}`);}
const server=spawn(process.execPath,[join(ROOT,'tests/weblogic/immersive_preview.mjs'),'0'],{cwd:ROOT});
let output='';server.stdout.on('data',x=>output+=x);server.stderr.on('data',x=>output+=x);
const profile=await mkdtemp('/var/tmp/astra-adaptive-chrome-');
let chrome,ws,interceptPlay;let passes=0, failures=0;const metrics=[],errors=[],requests=[],transportSnapshots=[];
async function test(name,fn){try{await fn();passes++;console.log(`ok - ${name}`);}catch(e){failures++;console.error(`FAIL - ${name}`,e);}}
try {
await until(()=>output.includes('http://'),'demo startup');
const port=Number(output.match(/0\.0\.0\.0:(\d+)/)[1]);const base=`http://127.0.0.1:${port}`;
chrome=spawn(process.env.CHROME||'/usr/bin/google-chrome',['--headless=new','--no-sandbox','--disable-dev-shm-usage','--use-angle=swiftshader','--enable-unsafe-swiftshader','--remote-debugging-port=0',`--user-data-dir=${profile}`,'about:blank']);
let stderr='';chrome.stderr.on('data',x=>stderr+=x);await until(()=>stderr.includes('DevTools listening'),'chrome');
const debug=stderr.match(/ws:\/\/127.0.0.1:(\d+)/)[1];const targets=await(await fetch(`http://127.0.0.1:${debug}/json/list`)).json();
ws=new WS(targets.find(x=>x.type==='page').webSocketDebuggerUrl);await new Promise(r=>ws.once('open',r));let next=0;const pending=new Map();
ws.on('message',raw=>{const m=JSON.parse(raw);if(pending.has(m.id)){const [r,j]=pending.get(m.id);pending.delete(m.id);m.error?j(m.error):r(m.result);}if(m.method==='Fetch.requestPaused'&&interceptPlay)interceptPlay(m.params);if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails);if(m.method==='Network.requestWillBeSent')requests.push(m.params.request);if(m.method==='Network.responseReceived'&&m.params.response.status>=400)errors.push(m.params.response);});
const cdp=(method,params={})=>new Promise((r,j)=>{const id=++next;pending.set(id,[r,j]);ws.send(JSON.stringify({id,method,params}));});
const ev=async expression=>{const r=await cdp('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw r.exceptionDetails;return r.result.value;};
const wait=expr=>until(()=>ev(expr),expr);
async function viewport(w,h){await cdp('Emulation.setDeviceMetricsOverride',{width:w,height:h,deviceScaleFactor:1,mobile:false});await ev('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');}
async function route(r){await ev(`location.hash='#/${r}'`);await wait(`document.body.dataset.ui008Route==='${r}'`);}
async function view(v,r){await ev(`{let e=document.querySelector('#ui008-view-${r}');e.value='${v}';e.dispatchEvent(new Event('change'));}`);await wait(`document.querySelector('.workspace-scene').dataset.view==='${v}'`);await ev('new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))');}
async function click(sel){await wait(`!document.querySelector('${sel}').disabled`);await ev(`document.querySelector('${sel}').click()`);}
async function shot(name){await pause(400);await wait(`document.querySelector('#toast').hidden`);await pause(200);const s=await cdp('Page.captureScreenshot',{format:'png'});await writeFile(join(OUT,name+'.png'),Buffer.from(s.data,'base64'));}
async function control(sel,scroll=false){if(scroll)await ev(`document.querySelector('${sel}').scrollIntoView({block:'center'})`);return ev(`(()=>{const e=document.querySelector('${sel}'),r=e.getBoundingClientRect(),hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);return {w:r.width,h:r.height,visible:r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth,hit:e===hit||e.contains(hit),hitId:hit?.id,hitClass:hit?.className,top:r.top,bottom:r.bottom}})()`);}
async function assertTransportControl(sel,w,h){
 // Measure the interactive control and evaluate the original predicates in
 // one browser evaluation. Diagnostics never wait, scroll or mutate layout.
 const snapshot=await ev(`(()=>{
  const selector=${JSON.stringify(sel)},e=document.querySelector(selector),r=e.getBoundingClientRect();
  const hit=document.elementFromPoint(r.x+r.width/2,r.y+r.height/2);
  const visible=r.top>=0&&r.bottom<=innerHeight&&r.left>=0&&r.right<=innerWidth;
  const hitTarget=e===hit||e.contains(hit),primary=selector==='#btn-resume';
  const styleKeys=['display','visibility','boxSizing','width','height','minWidth','minHeight','maxWidth','maxHeight',
   'paddingTop','paddingBottom','paddingLeft','paddingRight','borderTopWidth','borderBottomWidth','borderLeftWidth','borderRightWidth',
   'transform','transformOrigin','fontSize','lineHeight','flex','flexGrow','flexShrink','flexBasis','alignItems','alignSelf','justifyContent',
   'gridTemplateColumns','gridTemplateRows','gap','overflow','animationName','animationDuration','animationPlayState','transitionProperty','transitionDuration'];
  const identify=node=>({tag:node.tagName,id:node.id,className:node.className});
  const rectOf=node=>{const box=node.getBoundingClientRect();return {width:box.width,height:box.height,top:box.top,bottom:box.bottom,left:box.left,right:box.right};};
  const describe=node=>{const s=getComputedStyle(node);return {...identify(node),rect:rectOf(node),
   offsetWidth:node.offsetWidth,offsetHeight:node.offsetHeight,clientWidth:node.clientWidth,clientHeight:node.clientHeight,
   styles:Object.fromEntries(styleKeys.map(key=>[key,s[key]]))};};
  const parents=[];for(let node=e.parentElement;node;node=node.parentElement){parents.push(describe(node));if(node===document.body)break;}
  const ancestors=new Set([e]);for(let node=e.parentElement;node;node=node.parentElement)ancestors.add(node);
  const animations=document.getAnimations().filter(a=>ancestors.has(a.effect?.target)).map(a=>({
   target:identify(a.effect.target),playState:a.playState,currentTime:a.currentTime,
   timing:a.effect.getComputedTiming(),keyframes:a.effect.getKeyframes()}));
  const vv=visualViewport;
  return {assertion:{name:'Player '+${w}+'x'+${h}+': transport fits, touch and hit testing',selector,
    expressions:['c.visible&&c.hit','c.w>=44&&c.h>=44',...(primary?['c.h>=56&&c.w>=56']:[])],
    expected:{secondaryMinimum:44,primaryMinimum:primary?56:null},
    actual:{w:r.width,h:r.height,visible,hit:hitTarget},
    predicates:{visibleAndHit:visible&&hitTarget,secondaryTouch:r.width>=44&&r.height>=44,primaryTouch:!primary||(r.height>=56&&r.width>=56)}},
   viewport:{innerWidth,innerHeight,devicePixelRatio,visualViewport:vv?{width:vv.width,height:vv.height,scale:vv.scale,offsetLeft:vv.offsetLeft,offsetTop:vv.offsetTop}:null,
    scrollX,scrollY,route:document.body.dataset.ui008Route,timestamp:new Date().toISOString(),performanceTime:performance.now()},
   control:{...describe(e),connected:e.isConnected,hidden:e.hidden,disabled:e.disabled,
    aria:Object.fromEntries([...e.attributes].filter(a=>a.name.startsWith('aria-')).map(a=>[a.name,a.value])),hitElement:hit?identify(hit):null},
   context:{parents,playerState:document.querySelector('#player-state')?.textContent,
    performanceMode:document.body.dataset.ui008Performance,sheets:[...document.querySelectorAll('[role="dialog"]')].map(node=>({...identify(node),hidden:node.hidden,ariaHidden:node.getAttribute('aria-hidden')})),
    documentReadyState:document.readyState,fontsStatus:document.fonts.status,animations}};
 })()`);
 transportSnapshots.push(snapshot);
 const p=snapshot.assertion.predicates;
 if(!p.visibleAndHit||!p.secondaryTouch||!p.primaryTouch){
  const stem='transport-failure-'+w+'x'+h+'-'+sel.replace(/[^a-z0-9]+/gi,'-');
  await writeFile(join(OUT,stem+'.json'),JSON.stringify(snapshot,null,2)+'\n');
  console.error('TRANSPORT_GATE_FAILURE',JSON.stringify(snapshot));
  const screenshot=await cdp('Page.captureScreenshot',{format:'png'});
  await writeFile(join(OUT,stem+'.png'),Buffer.from(screenshot.data,'base64'));
 }
 assert(p.visibleAndHit,sel+JSON.stringify(snapshot));
 assert(p.secondaryTouch,sel+JSON.stringify(snapshot));
 if(sel==='#btn-resume')assert(p.primaryTouch,sel+JSON.stringify(snapshot));
}
await cdp('Page.enable');await cdp('Runtime.enable');await cdp('Network.enable');
await cdp('Page.addScriptToEvaluateOnNewDocument',{source:`window.__life={observers:0,paint:{}};window.__listeners=[];Object.defineProperty(window.__life,"listeners",{enumerable:true,get(){return window.__listeners.filter(x=>x===window||x===document||x.isConnected).length}});
const add=EventTarget.prototype.addEventListener;EventTarget.prototype.addEventListener=function(...a){if(!(this instanceof AbortSignal))window.__listeners.push(this);return add.apply(this,a)};
const RO=ResizeObserver;window.ResizeObserver=class extends RO{constructor(cb){super(cb);window.__life.observers++}};
for(const C of [WebGLRenderingContext,WebGL2RenderingContext])for(const method of ['drawArrays','drawElements']){const original=C.prototype[method];C.prototype[method]=function(...args){const id=this.canvas.id;window.__life.paint[id]=(window.__life.paint[id]||0)+1;return original.apply(this,args)}};
const clear=CanvasRenderingContext2D.prototype.clearRect;CanvasRenderingContext2D.prototype.clearRect=function(...a){const id=this.canvas.id;window.__life.paint[id]=(window.__life.paint[id]||0)+1;return clear.apply(this,a)};`});

await viewport(1024,768);await cdp('Page.navigate',{url:base+'/ui/#/library'});await wait(`!!document.querySelector('.btn-song')`);
await test('Library selection is not authoritative playback; Play sends one correct command',async()=>{
 await click('.btn-song');assert.equal(await ev(`document.querySelector('.song-playing').hidden`),true);
 const before=requests.filter(x=>x.url.endsWith('/playback/play')).length;
 await click('.song-play');await wait(`document.body.dataset.ui008Route==='player'`);
 const posts=requests.filter(x=>x.url.endsWith('/playback/play'));assert.equal(posts.length,before+1);assert.equal(JSON.parse(posts.at(-1).postData).song_id,'demo-study');
 assert.equal(await ev(`document.querySelector('#player-state').textContent`),'Playing');
});
await test('Selection of another song never claims playback or changes active title',async()=>{
 await route('library');await click('.song-item:nth-child(2) .btn-song');
 assert.equal(await ev(`document.querySelector('.song-item:nth-child(2)').getAttribute('aria-selected')`),'true');
 assert.equal(await ev(`document.querySelector('.song-item:nth-child(2) .song-playing').hidden`),true);
 assert.equal(await ev(`document.querySelector('.song-item:first-child .song-playing').textContent`),'Now playing');
 await route('player');assert.equal(await ev(`document.querySelector('#player-title').textContent`),'Evening Study');
 await route('library');await click('.btn-song');await route('player');
});
await test('Player has Now Playing, real duration, no teaching controls or scenes',async()=>{
 assert.equal(await ev(`document.querySelector('#ui008-view-player')`),null);
 assert.equal(await ev(`document.querySelector('#player .workspace-scene, #player .learn-target, #ui008-player-sheet .visual-mode-switch')`),null);
 assert(await ev(`document.querySelector('.now-playing').getBoundingClientRect().height>0`));
 assert.equal(await ev(`document.querySelector('#player-duration').textContent`),'1:04');
 assert.equal(await ev(`document.querySelector('#ui008-workspace-scene').hidden`),true);
});
await test('Pause / Resume / Stop exactly once and Library badge follows Core',async()=>{
 for(const [button,state,endpoint] of [['#btn-pause','Paused','pause'],['#btn-resume','Playing','resume'],['#btn-stop','Stopped','stop']]){
  const before=requests.filter(x=>x.url.endsWith('/playback/'+endpoint)).length;await click(button);await wait(`document.querySelector('#player-state').textContent==='${state}'`);assert.equal(requests.filter(x=>x.url.endsWith('/playback/'+endpoint)).length,before+1);
  await route('library');const badge=await ev(`({hidden:document.querySelector('.song-playing').hidden,text:document.querySelector('.song-playing').textContent})`);assert.equal(badge.hidden,state==='Stopped');if(state!=='Stopped')assert.equal(badge.text,state==='Playing'?'Now playing':state);await route('player');
 }
});
await test('Learn preserves all views and preference across Player and Piano; renderers idle in Player',async()=>{
 await route('learn');await click('#btn-practice');
 for(const v of ['falling','piano','hands','sheet']){await view(v,'learn');if(v==='hands'){await wait(`window.__pianoHands?.info()?.assetState==='ready'`);await wait(`window.__life.paint['hands-canvas']>0`);}assert.equal(await ev(`[...document.querySelectorAll('.scene-view')].filter(e=>!e.hidden).length`),1);}
 const learning=await(await fetch(base+'/api/v1/learning')).json();const n=requests.filter(x=>x.method==='POST'||x.method==='PUT').length;
 for(let i=0;i<4;i++){await route('player');await route('piano');await route('learn');assert.equal(await ev(`document.querySelector('#ui008-view-learn').value`),'sheet');}
 assert.deepEqual(await(await fetch(base+'/api/v1/learning')).json(),learning);assert.equal(requests.filter(x=>x.method==='POST'||x.method==='PUT').length,n);
 await route('player');await pause(400);const paint=await ev('JSON.stringify(window.__life.paint)');await pause(1200);assert.equal(await ev('JSON.stringify(window.__life.paint)'),paint);
 await route('library');await click('.song-play');await pause(300);assert.equal(await ev('document.body.dataset.ui008Route'),'library');assert.equal(requests.filter(x=>x.method==='POST'||x.method==='PUT').length,n,'mutex denies without command');assert(await ev(`document.querySelector('#library-feedback').textContent.includes('unavailable')`));
 await route('learn');await click('#btn-practice-stop');await route('player');await click('#btn-play');await click('#btn-pause');
});
await test('Library pending duplicate is suppressed; Core denial stays in Library; recovery uses one fresh command',async()=>{
 await route('player');await click('#btn-stop');await route('library');
 let held=null;interceptPlay=p=>held=p;
 await cdp('Fetch.enable',{patterns:[{urlPattern:'*/api/v1/playback/play',requestStage:'Request'}]});
 const before=requests.filter(x=>x.url.endsWith('/playback/play')).length;
 await click('.song-play');await until(()=>held,'intercepted command');
 await ev(`document.querySelector('.song-play').click()`);await pause(100);
 assert.equal(requests.filter(x=>x.url.endsWith('/playback/play')).length,before+1);
 assert.equal(await ev('document.body.dataset.ui008Route'),'library','do not navigate before result');
 assert(await ev(`document.querySelector('.song-play').disabled`));
 await cdp('Fetch.fulfillRequest',{requestId:held.requestId,responseCode:409,responseHeaders:[{name:'content-type',value:'application/json'}],body:Buffer.from(JSON.stringify({error:{code:'activity_conflict',message:'Practice is active'}})).toString('base64')});
 await wait(`!document.querySelector('.song-play').disabled`);await cdp('Fetch.disable');interceptPlay=null;
 assert.equal(await ev('document.body.dataset.ui008Route'),'library');assert(await ev(`document.querySelector('#library-feedback').textContent.length>0`));assert.notEqual(await ev(`document.querySelector('#player-state').textContent`),'Playing');
 const expected=errors.findIndex(x=>x.status===409&&x.url.endsWith('/playback/play'));assert(expected>=0);errors.splice(expected,1);
 await wait(`document.querySelector('#toast').hidden`);await click('.song-play');await wait(`document.body.dataset.ui008Route==='player'`);await click('#btn-pause');
 assert.equal(requests.filter(x=>x.url.endsWith('/playback/play')).length,before+2);
});
const MATRIX=[[320,568],[430,932],[1280,720],[2560,1440],[338,654],[390,844],[768,1024],[1024,768],[1440,900],[1920,1080],[844,390],[899,900],[900,900]];
for(const [w,h] of MATRIX)await test(`Player ${w}x${h}: transport fits, touch and hit testing`,async()=>{
 await viewport(w,h);await route('player');
 await ev(`Promise.all(document.querySelector('#player').getAnimations().map(animation=>animation.finished))`);
 const m=await ev(`(()=>{let l=document.querySelector('.layout'),a=document.querySelector('.piano-artwork').getBoundingClientRect();return {overflow:l.scrollHeight-l.clientHeight,doc:document.documentElement.scrollWidth-innerWidth,art:{w:a.width,h:a.height},title:document.querySelector('#player-title').textContent}})()`);metrics.push({viewport:[w,h],...m});assert(m.overflow<=1,JSON.stringify(m));assert(m.doc<=1);assert(m.art.w>=120);
 for(const sel of ['#btn-resume','#btn-stop','#ui008-performance-btn','#ui008-player-options','.player-library-link'])await assertTransportControl(sel,w,h);
 if([390,768,1440].includes(w)){await shot('player-'+w);await route('library');await shot('library-'+w);}
});
await test('Learn screenshot and 200% text preserve reachable controls',async()=>{
 await viewport(1024,768);await route('player');await click('#btn-stop');await route('learn');await view('falling','learn');await click('#btn-practice');await shot('learn-preserved');await click('#btn-practice-stop');
 await route('player');await wait(`document.querySelector('#toast').hidden`);await viewport(338,654);await ev(`document.documentElement.style.fontSize='200%'`);
 for(const sel of ['#btn-play','#btn-stop','#ui008-player-options']){const c=await control(sel,true);assert(c.visible&&c.hit,sel+JSON.stringify(c));}assert(await ev('document.documentElement.scrollWidth<=innerWidth+1'));await ev(`document.documentElement.style.fontSize=''`);
});
await test('No browser exceptions or unexpected network errors',async()=>{assert.deepEqual(errors,[]);});
await writeFile(join(OUT,'dimensions.json'),JSON.stringify(metrics,null,2)+'\n');
await writeFile(join(OUT,'transport-assertions.json'),JSON.stringify(transportSnapshots,null,2)+'\n');
console.log(`PASS ${passes}; FAIL ${failures}`);process.exitCode=failures?1:0;
} finally {if(ws)ws.close();if(chrome){chrome.kill('SIGTERM');await Promise.race([new Promise(r=>chrome.once('exit',r)),pause(2000)]);}server.kill('SIGTERM');await rm(profile,{recursive:true,force:true,maxRetries:5});}
