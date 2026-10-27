"""Bounded cache binding fingering plans to exact score-source versions."""

from __future__ import annotations

import threading
from collections import OrderedDict

from piano_core.fingering.engine import generate_plan
from piano_core.fingering.model import FingeringConfig, FingeringPlan


class FingeringService:
    def __init__(self, score_service, *, max_cached: int = 8) -> None:
        if score_service is None:
            raise ValueError("score_service is required")
        if not isinstance(max_cached, int) or isinstance(max_cached, bool) or max_cached < 1:
            raise ValueError("max_cached must be a positive int")
        self._score = score_service
        self._max_cached = max_cached
        self._lock = threading.Lock()
        self._cache: "OrderedDict[tuple, FingeringPlan]" = OrderedDict()

    @property
    def available(self) -> bool:
        return bool(self._score.available)

    def get(
        self, song_id: str, config: FingeringConfig | None = None
    ) -> FingeringPlan:
        config = config or FingeringConfig()
        score = self._score.get(song_id)
        key = (score.song_id, score.source_version, tuple(config.identity().items()))
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                self._cache.move_to_end(key)
                return cached
        plan = generate_plan(score, config)
        with self._lock:
            self._cache[key] = plan
            self._cache.move_to_end(key)
            while len(self._cache) > self._max_cached:
                self._cache.popitem(last=False)
        return plan
