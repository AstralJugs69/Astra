"""Evaluation storage adapter using isolated local SQLite and JSONL files."""

import json
import sqlite3
from pathlib import Path
from typing import List, Optional
import structlog

from astra.evaluation.models import (
    EvaluationCondition,
    RunRecord,
    SecondaryMetrics,
    TrialOutcome,
)

logger = structlog.get_logger(__name__)

DEFAULT_EVAL_DIR = Path("evaluation") / "runs"


class EvaluationStore:
    """Isolated store for benchmark and evaluation run records."""

    def __init__(self, runs_dir: Path = DEFAULT_EVAL_DIR):
        self.runs_dir = Path(runs_dir)
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.runs_dir / "evaluation.sqlite"
        self.jsonl_path = self.runs_dir / "eval_runs.jsonl"
        self._init_sqlite()

    def _init_sqlite(self) -> None:
        """Initializes SQLite schema for evaluation records."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    condition TEXT NOT NULL,
                    antigravity_version TEXT,
                    main_agent_model_version TEXT,
                    started_at INTEGER,
                    finished_at INTEGER,
                    turns_to_fix INTEGER,
                    outcome TEXT NOT NULL,
                    invalid_reason TEXT,
                    failed_verifications INTEGER,
                    time_to_fix_seconds REAL,
                    astra_interventions INTEGER,
                    raw_record_json TEXT
                )
                """
            )
            conn.commit()

    def record_run(self, record: RunRecord) -> None:
        """Persists a RunRecord to SQLite and append-only JSONL."""
        raw_json = record.model_dump_json()

        # 1. Append to JSONL
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(raw_json + "\n")

        # 2. Insert into SQLite
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runs (
                    run_id, task_id, condition, antigravity_version, main_agent_model_version,
                    started_at, finished_at, turns_to_fix, outcome, invalid_reason,
                    failed_verifications, time_to_fix_seconds, astra_interventions, raw_record_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.run_id,
                    record.task_id,
                    record.condition.value,
                    record.antigravity_version,
                    record.main_agent_model_version,
                    record.started_at,
                    record.finished_at,
                    record.turns_to_fix,
                    record.outcome.value,
                    record.invalid_reason,
                    record.secondary_metrics.failed_verification_attempts,
                    record.secondary_metrics.time_to_fix_seconds,
                    record.secondary_metrics.astra_interventions,
                    raw_json,
                ),
            )
            conn.commit()

        logger.info("evaluation_run_recorded", run_id=record.run_id, outcome=record.outcome.value, turns=record.turns_to_fix)

    def list_runs(self, task_id: Optional[str] = None) -> List[RunRecord]:
        """Lists run records from SQLite."""
        records: List[RunRecord] = []
        with sqlite3.connect(self.db_path) as conn:
            if task_id:
                cursor = conn.execute("SELECT raw_record_json FROM runs WHERE task_id = ?", (task_id,))
            else:
                cursor = conn.execute("SELECT raw_record_json FROM runs ORDER BY started_at ASC")

            for (raw_json,) in cursor.fetchall():
                records.append(RunRecord.model_validate_json(raw_json))

        return records
