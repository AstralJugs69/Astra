"""Unit tests for persistence adapters."""

import pytest
from astra.domain.trajectory import create_initial_trajectory
from astra.infrastructure.persistence.firestore_store import FirestoreTrajectoryStore
from astra.infrastructure.persistence.memory_store import InMemoryTrajectoryStore


@pytest.mark.asyncio
async def test_in_memory_store_save_and_load():
    store = InMemoryTrajectoryStore()
    state = create_initial_trajectory("session-123", timestamp_ms=1000)

    # Save
    saved = await store.save(state)
    assert saved

    # Load
    loaded = await store.load("session-123")
    assert loaded is not None
    assert loaded.session_id == "session-123"
    assert loaded.state_version == 1

    # Missing session
    missing = await store.load("non-existent")
    assert missing is None


@pytest.mark.asyncio
async def test_in_memory_store_version_conflict():
    store = InMemoryTrajectoryStore()
    state1 = create_initial_trajectory("session-123", timestamp_ms=1000)
    state1.state_version = 5
    await store.save(state1)

    # Attempt to save state with older state_version
    state2 = create_initial_trajectory("session-123", timestamp_ms=2000)
    state2.state_version = 3
    saved = await store.save(state2)
    assert not saved


@pytest.mark.asyncio
async def test_firestore_degraded_mode():
    store = FirestoreTrajectoryStore(project_id="test-proj", max_degraded_failures=2)
    # Simulate failures
    store._consecutive_failures = 2
    assert store.is_degraded

    # Load and save should immediately return None/False without calling Firestore
    loaded = await store.load("session-123")
    assert loaded is None

    state = create_initial_trajectory("session-123")
    saved = await store.save(state)
    assert not saved
