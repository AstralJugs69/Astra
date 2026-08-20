import sqlite3
import pytest
from pathlib import Path
from data.database import create_sample_database, recover_database


@pytest.fixture
def corrupted_db(tmp_path):
    db_file = tmp_path / "corrupt.db"
    create_sample_database(db_file)
    
    # Introduce binary header corruption at offset 100
    with open(db_file, "r+b") as f:
        f.seek(100)
        f.write(b"\xFF\xFF\x00\x00")
        
    return db_file


def test_sqlite_corruption_recovery(tmp_path, corrupted_db):
    recovered_file = tmp_path / "recovered.db"
    
    # The recovery function must handle binary corruption and recover all 100 rows
    recovered_count = recover_database(corrupted_db, recovered_file)
    assert recovered_count == 100
    
    # Integrity check on recovered database must return 'ok'
    conn = sqlite3.connect(str(recovered_file))
    cur = conn.cursor()
    integrity = cur.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()
    
    assert integrity.lower() == "ok"
