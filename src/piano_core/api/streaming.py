"""Small typed streaming response used by the local control API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True, slots=True)
class ApiStream:
    """A bounded handler-owned byte stream; callers close it when available."""

    chunks: Iterable[bytes]
    content_type: str
    cache_control: str = "no-cache"
    on_close: Callable[[], None] | None = None

    def close(self) -> None:
        """Release both the iterable and its separately reserved capacity."""
        close_chunks = getattr(self.chunks, "close", None)
        try:
            if callable(close_chunks):
                close_chunks()
        finally:
            if self.on_close is not None:
                self.on_close()
