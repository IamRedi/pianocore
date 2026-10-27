import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {FingeringReplica,handsStatusModel} from '../../src/piano_core/web/static/app.js';
let passed=0;
function test(name,fn){fn(); console.log(`ok - ${name}`);passed++;}
test('422 reason stays visible during the retained same-source retry',()=>{
 const r=new FingeringReplica();const t=r.beginRequest('a','source-a');
 assert.equal(r.noteFailure(t,'fingering_unsupported'),true);
 assert.equal(r.state,'error');assert.equal(r.error,'fingering_unsupported');
 assert.ok(r.beginRequest('a','source-a'));assert.equal(r.error,'fingering_unsupported');
 assert.equal(handsStatusModel({rendererStatus:'ready',planState:r.state,planError:r.error}),'unsupported');
});
test('a new source clears unsupported and stale 422 cannot replace it',()=>{
 const r=new FingeringReplica();const old=r.beginRequest('a','v1');r.noteFailure(old,'fingering_unsupported');
 const fresh=r.beginRequest('a','v2');assert.ok(fresh);assert.equal(r.error,null);
 assert.equal(r.noteFailure(old,'fingering_unsupported'),false);assert.equal(r.state,'loading');
 r.reset();assert.equal(r.error,null);assert.ok(r.beginRequest('a','v1'));
});
test('transient failures remain retryable and are not mislabeled unsupported',()=>{
 const r=new FingeringReplica();const t=r.beginRequest('a','v1');r.noteFailure(t,'internal_error');
 assert.equal(r.error,'fingering_unavailable');assert.equal(r.state,'error');assert.ok(r.beginRequest('a','v1'));
});
test('a successful retained retry clears the unsupported message',()=>{
 const fixture=JSON.parse(readFileSync(new URL('./fixtures/bass_melody.json',import.meta.url),'utf8'));
 const r=new FingeringReplica();const {song_id,source_version}=fixture.score;
 const t=r.beginRequest(song_id,source_version);r.noteFailure(t,'fingering_unsupported');
 const retry=r.beginRequest(song_id,source_version);
 assert.equal(r.accept(fixture.fingering,retry,fixture.score),true);
 assert.equal(r.error,null);assert.equal(r.state,'ready');
 assert.equal(handsStatusModel({rendererStatus:'ready',planState:r.state,planError:r.error}),'ready');
});
console.log(`${passed} passed`);
