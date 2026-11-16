# Pi-connected client and Crystal Studio product presentation

Status: proposed for review · 16 November 2026. Owner website revision supersedes the first candidate's presentation copy.

## Presentation website

The website introduces PianoCore through music, guided practice, sheet/falling-note views and Crystal Stage. The owner explicitly removed appliance connection/install instructions and internal release qualifications from visitor copy. The public page has no address form, discovery, login, pricing, availability narrative or installation CTA. Engineering and release evidence belongs in repository records.

The requested material pair is luminous teal glass with fine red glass edges for evening, and cream/gold/warm light for day. The approved transparent crystal piano PNG remains the mark. Four locales and the natural legal/author footer remain. Auto follows the device appearance.

The two silent illustrations use the existing FallingNotesRenderer, five crystal sprites and a bounded extraction of pure keyboard/time mappings. They do not import the app client, schedule audio/MIDI or connect to a device. The sample melody and fingering are illustrative. Motion can be paused, follows reduced-motion preference, and stops while hidden or offscreen. The hero is capped at 30 fps / 2× backing scale; the practice illustration is static. `tools/sync_presentation_assets.py --check` prevents renderer/geometry/sprite drift. The portfolio receives the same native website files.

The native About context alone reveals a return-to-app arrow. It is hidden on the public site. Assets remain an exact allowlist and ship in the wheel; no directory listing or new listener.

## App installation remains inside the client

The client preserves the legacy implicit `/` manifest identity, with `/ui/` scope/start. Real secure-origin browser capability controls the install button; manual/Safari help stays in app Settings. No service worker or API cache is added. No musical Core, MIDI or LED change is introduced by this presentation revision.

Private client release, HTTPS provisioning and physical acceptance remain separate internal engineering steps. The product website is not gated on those operations and does not present their status to visitors.
