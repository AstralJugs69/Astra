"""SQLite database management and corruption recovery module."""

import sqlite3
from pathlib import Path


def create_sample_database(db_path: Path):
    """Creates a sample transactions database."""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("CREATE TABLE transactions (id INTEGER PRIMARY KEY, account TEXT, amount REAL, status TEXT)")
    
    rows = [(i, f"ACC_{i:04d}", float(i * 10.5), "completed") for i in range(1, 101)]
    cur.executemany("INSERT INTO transactions VALUES (?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()


def recover_database(corrupt_db_path: Path, output_db_path: Path) -> int:
    """Recovers records from database.
    
    FLAW: Simple naive copy that fails if database is locked or corrupted.
    """
    if not corrupt_db_path.exists():
        raise FileNotFoundError(f"{corrupt_db_path} not found")
    
    # Bug: Naive open without proper dump/recovery handling
    src_conn = sqlite3.connect(str(corrupt_db_path))
    dst_conn = sqlite3.connect(str(output_db_path))
    
    src_cur = src_conn.cursor()
    dst_cur = dst_conn.cursor()
    
    dst_cur.execute("CREATE TABLE IF NOT EXISTS transactions (id INTEGER PRIMARY KEY, account TEXT, amount REAL, status TEXT)")
    
    rows = src_cur.execute("SELECT id, account, amount, status FROM transactions").fetchall()
    dst_cur.executemany("INSERT OR REPLACE INTO transactions VALUES (?, ?, ?, ?)", rows)
    
    dst_conn.commit()
    count = len(rows)
    
    src_conn.close()
    dst_conn.close()
    return count
