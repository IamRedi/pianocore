// Presentation regression: the complete keyboard must remain framed without a plan.
import assert from 'node:assert/strict';
import * as THREE from '../../src/piano_core/web/static/vendor/three.webgpu.js';
import {HandRenderer, fullCameraPose} from '../../src/piano_core/web/static/handrender.js';
let passed=0;
for (const mode of ['FULL','FOLLOW']) {
  for (const [width,height] of [[1333,449],[390,844],[844,390],[1024,768],[1440,900]]) {
    const r = new HandRenderer({canvas:{},geometry:{centerFractions:new Map()}});
    r.status='ready'; r.quality='BATTERY'; r.cameraMode=mode;
    r._camera=new THREE.PerspectiveCamera(40,1,0.02,6);
    r._renderer={setPixelRatio(){},setSize(){},render(){}};
    r._scene=new THREE.Scene();
    r.resize(width,height); r.render(null);
    const expected=fullCameraPose(width/height);
    assert.deepEqual(r._camera.position.toArray(),Object.values(expected.position),`${mode} ${width}x${height} null plan framing`);
    // FOLLOW after a real focus must return to the same full keyboard fit.
    r._updateCamera({hands:{LEFT:{root:{x:-0.2}},RIGHT:{root:{x:0.2}}}});
    r.render(null);
    assert.deepEqual(r._camera.position.toArray(),Object.values(expected.position),`${mode} stale focus cleared`);
    assert.ok(Object.values(r.handSource).every(x=>x==='hidden'));
    console.log(`ok - ${mode} no-plan camera and resize ${width}x${height}`);passed++;
  }
}
console.log(`${passed} passed`);
