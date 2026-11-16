# ADR-021 — Studio design system, Music flow, themes and brand mark

Status: ACCEPTED by the Owner on 2026-10-31 and running in production on the
Pi (commit `5443d4f`). Branch `claude/project-thread-mzoqi4` (PR #2) on top of
`feat/premium-learning-studio-ui008`. Deploy record:
`docs/work/evidence/PIANO-UI-STUDIO-DEPLOY-2026-10-31.md`.

Presentation only. Nothing here changes musical authority: Piano Core stays
the single musical truth, and every preference below is browser-local.

## Decisions

### 1. Music replaces the Library menu
- The primary navigation is **Home · Music · Learn · Piano · Settings**.
  `#/library` stays as an alias that opens Music.
- Tapping a song plays it immediately (Spotify-like). Search, Playlists and
  Recent live in a **Library drawer** next to Now Playing, opened with the
  Library button and closed with ×, Escape (phone) or by starting a song
  (phone). It is never open by default.
- Queue: previous / next and auto-advance through the visible list.
- Playlists are stored in `localStorage` on the device (no core API exists).
- Views: **Cover** (per-song art), **Notes** (falling notes), **Hands**
  (3D hands). The 3D hands camera defaults to `FOLLOW`.
- Modules: `ui008/music.js` (`createPlaylistStore`, `createPlayQueue`,
  `createMusicController`, `songArt`, `paintCover`).

### 2. Studio visual language
- Same language as the owner's portfolio (redioncekrezi.de): faint
  engineering grid, solid cards with a hairline border and a cyan top
  highlight, cyan + violet accents, mono uppercase eyebrows, white (dark
  theme) or ink (light theme) primary actions, compact pill controls.
- One CSS layer, "PIANO CORE — STUDIO", at the end of `styles.css` owns
  colour, surfaces and controls through tokens (`--fg`, `--ink`, `--card`,
  `--edge`, `--primary-*`, `--glow-*`, `--cyan`, `--violet`). No further
  theme layers are stacked on top of it.
- Song covers are generated, not images: a stable level-meter in one of 8
  accent pairs per song (FNV hash of the song id). No letters on gradients.

### 3. Two themes
- **Dark** (default): lifted charcoal canvas `#12141c` with soft cyan,
  violet and ice-red glows, like the covers. **Light**: `#f3f4f8` canvas,
  white cards, deeper cyan/violet accents for contrast.
- Settings › Appearance: **Dark / Light / Auto** (Auto follows
  `prefers-color-scheme`). Stored as `piano-core.theme` in `localStorage`;
  applied as `html[data-theme]`; `theme-color` meta follows. Module
  `ui008/theme.js`; `SETTINGS_FEATURES.themeChoice` is now `true`.
- Song screens (falling notes, keyboard, 3D hands) stay dark in both themes
  so notes and keys stay readable; they re-declare the dark tokens.

### 4. Brand mark and icons
- New dedicated logo: a white grand piano (top view, lid curve, keyboard
  edge) on an ice-red squircle (`#ff9db0 → #f0304f → #a3123a → #5c0b2b`)
  with a frost highlight and a cool cyan rim. Used as `#pc-logo` in the
  inline sprite and as `favicon.svg`.
- Icons are an inline SVG sprite in `index.html`: 1.6 px rounded strokes
  with a soft duotone fill (`.ico-tone`), no boxes or tiles around them.
  The active navigation icon glows in the accent colour.

## Constraints kept
- No external fonts, CDNs or `http(s)://` in `index.html`, `styles.css`,
  `app.js` or the manifest (`tests/test_web_contract.py`), no inline script.
- Fake-DOM unit tests: state goes through `dataset`/attributes, not
  `style.setProperty`.

## Verification
- Python: 1241 tests OK (1 skip), run as a non-root user.
- Node UI tests: all `tests/weblogic/*.test.mjs` pass except
  `release_harness.test.mjs`, which needs the npm `ws` package (pre-existing).
- Every route and sheet screenshotted in both themes on tablet (1280×800)
  and phone (390×844) with a seeded Learn catalog before deploy.
- Production: deployed with the Pi deploy helper; service active, `/ui/`,
  `theme.js`, `music.js`, `styles.css` served 200. The Roland was switched
  off during the deploys (MIDI "disconnected" was pre-existing, not a
  regression); a physical acceptance run with the piano on is still open.
