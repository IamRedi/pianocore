/* PIANO-UI-007 — MotionEngine (pure hand-motion intelligence).
 *
 * Renderer-independent, browser-independent, Node-testable. This module
 * is free of Three.js, free of DOM access, deterministic and stateless:
 * `evaluate` is a pure function of (view, canonical song position,
 * tempo rate) and never replays history, keeps timers, or owns musical
 * time. Canonical song time is the ONLY clock.
 *
 * Authority chain (absolute):
 *   ScoreProjection -> FingeringPlan -> MotionEngine -> IK/Pose -> HandRenderer
 *
 * UI-006 remains the only fingering authority. This module never parses
 * MIDI, never selects hand/finger, never reassigns a key, never grades
 * Practice, never advances Learning, never controls Playback or LEDs,
 * and is never a musical timing authority. When musical truth and visual
 * perfection conflict, musical truth wins (contact immutability).
 *
 * Keyboard X truth: every world X coordinate derives from the canonical
 * UI-003/UI-005 88-key geometry fractions passed in by the caller (the
 * ONE keyboard model). Only presentation-level Y/Z dimensions are
 * defined here.
 */

export const MOTION_SCHEMA_VERSION = 1;
export const POSE_SCHEMA_VERSION = 1;

/** The locked deterministic phase model (canonical song time only). */
export const PHASES = Object.freeze([
  "PREPARE",
  "APPROACH",
  "PRESS",
  "HOLD",
  "RELEASE",
  "RECOVER",
]);

export const HANDS = Object.freeze(["LEFT", "RIGHT"]);

/** Presentation-level keyboard dimensions (meters). X truth is never
 * defined here — it always comes from the canonical geometry fractions. */
export const KEYBOARD_WIDTH_M = 1.222; // 52 white keys at ~23.5 mm
export const WHITE_KEY_LENGTH_M = 0.15;
export const BLACK_KEY_LENGTH_M = 0.095;
export const BLACK_KEY_RISE_M = 0.012;
export const KEY_DIP_M = 0.01;
// z = 0 is the front edge of the white keys (player side); keys extend
// toward negative z (fallboard). Contact bands are immutable per key type.
export const CONTACT_Z_FRACTION_WHITE = 0.6; // back from the front edge
export const CONTACT_Z_FRACTION_BLACK = 0.3; // near the black's front edge

/** Motion timing, in canonical song seconds. */
export const PRESS_SECONDS = 0.05;
export const RELEASE_SECONDS = 0.12;
export const RECOVER_SECONDS = 0.2;
export const APPROACH_MIN_LEAD_SECONDS = 0.03;
export const APPROACH_MAX_LEAD_SECONDS = 0.6;
/** Hand/finger travel speed used to derive the approach lead: the lead in
 * song-seconds needed to cover distance d (meters) at tempo rate r is
 * d * r / WALL_TRAVEL_SPEED_M_PER_S (one song-second lasts 1/r wall
 * seconds, so the finger has d/r wall-seconds and travels at most
 * WALL_TRAVEL_SPEED_M_PER_S * d/r meters). */
export const WALL_TRAVEL_SPEED_M_PER_S = 0.9;
/** How far ahead/behind (song seconds) an attack contributes to the hand
 * root anchor. Presentation only. */
export const ROOT_LOOKAHEAD_SECONDS = 0.8;
/** Minimum world-X separation between the two hand roots (meters). */
export const MIN_HAND_SEPARATION_M = 0.09;

/** Canonical hand model (presentation-level; shared by every rig). */
export const FINGER_MODEL = Object.freeze({
  // phalange lengths [proximal, middle, distal] in meters
  1: Object.freeze([0.042, 0.036, 0.03]), // thumb
  2: Object.freeze([0.045, 0.03, 0.024]), // index
  3: Object.freeze([0.05, 0.034, 0.026]), // middle
  4: Object.freeze([0.046, 0.032, 0.025]), // ring
  5: Object.freeze([0.036, 0.024, 0.019]), // little
});
export const KNUCKLE_X_OFFSETS_M = Object.freeze({
  // RIGHT hand, +x toward the little finger (higher keys); LEFT mirrors.
  1: -0.048, // thumb
  2: -0.024,
  3: 0.0,
  4: 0.024,
  5: 0.048,
});
export const PALM_LENGTH_M = 0.088; // wrist -> knuckle line distance
export const PALM_WIDTH_M = 0.096;
export const THUMB_BASE_Z_M = -0.03; // thumb CMC sits forward of the wrist
export const THUMB_BASE_Y_M = -0.012;
export const THUMB_OPPOSITION_YAW = 0.55; // radians toward the key plane
export const FINGER_THICKNESS_M = 0.016;
export const HOVER_HEIGHT_M = 0.055; // root hover above the key tops
export const HOVER_GAP_M = 0.018; // fingertip hover before a press
export const WRIST_Z_M = 0.055; // wrist sits toward the player of z=0
export const WRIST_MAX_SHIFT_M = 0.035; // bounded wrist correction
export const WRIST_MAX_LATERAL_M = 0.03; // bounded lateral collision shift
export const INACTIVE_LIFT_STEP_M = 0.02; // collision-priority lift step
export const FINGER_IDLE_CURL = Object.freeze([-0.2, -0.7, -1.1]); // abs angles

/** Locked warning vocabulary (deterministic strings). */
export const MOTION_WARNINGS = Object.freeze({
  OCCUPANCY_VIOLATION: "plan_occupancy_violation",
  INVALID_TARGET_DROPPED: "invalid_target_dropped",
  HAND_OMITTED: "hand_omitted",
  POSE_CLAMPED: "pose_clamped",
  COLLISION_UNRESOLVED: "collision_unresolved",
});

const BLACK_PITCH_CLASSES = new Set([1, 3, 6, 8, 10]);
const EPS = 1e-9;

function isFiniteNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

function clamp(value, min, max) {
  return value < min ? min : value > max ? max : value;
}

function smoothstep(t) {
  const x = clamp(t, 0, 1);
  return x * x * (3 - 2 * x);
}

/** World X (meters, keyboard center at 0) for one canonical X fraction. */
export function worldXForFraction(fraction) {
  return (fraction - 0.5) * KEYBOARD_WIDTH_M;
}

/** World X for one MIDI key from a canonical center fraction map. */
export function worldXForKey(midi, centerFractions) {
  const fraction = centerFractions.get(midi);
  return fraction === undefined ? null : worldXForFraction(fraction);
}

/** The immutable fingertip contact point for one key (canonical geometry
 * + presentation constants only; fixed for the whole lifetime of the
 * attack — collision/IK/rendering may never move it). */
export function contactPointForKey(midi, centerFractions) {
  const fraction = centerFractions.get(midi);
  if (fraction === undefined) return null;
  const black = BLACK_PITCH_CLASSES.has(((midi % 12) + 12) % 12);
  const x = worldXForFraction(fraction);
  if (black) {
    const frontZ = -(WHITE_KEY_LENGTH_M - BLACK_KEY_LENGTH_M);
    return {
      x,
      y: BLACK_KEY_RISE_M,
      z: frontZ - CONTACT_Z_FRACTION_BLACK * BLACK_KEY_LENGTH_M,
      band: "black",
    };
  }
  return {
    x,
    y: 0,
    z: -CONTACT_Z_FRACTION_WHITE * WHITE_KEY_LENGTH_M,
    band: "white",
  };
}

/* --------------------------------------------------------------------
 * Motion view construction (occupancy validation happens HERE)
 * -------------------------------------------------------------------- */

function validTarget(target) {
  return (
    target &&
    typeof target === "object" &&
    Number.isInteger(target.score_index) && target.score_index >= 0 &&
    typeof target.target_id === "string" && target.target_id.length > 0 &&
    Number.isInteger(target.midi) && target.midi >= 21 && target.midi <= 108 &&
    Number.isInteger(target.channel) && target.channel >= 1 && target.channel <= 16 &&
    isFiniteNumber(target.onset_seconds) && target.onset_seconds >= 0 &&
    isFiniteNumber(target.duration_seconds) && target.duration_seconds >= 0 &&
    (target.hand === "LEFT" || target.hand === "RIGHT") &&
    Number.isInteger(target.finger) && target.finger >= 1 && target.finger <= 5
  );
}

function compareAttacks(a, b) {
  return a.onset - b.onset || a.midi - b.midi || a.scoreIndex - b.scoreIndex;
}

/** Phase window ends for one attack (pure; independent of tempo rate —
 * only the APPROACH start depends on the lead). */
function attackWindows(attack) {
  const pressEnd = attack.onset + PRESS_SECONDS;
  let releaseStart;
  let releaseEnd;
  if (attack.duration > 0) {
    releaseStart = Math.max(pressEnd, attack.onset + attack.duration - RELEASE_SECONDS);
    releaseEnd = releaseStart + RELEASE_SECONDS;
  } else {
    // Zero-duration: never held; press then release immediately.
    releaseStart = pressEnd;
    releaseEnd = pressEnd + RELEASE_SECONDS;
  }
  return { pressEnd, releaseStart, releaseEnd, recoverEnd: releaseEnd + RECOVER_SECONDS };
}

/**
 * Build the deterministic motion view from one validated fingering plan
 * plus the canonical key-center fractions. Defensive by contract:
 *
 * - malformed/non-finite targets are DROPPED (never thrown, never
 *   repaired) with an `invalid_target_dropped` warning;
 * - same hand+finger whose half-open occupancies [onset, onset+duration)
 *   overlap over DIFFERENT keys is a `plan_occupancy_violation`: the
 *   whole hand is marked omitted (hidden) and the other hand stays
 *   usable — no reassignment, no alternative fingering, no key changes;
 * - same-MIDI overlaps are aliases, not violations;
 * - zero-duration attacks press and release but create no occupancy.
 *
 * This path is deterministic and non-throwing for any payload.
 */
export function createMotionView({ songId, sourceVersion, plan, geometry }) {
  if (!geometry || !(geometry.centerFractions instanceof Map)) {
    throw new TypeError("geometry.centerFractions Map is required");
  }
  const warnings = [];
  const byHand = { LEFT: [], RIGHT: [] };
  let dropped = 0;
  const targets = plan && Array.isArray(plan.targets) ? plan.targets : [];
  for (const target of targets) {
    const contact = validTarget(target)
      ? contactPointForKey(target.midi, geometry.centerFractions)
      : null;
    if (!contact) {
      dropped += 1;
      continue;
    }
    byHand[target.hand].push({
      onset: target.onset_seconds,
      duration: target.duration_seconds,
      midi: target.midi,
      scoreIndex: target.score_index,
      targetId: target.target_id,
      finger: target.finger,
      contact,
      xWorld: contact.x,
    });
  }
  if (dropped > 0) {
    warnings.push(`${MOTION_WARNINGS.INVALID_TARGET_DROPPED}:${dropped}`);
  }

  const hands = {};
  for (const hand of HANDS) {
    const attacks = byHand[hand].sort(compareAttacks);
    const omission = validateOccupancy(attacks);
    if (omission) {
      warnings.push(`${MOTION_WARNINGS.OCCUPANCY_VIOLATION}:${hand}`);
      warnings.push(`${MOTION_WARNINGS.HAND_OMITTED}:${hand}`);
      hands[hand] = {
        hand,
        omitted: true,
        omittedReason: MOTION_WARNINGS.OCCUPANCY_VIOLATION,
        attacks: [],
        restX: null,
      };
      continue;
    }
    // Deterministic rest anchor: the mean key X of this hand's attacks.
    let restX = null;
    if (attacks.length > 0) {
      let sum = 0;
      for (const attack of attacks) {
        sum += attack.xWorld;
      }
      restX = sum / attacks.length;
    }
    hands[hand] = {
      hand,
      omitted: false,
      omittedReason: null,
      attacks,
      restX,
    };
  }

  return Object.freeze({
    schemaVersion: MOTION_SCHEMA_VERSION,
    identity: Object.freeze({
      songId: typeof songId === "string" ? songId : null,
      sourceVersion: typeof sourceVersion === "string" ? sourceVersion : null,
      planId: plan && typeof plan.plan_id === "string" ? plan.plan_id : null,
    }),
    hands: Object.freeze(hands),
    warnings: Object.freeze(warnings),
  });
}

/**
 * Fail-closed occupancy validation. Returns true when the hand must be
 * omitted. Half-open intervals [onset, onset + duration); same hand +
 * finger overlapping over DIFFERENT MIDI keys is a violation. Same-MIDI
 * aliases are allowed (the contact is held to the longest end).
 * Zero-duration attacks create no occupancy.
 */
export function validateOccupancy(attacks) {
  const open = { 1: null, 2: null, 3: null, 4: null, 5: null };
  for (const attack of attacks) {
    if (!(attack.duration > 0)) continue; // zero-duration: no occupancy
    const held = open[attack.finger];
    if (held && held.end > attack.onset && held.midi !== attack.midi) {
      return true;
    }
    open[attack.finger] = {
      end: held && held.midi === attack.midi
        ? Math.max(held.end, attack.onset + attack.duration)
        : attack.onset + attack.duration,
      midi: attack.midi,
    };
  }
  return false;
}

function approachLead(xWorld, xOrigin, tempoRate) {
  const distance = Math.abs(xWorld - xOrigin);
  const raw = (distance * tempoRate) / WALL_TRAVEL_SPEED_M_PER_S;
  return clamp(raw, APPROACH_MIN_LEAD_SECONDS, APPROACH_MAX_LEAD_SECONDS);
}

/* --------------------------------------------------------------------
 * MotionEngine — the stateless evaluator
 * -------------------------------------------------------------------- */

function emptyFingerMotion(finger, phase) {
  return {
    finger,
    phase,
    midi: null,
    scoreIndex: null,
    targetId: null,
    contact: null,
    press: 0,
    lift: 1,
    approachProgress: null,
    origin: null,
  };
}

/** Phase/press/lift for one finger at position p from its enriched
 * attack list. Governing attack = the latest attack whose APPROACH has
 * started (the newer attack always wins from its approach start). */
function fingerStateAt(finger, attackList, p) {
  let governing = null;
  for (const attack of attackList) {
    if (p >= attack.onset - attack.lead) governing = attack;
    else break;
  }
  if (!governing) {
    return emptyFingerMotion(finger, "PREPARE");
  }
  const a = governing;
  const index = attackList.indexOf(a);
  const previous = index > 0 ? attackList[index - 1] : null;
  // The approach origin is the previous attack's key surface on the same
  // finger (fingers travel key-to-key), else the hand rest hover line.
  const origin = previous
    ? { x: previous.contact.x, y: previous.contact.y, z: previous.contact.z }
    : null;

  const motion = emptyFingerMotion(a.finger, "PREPARE");
  motion.midi = a.midi;
  motion.scoreIndex = a.scoreIndex;
  motion.targetId = a.targetId;
  motion.contact = a.contact;
  motion.origin = origin;

  if (p < a.onset) {
    // APPROACH: travel from the origin to the contact hover; press 0.
    const start = a.onset - a.lead;
    const t = a.lead > EPS ? clamp((p - start) / a.lead, 0, 1) : 1;
    motion.phase = "APPROACH";
    motion.approachProgress = smoothstep(t);
    motion.lift = 1 - 0.5 * motion.approachProgress;
    return motion;
  }
  if (p < a.windows.pressEnd) {
    // PRESS: 0 -> 1 exactly at the canonical onset.
    motion.phase = "PRESS";
    motion.press = smoothstep((p - a.onset) / Math.max(PRESS_SECONDS, EPS));
    motion.lift = 0;
    return motion;
  }
  if (p < a.windows.releaseStart) {
    motion.phase = "HOLD";
    motion.press = 1;
    motion.lift = 0;
    return motion;
  }
  if (p < a.windows.releaseEnd) {
    motion.phase = "RELEASE";
    motion.press = 1 - smoothstep(
      (p - a.windows.releaseStart) /
        Math.max(a.windows.releaseEnd - a.windows.releaseStart, EPS)
    );
    motion.lift = 0;
    return motion;
  }
  if (p < a.windows.recoverEnd) {
    // RECOVER: lift back toward hover over the same immutable band.
    motion.phase = "RECOVER";
    motion.lift = smoothstep(
      (p - a.windows.releaseEnd) /
        Math.max(a.windows.recoverEnd - a.windows.releaseEnd, EPS)
    );
    return motion;
  }
  // Past the recovery of the latest started attack: hover rest.
  motion.phase = "PREPARE";
  motion.lift = 1;
  return motion;
}

/**
 * The pure MotionEngine. `evaluate(view, positionSeconds, tempoRate)`
 * returns one immutable MotionState. Canonical song time is the only
 * input clock; pause freezes the pose because position stops moving;
 * seek reconstructs immediately because nothing is stateful; the view
 * is never mutated.
 *
 * Throws TypeError for non-finite positionSeconds/tempoRate (the caller
 * boundary guards; the module never silently invents time).
 */
export function evaluate(view, positionSeconds, tempoRate) {
  if (!view || view.schemaVersion !== MOTION_SCHEMA_VERSION) {
    throw new TypeError("evaluate requires a createMotionView result");
  }
  if (!isFiniteNumber(positionSeconds) || positionSeconds < 0) {
    throw new TypeError("positionSeconds must be a finite non-negative number");
  }
  if (!isFiniteNumber(tempoRate) || tempoRate <= 0) {
    throw new TypeError("tempoRate must be a finite positive number");
  }
  const p = positionSeconds;

  const hands = {};
  for (const hand of HANDS) {
    const handView = view.hands[hand];
    if (!handView || handView.omitted || handView.attacks.length === 0) {
      hands[hand] = null;
      continue;
    }
    hands[hand] = handMotionAt(handView, p, tempoRate);
  }
  separateHands(hands);

  return Object.freeze({
    schemaVersion: MOTION_SCHEMA_VERSION,
    identity: view.identity,
    positionSeconds: p,
    tempoRate,
    hands: Object.freeze(hands),
    warnings: view.warnings,
  });
}

/** Per-finger local enrichment (lead with THIS tempo rate and the
 * finger's real previous key). Pure: the view is never modified. */
function enrichByFinger(handView, tempoRate) {
  const byFinger = { 1: [], 2: [], 3: [], 4: [], 5: [] };
  for (const attack of handView.attacks) byFinger[attack.finger].push(attack);
  const enriched = {};
  for (const fingerKey of Object.keys(byFinger)) {
    const finger = Number(fingerKey);
    enriched[finger] = [];
    let previousX = handView.restX;
    for (const attack of byFinger[finger]) {
      enriched[finger].push({
        ...attack,
        lead: approachLead(attack.xWorld, previousX, tempoRate),
        windows: attackWindows(attack),
      });
      previousX = attack.xWorld;
    }
  }
  return enriched;
}

function handMotionAt(handView, p, tempoRate) {
  const enriched = enrichByFinger(handView, tempoRate);
  const fingers = {};
  for (let finger = 1; finger <= 5; finger += 1) {
    fingers[finger] = fingerStateAt(finger, enriched[finger], p);
  }

  // Root/wrist anchor: centroid of approaches/attacks near p (the hand
  // drifts toward its material), else the hand's rest anchor.
  let anchorSum = 0;
  let anchorCount = 0;
  for (const fingerKey of Object.keys(enriched)) {
    for (const attack of enriched[fingerKey]) {
      const from = attack.onset - attack.lead - ROOT_LOOKAHEAD_SECONDS;
      const to = attackWindows(attack).recoverEnd + ROOT_LOOKAHEAD_SECONDS;
      if (p >= from && p <= to) {
        anchorSum += attack.xWorld;
        anchorCount += 1;
      }
    }
  }
  const rootX = anchorCount > 0 ? anchorSum / anchorCount : handView.restX;

  let span = 0;
  for (let finger = 1; finger <= 5; finger += 1) {
    const motion = fingers[finger];
    if (motion.contact && (motion.press > 0 || motion.phase === "APPROACH")) {
      span = Math.max(span, Math.abs(motion.contact.x - rootX));
    }
  }
  const yaw = clamp(span * -2.2, -0.35, 0.35) * (handView.hand === "LEFT" ? 1 : -1);

  return {
    hand: handView.hand,
    omitted: false,
    omittedReason: null,
    root: { x: rootX, y: HOVER_HEIGHT_M, z: -0.01 },
    wrist: { x: rootX, y: HOVER_HEIGHT_M * 0.6, z: WRIST_Z_M, yaw },
    fingers,
  };
}

/** Presentation-only deterministic separation so the two hand roots
 * never overlap; it can never change any finger's key target. */
function separateHands(hands) {
  const left = hands.LEFT;
  const right = hands.RIGHT;
  if (!left || !right) return;
  const delta = right.root.x - left.root.x;
  if (delta >= MIN_HAND_SEPARATION_M) return;
  const push = (MIN_HAND_SEPARATION_M - delta) / 2;
  left.root.x -= push;
  left.wrist.x -= push;
  right.root.x += push;
  right.wrist.x += push;
}

/* --------------------------------------------------------------------
 * Analytical IK / pose with the locked collision priority
 * -------------------------------------------------------------------- */

function fingerBase(wrist, finger, hand, splay) {
  const mirror = hand === "LEFT" ? -1 : 1;
  const offsetX = KNUCKLE_X_OFFSETS_M[finger] * mirror * splay;
  if (finger === 1) {
    return {
      x: wrist.x + offsetX,
      y: wrist.y + THUMB_BASE_Y_M,
      z: wrist.z + THUMB_BASE_Z_M,
    };
  }
  return {
    x: wrist.x + offsetX,
    y: wrist.y,
    z: wrist.z - PALM_LENGTH_M,
  };
}

/** Where the fingertip must be for one finger motion, given an extra
 * lift applied ONLY to non-pressed fingers (collision priority 4).
 * Active pressed fingers hold the immutable contact band; the only
 * vertical motion is the key surface dipping under the press. */
function fingerTargetPoint(fingerMotion, inactiveLift) {
  if (!fingerMotion.contact) return null;
  const c = fingerMotion.contact;
  if (fingerMotion.press > 0) {
    return { x: c.x, y: c.y - KEY_DIP_M * fingerMotion.press, z: c.z };
  }
  const gapY = c.y + HOVER_GAP_M + inactiveLift;
  if (fingerMotion.phase === "APPROACH") {
    const t = fingerMotion.approachProgress ?? 1;
    const origin = fingerMotion.origin;
    if (!origin) return { x: c.x, y: gapY, z: c.z };
    return {
      x: origin.x + (c.x - origin.x) * t,
      y: origin.y + (gapY - origin.y) * t,
      z: origin.z + (c.z - origin.z) * t,
    };
  }
  // RELEASE / RECOVER / rest PREPARE at the same key: rise off the band.
  return { x: c.x, y: gapY * fingerMotion.lift + c.y * (1 - fingerMotion.lift), z: c.z };
}

/**
 * Exact planar two-link solve for one finger in its vertical curl plane.
 * The distal assembly (middle + distal phalanges) stays colinear, so the
 * fingertip lands EXACTLY on its target when reachable. Absolute in-plane
 * joint angles are returned ([proximal, middle, distal]; distal angle
 * equals the middle angle — DIP relative bend 0). Elbow configuration is
 * deterministic: the middle knuckle arches toward the player.
 */
function solvePlanar2R(l1, l23, q, v) {
  const r = Math.hypot(q, v);
  const max = l1 + l23 - 1e-6;
  const min = Math.abs(l1 - l23) + 1e-6;
  if (r > max || r < min) return null;
  const cosBend = (r * r - l1 * l1 - l23 * l23) / (2 * l1 * l23);
  const bend = Math.acos(clamp(cosBend, -1, 1)); // relative curl at the PIP
  const beta = Math.atan2(v, q);
  const alpha1 =
    beta + Math.atan2(l23 * Math.sin(bend), l1 + l23 * Math.cos(bend));
  const alpha2 = alpha1 - bend;
  return { angles: [alpha1, alpha2, alpha2], reach: r };
}

function solveFingerOnce(fm, base, inactiveLift, hand) {
  const lengths = FINGER_MODEL[fm.finger];
  const l1 = lengths[0];
  const l23 = lengths[1] + lengths[2];
  const target = fingerTargetPoint(fm, inactiveLift);
  if (!target) {
    // Idle plane: fingers point toward the fallboard (planeYaw PI); the
    // thumb keeps its deterministic opposition. Presentation only.
    const mirror = hand === "LEFT" ? -1 : 1;
    const planeYaw =
      fm.finger === 1 ? Math.PI - THUMB_OPPOSITION_YAW * mirror : Math.PI;
    return {
      joints: FINGER_IDLE_CURL,
      base,
      elbow: null,
      tip: null,
      clamped: false,
      planeYaw,
    };
  }
  // Vertical curl plane containing base and target: q = horizontal
  // radial coordinate, v = vertical. planeYaw orients the rig only —
  // it never moves the tip off the target.
  const wx = target.x - base.x;
  const wz = target.z - base.z;
  const q = Math.hypot(wx, wz);
  const v = target.y - base.y;
  const planeYaw = Math.atan2(wx, -wz);
  const solve = solvePlanar2R(l1, l23, q, v);
  if (!solve) {
    // Unreachable even after the bounded wrist correction: clamp the
    // pose at the reach boundary along the SAME target direction —
    // the key, hand, finger and band are never touched.
    const r = Math.hypot(q, v);
    const scale = (l1 + l23 - 1e-6) / Math.max(r, EPS);
    return {
      joints: [Math.atan2(v, q), Math.PI - 0.02, Math.PI - 0.02],
      base,
      elbow: {
        x: base.x + (wx * l1) / Math.max(r, EPS),
        y: base.y + (v * l1) / Math.max(r, EPS),
        z: base.z + (wz * l1) / Math.max(r, EPS),
      },
      tip: {
        x: base.x + wx * scale,
        y: base.y + v * scale,
        z: base.z + wz * scale,
      },
      clamped: true,
      planeYaw,
    };
  }
  const [a1] = solve.angles;
  return {
    joints: solve.angles,
    base,
    elbow: {
      x: base.x + Math.sin(planeYaw) * l1 * Math.cos(a1),
      y: base.y + l1 * Math.sin(a1),
      z: base.z + Math.cos(planeYaw) * l1 * Math.cos(a1),
    },
    tip: { x: target.x, y: target.y, z: target.z },
    clamped: false,
    planeYaw,
  };
}

function solveAllFingers(motion, wrist, splay, inactiveLift) {
  const solved = {};
  let clamped = false;
  for (let finger = 1; finger <= 5; finger += 1) {
    const result = solveFingerOnce(
      motion.fingers[finger],
      fingerBase(wrist, finger, motion.hand, splay),
      inactiveLift,
      motion.hand
    );
    solved[finger] = result;
    if (result.clamped) clamped = true;
  }
  return { solved, clamped };
}

/** Minimal distance between two finger polylines ([base, elbow, tip]). */
function polylineDistance(a, b) {
  const segDist = (p, s0, s1) => {
    const vx = s1.x - s0.x;
    const vy = s1.y - s0.y;
    const vz = s1.z - s0.z;
    const wx = p.x - s0.x;
    const wy = p.y - s0.y;
    const wz = p.z - s0.z;
    const den = vx * vx + vy * vy + vz * vz;
    const t = den > EPS ? clamp((wx * vx + wy * vy + wz * vz) / den, 0, 1) : 0;
    return Math.hypot(
      p.x - (s0.x + vx * t),
      p.y - (s0.y + vy * t),
      p.z - (s0.z + vz * t)
    );
  };
  let min = Infinity;
  for (const p of a) {
    for (let i = 0; i + 1 < b.length; i += 1) {
      min = Math.min(min, segDist(p, b[i], b[i + 1]));
    }
  }
  for (const p of b) {
    for (let i = 0; i + 1 < a.length; i += 1) {
      min = Math.min(min, segDist(p, a[i], a[i + 1]));
    }
  }
  return min;
}

function hasCollision(solved) {
  const lines = [];
  for (let finger = 1; finger <= 5; finger += 1) {
    const f = solved[finger];
    if (f.elbow && f.tip) lines.push([f.base, f.elbow, f.tip]);
  }
  const minGap = FINGER_THICKNESS_M * 0.9;
  for (let i = 0; i < lines.length; i += 1) {
    for (let j = i + 1; j < lines.length; j += 1) {
      if (polylineDistance(lines[i], lines[j]) < minGap) return true;
    }
  }
  return false;
}

/**
 * Deterministic analytical hand pose for one hand's MotionState.
 *
 * Hard invariants, in the locked collision priority order:
 *   1. active contact fixed (key/hand/finger/band never move)
 *   2. bounded wrist/root adjustment
 *   3. non-contact joint posture (deterministic idle curl)
 *   4. transitioning/inactive-finger lift
 *   5. secondary pose clamp (knuckle splay)
 *   6. warning + visual imperfection (collision_unresolved)
 *
 * One bounded pass per stage — no iteration loops, no physics, and the
 * solver may never select another key or finger to improve appearance.
 */
export function solveHandPose(motion) {
  if (!motion || typeof motion !== "object" || motion.omitted) {
    throw new TypeError("solveHandPose requires one present hand's motion state");
  }
  const warnings = [];

  // Priority 2: ONE bounded wrist correction toward hard-to-reach keys
  // (down, toward the keys, and laterally toward the unreachable targets).
  const wrist = { ...motion.wrist };
  let lateralSum = 0;
  let lateralCount = 0;
  let needsReach = false;
  for (let finger = 1; finger <= 5; finger += 1) {
    const target = fingerTargetPoint(motion.fingers[finger], 0);
    if (!target) continue;
    const base = fingerBase(wrist, finger, motion.hand, 1);
    const lengths = FINGER_MODEL[finger];
    const reach = lengths[0] + lengths[1] + lengths[2];
    const r = Math.hypot(
      Math.hypot(target.x - base.x, target.z - base.z),
      target.y - base.y
    );
    if (r > reach - 1e-6) {
      needsReach = true;
      lateralSum += target.x - base.x;
      lateralCount += 1;
    }
  }
  if (needsReach) {
    wrist.y = Math.max(wrist.y - WRIST_MAX_SHIFT_M * 0.5, HOVER_GAP_M * 0.2);
    wrist.z -= WRIST_MAX_SHIFT_M;
    if (lateralCount > 0) {
      wrist.x += clamp(
        lateralSum / lateralCount,
        -WRIST_MAX_LATERAL_M,
        WRIST_MAX_LATERAL_M
      );
    }
  }

  // Priority 3 (idle posture is intrinsic to the solver), then 4.
  let result = solveAllFingers(motion, wrist, 1, INACTIVE_LIFT_STEP_M);
  // Priority 5: secondary clamp — narrow the knuckle splay once.
  if (hasCollision(result.solved)) {
    result = solveAllFingers(motion, wrist, 0.8, INACTIVE_LIFT_STEP_M * 2);
  }
  // Priority 6: unresolved — warn; musical truth stays untouched.
  if (hasCollision(result.solved)) {
    warnings.push(MOTION_WARNINGS.COLLISION_UNRESOLVED);
  }
  if (result.clamped) {
    warnings.push(MOTION_WARNINGS.POSE_CLAMPED);
  }
  return {
    schemaVersion: POSE_SCHEMA_VERSION,
    hand: motion.hand,
    clamped: result.clamped,
    warnings: Object.freeze(warnings),
    wrist,
    splay: 1,
    fingers: result.solved,
  };
}

/** True when every pressed fingertip sits exactly on its canonical
 * contact point (the contact-immutability assertion used by tests and
 * the renderer). */
export function contactIntegrity(motion, pose) {
  for (let finger = 1; finger <= 5; finger += 1) {
    const fm = motion.fingers[finger];
    const solvedFinger = pose.fingers[finger];
    if (fm.contact && fm.press > 0) {
      if (!solvedFinger.tip || solvedFinger.clamped) return false;
      const expectedY = fm.contact.y - KEY_DIP_M * fm.press;
      if (
        Math.abs(solvedFinger.tip.x - fm.contact.x) > 1e-6 ||
        Math.abs(solvedFinger.tip.y - expectedY) > 1e-6 ||
        Math.abs(solvedFinger.tip.z - fm.contact.z) > 1e-6
      ) {
        return false;
      }
    }
  }
  return true;
}
