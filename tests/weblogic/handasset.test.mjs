// PIANO-UI-007 Tier-A deterministic rig-adapter tests (Node, no DOM/GPU).
import assert from "node:assert/strict";
import * as THREE from "../../src/piano_core/web/static/vendor/three.webgpu.js";
import {
  TIER_A_BONE_MAP,
  buildTierAHandDefinitions,
  isFiniteAssetPose,
  smoothSkinWeights,
} from "../../src/piano_core/web/static/handasset.js";
import { readFileSync } from "node:fs";
import {
  HAND_SOURCES,
  HandRenderer,
  validateRigDefinition,
} from "../../src/piano_core/web/static/handrender.js";

let passes = 0;
function test(name, fn) {
  try {
    fn();
    passes += 1;
    console.log(`ok - ${name}`);
  } catch (error) {
    console.error(`FAIL - ${name}`);
    throw error;
  }
}

function buildSide(hand) {
  const names = TIER_A_BONE_MAP[hand];
  const armature = new THREE.Group();
  armature.name = names.armature;
  const mesh = new THREE.Group();
  mesh.name = names.mesh;
  const wrist = new THREE.Bone();
  wrist.name = names.wrist;
  armature.add(wrist);
  for (let finger = 1; finger <= 5; finger += 1) {
    let parent = wrist;
    for (let joint = 0; joint < 3; joint += 1) {
      const bone = new THREE.Bone();
      bone.name = names.fingers[finger][joint];
      bone.position.set((finger - 3) * 0.012, joint === 0 ? 0 : 0, -0.035);
      parent.add(bone);
      parent = bone;
    }
    const end = new THREE.Bone();
    end.name = `${names.fingers[finger][2].replace(hand === "LEFT" ? ".L" : ".R", "")}${hand === "LEFT" ? ".L_end" : ".R_end"}`;
    end.position.set(0, 0, -0.025);
    parent.add(end);
  }
  return [armature, mesh];
}

function sceneFixture() {
  const scene = new THREE.Scene();
  scene.add(...buildSide("LEFT"), ...buildSide("RIGHT"));
  return scene;
}

function poseFixture() {
  const fingers = {};
  for (let finger = 1; finger <= 5; finger += 1) {
    const x = (finger - 3) * 0.018;
    fingers[finger] = {
      base: { x, y: 0.07, z: -0.04 },
      elbow: { x, y: 0.045, z: -0.085 },
      tip: { x, y: 0.012, z: -0.14 },
      planeYaw: 0,
    };
  }
  return { wrist: { x: 0.1, y: 0.06, z: 0.01, yaw: 0.2 }, fingers };
}

function transformSnapshot(definition) {
  const snapshot = [];
  definition.root.traverse((node) => {
    snapshot.push({
      name: node.name,
      position: node.position.toArray(),
      quaternion: node.quaternion.toArray(),
      scale: node.scale.toArray(),
    });
  });
  return snapshot;
}

test("explicit mapping covers wrist and all five 3-joint chains", () => {
  for (const hand of ["LEFT", "RIGHT"]) {
    assert.ok(TIER_A_BONE_MAP[hand].wrist);
    assert.deepEqual(Object.keys(TIER_A_BONE_MAP[hand].fingers), ["1", "2", "3", "4", "5"]);
    for (let finger = 1; finger <= 5; finger += 1) {
      assert.equal(TIER_A_BONE_MAP[hand].fingers[finger].length, 3);
    }
  }
});

test("both generated sides satisfy the renderer-independent rig contract", () => {
  const definitions = buildTierAHandDefinitions(THREE, sceneFixture());
  assert.equal(validateRigDefinition(definitions.LEFT, { hand: "LEFT" }), true);
  assert.equal(validateRigDefinition(definitions.RIGHT, { hand: "RIGHT" }), true);
  definitions.dispose();
});

test("finite pose drives wrist and every joint without NaN or Infinity", () => {
  const definitions = buildTierAHandDefinitions(THREE, sceneFixture());
  const pose = poseFixture();
  assert.equal(definitions.LEFT.applyPose(pose), true);
  assert.equal(definitions.RIGHT.applyPose(pose), true);
  for (const definition of [definitions.LEFT, definitions.RIGHT]) {
    definition.root.traverse((node) => {
      for (const value of [
        node.position.x, node.position.y, node.position.z,
        node.quaternion.x, node.quaternion.y, node.quaternion.z, node.quaternion.w,
      ]) assert.equal(Number.isFinite(value), true, `${node.name}: ${value}`);
    });
  }
  definitions.dispose();
});

test("NaN and Infinity fail closed before transforms mutate", () => {
  const definitions = buildTierAHandDefinitions(THREE, sceneFixture());
  const pose = poseFixture();
  pose.fingers[3].tip.x = Number.NaN;
  assert.equal(isFiniteAssetPose(pose), false);
  assert.equal(definitions.LEFT.applyPose(pose), false);
  pose.fingers[3].tip.x = Number.POSITIVE_INFINITY;
  assert.equal(definitions.RIGHT.applyPose(pose), false);
  definitions.dispose();
});

test("optional wrist yaw accepts only nullish defaults or finite numbers", () => {
  const definitions = buildTierAHandDefinitions(THREE, sceneFixture());
  for (const yaw of [null, undefined, 0, 0.375, -0.625]) {
    const pose = poseFixture();
    pose.wrist.yaw = yaw;
    assert.equal(isFiniteAssetPose(pose), true, `wrist yaw ${String(yaw)}`);
    assert.equal(definitions.LEFT.applyPose(pose), true, `apply wrist yaw ${String(yaw)}`);
  }
  definitions.dispose();
});

test("non-finite wrist yaw rejects without partial mutation", () => {
  const definitions = buildTierAHandDefinitions(THREE, sceneFixture());
  assert.equal(definitions.LEFT.applyPose(poseFixture()), true);
  const before = transformSnapshot(definitions.LEFT);
  for (const yaw of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
    const pose = poseFixture();
    pose.wrist.yaw = yaw;
    assert.equal(isFiniteAssetPose(pose), false, `wrist yaw ${String(yaw)}`);
    assert.equal(definitions.LEFT.applyPose(pose), false, `apply wrist yaw ${String(yaw)}`);
    assert.deepEqual(transformSnapshot(definitions.LEFT), before, "invalid wrist yaw mutated rig");
  }
  definitions.dispose();
});

test("every finger plane yaw rejects non-finite values before any mutation", () => {
  const definitions = buildTierAHandDefinitions(THREE, sceneFixture());
  assert.equal(definitions.RIGHT.applyPose(poseFixture()), true);
  const before = transformSnapshot(definitions.RIGHT);
  for (let finger = 1; finger <= 5; finger += 1) {
    for (const yaw of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
      const pose = poseFixture();
      pose.fingers[finger].planeYaw = yaw;
      assert.equal(isFiniteAssetPose(pose), false, `finger ${finger} yaw ${String(yaw)}`);
      assert.equal(definitions.RIGHT.applyPose(pose), false, `apply finger ${finger} yaw ${String(yaw)}`);
      assert.deepEqual(
        transformSnapshot(definitions.RIGHT),
        before,
        `invalid finger ${finger} yaw mutated rig`
      );
    }
  }
  definitions.dispose();
});

test("every finger plane yaw accepts nullish defaults and finite values", () => {
  const definitions = buildTierAHandDefinitions(THREE, sceneFixture());
  const pose = poseFixture();
  const values = [null, undefined, 0, 0.25, -0.25];
  for (let finger = 1; finger <= 5; finger += 1) {
    pose.fingers[finger].planeYaw = values[finger - 1];
  }
  assert.equal(isFiniteAssetPose(pose), true);
  assert.equal(definitions.LEFT.applyPose(pose), true);
  definitions.dispose();
});

test("continuous planner input stays below the no-teleport threshold", () => {
  const definitions = buildTierAHandDefinitions(THREE, sceneFixture());
  const poseA = poseFixture();
  assert.equal(definitions.LEFT.applyPose(poseA), true);
  definitions.LEFT.root.updateMatrixWorld(true);
  const before = new Map();
  definitions.LEFT.root.traverse((node) => {
    if (node.isBone) before.set(node.name, node.getWorldPosition(new THREE.Vector3()));
  });
  const poseB = structuredClone(poseA);
  poseB.wrist.x += 0.004;
  for (let finger = 1; finger <= 5; finger += 1) {
    for (const point of ["base", "elbow", "tip"]) poseB.fingers[finger][point].x += 0.004;
  }
  assert.equal(definitions.LEFT.applyPose(poseB), true);
  definitions.LEFT.root.updateMatrixWorld(true);
  let maxBoneTravel = 0;
  definitions.LEFT.root.traverse((node) => {
    if (!node.isBone || !before.has(node.name)) return;
    maxBoneTravel = Math.max(
      maxBoneTravel,
      before.get(node.name).distanceTo(node.getWorldPosition(new THREE.Vector3()))
    );
  });
  assert.ok(maxBoneTravel <= 0.006, `max bone travel ${maxBoneTravel}`);
  definitions.dispose();
});

test("missing asset-specific bone rejects the whole bundle", () => {
  const scene = sceneFixture();
  const missing = scene.getObjectByName(TIER_A_BONE_MAP.RIGHT.fingers[4][1]);
  missing.parent.remove(missing);
  assert.throws(
    () => buildTierAHandDefinitions(THREE, scene),
    /expected 1, found 0/
  );
});

test("bundle disposal is idempotent across left/right release", () => {
  const scene = sceneFixture();
  const geometry = new THREE.BoxGeometry(1, 1, 1);
  const material = new THREE.MeshStandardMaterial();
  const mesh = new THREE.Mesh(geometry, material);
  scene.getObjectByName("HandMesh.L").add(mesh);
  let geometryDisposals = 0;
  let materialDisposals = 0;
  geometry.dispose = () => { geometryDisposals += 1; };
  material.dispose = () => { materialDisposals += 1; };
  const definitions = buildTierAHandDefinitions(THREE, scene);
  definitions.LEFT.dispose();
  assert.equal(geometryDisposals, 0, "RIGHT still owns shared bundle");
  definitions.RIGHT.dispose();
  definitions.dispose();
  assert.equal(geometryDisposals, 1);
  assert.equal(materialDisposals, 1);
});

test("renderer replacement disposes the old bundle and detach restores fallback", () => {
  const firstScene = sceneFixture();
  const geometry = new THREE.BoxGeometry(1, 1, 1);
  const material = new THREE.MeshStandardMaterial();
  firstScene.getObjectByName("HandMesh.L").add(new THREE.Mesh(geometry, material));
  let geometryDisposals = 0;
  geometry.dispose = () => { geometryDisposals += 1; };
  const first = buildTierAHandDefinitions(THREE, firstScene);
  const second = buildTierAHandDefinitions(THREE, sceneFixture());
  const renderer = Object.create(HandRenderer.prototype);
  renderer._renderer = {};
  renderer._scene = new THREE.Scene();
  renderer._fallbacks = {
    LEFT: { kind: HAND_SOURCES.PROCEDURAL, root: new THREE.Group() },
    RIGHT: { kind: HAND_SOURCES.PROCEDURAL, root: new THREE.Group() },
  };
  renderer.hands = { ...renderer._fallbacks };
  renderer.handSource = {
    LEFT: HAND_SOURCES.PROCEDURAL,
    RIGHT: HAND_SOURCES.PROCEDURAL,
  };
  renderer.attachAsset("LEFT", first.LEFT);
  renderer.attachAsset("RIGHT", first.RIGHT);
  renderer.attachAsset("LEFT", second.LEFT);
  assert.equal(geometryDisposals, 0, "right side still owns first bundle");
  renderer.attachAsset("RIGHT", second.RIGHT);
  assert.equal(geometryDisposals, 1, "first bundle disposed after deterministic replacement");
  assert.equal(renderer.detachAsset("LEFT"), HAND_SOURCES.PROCEDURAL);
  assert.equal(renderer.hands.LEFT, renderer._fallbacks.LEFT);
  renderer.detachAsset("RIGHT");
});

/* The authored rig (bones only) straight from the shipped GLB, so the
 * orientation tests run against the real bind pose, not a synthetic one. */
function authoredRigScene() {
  const glb = readFileSync(new URL("../../src/piano_core/web/static/assets/piano-hand-tier-a.glb", import.meta.url));
  const json = JSON.parse(glb.subarray(20, 20 + glb.readUInt32LE(12)).toString("utf8"));
  const nodes = json.nodes.map((node) => {
    const object = node.mesh !== undefined || /Armature/.test(node.name) ? new THREE.Group() : new THREE.Bone();
    object.name = node.name;
    if (node.translation) object.position.fromArray(node.translation);
    if (node.rotation) object.quaternion.fromArray(node.rotation);
    return object;
  });
  json.nodes.forEach((node, index) => {
    for (const child of node.children || []) nodes[index].add(nodes[child]);
  });
  const scene = new THREE.Scene();
  for (const index of json.scenes[json.scene ?? 0].nodes) scene.add(nodes[index]);
  return scene;
}

function keyboardPose(hand) {
  // Solver-space pose: knuckle line 88 mm toward the fallboard from the
  // wrist, fingertips on the white-key contact band (z = -0.09).
  const mirror = hand === "LEFT" ? -1 : 1;
  const fingers = {};
  for (let finger = 1; finger <= 5; finger += 1) {
    const x = (finger - 3) * 0.024 * mirror;
    const base = finger === 1
      ? { x: -0.048 * mirror, y: 0.021, z: 0.025 }
      : { x, y: 0.033, z: -0.033 };
    fingers[finger] = { base, elbow: { x, y: 0.05, z: -0.06 }, tip: { x, y: 0, z: -0.09 }, planeYaw: Math.PI };
  }
  return { wrist: { x: 0, y: 0.033, z: 0.055, yaw: 0 }, fingers };
}

const worldOf = (bone) => bone.getWorldPosition(new THREE.Vector3());

test("authored hand lies palm-down with fingers toward the fallboard", () => {
  const definitions = buildTierAHandDefinitions(THREE, authoredRigScene());
  for (const hand of ["LEFT", "RIGHT"]) {
    const definition = definitions[hand];
    const pressed = { 1: {}, 2: {}, 3: { press: 1 }, 4: {}, 5: {} };
    assert.equal(definition.applyPose(keyboardPose(hand), pressed), true);
    const wrist = worldOf(definition.wrist);
    const middle = worldOf(definition.fingers[3][0]);
    assert.ok(middle.z < wrist.z - 0.05, `${hand} knuckles ahead of the wrist`);
    assert.ok(Math.abs(middle.y - wrist.y) < 0.03, `${hand} palm is level, not hanging`);
    const thumb = worldOf(definition.fingers[1][0]).x;
    const little = worldOf(definition.fingers[5][0]).x;
    assert.ok(hand === "RIGHT" ? thumb < little : thumb > little, `${hand} thumb on the inner side`);
    for (let finger = 2; finger <= 5; finger += 1) {
      const knuckle = worldOf(definition.fingers[finger][0]);
      assert.ok(knuckle.y > 0.03, `${hand} finger ${finger} knuckle above the keys`);
    }
  }
  definitions.dispose();
});

test("a pressed fingertip reaches its contact without stretching the bones", () => {
  const definitions = buildTierAHandDefinitions(THREE, authoredRigScene());
  const definition = definitions.RIGHT;
  const chain = definition.fingers[3];
  const lengthsBefore = chain.map((bone) => bone.position.length());
  const pose = keyboardPose("RIGHT");
  assert.equal(definition.applyPose(pose, { 3: { press: 1 } }), true);
  assert.deepEqual(chain.map((bone) => bone.position.length()), lengthsBefore);
  const end = chain[2].children[0];
  const tip = worldOf(end);
  assert.ok(tip.distanceTo(new THREE.Vector3(0, 0, -0.09)) < 0.004, `tip ${tip.toArray()}`);
  // The middle joint arches above the line from knuckle to tip.
  const knuckle = worldOf(chain[0]);
  const pip = worldOf(chain[1]);
  const along = (pip.z - knuckle.z) / (tip.z - knuckle.z);
  assert.ok(pip.y > knuckle.y + (tip.y - knuckle.y) * along, "curved, not hyper-extended");
  definitions.dispose();
});

test("free fingers stay curled below their knuckle and in their lane", () => {
  const definitions = buildTierAHandDefinitions(THREE, authoredRigScene());
  const definition = definitions.RIGHT;
  const pose = keyboardPose("RIGHT");
  // Ring finger approaches the index finger's key, lifted high.
  pose.fingers[4].tip = { x: -0.024, y: 0.06, z: -0.09 };
  assert.equal(definition.applyPose(pose, {}), true);
  const knuckle = worldOf(definition.fingers[4][0]);
  const tip = worldOf(definition.fingers[4][2].children[0]);
  assert.ok(tip.y < knuckle.y, "lifted finger never bends backwards");
  assert.ok(Math.abs(tip.x - knuckle.x) <= 0.0101 + 1e-6, "no crossing over a neighbour");
  definitions.dispose();
});

test("skin smoothing blends a hard bone boundary and keeps weights normalized", () => {
  // A strip of quads: left half 100% bone 0, right half 100% bone 1.
  const columns = 8;
  const positions = [];
  const joints = [];
  const weights = [];
  for (let row = 0; row < 2; row += 1) {
    for (let column = 0; column < columns; column += 1) {
      positions.push(column * 0.01, row * 0.01, 0);
      joints.push(column < columns / 2 ? 0 : 1, 0, 0, 0);
      weights.push(1, 0, 0, 0);
    }
  }
  const index = [];
  for (let column = 0; column + 1 < columns; column += 1) {
    const a = column;
    const b = column + 1;
    const c = columns + column;
    const d = columns + column + 1;
    index.push(a, b, c, b, d, c);
  }
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
  geometry.setAttribute("skinIndex", new THREE.Uint16BufferAttribute(joints, 4));
  geometry.setAttribute("skinWeight", new THREE.Float32BufferAttribute(weights, 4));
  geometry.setIndex(index);
  assert.equal(smoothSkinWeights(geometry, 2), true);
  const weightOf = (vertex, joint) => {
    let total = 0;
    for (let k = 0; k < 4; k += 1) {
      if (geometry.attributes.skinIndex.getComponent(vertex, k) === joint) {
        total += geometry.attributes.skinWeight.getComponent(vertex, k);
      }
    }
    return total;
  };
  for (let vertex = 0; vertex < columns * 2; vertex += 1) {
    let sum = 0;
    for (let k = 0; k < 4; k += 1) sum += geometry.attributes.skinWeight.getComponent(vertex, k);
    assert.ok(Math.abs(sum - 1) < 1e-5, `vertex ${vertex} normalized`);
  }
  const boundary = columns / 2 - 1;
  assert.ok(weightOf(boundary, 1) > 0.1, "boundary vertex now shares the neighbour bone");
  assert.ok(weightOf(0, 0) > 0.9, "far vertex keeps its own bone");
});

console.log(`${passes} Tier-A rig adapter tests passed`);
