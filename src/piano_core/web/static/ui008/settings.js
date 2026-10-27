/* PIANO-UI-008 U8-P5: premium Settings presentation models.
 *
 * Presentation ONLY. Settings shows exactly the capabilities that really
 * exist: the accepted browser-local presentation preferences (same
 * localStorage family as the accepted visual-mode / hands preferences)
 * and read-only status. No new persistence, no backend, no musical
 * authority — a local preference can never change musical truth.
 */

export const SETTINGS_FEATURES = Object.freeze({
  visualPreference: true, // local: preferred visualization (accepted pref)
  handsPreference: true, // local: 3D hands visibility/camera/quality
  guidanceStatus: true, // read-only LED status + link to Piano controls
  about: true, // product name + display code + connection (read-only)
  technicalDisclosure: true, // existing opt-in diagnostic disclosure only
  durablePersistence: false, // NO server-side preferences contract exists
  audioOptions: false, // no audio settings contract exists
  safeReset: false, // no reset/recovery command contract exists
  themeChoice: false, // single accepted dark instrument theme only
  reducedMotionOverride: false, // OS-level preference honored; no override
});

/** The local presentation preference groups with consumer wording.
 * Values are the EXACT accepted local-preference values (hands camera
 * FULL/FOLLOW, quality AUTO/PREMIUM/BALANCED/BATTERY/COMPATIBILITY). */
export const VISUAL_PREFERENCE_OPTIONS = Object.freeze([
  Object.freeze({ value: "sheet", label: "Sheet music" }),
  Object.freeze({ value: "falling", label: "Falling notes" }),
  Object.freeze({ value: "hybrid", label: "Both" }),
]);

export const HANDS_CAMERA_OPTIONS = Object.freeze([
  Object.freeze({ value: "FULL", label: "Full keyboard" }),
  Object.freeze({ value: "FOLLOW", label: "Follow hands" }),
]);

export const HANDS_QUALITY_OPTIONS = Object.freeze([
  Object.freeze({ value: "AUTO", label: "Automatic" }),
  Object.freeze({ value: "PREMIUM", label: "Premium" }),
  Object.freeze({ value: "BALANCED", label: "Balanced" }),
  Object.freeze({ value: "BATTERY", label: "Battery saver" }),
  Object.freeze({ value: "COMPATIBILITY", label: "Compatibility" }),
]);

/**
 * One Settings view model (pure). Groups appear only when their
 * capability really exists; everything else stays absent.
 */
export function settingsModel({
  visualMode = null,
  hands = null,
  led = null,
  device = null,
  midiConnection = null,
  coreOffline = false,
} = {}) {
  const groups = [];

  groups.push({
    id: "visuals",
    title: "Visuals",
    description: "How songs look on this device. Saved in this browser only.",
    controls: [
      {
        kind: "segmented",
        id: "settings-visual-mode",
        label: "Preferred song view",
        options: VISUAL_PREFERENCE_OPTIONS,
        value: visualMode,
        hint: "Used when a song opens.",
      },
    ],
  });

  if (hands) {
    groups[0].controls.push(
      {
        kind: "switch",
        id: "settings-hands-enabled",
        label: "Show 3D hands",
        value: hands.enabled === true,
        hint: "Realistic hands over the keyboard.",
      },
      {
        kind: "segmented",
        id: "settings-hands-camera",
        label: "Hands camera",
        options: HANDS_CAMERA_OPTIONS,
        value: hands.camera,
        hint: null,
      },
      {
        kind: "select",
        id: "settings-hands-quality",
        label: "Hands detail",
        options: HANDS_QUALITY_OPTIONS,
        value: hands.quality,
        hint: "Lower detail keeps everything smooth.",
      },
    );
  }

  const ledStatus = settingsLedStatus(led);
  groups.push({
    id: "guidance",
    title: "Guidance",
    description: "Keyboard lights that show you what to play.",
    led: ledStatus,
  });

  groups.push({
    id: "about",
    title: "About",
    description: null,
    rows: settingsAboutRows({ device, midiConnection, coreOffline }),
  });

  return {
    groups,
    persistenceNotice:
      "Preferences marked “saved in this browser” live only on this device. Piano Core keeps no other preferences yet.",
  };
}

/** Consumer LED guidance status row (read-only; controls live on Piano). */
export function settingsLedStatus(led) {
  if (led === null || led === undefined) {
    return { chip: { label: "Lights unavailable", tone: "wait" }, guidance: "Keyboard lights are temporarily unavailable." };
  }
  if (led.configured !== true) {
    return { chip: { label: "No guidance lights", tone: "idle" }, guidance: "This Piano Core has no keyboard lights set up." };
  }
  if (led.enabled !== true) {
    return { chip: { label: "Lights off", tone: "idle" }, guidance: "Keyboard lights are turned off. Turn them on from the Piano screen." };
  }
  if (led.available !== true) {
    return { chip: { label: "Lights unavailable", tone: "bad" }, guidance: "The keyboard lights cannot be reached right now." };
  }
  return { chip: { label: "Guidance lights ready", tone: "ok" }, guidance: "Ready — control them from the Piano screen." };
}

/** Read-only About rows: consumer values only, never secrets or UUIDs. */
export function settingsAboutRows({ device = null, midiConnection = null, coreOffline = false } = {}) {
  const rows = [];
  const connection = coreOffline
    ? "Reconnecting…"
    : midiConnection === "CONNECTED"
      ? "Piano connected"
      : midiConnection
        ? "Waiting for piano"
        : "Checking…";
  rows.push({ label: "Connection", value: connection });
  if (device && typeof device.product === "string" && device.product) {
    rows.push({ label: "Device", value: device.product });
  }
  if (device && typeof device.display_code === "string" && device.display_code) {
    rows.push({ label: "This Piano Core", value: device.display_code });
  }
  return rows;
}
