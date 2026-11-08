"""LEADLIFT — Shared test isolation fixtures.

Provides a per-test isolated SQLCipher database fixture that ensures no
test can accidentally modify, delete, or recreate the persistent
application database. All database state is scoped to a unique temporary
directory within the system temporary directory for each test.

Trust boundary: This fixture protects against accidental misuse. It does
not protect against malicious code executing with unrestricted access
inside the same Python process or test session.

Concurrency: This fixture mutates a process-wide settings singleton
(via pytest monkeypatch). It is NOT safe for parallel execution
(e.g., pytest-xdist) or for concurrent test threads within one
process. Do not run this fixture concurrently with itself or with
any code that reads settings.database_path.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Generator

import pytest

from leadlift.config import get_settings
from leadlift.db import init_db_with_migrations
from tests.test_support import _safe_cleanup_directory


@pytest.fixture
def isolated_database(
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[Path, None, None]:
    """Provide a per-test isolated SQLCipher database.

    Creates a unique temporary directory and database path for each test,
    initializes the database, and yields the database path.

    On teardown, restores the original settings.database_path (via
    pytest monkeypatch) and cleans up all temporary files. Cleanup only
    removes files within the test's temporary directory — it can never
    target persistent application data. Teardown is safe even if setup
    fails partway through.

    Concurrency: This fixture mutates a process-wide settings singleton
    (via pytest monkeypatch). It is NOT safe for parallel execution
    (e.g., pytest-xdist) or for concurrent test threads within one
    process. Do not run this fixture concurrently with itself or with
    any code that reads settings.database_path.
    """
    settings = get_settings()

    # Create a unique temporary directory within the system temp
    # directory. Canonicalize immediately to defeat symlink attacks.
    temp_root = Path(tempfile.mkdtemp(prefix="leadlift_test_")).resolve()
    db_path = temp_root / "test_leadlift.db"

    # Verify temp_root is within the system temporary hierarchy.
    try:
        temp_root.relative_to(Path(tempfile.gettempdir()).resolve())
    except ValueError:
        # Clean up the directory we just created before raising.
        try:
            temp_root.rmdir()
        except OSError:
            pass
        raise RuntimeError(
            f"Temporary root {temp_root} is not within the system "
            f"temporary directory"
        )

    # Initialize the database using the explicit path. This does not
    # require a reset capability because initialization is non-destructive.
    init_db_with_migrations(db_path=db_path)

    # Override settings using pytest monkeypatch for reliable
    # restoration on every failure path, including setup failures and
    # nested fixtures.
    monkeypatch.setattr(settings, "database_path", str(db_path))

    try:
        yield db_path
    finally:
        # Restore settings first so no code path can observe a dangling
        # temporary database path after teardown.
        monkeypatch.undo()

        # Clean up only files within the fixture-owned temporary
        # directory, with protected-path validation on every path.
        _safe_cleanup_directory(temp_root)
