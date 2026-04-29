"""Regression tests for DatabaseSchema.verify_schema with sqlite-vec aux tables.

sqlite-vec creates auxiliary shadow tables (e.g. ``*_chunks``, ``*_rowids``,
``*_info``, ``*_vector_chunks00``) for each ``vec0`` virtual table. These are
not part of CuliFeed's "expected" schema set, but they must not cause schema
verification to fail on a populated production DB.
"""

import sqlite3

import sqlite_vec

from culifeed.database.schema import DatabaseSchema


def test_verify_schema_accepts_sqlite_vec_aux_tables(tmp_path):
    """verify_schema() should pass even with sqlite-vec auxiliary tables present."""
    db_path = tmp_path / "test.db"
    schema = DatabaseSchema(str(db_path))
    schema.create_tables()  # creates vec0 virtual tables -> aux tables

    # Sanity: confirm aux tables exist
    with sqlite3.connect(db_path) as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
        names = {row[0] for row in cur.fetchall()}

    aux_tables = {n for n in names if "_chunks" in n or "_rowids" in n or "_info" in n}
    assert aux_tables, "Expected sqlite-vec auxiliary tables to be present"

    # verify_schema must still pass
    assert schema.verify_schema() is True
