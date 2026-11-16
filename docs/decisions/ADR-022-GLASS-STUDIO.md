# ADR-022 — Glass Studio: photo canvas, clear glass, Day / Night

- Status: Proposed (Owner review on the device)
- Date: 2026-11-16
- Supersedes: the colour and surface parts of ADR-021 (Studio design system). Layout, routes, icons and the logo stay.

## Context

The Owner asked for a premium 2.5D glass look like his ArborTwin app, and explicitly
not a dark UI by default: the interface should fit the product, a piano studio.
Day and Night must work like the BaumdienstFlow Münster demo: the same scene photo,
once in daylight and once under warm artificial light.

## Decision

1. **Canvas = one studio photo per route.** Home, Music (and the Library alias), Learn,
   Piano and Settings each have a scene. Landscape screens load the wide photo
   (3840×2160), portrait screens the tall one (1440×3120). The tall photo is composed
   for portrait, never cropped from the wide one.
2. **Day = Night with different light.** Each pair is the same camera and composition;
   only the light changes. Theme values stay `light` (Day) and `dark` (Night);
   `auto` is the default and follows the local clock (Day 07:00–18:59).
3. **Nothing over the photo.** No fog, scrim, wash or tint layer. Readability comes from
   the glass itself. Only text that sits directly on the photo (page title, small
   labels) gets a tight shadow; text inside glass never does.
4. **Clear 2.5D glass.** Cards, navigation, menus, buttons and inputs use
   `backdrop-filter` blur, a thin rim light, a soft top sheen and a soft drop shadow.
   Night glass is a barely tinted dark; Day glass is light frosted white. Cards are
   sized to their content. Multi-line text (Now Playing, Piano identity and strip)
   sits in glass so it reads on any part of any photo.
5. **Song screens stay dark glass** (falling notes, 3D hands) in both themes so notes
   and keys keep their contrast.
6. **Menus open as glass sheets** with a short rise animation (off under
   `prefers-reduced-motion`); the studio behind blurs while a menu is open, without tint.
7. **PWA:** the manifest asks for `fullscreen` (`display_override: fullscreen, standalone`).
   No service worker (the existing contract forbids one intercepting assets).

## Photos

The photos are generated outside the repo (ChatGPT task "LEXO" in the Owner's Drive
folder `CLAUDE_INBOX/PianoCore_sfonde_4K_20261116`) and imported with
`tools/import_studio_backgrounds.py`, which only re-encodes PNG → WebP at the same size.

| Scene | Route(s) | Files |
|---|---|---|
| home | `#/home` | `pc-bg-home-{day,night}-{wide,tall}` |
| music | `#/player`, `#/library` | `pc-bg-music-…` |
| learn | `#/learn` | `pc-bg-learn-…` |
| piano | `#/piano` | `pc-bg-piano-…` |
| settings | `#/settings` | `pc-bg-settings-…` |

Until the photos exist, a warm gradient (Day: cream/sage; Night: plum with amber glow)
is the fallback canvas, so the UI is complete without them.

## Where

- `src/piano_core/web/static/styles.css`, section "PIANO CORE — GLASS STUDIO" and the
  short sections after it.
- `src/piano_core/web/static/ui008/theme.js` (`isDaytime`, default `auto`).
- `src/piano_core/web/static/manifest.webmanifest`.

## Verification

Screenshots at 1440×900 and 390×844, Day and Night, all five routes plus the menus
(Player More, Library, Lights & details, phone More), with a stand-in photo that is
deliberately busy and dark, before the real photos exist. Repeat with the real photos
when they land.
