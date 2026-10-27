"""Piano Core Learn Library (PIANO-LEARN-001).

Independent educational catalog for the Learn surface. The Player catalog
(``piano_core.library``) and the Learn catalog are separate namespaces:

- Player songs resolve only from the read-only Gold1000 library root.
- Learn lessons resolve only from hash-validated practice packages that
  were imported by the M5 package importer from Learn-built content.

This subsystem converts verified open-licensed educational sources into
the canonical learning representation and the ``practice-package/v1``
exchange format (ADR-PACKAGE-PRACTICE-V1). It is deterministic: the same
input bytes always produce the same packages, catalog and identities.
"""

from __future__ import annotations

__all__ = [
    "LearnError",
    "LearnConfigError",
    "LearnCatalogError",
    "LEARN_CATALOG_SCHEMA_VERSION",
]

LEARN_CATALOG_SCHEMA_VERSION = 1


class LearnError(RuntimeError):
    """Base error for the Learn subsystem."""


class LearnConfigError(LearnError):
    """Learn configuration is missing or invalid."""


class LearnCatalogError(LearnError):
    """The Learn catalog is missing, stale or unreadable."""
