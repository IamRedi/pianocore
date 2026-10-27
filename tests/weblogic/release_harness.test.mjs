import assert from 'node:assert/strict';
import {assertLessonDetail} from '../release_harness/lesson_assertions.mjs';
import {BrowserDriver} from '../release_harness/browser_driver.mjs';
import {mkdtemp} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
const body={lesson_id:'one',package_id:'pkg',course_id:'course',title:'Canonical',summary:'summary',composer:'Synthetic Author',level:0,difficulty:'easy',duration_seconds:8,
 license:{name:'CC0'},metrics:{note_count:4},progress_available:true,
 progress:{attempts:1,completions:1,full_lesson_completions:1},previous_lesson_id:null,next_lesson_id:'two'};
const receipt=()=>({sequence:2,status:200,url:'http://127.0.0.1/api/v1/learn/lessons/one',body:structuredClone(body)});
const view=()=>({visible:true,title:'✓ Canonical',summary:'summary',subtitle:'Synthetic Author · L0 First Notes · easy · 8s',license:'CC0',metrics:'4 notes',
 progress:'1 practice run · 1 complete',previous:{id:'',disabled:true},next:{id:'two',disabled:false}});
let passes=0;
function test(name,fn){fn();passes++;console.log('ok - '+name);}
test('original R4 assertion fails intentional completed decoration',()=>assert.throws(()=>assert.equal(view().title,body.title)));
test('completed detail validates persisted delivered snapshot',()=>assert.equal(assertLessonDetail(receipt(),body,view(),1).completed,true));
test('never completed detail requires no decoration',()=>{const r=receipt(),v=view();r.body.progress={attempts:0,completions:0,full_lesson_completions:0};v.title='Canonical';v.progress='Not practiced yet';assert.equal(assertLessonDetail(r,body,v,1).completed,false);});
for(const [name,mutate] of [
 ['arbitrary elsewhere matching text cannot replace scoped title',(r,v)=>{v.title='Wrong';v.arbitraryText='✓ Canonical';}],
 ['wrong package identity',(r)=>r.body.package_id='wrong'],
 ['wrong selected lesson',(r)=>r.body.lesson_id='two'],
 ['stale response',(r)=>r.sequence=1],
 ['wrong response URL',(r)=>r.url='http://localhost/api/v1/learn/lessons/two'],
 ['hidden detail',(r,v)=>v.visible=false],
 ['missing decoration',(r,v)=>v.title='Canonical'],
 ['extra arbitrary prefix',(r,v)=>v.title='Other ✓ Canonical'],
 ['wrong composer subtitle',(r,v)=>v.subtitle='Wrong composer'],
 ['wrong summary',(r,v)=>v.summary='Other'],
 ['wrong neighbor navigation',(r,v)=>v.next.id='other'],
 ['unavailable authoritative progress',(r)=>r.body.progress_available=false],
 ['racing progress cannot override rendered snapshot',(r,v)=>v.progress='2 practice runs · 2 complete'],
]) test(name,()=>{const r=receipt(),v=view();mutate(r,v);assert.throws(()=>assertLessonDetail(r,body,v,1));});
async function fakeDriver({hung=false,task=false,neverExit=false}={}){
 const d=new BrowserDriver({cleanupDeadline:500});d.profile=await mkdtemp(join(tmpdir(),'release-cleanup-control-'));
 d.ws={readyState:1,terminate(){}};d.ev=async()=>true;
 d.active.set('unresolved-id',{url:'http://local/api/v1/fixture',response:{status:200}});
 d.send=async method=>{if(method==='Browser.close'&&!hung)d.exit={code:0,signal:null};};
 d.chrome={kill(signal){if(!neverExit)d.exit={code:null,signal};}};
 if(task)d.tasks.add(new Promise(()=>{}));return d;
}
// A response that finishes after click still belongs to its pre-click request.
{
 const d=new BrowserDriver();d.send=async()=>({body:JSON.stringify(body)});
 d.message({method:'Network.requestWillBeSent',params:{requestId:'old',request:{url:'http://local/api/v1/learn/lessons/one'},type:'Fetch'}});
 const clickBarrier=d.sequence;
 d.message({method:'Network.responseReceived',params:{requestId:'old',response:{status:200,url:'http://local/api/v1/learn/lessons/one'}}});
 d.message({method:'Network.loadingFinished',params:{requestId:'old'}});
 await Promise.all([...d.tasks]);
 assert.throws(()=>assertLessonDetail(d.responses[0],body,view(),clickBarrier));
 passes++;console.log('ok - prior in-flight same-lesson response cannot impersonate new navigation');
}
for(const [name,options,expected] of [
 ['missing Network terminal event retained until actual process destruction',{},'PASS'],
 ['hung owned Chrome cannot pass cleanup',{hung:true},'FAIL'],
 ['ignored termination never causes unbounded cleanup wait',{hung:true,neverExit:true},'FAIL'],
 ['unsettled receipt task bounded and cannot pass cleanup',{task:true},'FAIL'],
]){
 const d=await fakeDriver(options),r=await d.close();assert.equal(r.status,expected);
 if(expected==='PASS'){assert.equal(r.requests_retired_by_process_exit[0].requestId,'unresolved-id');assert.equal(r.requests_retired_by_process_exit[0].disposition,'owner_process_destroyed');}
 else assert.equal(r.forced_termination,true);
 passes++;console.log('ok - '+name);
}
console.log(JSON.stringify({passes,failures:0}));
