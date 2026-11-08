"""LEADLIFT — Database integrity and atomic persistence tests (Phase 3H.2 + 3H.3).

Tests repository insertion semantics, migration behavior, atomic webhook
deduplication, cross-adapter identity, and concurrent insertion.

Uses temporary databases and synthetic records only.
"""

from __future__ import annotations

import os
import sqlite3
import sqlcipher3.dbapi2 as sqlcipher3
import tempfile
import threading
import uuid
from typing import Optional

import pytest

from leadlift.db import reset_db, init_db_with_migrations, get_schema_version, migrate_db, init_db, get_db, _connect_db
from leadlift.config import get_settings
from leadlift.models import Inquiry, InquirySource, CSVImportResult
from leadlift.repository import InquiryRepository


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db():
    """Provide a temporary isolated database."""
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


@pytest.fixture
def repo(temp_db):
    return InquiryRepository()


def _make_inquiry(source=InquirySource.WEBSITE_FORM, msg_id="msg-001", text="Test inquiry", inquiry_id=None):
    return Inquiry(
        inquiry_id=inquiry_id,
        source=source,
        source_platform=source.value,
        source_message_id=msg_id,
        original_text=text,
        received_at="2026-01-01T00:00:00Z",
        first_observed_at="2026-01-01T00:00:00Z",
    )


# ---------------------------------------------------------------------------
# Repository: save() tests
# ---------------------------------------------------------------------------

class TestRepositorySave:
    def test_new_inquiry_inserted(self, repo):
        inq = _make_inquiry(inquiry_id="inq-new-1")
        repo.save(inq)
        loaded = repo.get("inq-new-1")
        assert loaded is not None
        assert loaded.original_text == "Test inquiry"

    def test_duplicate_pk_raises(self, repo):
        inq = _make_inquiry(inquiry_id="inq-dup-pk")
        repo.save(inq)
        inq2 = _make_inquiry(inquiry_id="inq-dup-pk", text="Different text")
        with pytest.raises(Exception):
            repo.save(inq2)

    def test_original_preserved_after_failed_insert(self, repo):
        inq = _make_inquiry(inquiry_id="inq-preserve", text="Original text")
        repo.save(inq)
        inq2 = _make_inquiry(inquiry_id="inq-preserve", text="Overwritten text")
        try:
            repo.save(inq2)
        except Exception:
            pass
        loaded = repo.get("inq-preserve")
        assert loaded.original_text == "Original text"

    def test_response_draft_preserved(self, repo, temp_db):
        inq = _make_inquiry(inquiry_id="inq-draft")
        repo.save(inq)
        with get_db() as conn:
            conn.execute(
                "INSERT INTO response_drafts (draft_id, inquiry_id, draft_text, generated_by, status) VALUES (?, ?, ?, ?, ?)",
                ("draft-1", "inq-draft", "Draft text", "ai", "draft"),
            )
            conn.commit()
        inq2 = _make_inquiry(inquiry_id="inq-draft", text="Overwritten")
        try:
            repo.save(inq2)
        except Exception:
            pass
        with get_db() as conn:
            draft = conn.execute("SELECT * FROM response_drafts WHERE inquiry_id='inq-draft'").fetchone()
            assert draft is not None
            assert draft["draft_text"] == "Draft text"


# ---------------------------------------------------------------------------
# Repository: update() tests
# ---------------------------------------------------------------------------

class TestRepositoryUpdate:
    def test_update_existing(self, repo):
        inq = _make_inquiry(inquiry_id="inq-upd", text="Original")
        repo.save(inq)
        inq.original_text = "Updated text"
        inq.recommended_next_action = "Review"
        repo.update(inq)
        loaded = repo.get("inq-upd")
        assert loaded.original_text == "Updated text"
        assert loaded.recommended_next_action == "Review"

    def test_update_nonexistent_raises(self, repo):
        inq = _make_inquiry(inquiry_id="inq-nonexistent", text="Test")
        with pytest.raises(ValueError):
            repo.update(inq)

    def test_update_preserves_drafts(self, repo, temp_db):
        inq = _make_inquiry(inquiry_id="inq-upddraft")
        repo.save(inq)
        with get_db() as conn:
            conn.execute(
                "INSERT INTO response_drafts (draft_id, inquiry_id, draft_text, generated_by, status) VALUES (?, ?, ?, ?, ?)",
                ("draft-2", "inq-upddraft", "Draft text", "ai", "draft"),
            )
            conn.commit()
        inq.original_text = "Updated"
        repo.update(inq)
        with get_db() as conn:
            draft = conn.execute("SELECT * FROM response_drafts WHERE inquiry_id='inq-upddraft'").fetchone()
            assert draft is not None

    def test_update_preserves_id(self, repo):
        inq = _make_inquiry(inquiry_id="inq-idpres", text="Original")
        repo.save(inq)
        inq.original_text = "Updated"
        repo.update(inq)
        loaded = repo.get("inq-idpres")
        assert loaded.inquiry_id == "inq-idpres"


# ---------------------------------------------------------------------------
# Repository: save_if_not_duplicate() tests
# ---------------------------------------------------------------------------

class TestSaveIfNotDuplicate:
    def test_first_insert_succeeds(self, repo):
        inq = _make_inquiry(inquiry_id="inq-first", msg_id="msg-001")
        saved, inserted = repo.save_if_not_duplicate(inq)
        assert inserted is True
        assert saved.inquiry_id == "inq-first"

    def test_duplicate_rejected(self, repo):
        inq = _make_inquiry(inquiry_id="inq-orig", msg_id="msg-dup", text="Original text")
        repo.save(inq)
        inq2 = _make_inquiry(inquiry_id="inq-dup", msg_id="msg-dup", text="Different text")
        saved, inserted = repo.save_if_not_duplicate(inq2)
        assert inserted is False
        assert saved.inquiry_id == "inq-orig"
        assert saved.original_text == "Original text"

    def test_original_preserved(self, repo):
        inq = _make_inquiry(inquiry_id="inq-orig2", msg_id="msg-dup2", text="Original")
        repo.save(inq)
        inq2 = _make_inquiry(inquiry_id="inq-dup2", msg_id="msg-dup2", text="Overwritten")
        saved, inserted = repo.save_if_not_duplicate(inq2)
        assert saved.original_text == "Original"

    def test_null_message_id_supported(self, repo):
        inq1 = _make_inquiry(inquiry_id="inq-null1", msg_id=None, text="Text 1")
        inq2 = _make_inquiry(inquiry_id="inq-null2", msg_id=None, text="Text 2")
        saved1, ins1 = repo.save_if_not_duplicate(inq1)
        saved2, ins2 = repo.save_if_not_duplicate(inq2)
        assert ins1 is True
        assert ins2 is True

    def test_empty_message_id_supported(self, repo):
        inq1 = _make_inquiry(inquiry_id="inq-empty1", msg_id="", text="Text 1")
        inq2 = _make_inquiry(inquiry_id="inq-empty2", msg_id="", text="Text 2")
        saved1, ins1 = repo.save_if_not_duplicate(inq1)
        saved2, ins2 = repo.save_if_not_duplicate(inq2)
        assert ins1 is True
        assert ins2 is True

    def test_different_sources_same_msg_id(self, repo):
        inq1 = _make_inquiry(source=InquirySource.WHATSAPP, inquiry_id="inq-wa", msg_id="msg-shared")
        inq2 = _make_inquiry(source=InquirySource.TELEGRAM, inquiry_id="inq-tg", msg_id="msg-shared")
        s1, i1 = repo.save_if_not_duplicate(inq1)
        s2, i2 = repo.save_if_not_duplicate(inq2)
        assert i1 is True
        assert i2 is True

    def test_same_source_same_msg_id_rejected(self, repo):
        inq1 = _make_inquiry(source=InquirySource.WHATSAPP, inquiry_id="inq-wa2", msg_id="msg-same")
        inq2 = _make_inquiry(source=InquirySource.WHATSAPP, inquiry_id="inq-wa3", msg_id="msg-same")
        s1, i1 = repo.save_if_not_duplicate(inq1)
        s2, i2 = repo.save_if_not_duplicate(inq2)
        assert i1 is True
        assert i2 is False

    def test_draft_preserved_on_duplicate(self, repo, temp_db):
        inq = _make_inquiry(inquiry_id="inq-draftdup", msg_id="msg-draftdup", text="Original")
        repo.save(inq)
        with get_db() as conn:
            conn.execute(
                "INSERT INTO response_drafts (draft_id, inquiry_id, draft_text, generated_by, status) VALUES (?, ?, ?, ?, ?)",
                ("draft-3", "inq-draftdup", "Draft text", "ai", "draft"),
            )
            conn.commit()
        inq2 = _make_inquiry(inquiry_id="inq-dupdup", msg_id="msg-draftdup", text="Different")
        saved, inserted = repo.save_if_not_duplicate(inq2)
        assert inserted is False
        with get_db() as conn:
            draft = conn.execute("SELECT * FROM response_drafts WHERE inquiry_id='inq-draftdup'").fetchone()
            assert draft is not None


# ---------------------------------------------------------------------------
# Migration tests
# ---------------------------------------------------------------------------

class TestMigrations:
    def test_fresh_db(self, temp_db):
        v = get_schema_version()
        assert v == 4
        conn = _connect_db(temp_db)
        conn.row_factory = sqlcipher3.Row
        idx = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_inquiries_source_message'").fetchone()
        assert idx is not None
        conn.close()

    def test_v1_to_v4(self, temp_db):
        import tempfile, os
        from leadlift.config import get_settings
        fd, db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        os.unlink(db_path)
        settings = get_settings()
        old_path = settings.database_path
        settings.database_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        conn = __import__('sqlite3').connect(db_path)
        conn.row_factory = __import__('sqlite3').Row
        conn.execute("""CREATE TABLE inquiries (
            inquiry_id TEXT PRIMARY KEY,
            source TEXT NOT NULL DEFAULT 'manual',
            source_message_id TEXT,
            original_text TEXT NOT NULL,
            received_at TEXT NOT NULL,
            first_observed_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.execute("INSERT INTO schema_version (id, version) VALUES (1, 1)")
        conn.execute("INSERT INTO inquiries (inquiry_id, source, source_message_id, original_text, received_at, first_observed_at) VALUES ('v1-rec', 'manual', 'msg-v1', 'v1 text', '2026-01-01', '2026-01-01')")
        conn.commit()
        conn.close()

        migrate_db()
        v = get_schema_version()
        assert v == 4

        conn = _connect_db(db_path)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        assert 'response_drafts' in tables
        cols = [r[1] for r in conn.execute("PRAGMA table_info(inquiries)").fetchall()]
        assert 'text_fingerprint' in cols
        idx = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_inquiries_source_message'").fetchone()
        assert idx is not None
        row = conn.execute("SELECT original_text FROM inquiries WHERE inquiry_id='v1-rec'").fetchone()
        assert row[0] == 'v1 text'
        conn.close()

        settings.database_path = old_path
        os.unlink(db_path)

    def test_v2_to_v4(self, temp_db):
        import tempfile, os
        fd, db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        os.unlink(db_path)
        settings = get_settings()
        old_path = settings.database_path
        settings.database_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        conn = __import__('sqlite3').connect(db_path)
        conn.row_factory = __import__('sqlite3').Row
        conn.execute("""CREATE TABLE inquiries (
            inquiry_id TEXT PRIMARY KEY,
            source TEXT NOT NULL DEFAULT 'manual',
            source_message_id TEXT,
            original_text TEXT NOT NULL,
            received_at TEXT NOT NULL,
            first_observed_at TEXT NOT NULL
        )""")
        conn.execute("""CREATE TABLE response_drafts (
            draft_id TEXT PRIMARY KEY,
            inquiry_id TEXT NOT NULL,
            draft_text TEXT NOT NULL,
            generated_by TEXT NOT NULL,
            model TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            FOREIGN KEY (inquiry_id) REFERENCES inquiries(inquiry_id)
        )""")
        conn.execute("""CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT (datetime('now')))""")
        conn.execute("INSERT INTO schema_version (id, version) VALUES (1, 2)")
        conn.execute("INSERT INTO inquiries (inquiry_id, source, source_message_id, original_text, received_at, first_observed_at) VALUES ('v2-rec', 'manual', 'msg-v2', 'v2 text', '2026-01-01', '2026-01-01')")
        conn.commit()
        conn.close()

        migrate_db()
        v = get_schema_version()
        assert v == 4

        conn = _connect_db(db_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(inquiries)").fetchall()]
        assert 'text_fingerprint' in cols
        idx = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_inquiries_source_message'").fetchone()
        assert idx is not None
        row = conn.execute("SELECT original_text FROM inquiries WHERE inquiry_id='v2-rec'").fetchone()
        assert row[0] == 'v2 text'
        conn.close()

        settings.database_path = old_path
        os.unlink(db_path)

    def test_v3_no_collisions(self, temp_db):
        fd, db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        os.unlink(db_path)
        settings = get_settings()
        old_path = settings.database_path
        settings.database_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        conn = _connect_db(db_path)
        conn.row_factory = sqlcipher3.Row
        conn.execute('''CREATE TABLE inquiries (inquiry_id TEXT PRIMARY KEY, source TEXT NOT NULL DEFAULT 'manual', source_message_id TEXT, original_text TEXT NOT NULL, text_fingerprint TEXT, received_at TEXT NOT NULL, first_observed_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT (datetime('now')))''')
        conn.execute('INSERT INTO schema_version (id, version) VALUES (1, 3)')
        conn.execute("INSERT INTO inquiries (inquiry_id, source, source_message_id, original_text, received_at, first_observed_at) VALUES ('1', 'web', 'msg-1', 'text1', '2026-01-01', '2026-01-01')")
        conn.commit()
        conn.close()

        migrate_db()
        v = get_schema_version()
        assert v == 4

        conn = _connect_db(db_path)
        conn.row_factory = sqlcipher3.Row
        idx = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_inquiries_source_message'").fetchone()
        assert idx is not None
        row = conn.execute("SELECT original_text FROM inquiries WHERE inquiry_id='1'").fetchone()
        assert row[0] == 'text1'
        conn.close()

        settings.database_path = old_path
        os.unlink(db_path)

    def test_v3_with_collisions_fails(self, temp_db):
        fd, db_path = tempfile.mkstemp(suffix='.db')
        os.close(fd)
        os.unlink(db_path)
        settings = get_settings()
        old_path = settings.database_path
        settings.database_path = db_path
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        conn = _connect_db(db_path)
        conn.row_factory = sqlcipher3.Row
        conn.execute('''CREATE TABLE inquiries (inquiry_id TEXT PRIMARY KEY, source TEXT NOT NULL DEFAULT 'manual', source_message_id TEXT, original_text TEXT NOT NULL, text_fingerprint TEXT, received_at TEXT NOT NULL, first_observed_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL DEFAULT (datetime('now')))''')
        conn.execute('INSERT INTO schema_version (id, version) VALUES (1, 3)')
        conn.execute("INSERT INTO inquiries (inquiry_id, source, source_message_id, original_text, received_at, first_observed_at) VALUES ('1', 'web', 'msg-1', 'text1', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO inquiries (inquiry_id, source, source_message_id, original_text, received_at, first_observed_at) VALUES ('2', 'web', 'msg-1', 'text2', '2026-01-01', '2026-01-01')")
        conn.commit()
        conn.close()

        with pytest.raises(RuntimeError):
            migrate_db()

        conn = _connect_db(db_path)
        conn.row_factory = sqlcipher3.Row
        v = conn.execute('SELECT version FROM schema_version WHERE id=1').fetchone()
        assert v[0] == 3
        rows = conn.execute("SELECT inquiry_id, original_text FROM inquiries").fetchall()
        assert len(rows) == 2
        idx = conn.execute("SELECT name FROM sqlite_master WHERE type='index' AND name='idx_inquiries_source_message'").fetchone()
        assert idx is None
        conn.close()

        settings.database_path = old_path
        os.unlink(db_path)

    def test_repeated_init(self, temp_db):
        init_db_with_migrations()
        init_db_with_migrations()
        v = get_schema_version()
        assert v == 4

    def test_null_identifiers(self, temp_db):
        conn = _connect_db(temp_db)
        conn.row_factory = sqlcipher3.Row
        conn.execute("INSERT INTO inquiries (inquiry_id, source, source_message_id, original_text, received_at, first_observed_at) VALUES ('null-test', 'manual', NULL, 'text', '2026-01-01', '2026-01-01')")
        conn.commit()
        conn.close()

    def test_empty_string_identifiers(self, temp_db):
        conn = _connect_db(temp_db)
        conn.row_factory = sqlcipher3.Row
        conn.execute("INSERT INTO inquiries (inquiry_id, source, source_message_id, original_text, received_at, first_observed_at) VALUES ('empty-test', 'manual', '', 'text', '2026-01-01', '2026-01-01')")
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# Cross-adapter identity tests
# ---------------------------------------------------------------------------

class TestCrossAdapterIdentity:
    def test_different_sources_same_msg_id(self, repo):
        inq1 = _make_inquiry(source=InquirySource.WHATSAPP, inquiry_id="inq-wa", msg_id="msg-shared")
        inq2 = _make_inquiry(source=InquirySource.TELEGRAM, inquiry_id="inq-tg", msg_id="msg-shared")
        s1, i1 = repo.save_if_not_duplicate(inq1)
        s2, i2 = repo.save_if_not_duplicate(inq2)
        assert i1 is True
        assert i2 is True

    def test_same_source_same_msg_id_rejected(self, repo):
        inq1 = _make_inquiry(source=InquirySource.WHATSAPP, inquiry_id="inq-wa2", msg_id="msg-same")
        inq2 = _make_inquiry(source=InquirySource.WHATSAPP, inquiry_id="inq-wa3", msg_id="msg-same")
        s1, i1 = repo.save_if_not_duplicate(inq1)
        s2, i2 = repo.save_if_not_duplicate(inq2)
        assert i1 is True
        assert i2 is False

    def test_null_no_collision(self, repo):
        inq1 = _make_inquiry(source=InquirySource.WHATSAPP, inquiry_id="inq-n1", msg_id=None)
        inq2 = _make_inquiry(source=InquirySource.WHATSAPP, inquiry_id="inq-n2", msg_id=None)
        s1, i1 = repo.save_if_not_duplicate(inq1)
        s2, i2 = repo.save_if_not_duplicate(inq2)
        assert i1 is True
        assert i2 is True

    def test_empty_no_collision(self, repo):
        inq1 = _make_inquiry(source=InquirySource.WHATSAPP, inquiry_id="inq-e1", msg_id="")
        inq2 = _make_inquiry(source=InquirySource.WHATSAPP, inquiry_id="inq-e2", msg_id="")
        s1, i1 = repo.save_if_not_duplicate(inq1)
        s2, i2 = repo.save_if_not_duplicate(inq2)
        assert i1 is True
        assert i2 is True

    def test_provider_id_preserved(self, repo):
        inq = _make_inquiry(source=InquirySource.WHATSAPP, inquiry_id="inq-pres", msg_id="provider-id-123")
        saved, inserted = repo.save_if_not_duplicate(inq)
        loaded = repo.get("inq-pres")
        assert loaded.source_message_id == "provider-id-123"


# ---------------------------------------------------------------------------
# Concurrent insertion tests
# ---------------------------------------------------------------------------

class TestConcurrentInsertion:
    """Genuine concurrency tests using threading.Barrier for synchronization."""

    def test_concurrent_same_message(self, temp_db):
        """2 workers, same source/message_id, barrier-synchronized."""
        from leadlift.repository import InquiryRepository
        errors = []
        results = []
        barrier = threading.Barrier(2, timeout=10)

        def worker():
            try:
                repo = InquiryRepository()
                inq = _make_inquiry(source=InquirySource.WHATSAPP, inquiry_id=f"inq-{threading.current_thread().name}", msg_id="concurrent-msg-1")
                barrier.wait()  # synchronize before insert
                saved, inserted = repo.save_if_not_duplicate(inq)
                results.append((saved.inquiry_id, inserted))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, name=f"t{i}") for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f"Errors: {errors}"
        assert len(results) == 2
        inserted_count = sum(1 for _, ins in results if ins)
        assert inserted_count == 1, f"Expected 1 insert, got {inserted_count}: {results}"

        # Verify exactly one record in DB
        with get_db() as conn:
            count = conn.execute("SELECT COUNT(*) FROM inquiries WHERE source='whatsapp' AND source_message_id='concurrent-msg-1'").fetchone()[0]
            assert count == 1

    def test_concurrent_same_message_repeated(self, temp_db):
        """Repeat 5 times to increase race likelihood."""
        from leadlift.repository import InquiryRepository
        for iteration in range(5):
            # Clean DB
            reset_db_temp = True
            errors = []
            results = []
            barrier = threading.Barrier(2, timeout=10)

            def worker():
                try:
                    repo = InquiryRepository()
                    inq = _make_inquiry(source=InquirySource.WHATSAPP, inquiry_id=f"inq-iter{iteration}-{threading.current_thread().name}", msg_id=f"concurrent-msg-iter{iteration}")
                    barrier.wait()
                    saved, inserted = repo.save_if_not_duplicate(inq)
                    results.append((saved.inquiry_id, inserted))
                except Exception as e:
                    errors.append(str(e))

            threads = [threading.Thread(target=worker, name=f"t{i}") for i in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=30)

            assert len(errors) == 0, f"Iter {iteration} errors: {errors}"
            inserted_count = sum(1 for _, ins in results if ins)
            assert inserted_count == 1, f"Iter {iteration}: expected 1 insert, got {inserted_count}"

    def test_concurrent_different_messages(self, temp_db):
        """2 workers, different messages, barrier-synchronized."""
        errors = []
        results = []
        barrier = threading.Barrier(2, timeout=10)

        def worker(msg_id, inq_id):
            try:
                repo = InquiryRepository()
                inq = _make_inquiry(source=InquirySource.WHATSAPP, inquiry_id=inq_id, msg_id=msg_id)
                barrier.wait()
                saved, inserted = repo.save_if_not_duplicate(inq)
                results.append((saved.inquiry_id, inserted))
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=worker, args=("msg-a", "inq-a"))
        t2 = threading.Thread(target=worker, args=("msg-b", "inq-b"))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert len(errors) == 0, f"Errors: {errors}"
        assert len(results) == 2
        assert all(ins for _, ins in results)

    def test_concurrent_same_different_sources(self, temp_db):
        """Same msg ID, different sources, barrier-synchronized."""
        errors = []
        results = []
        barrier = threading.Barrier(2, timeout=10)

        def worker(source, inq_id):
            try:
                repo = InquiryRepository()
                inq = _make_inquiry(source=source, inquiry_id=inq_id, msg_id="shared-msg")
                barrier.wait()
                saved, inserted = repo.save_if_not_duplicate(inq)
                results.append((saved.inquiry_id, inserted, source.value))
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=worker, args=(InquirySource.WHATSAPP, "inq-wa"))
        t2 = threading.Thread(target=worker, args=(InquirySource.TELEGRAM, "inq-tg"))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert len(errors) == 0, f"Errors: {errors}"
        assert len(results) == 2
        assert all(ins for _, ins, _ in results)

    def test_original_preserved_concurrent(self, temp_db):
        """5 workers, same source/msg, original preserved, draft intact."""
        repo = InquiryRepository()
        inq = _make_inquiry(source=InquirySource.WHATSAPP, inquiry_id="inq-orig", msg_id="msg-orig", text="Original text")
        repo.save(inq)

        with get_db() as conn:
            conn.execute(
                "INSERT INTO response_drafts (draft_id, inquiry_id, draft_text, generated_by, status) VALUES (?, ?, ?, ?, ?)",
                ("draft-conc", "inq-orig", "Draft text", "ai", "draft"),
            )
            conn.commit()

        errors = []
        results = []
        barrier = threading.Barrier(5, timeout=10)

        def worker():
            try:
                r = InquiryRepository()
                inq2 = _make_inquiry(source=InquirySource.WHATSAPP, inquiry_id=f"inq-dup-{threading.current_thread().name}", msg_id="msg-orig", text="Overwritten text")
                barrier.wait()
                saved, inserted = r.save_if_not_duplicate(inq2)
                results.append((saved.inquiry_id, inserted, saved.original_text))
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=worker, name=f"w{i}") for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert len(errors) == 0, f"Errors: {errors}"
        assert len(results) == 5
        for inq_id, inserted, text in results:
            assert inserted is False
            assert inq_id == "inq-orig"
            assert text == "Original text"

        with get_db() as conn:
            draft = conn.execute("SELECT * FROM response_drafts WHERE inquiry_id='inq-orig'").fetchone()
            assert draft is not None
            count = conn.execute("SELECT COUNT(*) FROM inquiries WHERE source='whatsapp' AND source_message_id='msg-orig'").fetchone()[0]
            assert count == 1

# ---------------------------------------------------------------------------
# Adapter-level atomic persistence tests
# ---------------------------------------------------------------------------

class TestAdapterAtomicPersistence:
    def test_whatsapp_atomic(self, temp_db):
        from leadlift.adapters.whatsapp import WhatsAppIntakeAdapter
        settings = {"whatsapp_verify_token": "t", "whatsapp_app_secret": "s"}
        payload = {
            "object": "whatsapp_business_account",
            "entry": [{"id": "W", "changes": [{"field": "messages", "value": {
                "messaging_product": "whatsapp",
                "metadata": {"display_phone_number": "1", "phone_number_id": "1"},
                "contacts": [{"profile": {"name": "Test"}, "wa_id": "123"}],
                "messages": [{"from": "123", "id": "msg-wa-atomic", "timestamp": "1729999999", "text": {"body": "Hello"}, "type": "text"}],
            }}]}],
        }
        a1 = WhatsAppIntakeAdapter(settings=settings)
        r1 = a1.process_webhook_payload(payload)
        a1.save_confirmed_inquiry(r1.extracted_inquiry)

        a2 = WhatsAppIntakeAdapter(settings=settings)
        r2 = a2.process_webhook_payload(payload)
        assert r2.is_duplicate is True

    def test_instagram_atomic(self, temp_db):
        from leadlift.adapters.instagram import InstagramIntakeAdapter
        settings = {"instagram_verify_token": "t", "instagram_app_secret": "s"}
        payload = {"object": "instagram", "entry": [{"id": "IG", "time": 1, "messaging": [{"sender": {"id": "123"}, "recipient": {"id": "456"}, "timestamp": "1729999999", "message": {"mid": "msg-ig-atomic", "text": "Hello"}}]}]}
        a1 = InstagramIntakeAdapter(settings=settings)
        r1 = a1.process_webhook_payload(payload)
        a1.save_confirmed_inquiry(r1.extracted_inquiry)

        a2 = InstagramIntakeAdapter(settings=settings)
        r2 = a2.process_webhook_payload(payload)
        assert r2.is_duplicate is True

    def test_facebook_atomic(self, temp_db):
        from leadlift.adapters.facebook import FacebookIntakeAdapter
        settings = {"facebook_verify_token": "t", "facebook_app_secret": "s"}
        payload = {"object": "page", "entry": [{"id": "PG", "time": 1, "messaging": [{"sender": {"id": "123"}, "recipient": {"id": "456"}, "timestamp": 1729999999, "message": {"mid": "msg-fb-atomic", "text": "Hello"}}]}]}
        a1 = FacebookIntakeAdapter(settings=settings)
        r1 = a1.process_webhook_payload(payload)
        a1.save_confirmed_inquiry(r1.extracted_inquiry)

        a2 = FacebookIntakeAdapter(settings=settings)
        r2 = a2.process_webhook_payload(payload)
        assert r2.is_duplicate is True

    def test_telegram_atomic(self, temp_db):
        from leadlift.adapters.telegram import TelegramIntakeAdapter
        settings = {"telegram_webhook_secret": "t", "telegram_bot_token": "s"}
        payload = {"update_id": 100, "message": {"message_id": 200, "from": {"id": 123, "first_name": "John"}, "chat": {"id": 456, "type": "private"}, "date": 1729999999, "text": "Hello"}}
        a1 = TelegramIntakeAdapter(settings=settings)
        r1 = a1.process_webhook_payload(payload)
        a1.save_confirmed_inquiry(r1.extracted_inquiry)

        a2 = TelegramIntakeAdapter(settings=settings)
        r2 = a2.process_webhook_payload(payload)
        assert r2.is_duplicate is True

    def test_gmail_atomic(self, temp_db):
        from leadlift.adapters.email import GmailIntakeAdapter
        from leadlift.models import Inquiry, InquirySource
        adapter = GmailIntakeAdapter(settings={})
        inq = Inquiry(
            source=InquirySource.EMAIL,
            source_platform="gmail",
            source_message_id="gmail-msg-atomic",
            original_text="Hello",
            received_at="2026-01-01T00:00:00Z",
        )
        saved = adapter.save_confirmed_inquiry(inq)
        assert saved.inquiry_id is not None

        inq2 = Inquiry(
            source=InquirySource.EMAIL,
            source_platform="gmail",
            source_message_id="gmail-msg-atomic",
            original_text="Different",
            received_at="2026-01-01T00:00:00Z",
        )
        saved2 = adapter.save_confirmed_inquiry(inq2)
        assert saved2.inquiry_id == saved.inquiry_id
        assert saved2.original_text == "Hello"

    def test_website_form_atomic(self, temp_db):
        from leadlift.adapters.website_form import WebsiteFormIntakeAdapter
        settings = {"website_form_webhook_secret": "test_secret"}
        payload = {"submission_id": "sub-atomic-2", "message": "Hello"}
        a1 = WebsiteFormIntakeAdapter(settings=settings)
        r1 = a1.process_webhook_payload(payload, secret="test_secret", configured_secret="test_secret")
        a1.save_confirmed_inquiry(r1.extracted_inquiry)

        a2 = WebsiteFormIntakeAdapter(settings=settings)
        r2 = a2.process_webhook_payload(payload, secret="test_secret", configured_secret="test_secret")
        assert r2.is_duplicate is True

    def test_different_messages_both_succeed(self, temp_db):
        from leadlift.adapters.website_form import WebsiteFormIntakeAdapter
        settings = {"website_form_webhook_secret": "test_secret"}
        a1 = WebsiteFormIntakeAdapter(settings=settings)
        r1 = a1.process_webhook_payload({"submission_id": "sub-a", "message": "First"}, secret="test_secret", configured_secret="test_secret")
        a1.save_confirmed_inquiry(r1.extracted_inquiry)

        a2 = WebsiteFormIntakeAdapter(settings=settings)
        r2 = a2.process_webhook_payload({"submission_id": "sub-b", "message": "Second"}, secret="test_secret", configured_secret="test_secret")
        a2.save_confirmed_inquiry(r2.extracted_inquiry)

        assert r1.extracted_inquiry.inquiry_id != r2.extracted_inquiry.inquiry_id


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
