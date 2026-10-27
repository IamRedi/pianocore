"""Packaged web UI surface for Piano Core (PIANO-P013).

The UI is a lightweight first-party frontend (vanilla HTML/CSS/
browser-native ES modules) served by the EXISTING stdlib HTTP server on
the same origin as the ``/api/v1`` control API. The browser is a client
of the P012 API only: no business logic lives here and the UI assets are
pure static files with no build step, no npm/Node production chain, no
CDN assets, and no external fonts — the appliance must work offline.

Serving contract (enforced with :mod:`~piano_core.api.server`):

- the UI is an ALLOWLIST, never a directory listing: only the exact
  asset names in :data:`UI_ASSETS` are reachable, resolved through
  ``importlib.resources`` inside the installed package;
- arbitrary filesystem paths, parent-directory segments, and
  percent-encoded traversal attempts never resolve (membership check
  happens AFTER decoding, on the decoded asset name);
- unknown paths fall through to the deterministic JSON ``404 not_found``
  error model — no repository or appliance filesystem details leak;
- responses carry explicit content types and conservative ``no-cache``
  caching (early appliance phase: correctness over caching cleverness —
  a deployment can never strand a browser on stale JS/CSS).

``ui_enabled`` (default ``true``) only gates serving when the API server
is enabled; the UI adds no listener, no dependency, and no network
surface of its own.
"""

from __future__ import annotations

from importlib import resources

__all__ = ["UI_ASSETS", "read_ui_asset", "resolve_ui_asset", "resolve_ui_path"]

#: Package that carries the static assets (shipped inside the wheel).
_STATIC_PACKAGE = "piano_core.web.static"

#: Allowlisted UI assets: filename -> Content-Type (exact, with charset).
#: PIANO-UI-007 adds the pure motion/render modules and the vendored,
#: pinned Three.js build (no CDN; see ADR-018 for provenance and hashes).
UI_ASSETS = {
    "index.html": "text/html; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "visualizer.js": "text/javascript; charset=utf-8",
    "handmotion.js": "text/javascript; charset=utf-8",
    "handrender.js": "text/javascript; charset=utf-8",
    "handasset.js": "text/javascript; charset=utf-8",
    "ui008/router.js": "text/javascript; charset=utf-8",
    "ui008/layers.js": "text/javascript; charset=utf-8",
    "ui008/shell.js": "text/javascript; charset=utf-8",
    "ui008/home.js": "text/javascript; charset=utf-8",
    "ui008/library.js": "text/javascript; charset=utf-8",
    "ui008/player.js": "text/javascript; charset=utf-8",
    "ui008/learn.js": "text/javascript; charset=utf-8",
    "ui008/learnlibrary.js": "text/javascript; charset=utf-8",
    "ui008/piano.js": "text/javascript; charset=utf-8",
    "ui008/settings.js": "text/javascript; charset=utf-8",
    "ui008/studio.js": "text/javascript; charset=utf-8",
    "ui008/workspace.js": "text/javascript; charset=utf-8",
    "vendor/three.core.js": "text/javascript; charset=utf-8",
    "vendor/three.webgpu.js": "text/javascript; charset=utf-8",
    "vendor/loaders/GLTFLoader.js": "text/javascript; charset=utf-8",
    "vendor/utils/BufferGeometryUtils.js": "text/javascript; charset=utf-8",
    "vendor/utils/SkeletonUtils.js": "text/javascript; charset=utf-8",
    "vendor/three.LICENSE.txt": "text/plain; charset=utf-8",
    "favicon.svg": "image/svg+xml",
    "manifest.webmanifest": "application/manifest+json",
}

#: Paths that serve the UI document itself (redirect-free, same URL).
_DOCUMENT_PATHS = frozenset({"/", "/ui", "/ui/", "/ui/index.html"})


def resolve_ui_path(path: str) -> str | None:
    """Map a request path to an allowlisted asset name (or ``None``).

    The comparison happens on the DECODED name, so percent-encoded
    traversal (``%2e%2e``) cannot slip through. Any path that does not
    resolve to an allowlisted asset returns ``None``; the caller falls
    through to the deterministic API 404.
    """
    from urllib.parse import unquote

    if not isinstance(path, str) or not path:
        return None
    if path in _DOCUMENT_PATHS:
        return "index.html"
    if path.startswith("/ui/"):
        name = unquote(path[len("/ui/") :])
        if name in UI_ASSETS:
            return name
    return None


def resolve_ui_asset(path: str) -> tuple[str, str] | None:
    """Resolve a request path to ``(asset_name, content_type)``."""
    name = resolve_ui_path(path)
    if name is None:
        return None
    return name, UI_ASSETS[name]


def read_ui_asset(name: str) -> bytes:
    """Read one allowlisted asset from the installed package.

    Only names in :data:`UI_ASSETS` may be passed (the allowlist is the
    security boundary — never a user-controlled path).
    """
    if name not in UI_ASSETS:
        raise ValueError(f"non-allowlisted UI asset: {name!r}")
    return (resources.files(_STATIC_PACKAGE) / name).read_bytes()
