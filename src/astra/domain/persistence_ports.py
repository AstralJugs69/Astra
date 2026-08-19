"""Pure TrajectoryStateStore port interface.

Zero I/O, zero framework imports. Defines the protocol for persisting and loading trajectory state.
"""

from typing import Optional, Protocol
from astra.domain.trajectory import TrajectoryState


class TrajectoryStateStore(Protocol):
    """Protocol for trajectory state storage (Firestore / In-Memory)."""

    async def load(self, session_id: str) -> Optional[TrajectoryState]:
        """Loads the TrajectoryState for a session, or returns None if not found."""
        ...

    async def save(self, state: TrajectoryState) -> bool:
        """Saves or updates the TrajectoryState with optimistic concurrency."""
        ...
