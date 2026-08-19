"""In-memory implementation of TrajectoryStateStore for local dev and tests."""

import asyncio
from typing import Dict, Optional

from astra.domain.trajectory import TrajectoryState


class InMemoryTrajectoryStore:
    """Thread-safe in-memory store for TrajectoryState."""

    def __init__(self):
        self._store: Dict[str, TrajectoryState] = {}
        self._lock = asyncio.Lock()

    async def load(self, session_id: str) -> Optional[TrajectoryState]:
        """Loads a deep copy of TrajectoryState."""
        async with self._lock:
            state = self._store.get(session_id)
            if state:
                return state.model_copy(deep=True)
            return None

    async def save(self, state: TrajectoryState) -> bool:
        """Saves TrajectoryState with optimistic concurrency."""
        async with self._lock:
            existing = self._store.get(state.session_id)
            if existing and existing.state_version > state.state_version:
                # Version conflict
                return False
            self._store[state.session_id] = state.model_copy(deep=True)
            return True

    def clear(self) -> None:
        """Clears all in-memory state."""
        self._store.clear()
