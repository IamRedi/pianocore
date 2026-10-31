/* PIANO-UI-007 Tier-A asset adapter.
 *
 * Asset-specific node names live only here. The adapter turns Piano Core's
 * rigging of the Owner-generated Fal mesh into the renderer-independent
 * wrist + five-finger contract and applies the already-authoritative pose.
 * It never reads MIDI, chooses fingering, or owns time.
 */

export const TIER_A_ASSET_URL = "/ui/assets/piano-hand-tier-a.glb";

export const TIER_A_BONE_MAP = Object.freeze({
  LEFT: Object.freeze({
    root: "PianoHand.LEFT",
    armature: "HandArmature.L",
    mesh: "HandMesh.L",
    wrist: "wrist.L",
    fingers: Object.freeze({
      1: Object.freeze(["thumb.01.L", "thumb.02.L", "thumb.03.L"]),
      2: Object.freeze(["index.01.L", "index.02.L", "index.03.L"]),
      3: Object.freeze(["middle.01.L", "middle.02.L", "middle.03.L"]),
      4: Object.freeze(["ring.01.L", "ring.02.L", "ring.03.L"]),
      5: Object.freeze(["pinky.01.L", "pinky.02.L", "pinky.03.L"]),
    }),
  }),
  RIGHT: Object.freeze({
    root: "PianoHand.RIGHT",
    armature: "HandArmature.R",
    mesh: "HandMesh.R",
    wrist: "wrist.R",
    fingers: Object.freeze({
      1: Object.freeze(["thumb.01.R", "thumb.02.R", "thumb.03.R"]),
      2: Object.freeze(["index.01.R", "index.02.R", "index.03.R"]),
      3: Object.freeze(["middle.01.R", "middle.02.R", "middle.03.R"]),
      4: Object.freeze(["ring.01.R", "ring.02.R", "ring.03.R"]),
      5: Object.freeze(["pinky.01.R", "pinky.02.R", "pinky.03.R"]),
    }),
  }),
});

function findUnique(root, name) {
  const matches = [];
  root.traverse((node) => {
    // GLTFLoader sanitizes '.', ':', '/', '[' and ']' from Object3D.name
    // for animation binding, while preserving the authored glTF name here.
    if (node.userData?.name === name || node.name === name) matches.push(node);
  });
  if (matches.length !== 1) {
    throw new Error(`Tier-A node ${name}: expected 1, found ${matches.length}`);
  }
  return matches[0];
}

function finitePoint(point) {
  return (
    point &&
    Number.isFinite(point.x) &&
    Number.isFinite(point.y) &&
    Number.isFinite(point.z)
  );
}

function optionalFiniteNumber(value) {
  return value === null || value === undefined || Number.isFinite(value);
}

export function isFiniteAssetPose(pose) {
  if (!pose || !finitePoint(pose.wrist) || !optionalFiniteNumber(pose.wrist.yaw)) {
    return false;
  }
  for (let finger = 1; finger <= 5; finger += 1) {
    const solved = pose.fingers?.[finger];
    if (!solved || !finitePoint(solved.base)) return false;
    if ((solved.elbow || solved.tip) && !(finitePoint(solved.elbow) && finitePoint(solved.tip))) {
      return false;
    }
    if (!optionalFiniteNumber(solved.planeYaw)) return false;
  }
  return true;
}

function collectDisposableResources(scene) {
  const geometries = new Set();
  const materials = new Set();
  const textures = new Set();
  scene.traverse((node) => {
    if (node.geometry) geometries.add(node.geometry);
    const entries = Array.isArray(node.material) ? node.material : [node.material];
    for (const material of entries) {
      if (!material) continue;
      materials.add(material);
      for (const value of Object.values(material)) {
        if (value?.isTexture) textures.add(value);
      }
    }
  });
  return { geometries, materials, textures };
}

function createBundle(scene) {
  const resources = collectDisposableResources(scene);
  const attached = new Set(["LEFT", "RIGHT"]);
  let disposed = false;
  const release = (hand) => {
    attached.delete(hand);
    if (attached.size > 0 || disposed) return;
    disposed = true;
    for (const texture of resources.textures) {
      texture.image?.close?.();
      texture.dispose?.();
    }
    for (const material of resources.materials) material.dispose?.();
    for (const geometry of resources.geometries) geometry.dispose?.();
  };
  return {
    release,
    dispose: () => {
      attached.clear();
      release("ALL");
    },
    get disposed() {
      return disposed;
    },
  };
}

function bindSegment(three, bone, next) {
  bone.updateWorldMatrix(true, false);
  next.updateWorldMatrix(true, false);
  const origin = bone.getWorldPosition(new three.Vector3());
  const end = next.getWorldPosition(new three.Vector3());
  const axis = end.sub(origin);
  if (axis.lengthSq() < 1e-12) throw new Error(`zero-length bone ${bone.name}`);
  return {
    worldAxis: axis.normalize(),
    worldQuaternion: bone.getWorldQuaternion(new three.Quaternion()),
  };
}

/* The authored rig hangs the hand fingers-down (wrist bind = 180° about X,
 * bones along +Y). Piano Core's pose space is palm-down over the keys with
 * the fingers pointing toward the fallboard (-Z). This quarter turn about
 * X maps the bind hand into that space; yaw is applied on top of it. */
const BIND_TO_POSE_AXIS = Object.freeze([1, 0, 0]);
const BIND_TO_POSE_ANGLE = Math.PI / 2;
/* Knuckle-line height above the white key tops for the authored hand: the
 * solver's thin procedural palm hovers lower than a real hand can. */
const ASSET_KNUCKLE_HEIGHT_M = 0.048;
/* A finger that is not pressing never rises above this distance below its
 * own knuckle: lifted fingers stay curled instead of bending backwards. */
const ASSET_FREE_TIP_DROP_M = 0.012;
/* ...and stays in its own lane: a finger approaching a key its neighbour is
 * leaving must not cross over it, which tears the shared skin between them. */
const ASSET_FREE_TIP_LANE_M = 0.01;

/** Rotate one bone so its segment points along `direction` (world). Only
 * the rotation changes: bone offsets stay at their authored bind lengths,
 * so the skinned surface is never stretched between joints. */
function aimBone(three, bone, direction, bind, handDelta) {
  if (direction.lengthSq() < 1e-12) return false;
  const desired = direction.clone().normalize();
  const bindAxis = bind.worldAxis.clone().applyQuaternion(handDelta);
  const swing = new three.Quaternion().setFromUnitVectors(bindAxis, desired);
  const desiredWorld = swing.multiply(handDelta.clone().multiply(bind.worldQuaternion));
  const parent = bone.parent;
  parent?.updateWorldMatrix(true, false);
  if (parent) {
    const parentWorld = parent.getWorldQuaternion(new three.Quaternion()).invert();
    bone.quaternion.copy(parentWorld.multiply(desiredWorld));
  } else {
    bone.quaternion.copy(desiredWorld);
  }
  bone.updateWorldMatrix(false, true);
  return true;
}

/** Planar finger reach from the asset's own knuckle with the asset's own
 * phalanx lengths, in the vertical plane through knuckle and fingertip.
 * Like a pianist's curved finger, the knuckle joint lifts the proximal
 * phalanx only slightly and the middle and end joints take the remaining
 * curl, so a near target hooks the finger down instead of folding it
 * upward. Returns { elbow, dip }; an out-of-reach tip yields a straight
 * finger toward it. */
function solveAssetFinger(three, base, tip, lengths) {
  const [l1, l2, l3] = lengths;
  const delta = tip.clone().sub(base);
  const horizontal = new three.Vector3(delta.x, 0, delta.z);
  const q = horizontal.length();
  const v = delta.y;
  const dirH = q > 1e-9 ? horizontal.divideScalar(q) : new three.Vector3(0, 0, -1);
  const toWorld = (qq, vv) =>
    new three.Vector3(base.x + dirH.x * qq, base.y + vv, base.z + dirH.z * qq);
  const r = Math.hypot(q, v);
  if (r >= l1 + l2 + l3 - 1e-6 || r < 1e-9) {
    const unit = r > 1e-9 ? delta.clone().divideScalar(r) : new three.Vector3(0, 0, -1);
    return {
      elbow: base.clone().addScaledVector(unit, l1),
      dip: base.clone().addScaledVector(unit, l1 + l2),
    };
  }
  for (let angle = 0.35; angle >= -1.45; angle -= 0.05) {
    const eq = Math.cos(angle) * l1;
    const ev = Math.sin(angle) * l1;
    const dq = q - eq;
    const dv = v - ev;
    const d = Math.hypot(dq, dv);
    if (d > (l2 + l3) * 0.985 || d < Math.abs(l2 - l3) + 1e-3 || dq <= 0) continue;
    const cosBend = (d * d - l2 * l2 - l3 * l3) / (2 * l2 * l3);
    const bend = Math.acos(Math.min(1, Math.max(-1, cosBend)));
    const alpha = Math.atan2(dv, dq) + Math.atan2(l3 * Math.sin(bend), l2 + l3 * Math.cos(bend));
    return {
      elbow: toWorld(eq, ev),
      dip: toWorld(eq + Math.cos(alpha) * l2, ev + Math.sin(alpha) * l2),
    };
  }
  const unit = delta.clone().divideScalar(r);
  return {
    elbow: base.clone().addScaledVector(unit, Math.min(l1, r * 0.4)),
    dip: base.clone().addScaledVector(unit, Math.min(l1 + l2, r * 0.75)),
  };
}

function idlePoints(three, solved, lengths) {
  const yaw = solved.planeYaw ?? 0;
  const direction = new three.Vector3(Math.sin(yaw), 0, Math.cos(yaw));
  const point = (start, angle, length) =>
    new three.Vector3(
      start.x + direction.x * Math.cos(angle) * length,
      start.y + Math.sin(angle) * length,
      start.z + direction.z * Math.cos(angle) * length
    );
  const base = new three.Vector3(solved.base.x, solved.base.y, solved.base.z);
  const elbow = point(base, -0.28, lengths[0]);
  const dip = point(elbow, -0.46, lengths[1]);
  const tip = point(dip, -0.32, lengths[2]);
  return { base, elbow, dip, tip };
}

/* The authored skin binds some continuous surface regions (the webbing
 * between fingers above all) 100% to different finger bones, so the skin
 * tears whenever neighbouring fingers move apart. At load time the weights
 * are diffused a few steps across the connected surface (coincident seam
 * vertices count as connected) and re-packed into the four strongest
 * influences. The asset file itself stays unchanged. */
export const SKIN_SMOOTHING_ITERATIONS = 4;
const SKIN_SMOOTHING_SELF = 0.5;

export function smoothSkinWeights(geometry, jointCount, iterations = SKIN_SMOOTHING_ITERATIONS) {
  const position = geometry?.attributes?.position;
  const skinIndex = geometry?.attributes?.skinIndex;
  const skinWeight = geometry?.attributes?.skinWeight;
  if (!position || !skinIndex || !skinWeight || !geometry.index || jointCount < 1) return false;
  const count = position.count;
  // Weld coincident vertices so UV/normal seams diffuse as one surface.
  const weld = new Int32Array(count);
  const seen = new Map();
  for (let i = 0; i < count; i += 1) {
    const key = `${Math.round(position.getX(i) * 1e5)},${Math.round(position.getY(i) * 1e5)},${Math.round(position.getZ(i) * 1e5)}`;
    const first = seen.get(key);
    if (first === undefined) {
      seen.set(key, i);
      weld[i] = i;
    } else {
      weld[i] = first;
    }
  }
  const neighbours = Array.from({ length: count }, () => new Set());
  const index = geometry.index.array;
  for (let t = 0; t + 2 < index.length; t += 3) {
    const a = weld[index[t]];
    const b = weld[index[t + 1]];
    const c = weld[index[t + 2]];
    neighbours[a].add(b).add(c);
    neighbours[b].add(a).add(c);
    neighbours[c].add(a).add(b);
  }
  let weights = new Float32Array(count * jointCount);
  for (let i = 0; i < count; i += 1) {
    if (weld[i] !== i) continue;
    for (let k = 0; k < 4; k += 1) {
      const joint = skinIndex.getComponent(i, k);
      const weight = skinWeight.getComponent(i, k);
      if (joint < jointCount && weight > 0) weights[i * jointCount + joint] += weight;
    }
  }
  let next = new Float32Array(weights.length);
  for (let step = 0; step < iterations; step += 1) {
    for (let i = 0; i < count; i += 1) {
      if (weld[i] !== i) continue;
      const list = neighbours[i];
      const base = i * jointCount;
      if (list.size === 0) {
        for (let j = 0; j < jointCount; j += 1) next[base + j] = weights[base + j];
        continue;
      }
      const share = (1 - SKIN_SMOOTHING_SELF) / list.size;
      for (let j = 0; j < jointCount; j += 1) next[base + j] = weights[base + j] * SKIN_SMOOTHING_SELF;
      for (const n of list) {
        const other = n * jointCount;
        for (let j = 0; j < jointCount; j += 1) next[base + j] += weights[other + j] * share;
      }
    }
    [weights, next] = [next, weights];
  }
  const top = [0, 0, 0, 0];
  for (let i = 0; i < count; i += 1) {
    const base = weld[i] * jointCount;
    top.fill(-1);
    for (let j = 0; j < jointCount; j += 1) {
      const w = weights[base + j];
      for (let k = 0; k < 4; k += 1) {
        if (top[k] < 0 || w > weights[base + top[k]]) {
          top.splice(k, 0, j);
          top.length = 4;
          break;
        }
      }
    }
    let total = 0;
    for (let k = 0; k < 4; k += 1) total += top[k] >= 0 ? weights[base + top[k]] : 0;
    if (!(total > 0)) continue;
    for (let k = 0; k < 4; k += 1) {
      const joint = top[k] >= 0 ? top[k] : 0;
      const weight = top[k] >= 0 ? weights[base + top[k]] / total : 0;
      skinIndex.setComponent(i, k, joint);
      skinWeight.setComponent(i, k, weight);
    }
  }
  skinIndex.needsUpdate = true;
  skinWeight.needsUpdate = true;
  return true;
}

function createDefinition(three, scene, hand, bundle) {
  const names = TIER_A_BONE_MAP[hand];
  const armature = findUnique(scene, names.armature);
  const mesh = findUnique(scene, names.mesh);
  const root = new three.Group();
  root.name = names.root;
  scene.add(root);
  root.add(armature, mesh);
  mesh.traverse((node) => {
    if (node.isSkinnedMesh) smoothSkinWeights(node.geometry, node.skeleton?.bones?.length ?? 0);
  });
  const wrist = findUnique(armature, names.wrist);
  const fingers = {};
  const endBones = {};
  for (let finger = 1; finger <= 5; finger += 1) {
    fingers[finger] = names.fingers[finger].map((name) => findUnique(armature, name));
    const endSuffix = hand === "LEFT" ? ".L_end" : ".R_end";
    endBones[finger] = findUnique(
      armature,
      `${names.fingers[finger][2].replace(hand === "LEFT" ? ".L" : ".R", "")}${endSuffix}`
    );
  }
  scene.updateMatrixWorld(true);
  const wristBindWorldPosition = wrist.getWorldPosition(new three.Vector3());
  const wristBindWorldQuaternion = wrist.getWorldQuaternion(new three.Quaternion());
  const bindToPose = new three.Quaternion().setFromAxisAngle(
    new three.Vector3(...BIND_TO_POSE_AXIS),
    BIND_TO_POSE_ANGLE
  );
  const segmentBinds = {};
  const segmentLengths = {};
  const knuckleCentroid = new three.Vector3();
  for (let finger = 1; finger <= 5; finger += 1) {
    const chain = fingers[finger];
    const next = [chain[1], chain[2], endBones[finger]];
    segmentBinds[finger] = chain.map((bone, index) => bindSegment(three, bone, next[index]));
    segmentLengths[finger] = chain.map((bone, index) => {
      const a = bone.getWorldPosition(new three.Vector3());
      const b = next[index].getWorldPosition(new three.Vector3());
      return a.distanceTo(b);
    });
    if (finger > 1) {
      knuckleCentroid.add(
        chain[0].getWorldPosition(new three.Vector3()).sub(wristBindWorldPosition).applyQuaternion(bindToPose)
      );
    }
  }
  // Wrist -> knuckle-line offset of the authored hand, in pose space.
  knuckleCentroid.divideScalar(4);

  return {
    root,
    wrist,
    fingers,
    applyPose(pose, fingerMotion = null) {
      if (!isFiniteAssetPose(pose)) return false;
      const yaw = new three.Quaternion().setFromAxisAngle(new three.Vector3(0, 1, 0), pose.wrist.yaw ?? 0);
      const handDelta = yaw.clone().multiply(bindToPose);

      // Seat the authored knuckle line on the solver's knuckle line; the
      // solver's wrist height keeps the palm hovering over the keys.
      const knuckleTarget = new three.Vector3();
      for (let finger = 2; finger <= 5; finger += 1) {
        const base = pose.fingers[finger].base;
        knuckleTarget.add(new three.Vector3(base.x, base.y, base.z));
      }
      knuckleTarget.divideScalar(4);
      const offset = knuckleCentroid.clone().applyQuaternion(yaw);
      const wristWorld = new three.Vector3(
        knuckleTarget.x - offset.x,
        Math.max(pose.wrist.y, ASSET_KNUCKLE_HEIGHT_M - offset.y),
        knuckleTarget.z - offset.z
      );
      const wristParent = wrist.parent;
      wristParent?.updateWorldMatrix(true, false);
      const wristWorldQuaternion = handDelta.clone().multiply(wristBindWorldQuaternion);
      if (wristParent) {
        wristParent.worldToLocal(wristWorld);
        const parentWorld = wristParent.getWorldQuaternion(new three.Quaternion()).invert();
        wrist.quaternion.copy(parentWorld.multiply(wristWorldQuaternion));
      } else {
        wrist.quaternion.copy(wristWorldQuaternion);
      }
      wrist.position.copy(wristWorld);
      wrist.scale.set(1, 1, 1);
      wrist.updateWorldMatrix(true, true);

      for (let finger = 1; finger <= 5; finger += 1) {
        const solved = pose.fingers[finger];
        const chain = fingers[finger];
        const lengths = segmentLengths[finger];
        const base = chain[0].getWorldPosition(new three.Vector3());
        let points;
        if (solved.elbow && solved.tip) {
          // The fingertip keeps the solver's contact; the joints are
          // re-solved from the asset's own knuckle and phalanx lengths.
          const tip = new three.Vector3(solved.tip.x, solved.tip.y, solved.tip.z);
          const motion = fingerMotion?.[finger];
          if (!(motion?.press > 0)) {
            tip.y = Math.min(tip.y, base.y - ASSET_FREE_TIP_DROP_M);
            const lateral = tip.x - base.x;
            tip.x = base.x + Math.max(-ASSET_FREE_TIP_LANE_M, Math.min(ASSET_FREE_TIP_LANE_M, lateral));
          }
          const { elbow, dip } = solveAssetFinger(three, base, tip, lengths);
          points = { base, elbow, dip, tip };
        } else {
          points = idlePoints(three, { ...solved, base }, lengths);
        }
        const binds = segmentBinds[finger];
        if (!aimBone(three, chain[0], points.elbow.clone().sub(points.base), binds[0], handDelta)) return false;
        if (!aimBone(three, chain[1], points.dip.clone().sub(points.elbow), binds[1], handDelta)) return false;
        if (!aimBone(three, chain[2], points.tip.clone().sub(points.dip), binds[2], handDelta)) return false;
      }
      root.updateWorldMatrix(true, true);
      return true;
    },
    dispose: () => bundle.release(hand),
  };
}

export function buildTierAHandDefinitions(three, scene) {
  if (!three || !scene?.traverse) throw new TypeError("three and GLB scene are required");
  const bundle = createBundle(scene);
  try {
    return {
      LEFT: createDefinition(three, scene, "LEFT", bundle),
      RIGHT: createDefinition(three, scene, "RIGHT", bundle),
      dispose: bundle.dispose,
    };
  } catch (error) {
    bundle.dispose();
    throw error;
  }
}

export async function loadTierAHandAsset(three, url = TIER_A_ASSET_URL) {
  const { GLTFLoader } = await import("./vendor/loaders/GLTFLoader.js");
  const loader = new GLTFLoader();
  const gltf = await loader.loadAsync(url);
  return buildTierAHandDefinitions(three, gltf.scene);
}
