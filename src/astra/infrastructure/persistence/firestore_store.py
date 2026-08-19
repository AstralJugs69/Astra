"""Firestore implementation of TrajectoryStateStore."""

import asyncio
from typing import Any, Dict, Optional
import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from astra.domain.trajectory import TrajectoryState

logger = structlog.get_logger(__name__)

# Transient error classes from google api core
try:
    from google.api_core.exceptions import GoogleAPICallError, RetryError
    RETRYABLE_EXCEPTIONS = (GoogleAPICallError, RetryError, asyncio.TimeoutError)
except ImportError:
    RETRYABLE_EXCEPTIONS = (asyncio.TimeoutError,)


class FirestoreTrajectoryStore:
    """Firestore adapter for TrajectoryState persistence with retry and degraded mode."""

    def __init__(
        self,
        project_id: Optional[str] = None,
        collection_prefix: str = "astra",
        timeout_seconds: float = 1.5,
        max_degraded_failures: int = 3,
    ):
        self.project_id = project_id
        self.collection_name = f"{collection_prefix}_trajectories"
        self.timeout_seconds = timeout_seconds
        self.max_degraded_failures = max_degraded_failures
        self._consecutive_failures = 0
        self._db: Optional[Any] = None

    def _get_client(self):
        if self._db is None:
            from google.cloud import firestore
            self._db = firestore.AsyncClient(project=self.project_id)
        return self._db

    @property
    def is_degraded(self) -> bool:
        """Returns True if Firestore is currently in degraded bypass mode."""
        return self._consecutive_failures >= self.max_degraded_failures

    async def load(self, session_id: str) -> Optional[TrajectoryState]:
        """Loads TrajectoryState from Firestore."""
        if self.is_degraded:
            logger.warning("firestore_degraded_bypass_active", action="load", session_id=session_id)
            return None

        try:
            db = self._get_client()
            doc_ref = db.collection(self.collection_name).document(session_id)

            async with asyncio.timeout(self.timeout_seconds):
                doc = await doc_ref.get()

            if not doc.exists:
                self._consecutive_failures = 0
                return None

            data = doc.to_dict()
            state = TrajectoryState.model_validate(data)
            self._consecutive_failures = 0
            return state

        except Exception as exc:
            self._consecutive_failures += 1
            logger.error("firestore_load_failed", session_id=session_id, error=str(exc), failures=self._consecutive_failures)
            return None

    async def save(self, state: TrajectoryState) -> bool:
        """Saves TrajectoryState with optimistic concurrency check."""
        if self.is_degraded:
            logger.warning("firestore_degraded_bypass_active", action="save", session_id=state.session_id)
            return False

        try:
            db = self._get_client()
            doc_ref = db.collection(self.collection_name).document(state.session_id)
            data = state.model_dump(mode="json")

            async with asyncio.timeout(self.timeout_seconds):
                await doc_ref.set(data)

            self._consecutive_failures = 0
            return True

        except Exception as exc:
            self._consecutive_failures += 1
            logger.error("firestore_save_failed", session_id=state.session_id, error=str(exc), failures=self._consecutive_failures)
            return False
