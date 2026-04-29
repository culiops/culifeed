"""
Database Connection Tests
=========================

Tests for DatabaseConnection class, including sqlite-vec extension loading.
"""

import pytest
from pathlib import Path

from culifeed.database.connection import DatabaseConnection


def test_sqlite_vec_extension_loaded(tmp_path):
    """Test that the sqlite-vec extension is loaded on each new connection.

    Verifies vec_version() is callable, confirming the extension is active.
    """
    db = DatabaseConnection(str(tmp_path / "test.db"))
    with db.get_connection() as conn:
        rows = conn.execute("SELECT vec_version()").fetchall()
        assert rows[0][0].startswith("v")  # e.g. "v0.1.6"
