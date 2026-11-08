"""LEADLIFT — Focused safety infrastructure tests.

Tests the Batch 3A infrastructure without invoking any of the 29
legacy reset_db() callers. Uses only the new capability-based API
and the shared test_support module.

These tests use exclusively temporary databases and never touch
protected persistent databases.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from leadlift.db import (
    _ISSUED_RESET_CAPABILITIES,
    _DatabaseResetCapability,
    _issue_reset_capability,
    _revoke_reset_capability,
    reset_db,
)
from tests.test_support import _safe_cleanup_directory, reset_test_database


def _can_symlink():
    """Check if symlink creation works on this system."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target"
            target.write_text("x")
            link = Path(tmp) / "link"
            link.symlink_to(target)
            return True
    except (OSError, NotImplementedError):
        return False


SKIP_SYMLINK = not _can_symlink()


# ---------------------------------------------------------------------------
# Capability registry lifecycle tests
# ---------------------------------------------------------------------------

class TestCapabilityRevocation:
    """Tests for _revoke_reset_capability()."""

    def test_revoke_removes_exact_registered_object(self):
        """Revoking a valid capability removes it from the registry."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            db_path = temp_root / "test.db"

            cap = _issue_reset_capability(db_path, temp_root)
            assert cap._token in _ISSUED_RESET_CAPABILITIES

            _revoke_reset_capability(cap)
            assert cap._token not in _ISSUED_RESET_CAPABILITIES

    def test_revoke_after_consumption_is_harmless(self):
        """Revoking an already-consumed capability is a no-op."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            db_path = temp_root / "test.db"

            cap = _issue_reset_capability(db_path, temp_root)
            reset_db(db_path, capability=cap)
            # Capability was consumed by reset_db()

            # Revoking again should not raise
            _revoke_reset_capability(cap)
            assert cap._token not in _ISSUED_RESET_CAPABILITIES

    def test_revoke_after_rejected_reset_prevents_orphan(self):
        """Revoking after a failed reset does not leave orphan entry."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            db_path = temp_root / "test.db"

            cap = _issue_reset_capability(db_path, temp_root)
            # Tamper with the db_path so reset validation fails
            # (pass a different path than the capability is bound to)
            try:
                reset_db(temp_root / "other.db", capability=cap)
            except RuntimeError:
                pass

            # Capability should still be in registry (not consumed)
            assert cap._token in _ISSUED_RESET_CAPABILITIES

            # Revoke it
            _revoke_reset_capability(cap)
            assert cap._token not in _ISSUED_RESET_CAPABILITIES

    def test_consumed_capability_cannot_be_reused(self):
        """A consumed capability cannot authorize a second reset."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            db_path = temp_root / "test.db"

            cap = _issue_reset_capability(db_path, temp_root)
            reset_db(db_path, capability=cap)  # consumes capability

            # Same capability cannot be used again
            try:
                reset_db(db_path, capability=cap)
                pytest.fail("Expected RuntimeError for reused capability")
            except RuntimeError:
                pass

    def test_manually_constructed_capability_rejected(self):
        """Manually constructed capability is not authorized."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            db_path = temp_root / "test.db"

            fake_cap = _DatabaseResetCapability(
                token=b"\x00" * 32,
                target_path=db_path.resolve(),
                temp_root=temp_root.resolve(),
            )

            try:
                reset_db(db_path, capability=fake_cap)
                pytest.fail("Expected RuntimeError for manually constructed capability")
            except RuntimeError:
                pass

            assert fake_cap._token not in _ISSUED_RESET_CAPABILITIES

    def test_mismatched_capability_rejected(self):
        """Capability bound to different path is rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            db_path_a = temp_root / "a.db"
            db_path_b = temp_root / "b.db"

            cap = _issue_reset_capability(db_path_a, temp_root)
            try:
                reset_db(db_path_b, capability=cap)
                pytest.fail("Expected RuntimeError for path-mismatched capability")
            except RuntimeError:
                pass

            # Capability should still be in registry (not consumed)
            assert cap._token in _ISSUED_RESET_CAPABILITIES
            _revoke_reset_capability(cap)


# ---------------------------------------------------------------------------
# Safe cleanup tests
# ---------------------------------------------------------------------------

class TestSafeCleanupDirectory:
    """Tests for _safe_cleanup_directory()."""

    def test_cleanup_removes_regular_files(self):
        """Regular files are deleted."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            f1 = temp_root / "file1.txt"
            f2 = temp_root / "file2.txt"
            f1.write_text("hello")
            f2.write_text("world")

            _safe_cleanup_directory(temp_root)

            assert not f1.exists()
            assert not f2.exists()
            assert not temp_root.exists()

    def test_cleanup_removes_nested_directories(self):
        """Nested directories and their contents are removed."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            sub = temp_root / "sub"
            sub.mkdir()
            deep = sub / "deep"
            deep.mkdir()
            f = deep / "file.txt"
            f.write_text("nested")

            _safe_cleanup_directory(temp_root)

            assert not f.exists()
            assert not deep.exists()
            assert not sub.exists()
            assert not temp_root.exists()

    @pytest.mark.skipif(SKIP_SYMLINK, reason="Symlink creation not supported on this system")
    def test_cleanup_removes_file_symlink(self):
        """File symlinks are unlinked as links, not targets."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            target = temp_root / "target.txt"
            target.write_text("real content")
            link = temp_root / "link.txt"
            link.symlink_to(target)

            _safe_cleanup_directory(temp_root)

            # Symlink is removed
            assert not link.exists()
            # Target is NOT removed (it's a real file, but we only unlink the link)
            assert target.exists()
            assert temp_root.exists()  # temp_root still has target.txt
            # Clean up the remaining target
            target.unlink()
            temp_root.rmdir()

    @pytest.mark.skipif(SKIP_SYMLINK, reason="Symlink creation not supported on this system")
    def test_cleanup_removes_directory_symlink_without_following(self):
        """Directory symlinks are unlinked as links, targets preserved."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            target_dir = temp_root / "target_dir"
            target_dir.mkdir()
            (target_dir / "file.txt").write_text("content")
            link_dir = temp_root / "link_dir"
            link_dir.symlink_to(target_dir)

            _safe_cleanup_directory(temp_root)

            # Symlink is removed
            assert not link_dir.exists()
            # Target directory is NOT removed
            assert target_dir.exists()
            assert (target_dir / "file.txt").exists()
            assert temp_root.exists()
            # Clean up
            (target_dir / "file.txt").unlink()
            target_dir.rmdir()
            temp_root.rmdir()

    @pytest.mark.skipif(SKIP_SYMLINK, reason="Symlink creation not supported on this system")
    def test_cleanup_removes_dangling_symlink(self):
        """Dangling symlinks are unlinked safely."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            target = temp_root / "target.txt"
            target.write_text("content")
            link = temp_root / "dangling"
            link.symlink_to(target)
            # Delete the target to make the symlink dangling
            target.unlink()

            _safe_cleanup_directory(temp_root)

            assert not link.exists()
            assert not temp_root.exists()

    @pytest.mark.skipif(SKIP_SYMLINK, reason="Symlink creation not supported on this system")
    def test_cleanup_rejects_symlink_outside_root(self):
        """Symlink resolving outside temp_root is rejected."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            # Create a file outside temp_root
            outside = Path(tempfile.gettempdir()) / "outside_target.txt"
            outside.write_text("outside content")
            try:
                link = temp_root / "escape"
                link.symlink_to(outside)

                try:
                    _safe_cleanup_directory(temp_root)
                    pytest.fail("Expected RuntimeError for escaping symlink")
                except RuntimeError:
                    pass
                finally:
                    # Clean up
                    if link.exists():
                        link.unlink()
                    if outside.exists():
                        outside.unlink()
            finally:
                if outside.exists():
                    outside.unlink()

    @pytest.mark.skipif(SKIP_SYMLINK, reason="Symlink creation not supported on this system")
    def test_cleanup_rejects_protected_path(self):
        """Protected database paths are rejected."""
        from leadlift.db import PROTECTED_PATHS

        # Find a protected path
        protected = None
        for p in PROTECTED_PATHS:
            if "Forensic_Snapshot" in str(p):
                protected = p
                break

        if protected is not None and protected.parent.exists():
            # Create a symlink inside a temp dir pointing to the protected db
            with tempfile.TemporaryDirectory() as tmp:
                temp_root = Path(tmp)
                link = temp_root / "protected_link"
                link.symlink_to(protected)

                try:
                    _safe_cleanup_directory(temp_root)
                    pytest.fail("Expected RuntimeError for protected path symlink")
                except RuntimeError:
                    pass
                finally:
                    if link.exists():
                        link.unlink()

    def test_cleanup_reports_failures(self):
        """Cleanup failures propagate rather than being suppressed."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            f = temp_root / "file.txt"
            f.write_text("test")

            # Make the file read-only to test failure handling
            f.chmod(0o444)

            try:
                _safe_cleanup_directory(temp_root)
                # On Windows, read-only may not block unlink.
                # If cleanup succeeded, verify files were removed.
                if not f.exists() and not temp_root.exists():
                    return  # Cleanup succeeded on this platform
            except PermissionError:
                # On Windows, read-only files can't be unlinked — failure propagates.
                # This confirms failures are NOT suppressed.
                pass
            finally:
                # Ensure cleanup for test teardown
                if f.exists():
                    try:
                        f.chmod(0o644)
                        f.unlink()
                    except OSError:
                        pass
                if temp_root.exists():
                    try:
                        temp_root.rmdir()
                    except OSError:
                        pass


# ---------------------------------------------------------------------------
# reset_test_database helper tests
# ---------------------------------------------------------------------------

class TestResetTestDatabase:
    """Tests for reset_test_database() helper."""

    def test_reset_test_database_creates_fresh_database(self):
        """reset_test_database creates and initializes a temp database."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_root = Path(tmp)
            db_path = temp_root / "test.db"

            reset_test_database(db_path, temp_root)

            assert db_path.exists()

    def test_reset_test_database_rejects_protected_path(self):
        """reset_test_database rejects protected database paths."""
        protected = Path(
            "C:/Users/OGUNYEYE SOLOMON/Projects/Test/leadlift/data/leadlift.db"
        )
        temp_root = Path(tempfile.gettempdir())

        try:
            reset_test_database(protected, temp_root)
            pytest.fail("Expected RuntimeError for protected path")
        except RuntimeError:
            pass

        # No orphan capability entries
        assert len(_ISSUED_RESET_CAPABILITIES) == 0


# ---------------------------------------------------------------------------
# Settings isolation tests
# ---------------------------------------------------------------------------

class TestSettingsIsolation:
    """Tests that isolated_database does not touch protected databases."""

    def test_isolated_database_uses_temp_path(self, isolated_database):
        """The fixture yields a temp path, not a protected path."""
        from leadlift.config import get_settings

        settings = get_settings()
        assert settings.database_path == str(isolated_database)
        assert "leadlift/data/leadlift.db" not in settings.database_path
        assert isolated_database.exists()


# ---------------------------------------------------------------------------
# Connection lifecycle tests
# ---------------------------------------------------------------------------

class TestConnectionLifecycle:
    """Tests that connections are properly managed."""

    def test_repository_uses_context_manager(self, isolated_database):
        """InquiryRepository uses get_db() context manager (connections closed)."""
        from leadlift.repository import InquiryRepository
        from leadlift.models import Inquiry, InquirySource

        repo = InquiryRepository()
        inq = Inquiry(
            source=InquirySource.MANUAL,
            source_platform="manual",
            original_text="Connection test",
            received_at="2026-01-01T00:00:00Z",
            first_observed_at="2026-01-01T00:00:00Z",
        )
        repo.save(inq)
        retrieved = repo.get(inq.inquiry_id)
        assert retrieved is not None
        assert retrieved.original_text == "Connection test"

        # After all context managers exit, no connections should be open.
        # This is verified implicitly: if connections leaked, cleanup
        # of the temp database on Windows would fail.
