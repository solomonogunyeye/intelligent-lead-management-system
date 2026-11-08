"""LEADLIFT — Database health check tests.

Uses temporary databases and synthetic records only.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile

import pytest

from leadlift.db import reset_db, init_db_with_migrations, get_settings, check_database_health, _connect_db


@pytest.fixture
def temp_db():
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    os.unlink(db_path)

    settings = get_settings()
    old_path = settings.database_path
    settings.database_path = db_path
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    init_db_with_migrations()

    yield db_path

    settings.database_path = old_path
    if os.path.exists(db_path):
        os.unlink(db_path)


class TestDatabaseHealth:
    def test_healthy_database(self, temp_db):
        health = check_database_health()
        assert health["status"] == "healthy"
        assert health["integrity"] == "ok"
        assert health["foreign_keys"] == "ok"
        assert health["schema_version"] == 4
        assert health["error"] is None

    def test_missing_database(self, temp_db):
        settings = get_settings()
        old_path = settings.database_path
        settings.database_path = "/nonexistent/db.sqlite"
        try:
            health = check_database_health()
            assert health["status"] == "unable_to_check"
            assert health["error"] is not None
            assert "does not exist" in health["error"]
        finally:
            settings.database_path = old_path

    def test_corrupted_database(self, temp_db):
        fd, bad_db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        with open(bad_db, 'wb') as f:
            f.write(b'Not a valid SQLite database')
        settings = get_settings()
        old_path = settings.database_path
        settings.database_path = bad_db
        try:
            health = check_database_health()
            assert health["status"] == "unable_to_check"
            assert health["error"] is not None
        finally:
            settings.database_path = old_path
            if os.path.exists(bad_db):
                os.unlink(bad_db)

    def test_unreadable_database(self, temp_db):
        # Create a DB file that exists but is a directory (unreadable as DB)
        import tempfile as tf
        bad_path = os.path.join(tf.gettempdir(), "not_a_db_file.db")
        with open(bad_path, "w") as f:
            f.write("not a db")
        settings = get_settings()
        old_path = settings.database_path
        settings.database_path = bad_path
        try:
            health = check_database_health()
            # Should be unable_to_check (connection fails) or unhealthy
            assert health["status"] in ("unable_to_check", "unhealthy")
        finally:
            settings.database_path = old_path
            if os.path.exists(bad_path):
                os.unlink(bad_path)

    def test_fk_violation_detected(self, temp_db):
        # Create a DB with FK violation
        fd, bad_db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        os.unlink(bad_db)
        settings = get_settings()
        old_settings = settings.database_path
        settings.database_path = bad_db
        os.makedirs(os.path.dirname(bad_db), exist_ok=True)
        init_db_with_migrations()
        conn = _connect_db(bad_db)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.commit()
        conn.execute("INSERT INTO inquiries (inquiry_id, source, original_text, received_at, first_observed_at) VALUES ('inq-1', 'manual', 'test', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO response_drafts (draft_id, inquiry_id, draft_text, generated_by, status) VALUES ('draft-bad', 'nonexistent', 'text', 'ai', 'draft')")
        conn.commit()
        conn.close()
        settings.database_path = old_settings

        settings = get_settings()
        old_path = settings.database_path
        settings.database_path = bad_db
        try:
            health = check_database_health()
            assert health["status"] == "unhealthy"
            assert health["foreign_keys"] != "ok"
        finally:
            settings.database_path = old_path
            if os.path.exists(bad_db):
                os.unlink(bad_db)

    def test_schema_version_missing(self, temp_db):
        # Create a DB without schema_version table
        fd, bad_db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        os.unlink(bad_db)
        settings = get_settings()
        old_settings = settings.database_path
        settings.database_path = bad_db
        os.makedirs(os.path.dirname(bad_db), exist_ok=True)
        conn = _connect_db(bad_db)
        conn.execute("CREATE TABLE inquiries (inquiry_id TEXT PRIMARY KEY, source TEXT, original_text TEXT, received_at TEXT, first_observed_at TEXT)")
        conn.commit()
        conn.close()
        settings.database_path = old_settings

        settings = get_settings()
        old_path = settings.database_path
        settings.database_path = bad_db
        try:
            health = check_database_health()
            # Schema version should be None (table missing)
            assert health["schema_version"] is None
        finally:
            settings.database_path = old_path
            if os.path.exists(bad_db):
                os.unlink(bad_db)

    def test_never_claims_healthy_on_error(self, temp_db):
        settings = get_settings()
        old_path = settings.database_path
        settings.database_path = "/nonexistent/db.sqlite"
        try:
            health = check_database_health()
            assert health["status"] != "healthy"
            assert health["status"] == "unable_to_check"
        finally:
            settings.database_path = old_path

    def test_integrity_failure_genuine(self, temp_db):
        """Force integrity_check to return non-ok by corrupting DB pages."""
        # Create a copy of the DB and corrupt a page
        import shutil
        fd, corrupt_db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        os.unlink(corrupt_db)
        # Copy the healthy DB
        import shutil as sh
        sh.copy2(temp_db, corrupt_db)
        # Corrupt by writing garbage at a specific offset (page 2)
        with open(corrupt_db, 'r+b') as f:
            f.seek(4096)  # Skip first page
            f.write(b'CORRUPTED_DATA_HERE')
        settings = get_settings()
        old_path = settings.database_path
        settings.database_path = corrupt_db
        try:
            health = check_database_health()
            # Should be unhealthy (integrity check fails) or unable_to_check
            assert health["status"] in ("unhealthy", "unable_to_check")
            assert health["status"] != "healthy"
        finally:
            settings.database_path = old_path
            if os.path.exists(corrupt_db):
                os.unlink(corrupt_db)

    def test_unexpected_schema_version(self, temp_db):
        """Test that an unexpected schema version is reported."""
        fd, bad_db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        os.unlink(bad_db)
        settings = get_settings()
        old_settings = settings.database_path
        settings.database_path = bad_db
        os.makedirs(os.path.dirname(bad_db), exist_ok=True)
        init_db_with_migrations()
        conn = _connect_db(bad_db)
        conn.execute("UPDATE schema_version SET version = 99 WHERE id = 1")
        conn.commit()
        conn.close()
        settings.database_path = old_settings

        settings = get_settings()
        old_path = settings.database_path
        settings.database_path = bad_db
        try:
            health = check_database_health()
            # Schema version should be 99 (unexpected but reported)
            assert health["schema_version"] == 99
            # Integrity should still be ok (schema exists)
            assert health["integrity"] == "ok"
        finally:
            settings.database_path = old_path
            if os.path.exists(bad_db):
                os.unlink(bad_db)

    def test_readonly_behavior(self, temp_db):
        """Verify health check does not modify database."""
        settings = get_settings()
        # Snapshot before
        conn = _connect_db(temp_db)
        before_count = conn.execute("SELECT COUNT(*) FROM inquiries").fetchone()[0]
        before_version = conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0]
        before_tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        conn.close()

        # Run health check
        health = check_database_health()
        assert health["status"] == "healthy"

        # Snapshot after
        conn = _connect_db(temp_db)
        after_count = conn.execute("SELECT COUNT(*) FROM inquiries").fetchone()[0]
        after_version = conn.execute("SELECT version FROM schema_version WHERE id=1").fetchone()[0]
        after_tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        conn.close()

        assert before_count == after_count
        assert before_version == after_version
        assert before_tables == after_tables

    def test_connection_cleanup_on_failure(self, temp_db):
        """Verify connection is closed when an exception occurs during check."""
        import leadlift.db as db_module
        import leadlift.db as db_module
        original_connect = db_module._connect_db

        class FailingConn:
            def __init__(self, *args, **kwargs):
                self._conn = original_connect(*args, **kwargs)
                self.closed = False
            def execute(self, sql, *a, **k):
                if "integrity_check" in sql:
                    raise sqlite3.Error("Simulated integrity check failure")
                return self._conn.execute(sql, *a, **k)
            def close(self):
                self.closed = True
                self._conn.close()
            def __enter__(self):
                return self
            def __exit__(self, *args):
                self.close()

        conn_created = []

        def failing_connect(*args, **kwargs):
            fc = FailingConn(*args, **kwargs)
            conn_created.append(fc)
            return fc

        from unittest.mock import patch
        with patch("leadlift.db._connect_db", side_effect=failing_connect):
            health = check_database_health()
            assert health["status"] == "unable_to_check"
            assert health["error"] is not None
            assert "Integrity check failed" in health["error"]

        assert len(conn_created) == 1
        assert conn_created[0].closed is True

    def test_result_classification_healthy(self, temp_db):
        health = check_database_health()
        assert health["status"] == "healthy"
        assert health["error"] is None
        assert health["integrity"] == "ok"
        assert health["foreign_keys"] == "ok"

    def test_result_classification_unhealthy(self, temp_db):
        fd, bad_db = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        os.unlink(bad_db)
        settings = get_settings()
        old_settings = settings.database_path
        settings.database_path = bad_db
        os.makedirs(os.path.dirname(bad_db), exist_ok=True)
        init_db_with_migrations()
        conn = _connect_db(bad_db)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.commit()
        conn.execute("INSERT INTO inquiries (inquiry_id, source, original_text, received_at, first_observed_at) VALUES ('inq-1', 'manual', 'test', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO response_drafts (draft_id, inquiry_id, draft_text, generated_by, status) VALUES ('draft-bad', 'nonexistent', 'text', 'ai', 'draft')")
        conn.commit()
        conn.close()
        settings.database_path = old_settings

        settings = get_settings()
        old_path = settings.database_path
        settings.database_path = bad_db
        try:
            health = check_database_health()
            assert health["status"] == "unhealthy"
            assert health["error"] is None  # No error, just unhealthy
            assert health["foreign_keys"] != "ok"
        finally:
            settings.database_path = old_path
            if os.path.exists(bad_db):
                os.unlink(bad_db)

    def test_result_classification_unable_to_check(self, temp_db):
        settings = get_settings()
        old_path = settings.database_path
        settings.database_path = "/nonexistent/db.sqlite"
        try:
            health = check_database_health()
            assert health["status"] == "unable_to_check"
            assert health["error"] is not None
        finally:
            settings.database_path = old_path

    def test_ui_smoke(self, temp_db):
        """Smoke test: health check function works, UI not directly testable."""
        # Streamlit AppTest cannot navigate to specific sections reliably
        # Verify the function exists and returns correct structure
        health = check_database_health()
        assert "status" in health
        assert "integrity" in health
        assert "foreign_keys" in health
        assert "schema_version" in health
        assert "error" in health
        assert "details" in health



