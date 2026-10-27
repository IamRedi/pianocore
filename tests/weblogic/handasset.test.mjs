// PIANO-UI-007 Tier-A deterministic rig-adapter tests (Node, no DOM/GPU).
import assert from "node:assert/strict";
import * as THREE from "../../src/piano_core/web/static/vendor/three.webgpu.js";
import {
  TIER_A_BONE_MAP,
  buildTierAHandDefinitions,
  isFiniteAssetPose,
} from "../../src/piano_core/web/static/handasset.js";
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

console.log(`${passes} Tier-A rig adapter tests passed`);
