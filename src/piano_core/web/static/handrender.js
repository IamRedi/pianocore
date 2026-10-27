/* PIANO-UI-007 — HandRenderer (3D hand presentation).
 *
 * Presentation only. The renderer consumes MotionState (and the pose the
 * pure solver derives from it) — it NEVER reads musical data to make
 * fingering decisions, never grades, never schedules, and never touches
 * musical time. Quality may degrade geometry and render effects; it can
 * never change hand identity, finger identity, target key, or transport
 * synchronization.
 *
 * Three.js is vendored locally (pinned exact version, recorded SHA-256,
 * MIT license — see ADR-018) and loaded through the import map as
 * "three/webgpu" via a DYNAMIC import inside initialize(): a renderer
 * failure must degrade to the existing 2D UI (COMPATIBILITY), never
 * break the page. Backend ladder: WebGPU when genuinely available ->
 * WebGL2 fallback -> COMPATIBILITY. Plain-LAN HTTP stays fully
 * functional through the WebGL2 path (WebGPU needs a secure context).
 *
 * This module has NO top-level DOM or Three.js access: importing it in
 * Node is safe; only initialize()/render() touch the browser.
 */

import {
  BLACK_KEY_LENGTH_M,
  BLACK_KEY_RISE_M,
  FINGER_IDLE_CURL,
  FINGER_MODEL,
  KEY_DIP_M,
  KEYBOARD_WIDTH_M,
  WHITE_KEY_LENGTH_M,
  solveHandPose,
} from "./handmotion.js";
import { loadTierAHandAsset, TIER_A_ASSET_URL } from "./handasset.js";

export const HANDS_SCHEMA = Object.freeze(["LEFT", "RIGHT"]);
export const QUALITY_MODES = Object.freeze([
  "AUTO",
  "PREMIUM",
  "BALANCED",
  "BATTERY",
  "COMPATIBILITY",
]);
export const CAMERA_MODES = Object.freeze(["FULL", "FOLLOW"]);
export const RENDERER_STATUS = Object.freeze({
  IDLE: "idle",
  READY: "ready",
  UNAVAILABLE: "unavailable",
  RECOVERING: "recovering",
});
export const HAND_SOURCES = Object.freeze({
  HIDDEN: "hidden",
  PROCEDURAL: "procedural",
  ASSET: "asset",
});

export class RendererUnavailableError extends Error {
  constructor(message) {
    super(message);
    this.name = "RendererUnavailableError";
  }
}

export class RigValidationError extends Error {
  constructor(message) {
    super(message);
    this.name = "RigValidationError";
  }
}

/** Camera fit (deterministic; presentation only). */
export function fullCameraPose(aspect) {
  const fovY = (40 * Math.PI) / 180;
  const halfWidth = KEYBOARD_WIDTH_M / 2 + 0.08;
  const fovX = 2 * Math.atan(Math.tan(fovY / 2) * Math.max(aspect, 0.3));
  const distance = Math.max(0.9, halfWidth / Math.tan(fovX / 2) + 0.15);
  return {
    position: { x: 0, y: distance * 0.72, z: distance * 0.78 },
    target: { x: 0, y: 0, z: -0.03 },
  };
}

export function followCameraPose(aspect, focusX) {
  const pose = fullCameraPose(aspect);
  const zoom = 0.62;
  return {
    position: {
      x: focusX,
      y: pose.position.y * zoom,
      z: pose.position.z * zoom,
    },
    target: { x: focusX, y: 0, z: -0.03 },
  };
}

/* --------------------------------------------------------------------
 * Canonical rig validation (asset-independent, fail-closed)
 * -------------------------------------------------------------------- */

/**
 * Validate a rig definition fail-closed: a wrist plus three joints per
 * finger (an optional fourth/ext joint is allowed) must all resolve to
 * Object3D-like nodes. Bone NAMES never matter — the caller supplies
 * resolved nodes through its own name map. Throws RigValidationError.
 */
export function validateRigDefinition(resolved, { hand }) {
  const problems = [];
  if (!HANDS_SCHEMA.includes(hand)) problems.push("invalid hand");
  if (!resolved || typeof resolved !== "object") {
    throw new RigValidationError(`[${hand}] rig definition is not an object`);
  }
  if (!isNode(resolved.wrist)) problems.push("missing wrist");
  for (let finger = 1; finger <= 5; finger += 1) {
    const chain = resolved.fingers?.[finger];
    if (!Array.isArray(chain) || chain.length < 3 || chain.length > 4) {
      problems.push(`finger ${finger}: expected 3-4 joints`);
      continue;
    }
    chain.forEach((joint, index) => {
      if (!isNode(joint)) problems.push(`finger ${finger} joint ${index + 1} missing`);
    });
  }
  if (problems.length > 0) {
    throw new RigValidationError(`[${hand}] ${problems.join("; ")}`);
  }
  return true;
}

function isNode(candidate) {
  return (
    candidate &&
    typeof candidate === "object" &&
    typeof candidate.updateMatrix === "function" &&
    typeof candidate.updateWorldMatrix === "function"
  );
}

/* --------------------------------------------------------------------
 * Procedural fallback hand (Tier R) — the PERMANENT runtime fallback
 * -------------------------------------------------------------------- */

/**
 * Builds the deterministic procedural rig from primitive geometry. It
 * exercises the same wrist/root planner, the same IK, the same finger
 * chains, the same contact model, the same renderer lifecycle and the
 * same collision rules as any future Tier-A realistic asset. This is
 * not a throwaway mock.
 *
 * Joint pivots are FLAT children of the rig root (the rig root stays at
 * scene identity) and are placed at the solver's exact WORLD joint
 * points every frame — point-matching keeps fingertips EXACTLY on the
 * canonical contacts with no forward-kinematics drift. A future Tier-A
 * asset implements the same contract through attachAsset + applyPose.
 */
export function buildProceduralHand(three, { hand }) {
  const root = new three.Group();
  root.name = `procedural-hand-${hand}`;
  const skin = new three.MeshStandardMaterial({
    color: hand === "LEFT" ? 0xc9a284 : 0xd8b294,
    roughness: 0.7,
    metalness: 0.05,
  });
  const disposables = [skin];

  // Palm + wrist visuals follow the planned wrist pose (visuals only).
  const handGroup = new three.Group();
  root.add(handGroup);
  const palmGeometry = new three.BoxGeometry(0.088, 0.018, 0.075);
  disposables.push(palmGeometry);
  const palm = new three.Mesh(palmGeometry, skin);
  palm.position.set(0, -0.002, -0.045);
  palm.castShadow = true;
  handGroup.add(palm);
  const wristGeometry = new three.BoxGeometry(0.05, 0.024, 0.034);
  disposables.push(wristGeometry);
  const wristMesh = new three.Mesh(wristGeometry, skin);
  wristMesh.position.set(0, -0.004, 0.008);
  handGroup.add(wristMesh);

  const fingers = {};
  for (let finger = 1; finger <= 5; finger += 1) {
    const lengths = FINGER_MODEL[finger];
    const thickness = finger === 1 ? 0.0095 : 0.0085;
    const pivots = [];
    for (let joint = 0; joint < 3; joint += 1) {
      const pivot = new three.Group();
      root.add(pivot);
      const length = lengths[joint];
      const geometry = new three.CapsuleGeometry(
        thickness,
        Math.max(length - thickness, 0.002),
        2, 6
      );
      disposables.push(geometry);
      const mesh = new three.Mesh(geometry, skin);
      mesh.rotation.x = Math.PI / 2; // capsule axis Y -> pivot -Z
      mesh.position.z = -length / 2;
      mesh.castShadow = true;
      pivot.add(mesh);
      pivots.push(pivot);
    }
    const tipGeometry = new three.SphereGeometry(thickness * 1.05, 8, 6);
    disposables.push(tipGeometry);
    const tipMesh = new three.Mesh(tipGeometry, skin);
    tipMesh.position.z = -lengths[2];
    pivots[2].add(tipMesh);
    fingers[finger] = pivots;
  }

  return {
    kind: HAND_SOURCES.PROCEDURAL,
    hand,
    root,
    handGroup,
    fingers,
    disposables,
  };
}

const NEG_Z = { x: 0, y: 0, z: -1 };

/** Place one pivot at `from`, aiming its -Z axis at `to` (world space). */
function aimPivot(three, pivot, from, to) {
  pivot.position.set(from.x, from.y, from.z);
  const dir = new three.Vector3(to.x - from.x, to.y - from.y, to.z - from.z);
  if (dir.lengthSq() < 1e-12) {
    dir.set(NEG_Z.x, NEG_Z.y, NEG_Z.z);
  } else {
    dir.normalize();
  }
  pivot.quaternion.setFromUnitVectors(
    new three.Vector3(NEG_Z.x, NEG_Z.y, NEG_Z.z),
    dir
  );
}

/** Deterministic relaxed idle curl from the knuckle along planeYaw. */
function idleChainPoints(base, lengths, planeYaw) {
  const h = {
    x: Math.sin(planeYaw),
    y: 0,
    z: Math.cos(planeYaw),
  };
  const pointAt = (start, angle, length) => ({
    x: start.x + h.x * Math.cos(angle) * length,
    y: start.y + Math.sin(angle) * length,
    z: start.z + h.z * Math.cos(angle) * length,
  });
  const elbow = pointAt(base, FINGER_IDLE_CURL[0], lengths[0]);
  const pip = pointAt(elbow, FINGER_IDLE_CURL[1], lengths[1]);
  const tip = pointAt(pip, FINGER_IDLE_CURL[2], lengths[2]);
  return { elbow, pip, tip };
}

/* --------------------------------------------------------------------
 * HandRenderer
 * -------------------------------------------------------------------- */

const QUALITY_PRESETS = Object.freeze({
  PREMIUM: Object.freeze({ maxPixelRatio: 2, shadows: true, shadowMapSize: 2048 }),
  BALANCED: Object.freeze({ maxPixelRatio: 1.5, shadows: true, shadowMapSize: 1024 }),
  BATTERY: Object.freeze({ maxPixelRatio: 1, shadows: false, shadowMapSize: 512 }),
});

export class HandRenderer {
  constructor({ canvas, geometry }) {
    if (!canvas) throw new TypeError("canvas is required");
    if (!geometry || !(geometry.centerFractions instanceof Map)) {
      throw new TypeError("geometry.centerFractions Map is required");
    }
    this.canvas = canvas;
    this.geometry = geometry;
    this.status = RENDERER_STATUS.IDLE;
    this.backend = null;
    this.quality = "AUTO";
    this.effectiveQuality = "BALANCED";
    this.cameraMode = "FULL";
    this.hands = { LEFT: null, RIGHT: null };
    this._fallbacks = { LEFT: null, RIGHT: null };
    this.handSource = { LEFT: HAND_SOURCES.HIDDEN, RIGHT: HAND_SOURCES.HIDDEN };
    this.assetState = "not_loaded";
    this.assetError = null;
    this._three = null;
    this._renderer = null;
    this._scene = null;
    this._camera = null;
    this._directional = null;
    this._keyMeshes = new Map();
    this._disposables = [];
    this._lastIdentity = null;
    this._frameEmaMs = 16;
    this._lastRenderMs = 0;
    this._upgradedFrames = 0;
    this._lastFrameNow = 0;
    this._contextHandlers = null;
    this._lastPoseWarnings = [];
    this._aspect = 1.6;
  }

  /**
   * Dynamic import of the vendored module through the import map.
   * Any failure rejects with RendererUnavailableError so the caller can
   * fall back to COMPATIBILITY (the existing 2D UI keeps working).
   */
  async _loadThree() {
    try {
      return await import("three/webgpu");
    } catch (cause) {
      throw new RendererUnavailableError(
        `vendored three/webgpu unavailable: ${cause?.message || cause}`
      );
    }
  }

  /** initialize: backend ladder WebGPU -> WebGL2 -> reject. */
  async initialize() {
    if (this._renderer) return this.getStatus();
    const three = await this._loadThree();
    this._three = three;
    let renderer = null;
    let backend = null;
    let primaryError = null;
    try {
      renderer = new three.WebGPURenderer({ canvas: this.canvas, antialias: true });
      await renderer.init();
      backend = renderer.backend?.isWebGPUBackend === true ? "webgpu" : "webgl2";
    } catch (cause) {
      primaryError = cause;
      try {
        renderer = new three.WebGPURenderer({
          canvas: this.canvas,
          antialias: true,
          forceWebGL: true,
        });
        await renderer.init();
        backend = "webgl2";
      } catch (fallbackCause) {
        throw new RendererUnavailableError(
          `no GPU backend available (webgpu: ${primaryError?.message || primaryError}; ` +
            `webgl2: ${fallbackCause?.message || fallbackCause})`
        );
      }
    }
    this._renderer = renderer;
    this.backend = backend;
    this._buildScene();
    this._installContextRecovery();
    this.setQuality(this.quality);
    this.status = RENDERER_STATUS.READY;
    return this.getStatus();
  }

  _buildScene() {
    const three = this._three;
    const scene = new three.Scene();
    scene.background = new three.Color(0x101725);
    this._scene = scene;

    this._camera = new three.PerspectiveCamera(40, this._aspect, 0.02, 6);

    scene.add(new three.AmbientLight(0xffffff, 0.55));
    const directional = new three.DirectionalLight(0xffffff, 1.6);
    directional.position.set(0.35, 0.8, 0.45);
    directional.target.position.set(0, 0, -0.05);
    directional.castShadow = true;
    directional.shadow.mapSize.set(1024, 1024);
    scene.add(directional);
    scene.add(directional.target);
    this._directional = directional;

    // Keyboard base (presentation only; all X from the canonical geometry).
    const baseGeometry = new three.BoxGeometry(
      KEYBOARD_WIDTH_M + 0.06,
      0.03,
      WHITE_KEY_LENGTH_M + 0.05
    );
    const baseMaterial = new three.MeshStandardMaterial({
      color: 0x1c2433,
      roughness: 0.9,
    });
    const base = new three.Mesh(baseGeometry, baseMaterial);
    base.position.set(0, -0.023, -(WHITE_KEY_LENGTH_M / 2));
    base.receiveShadow = true;
    scene.add(base);
    this._disposables.push(baseGeometry, baseMaterial);

    // 88 keys from the ONE canonical geometry (X = canonical fractions).
    const whiteGeometry = new three.BoxGeometry(
      KEYBOARD_WIDTH_M / 52 - 0.0015,
      0.018,
      WHITE_KEY_LENGTH_M
    );
    const blackGeometry = new three.BoxGeometry(
      (KEYBOARD_WIDTH_M / 52) * 0.62,
      0.018,
      BLACK_KEY_LENGTH_M
    );
    const whiteMaterial = new three.MeshStandardMaterial({
      color: 0xf2f4f8,
      roughness: 0.45,
    });
    const blackMaterial = new three.MeshStandardMaterial({
      color: 0x14181f,
      roughness: 0.5,
    });
    this._disposables.push(
      whiteGeometry, blackGeometry, whiteMaterial, blackMaterial
    );
    for (const [midi, fraction] of this.geometry.centerFractions) {
      const black = isBlackKey(midi);
      const x = (fraction - 0.5) * KEYBOARD_WIDTH_M;
      const mesh = new three.Mesh(
        black ? blackGeometry : whiteGeometry,
        black ? blackMaterial : whiteMaterial
      );
      if (black) {
        mesh.position.set(
          x,
          BLACK_KEY_RISE_M - 0.009,
          -(WHITE_KEY_LENGTH_M - BLACK_KEY_LENGTH_M / 2)
        );
      } else {
        mesh.position.set(x, -0.009, -WHITE_KEY_LENGTH_M / 2);
      }
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      scene.add(mesh);
      this._keyMeshes.set(midi, { mesh, baseY: mesh.position.y });
    }

    for (const hand of HANDS_SCHEMA) {
      const rig = buildProceduralHand(three, { hand });
      scene.add(rig.root);
      this.hands[hand] = rig;
      this._fallbacks[hand] = rig;
      this._disposables.push(...rig.disposables);
      this.handSource[hand] = HAND_SOURCES.PROCEDURAL;
    }
  }

  _installContextRecovery() {
    this._contextHandlers = {
      lost: (event) => {
        event.preventDefault?.();
        this.status = RENDERER_STATUS.RECOVERING;
      },
      restored: () => {
        void this.recover();
      },
    };
    this.canvas.addEventListener("webglcontextlost", this._contextHandlers.lost);
    this.canvas.addEventListener("webglcontextrestored", this._contextHandlers.restored);
    const device = this._renderer?.backend?.device;
    if (device && typeof device.lost?.addEventListener === "function") {
      this._contextHandlers.deviceLost = () => {
        this.status = RENDERER_STATUS.RECOVERING;
        void this.recover();
      };
      device.lost.addEventListener("contextlost", this._contextHandlers.deviceLost);
    }
  }

  /**
   * attachAsset: hand the renderer a rigged hand for one side. The
   * definition is validated fail-closed (wrist + 3-4 joints per finger,
   * Object3D nodes, optional applyPose); an INVALID asset NEVER reaches
   * the scene — the side keeps its procedural fallback. Returns the
   * applied source for that hand.
   */
  attachAsset(hand, definition) {
    if (!this._renderer) throw new Error("initialize() first");
    if (!HANDS_SCHEMA.includes(hand)) throw new TypeError("unknown hand");
    try {
      validateRigDefinition(definition, { hand });
    } catch {
      this.detachAsset(hand);
      return this.handSource[hand];
    }
    const previous = this.hands[hand];
    if (previous) {
      this._scene.remove(previous.root);
      if (previous.kind === HAND_SOURCES.ASSET) previous.dispose?.();
    }
    definition.kind = HAND_SOURCES.ASSET;
    definition.hand = hand;
    if (typeof definition.root.name === "string") {
      definition.root.name = `asset-hand-${hand}`;
    }
    definition.visibleHandGroup = definition.visibleHandGroup || definition.root;
    this._scene.add(definition.root);
    this.hands[hand] = definition;
    this.handSource[hand] = HAND_SOURCES.ASSET;
    return this.handSource[hand];
  }

  /** Remove the realistic asset for one side and restore Tier R. */
  detachAsset(hand) {
    if (!HANDS_SCHEMA.includes(hand)) throw new TypeError("unknown hand");
    const previous = this.hands[hand];
    if (previous?.kind === HAND_SOURCES.ASSET) {
      this._scene?.remove(previous.root);
      previous.dispose?.();
    }
    const fallback = this._fallbacks[hand];
    if (fallback && this._scene) {
      this._scene.add(fallback.root);
      fallback.root.visible = false;
      this.hands[hand] = fallback;
      this.handSource[hand] = HAND_SOURCES.PROCEDURAL;
    }
    return this.handSource[hand];
  }

  /** Load both sides from the selected Tier-A GLB through this renderer path. */
  async loadTierAAsset(url = TIER_A_ASSET_URL) {
    if (!this._renderer || !this._three) throw new Error("initialize() first");
    this.assetState = "loading";
    this.assetError = null;
    let definitions = null;
    try {
      definitions = await loadTierAHandAsset(this._three, url);
      if (
        this.attachAsset("LEFT", definitions.LEFT) !== HAND_SOURCES.ASSET ||
        this.attachAsset("RIGHT", definitions.RIGHT) !== HAND_SOURCES.ASSET
      ) {
        throw new RigValidationError("Tier-A left/right attachment failed");
      }
      this.assetState = "ready";
      return this.getStatus();
    } catch (error) {
      this.detachAsset("LEFT");
      this.detachAsset("RIGHT");
      definitions?.dispose?.();
      this.assetState = "fallback";
      this.assetError = error?.message || String(error);
      return this.getStatus();
    }
  }

  /**
   * One frame. `motionState` is the ONLY per-frame input (presentation
   * policy — camera/quality — lives on the renderer itself). Returns
   * the bounded status snapshot.
   */
  render(motionState) {
    if (this.status !== RENDERER_STATUS.READY || !this._renderer) {
      return this.getStatus();
    }
    const now = typeof performance !== "undefined" ? performance.now() : Date.now();
    if (this._lastFrameNow > 0) {
      const interval = Math.min(100, Math.max(0, now - this._lastFrameNow));
      this._frameEmaMs = this._frameEmaMs * 0.9 + interval * 0.1;
    }
    this._lastFrameNow = now;

    // Identity gate: an old pose never survives a source/plan change.
    const identity = motionState
      ? `${motionState.identity?.songId || ""}|${motionState.identity?.sourceVersion || ""}|${motionState.identity?.planId || ""}`
      : null;
    if (identity !== this._lastIdentity) {
      this._lastIdentity = identity;
      this._resetHands();
    }

    this._lastPoseWarnings = [];
    for (const hand of HANDS_SCHEMA) {
      const motion = motionState ? motionState.hands[hand] : null;
      const rig = this.hands[hand];
      if (!motion || motion.omitted || !rig) {
        if (rig) rig.root.visible = false;
        this.handSource[hand] = HAND_SOURCES.HIDDEN;
        continue;
      }
      const pose = solveHandPose(motion);
      const applied = this._applyPose(rig, motion, pose);
      if (!applied) {
        const fallback = this.hands[hand];
        if (!fallback || !this._applyPose(fallback, motion, pose)) {
          if (fallback) fallback.root.visible = false;
          this.handSource[hand] = HAND_SOURCES.HIDDEN;
          continue;
        }
      }
      const activeRig = this.hands[hand];
      activeRig.root.visible = true;
      this.handSource[hand] =
        activeRig.kind === HAND_SOURCES.ASSET
          ? HAND_SOURCES.ASSET
          : HAND_SOURCES.PROCEDURAL;
      for (const warning of pose.warnings) {
        this._lastPoseWarnings.push(`${warning}:${hand}`);
      }
    }
    if (motionState) this._dipKeys(motionState);
    // Framing belongs to the scene, even when no fingering is available.
    // Reuse the existing full-keyboard fit for an absent FOLLOW target.
    this._updateCamera(motionState);

    this._autoQuality();
    const renderStart = typeof performance !== "undefined" ? performance.now() : Date.now();
    this._renderer.render(this._scene, this._camera);
    this._lastRenderMs = Math.max(
      0,
      (typeof performance !== "undefined" ? performance.now() : Date.now()) - renderStart
    );
    return this.getStatus();
  }

  _resetHands() {
    for (const [, entry] of this._keyMeshes) {
      entry.mesh.position.y = entry.baseY;
    }
  }

  /** Presentation: pressed keys dip under the immutable contact. */
  _dipKeys(motionState) {
    const dips = new Map();
    for (const hand of HANDS_SCHEMA) {
      const motion = motionState.hands[hand];
      if (!motion || motion.omitted) continue;
      for (let finger = 1; finger <= 5; finger += 1) {
        const fm = motion.fingers[finger];
        if (fm.contact && fm.press > 0) {
          dips.set(fm.midi, Math.max(dips.get(fm.midi) || 0, fm.press));
        }
      }
    }
    for (const [midi, entry] of this._keyMeshes) {
      const dip = dips.get(midi) || 0;
      entry.mesh.position.y = entry.baseY - KEY_DIP_M * dip;
    }
  }

  _applyPose(rig, motion, pose) {
    const three = this._three;
    const wrist = pose.wrist;
    if (rig.kind === HAND_SOURCES.PROCEDURAL) {
      // Palm/wrist visuals follow the solved wrist (already includes the
      // bounded correction); finger pivots take the solver's exact points.
      rig.handGroup.position.set(
        wrist.x,
        Math.max(wrist.y, 0.012),
        Math.max(wrist.z, 0.005)
      );
      rig.handGroup.rotation.set(0, wrist.yaw || 0, 0);
      for (let finger = 1; finger <= 5; finger += 1) {
        const solvedFinger = pose.fingers[finger];
        const chain = rig.fingers[finger];
        const { base, elbow, tip } = solvedFinger;
        if (elbow && tip) {
          const lengths = FINGER_MODEL[finger];
          const distalLength = lengths[1];
          const dip = {
            x: elbow.x + (tip.x - elbow.x) * (distalLength / (lengths[1] + lengths[2])),
            y: elbow.y + (tip.y - elbow.y) * (distalLength / (lengths[1] + lengths[2])),
            z: elbow.z + (tip.z - elbow.z) * (distalLength / (lengths[1] + lengths[2])),
          };
          aimPivot(three, chain[0], base, elbow);
          aimPivot(three, chain[1], elbow, dip);
          aimPivot(three, chain[2], dip, tip);
        } else {
          // No material for this finger: deterministic idle curl.
          const points = idleChainPoints(
            base,
            FINGER_MODEL[finger],
            solvedFinger.planeYaw
          );
          aimPivot(three, chain[0], base, points.elbow);
          aimPivot(three, chain[1], points.elbow, points.pip);
          aimPivot(three, chain[2], points.pip, points.tip);
        }
      }
      return true;
    } else if (typeof rig.applyPose === "function") {
      // Tier-A asset rigs apply the same solved pose through their own
      // bone adapter (validated at attachAsset).
      try {
        if (rig.applyPose(pose) === false) {
          this.assetError = `non-finite or degenerate ${rig.hand} asset pose`;
          this.assetState = "fallback";
          this.detachAsset(rig.hand);
          return false;
        }
        return true;
      } catch (error) {
        this.assetError = error?.message || String(error);
        this.assetState = "fallback";
        this.detachAsset(rig.hand);
        return false;
      }
    }
    return false;
  }

  _updateCamera(motionState) {
    const focus =
      this.cameraMode === "FOLLOW" ? followFocus(motionState) : null;
    const pose =
      focus === null
        ? fullCameraPose(this._aspect)
        : followCameraPose(this._aspect, focus);
    this._camera.position.set(pose.position.x, pose.position.y, pose.position.z);
    this._camera.lookAt(pose.target.x, pose.target.y, pose.target.z);
  }

  _autoQuality() {
    if (this.quality !== "AUTO") return;
    // Downgrade fast, upgrade slowly with hysteresis; presentation only.
    if (this.effectiveQuality !== "BATTERY" && this._frameEmaMs > 24) {
      this.effectiveQuality = "BATTERY";
      this._applyQualityPreset();
      this._upgradedFrames = 0;
    } else if (this.effectiveQuality === "BATTERY" && this._frameEmaMs < 15) {
      this._upgradedFrames += 1;
      if (this._upgradedFrames > 240) {
        this.effectiveQuality = "BALANCED";
        this._applyQualityPreset();
        this._upgradedFrames = 0;
      }
    } else if (this.effectiveQuality === "BALANCED" && this._frameEmaMs < 13) {
      this._upgradedFrames += 1;
      if (this._upgradedFrames > 600) {
        this.effectiveQuality = "PREMIUM";
        this._applyQualityPreset();
        this._upgradedFrames = 0;
      }
    } else {
      this._upgradedFrames = 0;
    }
  }

  _applyQualityPreset() {
    const preset = QUALITY_PRESETS[this.effectiveQuality];
    if (!preset || !this._renderer) return;
    const dpr =
      typeof devicePixelRatio === "number" && devicePixelRatio > 0
        ? devicePixelRatio
        : 1;
    this._renderer.setPixelRatio(Math.min(dpr, preset.maxPixelRatio));
    try {
      if (this._renderer.shadowMap) {
        this._renderer.shadowMap.enabled = preset.shadows;
      }
      if (this._directional) {
        this._directional.castShadow = preset.shadows;
        if (preset.shadows) {
          this._directional.shadow.mapSize.set(
            preset.shadowMapSize,
            preset.shadowMapSize
          );
        }
      }
    } catch {
      // Shadow control is best-effort per backend; rendering continues.
    }
  }

  /** Quality changes affect pixels/shadows ONLY — never musical truth. */
  setQuality(mode) {
    if (!QUALITY_MODES.includes(mode)) return this.getStatus();
    this.quality = mode;
    if (mode === "COMPATIBILITY") {
      this.dispose();
      this.status = RENDERER_STATUS.IDLE;
      return this.getStatus();
    }
    if (!this._renderer) return this.getStatus();
    this.effectiveQuality = mode === "AUTO" ? "BALANCED" : mode;
    this._applyQualityPreset();
    return this.getStatus();
  }

  resize(cssWidth, cssHeight) {
    if (!this._renderer || !this._camera) return;
    const width = Math.max(1, Math.floor(cssWidth));
    const height = Math.max(1, Math.floor(cssHeight));
    this._aspect = width / height;
    this._camera.aspect = this._aspect;
    this._camera.updateProjectionMatrix();
    this._renderer.setSize(width, height, false);
  }

  setCameraMode(mode) {
    if (CAMERA_MODES.includes(mode)) this.cameraMode = mode;
    return this.cameraMode;
  }

  /**
   * recover: the single recovery path for context loss / device loss /
   * sleep-wake. Rebuilds the renderer stack with the same options; old
   * pose never survives (the next render re-solves from MotionState).
   * Context restoration is asynchronous on the browser side, so the
   * rebuild retries a bounded number of times before declaring the
   * renderer unavailable (COMPATIBILITY degrade).
   */
  async recover() {
    const reloadTierA = this.assetState === "ready";
    this.dispose();
    this.status = RENDERER_STATUS.RECOVERING;
    let lastError = null;
    for (let attempt = 0; attempt < 3; attempt += 1) {
      try {
        await this.initialize();
        if (reloadTierA) await this.loadTierAAsset();
        this.status = RENDERER_STATUS.READY;
        this._lastRecoveryError = null;
        return this.getStatus();
      } catch (error) {
        lastError = error?.message || String(error);
        // A lost WebGL context can poison the canvas ELEMENT for new
        // renderer instances; after the first failure replace it with a
        // clean clone (same id/classes) so a fresh context is created.
        if (
          attempt === 0 &&
          this.canvas &&
          typeof this.canvas.replaceWith === "function"
        ) {
          const fresh = this.canvas.cloneNode(false);
          this.canvas.replaceWith(fresh);
          this.canvas = fresh;
        }
        await new Promise((done) => setTimeout(done, 500 * (attempt + 1)));
      }
    }
    this._lastRecoveryError = lastError;
    this.status = RENDERER_STATUS.UNAVAILABLE;
    return this.getStatus();
  }

  dispose() {
    if (this._contextHandlers) {
      this.canvas.removeEventListener("webglcontextlost", this._contextHandlers.lost);
      this.canvas.removeEventListener("webglcontextrestored", this._contextHandlers.restored);
      const device = this._renderer?.backend?.device;
      if (device && this._contextHandlers.deviceLost) {
        device.lost.removeEventListener("contextlost", this._contextHandlers.deviceLost);
      }
      this._contextHandlers = null;
    }
    for (const disposable of this._disposables) {
      disposable.dispose?.();
    }
    for (const hand of HANDS_SCHEMA) {
      if (this.hands[hand]?.kind === HAND_SOURCES.ASSET) {
        this.hands[hand].dispose?.();
      }
    }
    this._disposables = [];
    this._keyMeshes = new Map();
    this.hands = { LEFT: null, RIGHT: null };
    this._fallbacks = { LEFT: null, RIGHT: null };
    this.handSource = { LEFT: HAND_SOURCES.HIDDEN, RIGHT: HAND_SOURCES.HIDDEN };
    this._scene = null;
    this._camera = null;
    this._directional = null;
    if (this._renderer) {
      try {
        this._renderer.dispose();
      } catch {
        // Already lost — nothing to dispose.
      }
    }
    this._renderer = null;
    this._lastIdentity = null;
    this._lastFrameNow = 0;
  }

  /** Bounded read-only status surface (what the debug API may expose). */
  getStatus() {
    return {
      status: this.status,
      backend: this.backend,
      quality: this.quality,
      effectiveQuality: this.effectiveQuality,
      cameraMode: this.cameraMode,
      hands: { ...this.handSource },
      lastFrameIntervalMs: Math.round(this._frameEmaMs * 100) / 100,
      lastRenderMs: Math.round(this._lastRenderMs * 100) / 100,
      recoveryError: this._lastRecoveryError || null,
      poseWarnings: [...this._lastPoseWarnings],
      assetState: this.assetState,
      assetError: this.assetError,
    };
  }
}

function followFocus(motionState) {
  if (!motionState) return null;
  let sum = 0;
  let count = 0;
  for (const hand of HANDS_SCHEMA) {
    const motion = motionState.hands[hand];
    if (motion && !motion.omitted) {
      sum += motion.root.x;
      count += 1;
    }
  }
  return count > 0 ? sum / count : null;
}

function isBlackKey(midi) {
  return [1, 3, 6, 8, 10].includes(((midi % 12) + 12) % 12);
}
