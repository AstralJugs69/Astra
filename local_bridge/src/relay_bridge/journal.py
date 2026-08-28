"""Transactional local journal for hash-chained site observations."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .observation_builder import canonical_bytes


class ObservationJournal:
    """Append observations and retryable publications atomically.

    The observation row is the immutable local evidence record.  Its paired
    outbox row is created in the same SQLite transaction so a bridge restart
    cannot acknowledge an observation without retaining the message to publish.
    """

    def __init__(self, path: str | Path) -> None:
        self.connection = sqlite3.connect(str(path))
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=FULL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS observations (
                sequence INTEGER PRIMARY KEY,
                observation_id TEXT UNIQUE NOT NULL,
                payload TEXT NOT NULL,
                payload_sha256 TEXT NOT NULL,
                previous_observation_sha256 TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS outbox (
                message_id TEXT PRIMARY KEY,
                observation_id TEXT UNIQUE NOT NULL,
                payload TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                published_at TEXT
            )
            """
        )
        columns = {row[1] for row in self.connection.execute("PRAGMA table_info(observations)")}
        if "payload_sha256" not in columns:
            self.connection.execute("ALTER TABLE observations ADD COLUMN payload_sha256 TEXT")
        if "previous_observation_sha256" not in columns:
            self.connection.execute(
                "ALTER TABLE observations ADD COLUMN previous_observation_sha256 TEXT"
            )
        self._backfill_outbox()
        self.connection.commit()
        self.verify_chain()

    def _backfill_outbox(self) -> None:
        """Make journals created before the outbox migration publishable."""
        rows = self.connection.execute(
            """
            SELECT observations.observation_id, observations.payload
            FROM observations
            LEFT JOIN outbox ON outbox.observation_id = observations.observation_id
            WHERE outbox.observation_id IS NULL
            ORDER BY observations.sequence
            """
        )
        for observation_id, payload in rows:
            self.connection.execute(
                """
                INSERT INTO outbox(
                    message_id, observation_id, payload, attempts, created_at
                ) VALUES (?, ?, ?, 0, ?)
                """,
                (
                    observation_id,
                    observation_id,
                    payload,
                    datetime.now(UTC).isoformat(),
                ),
            )

    @staticmethod
    def _observation_digest(payload: dict[str, Any]) -> str:
        body = dict(payload)
        observation_id = body.pop("observation_id", None)
        if not isinstance(observation_id, str):
            raise TypeError("observation payload must contain observation_id")
        return hashlib.sha256(canonical_bytes(body)).hexdigest()

    def verify_chain(self) -> str | None:
        previous_id: str | None = None
        previous_sequence = 0
        rows = self.connection.execute(
            "SELECT sequence, observation_id, payload, payload_sha256, previous_observation_sha256 "
            "FROM observations ORDER BY sequence"
        )
        for sequence, stored_id, raw_payload, stored_payload_hash, stored_previous in rows:
            if sequence <= previous_sequence:
                raise ValueError("observation sequence is not strictly increasing")
            payload = json.loads(raw_payload)
            if not isinstance(payload, dict):
                raise TypeError("observation payload is not an object")
            payload_id = payload.get("observation_id")
            if payload_id != stored_id or self._observation_digest(payload) != stored_id:
                raise ValueError("observation content hash does not match observation_id")
            expected_previous = payload.get("previous_observation_sha256")
            if expected_previous != previous_id or stored_previous not in (None, expected_previous):
                raise ValueError("observation hash chain is discontinuous")
            expected_payload_hash = hashlib.sha256(canonical_bytes(payload)).hexdigest()
            if stored_payload_hash not in (None, expected_payload_hash):
                raise ValueError("stored observation payload hash is invalid")
            previous_sequence = sequence
            previous_id = stored_id
        return previous_id

    def append(self, sequence: int, observation_id: str, payload: dict[str, object]) -> bool:
        if sequence <= 0:
            raise ValueError("observation sequence must be positive")
        if payload.get("observation_id") != observation_id:
            raise ValueError("observation ID argument does not match payload")
        if self._observation_digest(payload) != observation_id:
            raise ValueError("observation payload is not content-addressed by observation_id")
        self.verify_chain()
        last = self.connection.execute(
            "SELECT sequence, observation_id, payload FROM observations ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        if last and sequence <= int(last[0]):
            existing = self.connection.execute(
                "SELECT observation_id, payload FROM observations WHERE sequence = ?",
                (sequence,),
            ).fetchone()
            if existing and existing[0] == observation_id and json.loads(existing[1]) == payload:
                return False
            raise ValueError("observation sequence is not the next sequence")
        if last and sequence != int(last[0]) + 1:
            raise ValueError("observation sequence is not the next sequence")
        expected_previous = last[1] if last else None
        payload_previous = payload.get("previous_observation_sha256")
        if payload_previous != expected_previous:
            raise ValueError("observation payload does not extend the current chain")
        payload_json = canonical_bytes(payload).decode("utf-8")
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.execute(
                """
                INSERT INTO observations(
                    sequence, observation_id, payload, payload_sha256,
                    previous_observation_sha256
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (sequence, observation_id, payload_json, payload_hash, payload_previous),
            )
            self.connection.execute(
                """
                INSERT INTO outbox(
                    message_id, observation_id, payload, attempts, created_at
                ) VALUES (?, ?, ?, 0, ?)
                """,
                (observation_id, observation_id, payload_json, datetime.now(UTC).isoformat()),
            )
            self.connection.commit()
        except sqlite3.IntegrityError:
            self.connection.rollback()
            return False
        return True

    def pending_outbox(self, limit: int = 100) -> tuple[dict[str, object], ...]:
        if limit <= 0 or limit > 10_000:
            raise ValueError("outbox limit must be between 1 and 10000")
        rows = self.connection.execute(
            """
            SELECT message_id, observation_id, payload, attempts, created_at
            FROM outbox
            WHERE published_at IS NULL
            ORDER BY created_at, message_id
            LIMIT ?
            """,
            (limit,),
        )
        pending: list[dict[str, object]] = []
        for message_id, observation_id, raw_payload, attempts, created_at in rows:
            payload = json.loads(raw_payload)
            if not isinstance(payload, dict):
                raise TypeError("outbox payload is not an object")
            pending.append(
                {
                    "message_id": message_id,
                    "observation_id": observation_id,
                    "payload": payload,
                    "attempts": attempts,
                    "created_at": created_at,
                }
            )
        return tuple(pending)

    def mark_outbox_published(self, observation_id: str) -> bool:
        """Mark a message sent only after the external publisher accepts it."""
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            cursor = self.connection.execute(
                """
                UPDATE outbox
                SET attempts = attempts + 1, published_at = ?
                WHERE observation_id = ? AND published_at IS NULL
                """,
                (datetime.now(UTC).isoformat(), observation_id),
            )
            self.connection.commit()
        except sqlite3.Error:
            self.connection.rollback()
            raise
        return cursor.rowcount == 1

    def close(self) -> None:
        self.connection.close()
