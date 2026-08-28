"""Small SQLite journal for append-only observation evidence and outbox state."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


class ObservationJournal:
    def __init__(self, path: str | Path) -> None:
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS observations (sequence INTEGER PRIMARY KEY, observation_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)"
        )
        self.connection.commit()

    def append(self, sequence: int, observation_id: str, payload: dict[str, object]) -> bool:
        try:
            self.connection.execute(
                "INSERT INTO observations(sequence, observation_id, payload) VALUES (?, ?, ?)",
                (sequence, observation_id, json.dumps(payload, sort_keys=True, separators=(",", ":"))),
            )
            self.connection.commit()
            return True
        except sqlite3.IntegrityError:
            self.connection.rollback()
            return False

