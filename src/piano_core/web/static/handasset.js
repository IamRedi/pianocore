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

function setSegmentWorld(three, bone, from, to, bind) {
  const direction = new three.Vector3(to.x - from.x, to.y - from.y, to.z - from.z);
  if (direction.lengthSq() < 1e-12) return false;
  direction.normalize();
  const parent = bone.parent;
  parent?.updateWorldMatrix(true, false);
  const localPosition = new three.Vector3(from.x, from.y, from.z);
  if (parent) parent.worldToLocal(localPosition);
  bone.position.copy(localPosition);

  const delta = new three.Quaternion().setFromUnitVectors(bind.worldAxis, direction);
  const desiredWorld = delta.multiply(bind.worldQuaternion.clone());
  if (parent) {
    const parentWorld = parent.getWorldQuaternion(new three.Quaternion()).invert();
    bone.quaternion.copy(parentWorld.multiply(desiredWorld));
  } else {
    bone.quaternion.copy(desiredWorld);
  }
  bone.scale.set(1, 1, 1);
  bone.updateWorldMatrix(false, true);
  return true;
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

function createDefinition(three, scene, hand, bundle) {
  const names = TIER_A_BONE_MAP[hand];
  const armature = findUnique(scene, names.armature);
  const mesh = findUnique(scene, names.mesh);
  const root = new three.Group();
  root.name = names.root;
  scene.add(root);
  root.add(armature, mesh);
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
  const wristBindPosition = wrist.position.clone();
  const wristBindQuaternion = wrist.quaternion.clone();
  const segmentBinds = {};
  const segmentLengths = {};
  for (let finger = 1; finger <= 5; finger += 1) {
    const chain = fingers[finger];
    const next = [chain[1], chain[2], endBones[finger]];
    segmentBinds[finger] = chain.map((bone, index) => bindSegment(three, bone, next[index]));
    segmentLengths[finger] = chain.map((bone, index) => {
      const a = bone.getWorldPosition(new three.Vector3());
      const b = next[index].getWorldPosition(new three.Vector3());
      return a.distanceTo(b);
    });
  }

  return {
    root,
    wrist,
    fingers,
    applyPose(pose) {
      if (!isFiniteAssetPose(pose)) return false;
      wrist.position.set(pose.wrist.x, pose.wrist.y, pose.wrist.z).add(wristBindPosition);
      wrist.quaternion
        .setFromAxisAngle(new three.Vector3(0, 1, 0), pose.wrist.yaw ?? 0)
        .multiply(wristBindQuaternion);
      wrist.scale.set(1, 1, 1);
      wrist.updateWorldMatrix(true, true);

      for (let finger = 1; finger <= 5; finger += 1) {
        const solved = pose.fingers[finger];
        let points;
        if (solved.elbow && solved.tip) {
          const base = new three.Vector3(solved.base.x, solved.base.y, solved.base.z);
          const elbow = new three.Vector3(solved.elbow.x, solved.elbow.y, solved.elbow.z);
          const tip = new three.Vector3(solved.tip.x, solved.tip.y, solved.tip.z);
          const distal = segmentLengths[finger][1];
          const total = distal + segmentLengths[finger][2];
          const dip = elbow.clone().lerp(tip, total > 0 ? distal / total : 0.5);
          points = { base, elbow, dip, tip };
        } else {
          points = idlePoints(three, solved, segmentLengths[finger]);
        }
        const chain = fingers[finger];
        if (!setSegmentWorld(three, chain[0], points.base, points.elbow, segmentBinds[finger][0])) return false;
        if (!setSegmentWorld(three, chain[1], points.elbow, points.dip, segmentBinds[finger][1])) return false;
        if (!setSegmentWorld(three, chain[2], points.dip, points.tip, segmentBinds[finger][2])) return false;
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
