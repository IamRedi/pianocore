// R5 controlled real Core + Chromium. No hardware or hosted fallback.
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {resolve} from 'node:path';
import {writeFile} from 'node:fs/promises';
import {BrowserDriver,until} from '../release_harness/browser_driver.mjs';
import {assertLessonDetail,detailSnapshot} from '../release_harness/lesson_assertions.mjs';
const root=resolve(import.meta.dirname,'../..');
const core=spawn(process.env.PIANO_CORE_TEST_PYTHON||'/var/tmp/piano-premium-studio-venv/bin/python',['-m','tests.release_core_fixture'],{cwd:root,env:{...process.env,PYTHONPATH:root+'/src'}});
let output='',stderr='';core.stdout.on('data',x=>output+=x);core.stderr.on('data',x=>stderr+=x);
let driver;let passes=0;let primary;let base;let beforeFds;
const requests=[];
const test=async(name,fn)=>{await fn();passes++;console.log('ok - '+name);};
try{
 await until(()=>output.includes('\n')||core.exitCode!==null,'disposable Core startup',10000);
 assert.equal(core.exitCode,null,stderr);const info=JSON.parse(output.split('\n')[0]);
 base='http://127.0.0.1:'+info.port;
 beforeFds=(await(await fetch(base+'/api/v1/_test/resources')).json()).actual_fds;
 driver=await new BrowserDriver().start();
 driver.onEvent=m=>{if(m.method==='Network.requestWillBeSent')requests.push({url:m.params.request.url,method:m.params.request.method});};
 await driver.send('Emulation.setDeviceMetricsOverride',{width:1180,height:820,deviceScaleFactor:1,mobile:false});
 await driver.send('Page.navigate',{url:base+'/ui/#/learn'});
 await driver.wait("document.querySelectorAll('.learn-lesson-btn').length===2");
 async function select(lesson,selector=`.learn-lesson-btn[data-lesson-id="${lesson.lesson_id}"]`){
  const seq=driver.sequence;await driver.click(selector);
  let receipt;
  await until(()=>{receipt=driver.responses.find(r=>r.sequence>seq&&r.body.lesson_id===lesson.lesson_id);return !!receipt;},'actual selected detail response');
  await driver.wait(`!document.getElementById('ui008-lesson-detail').hidden&&document.getElementById('ui008-lesson-detail-title').textContent===${JSON.stringify((receipt.body.progress.completions>0?'✓ ':'')+lesson.title)}`);
  return assertLessonDetail(receipt,lesson,await driver.ev(detailSnapshot),seq);
 }
 const [first,second]=info.lessons;
 await test('real Core never-completed lesson no decoration',async()=>assert.equal((await select(first)).completed,false));
 await test('real Core completed lesson correct decoration and old R4 assertion FAIL',async()=>{
  assert.equal((await select(second)).completed,true);
  assert.throws(()=>assert.equal('✓ '+second.title,second.title));
 });
 await test('real browser package selections never request Gold song detail',async()=>{
  await driver.painted();
  const crossed=requests.filter(r=>/\/api\/v1\/library\/songs\/package(?:%3A|:)/i.test(r.url));
  assert.deepEqual(crossed,[],'Learn package identity crossed into Gold metadata namespace');
 });
 await test('arbitrary matching page text cannot falsely pass scoped wrong detail',async()=>{
  const receipt=driver.responses.at(-1);
  await driver.ev(`(()=>{const e=document.createElement('div');e.textContent=${JSON.stringify('✓ '+second.title)};document.body.append(e);document.getElementById('ui008-lesson-detail-title').textContent='Wrong detail'})()`);
  // Actual DOM snapshot, not the injected page-wide matching text.
  const snapshot=await driver.ev(detailSnapshot);
  assert.throws(()=>assertLessonDetail(receipt,second,snapshot,receipt.sequence-1));
 });
 await test('persisted progress changes during test then fresh navigation renders completion',async()=>{
  await select(first);const response=await fetch(base+'/api/v1/_test/complete',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({package_id:first.package_id})});assert.equal(response.status,200);
  // The rendered previous snapshot remains correct until a new selected response.
  assert.equal((await driver.ev(detailSnapshot)).title,first.title);
  assert.equal((await select(first)).completed,true);
 });
 await test('reopen after real reload preserves lesson identity and SQLite completion',async()=>{
  await driver.send('Page.reload',{ignoreCache:true});await driver.wait("document.querySelectorAll('.learn-lesson-btn').length===2");
  assert.equal((await select(first)).completed,true);
 });
 await test('actual previous next user navigation selects canonical neighbor',async()=>{
  assert.equal((await select(second,'#ui008-lesson-next')).lesson_id,second.lesson_id);
  assert.equal((await select(first,'#ui008-lesson-prev')).lesson_id,first.lesson_id);
 });
 await test('real trusted search and select events exercise release user helpers',async()=>{
  await driver.text('#ui008-learn-search',second.title);
  await driver.wait(`document.querySelectorAll('.learn-lesson-btn').length===1&&document.querySelector('.learn-lesson-btn').dataset.lessonId===${JSON.stringify(second.lesson_id)}`);
  await driver.choose('#studio-lesson-mode','FLOW');
  await driver.choose('#studio-lesson-mode','WAIT_FOR_NOTE');
 });
 await test('owned real SSE and blocked in-flight HTTP active before teardown',async()=>{
  await driver.ev(`(()=>{window.testStream=new EventSource('/api/v1/live-notes/stream');window.testSlow=fetch('/api/v1/_test/slow').catch(()=>{});return true})()`);
  await until(()=>[...driver.active.values()].some(r=>r.type==='EventSource')&&[...driver.active.values()].some(r=>r.url.endsWith('/_test/slow')),'owned live stream and request');
  assert((await(await fetch(base+'/api/v1/_test/resources')).json()).actual_fds>beforeFds);
 });
}catch(e){primary=e;console.error(e);}
finally{
 if(driver){const cleanup=await driver.close();console.log('cleanup '+JSON.stringify(cleanup));if(cleanup.status!=='PASS')primary ||= Error(cleanup.error);else{passes++;console.log('ok - owned browser requests and process cleanup');}}
 if(base){
  await fetch(base+'/api/v1/_test/release',{method:'POST'});
  try{
   await until(async()=>{const r=await(await fetch(base+'/api/v1/_test/resources')).json();return r.actual_fds<=beforeFds&&r.live_subscribers===0;},'real server FD and subscriber recovery after owned clients closed',5000);
   passes++;console.log('ok - actual server FD and SSE subscriber recovery');
   const failed=await new BrowserDriver().start();
   try{await failed.send('Page.navigate',{url:base+'/ui/#/learn'});await failed.wait("document.querySelectorAll('.learn-lesson-btn').length===2");throw Error('injected primary functional failure');}
   catch(e){assert.equal(e.message,'injected primary functional failure');}
   finally{assert.equal((await failed.close()).status,'PASS');}
   passes++;console.log('ok - primary functional FAIL retains normal owned cleanup');
  }catch(e){primary ||= e;console.error(e);}
 }
 core.kill('SIGTERM');await until(()=>core.exitCode!==null||core.signalCode!==null,'disposable Core exit',10000);
}
console.log(JSON.stringify({passes,failures:primary?1:0}));
if(process.env.SOURCE_ISOLATION_EVIDENCE)await writeFile(process.env.SOURCE_ISOLATION_EVIDENCE,JSON.stringify({passes,failures:primary?1:0,primary_failure:primary?String(primary):null,requests},null,2)+'\n');
if(primary)process.exitCode=1;
