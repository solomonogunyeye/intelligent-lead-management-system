"""LEADLIFT — Shared test support infrastructure.

Provides reusable utilities for test isolation:
- Safe cleanup of test-owned temporary directories (symlink-aware,
  protected-path-validated, no error suppression).
- Capability-based database reset for tests.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from leadlift.db import (
    _assert_not_protected,
    _issue_reset_capability,
    _revoke_reset_capability,
    init_db_with_migrations,
    reset_db,
)


def _safe_cleanup_directory(temp_root: Path) -> None:
    """Remove all files within temp_root, then remove temp_root itself.

    Symlinks are handled as filesystem entries in their own right:
    the link itself is unlinked, never its resolved target. Directory
    symlinks are not traversed. Dangling symlinks are unlinked safely.

    Every path is canonicalized and validated against protected-path
    rules before deletion. Cleanup never targets files outside
    temp_root. Errors are not suppressed — a partial cleanup failure
    propagates so callers are aware cleanup was incomplete.
    """
    resolved_root = temp_root.resolve()

    # Validate that temp_root is not a protected path.
    _assert_not_protected(resolved_root)

    # Validate that temp_root is within the system temporary hierarchy.
    try:
        resolved_root.relative_to(Path(tempfile.gettempdir()).resolve())
    except ValueError:
        raise RuntimeError(
            f"Temporary root {temp_root} is not within the system "
            f"temporary directory"
        )

    # ------------------------------------------------------------------
    # Phase 1: Build and validate the complete deletion plan.
    # ------------------------------------------------------------------
    files_to_unlink: list[Path] = []   # regular files
    symlinks_to_unlink: list[Path] = []  # symlink links themselves (not targets)
    dirs_to_rmdir: list[Path] = []     # real directories (deepest first)

    def walk(dir_path: Path) -> None:
        for entry in dir_path.iterdir():
            # Resolve for validation only; never delete via resolved path.
            try:
                entry_resolved = entry.resolve()
            except OSError as exc:
                raise RuntimeError(
                    f"Cleanup refused: cannot resolve {entry}: {exc}"
                )

            # Verify the resolved path stays within the fixture-owned root.
            try:
                entry_resolved.relative_to(resolved_root)
            except ValueError:
                raise RuntimeError(
                    f"Cleanup refused: {entry} resolves outside "
                    f"temporary root {temp_root}"
                )

            # Protected-path check on the resolved path.
            _assert_not_protected(entry_resolved)

            if entry.is_symlink():
                # Symlink: validate the target, then unlink the link itself.
                # For dangling symlinks, entry_resolved may not exist but
                # resolve() still returns a path for containment checking.
                symlinks_to_unlink.append(entry)
            elif entry.is_dir() and not entry.is_symlink():
                # Real directory: recurse into it, then rmdir it later.
                # is_dir() follows symlinks, but we already excluded symlinks
                # above, so this is a genuine directory.
                walk(entry)
                dirs_to_rmdir.append(entry)
            elif entry.is_file() and not entry.is_symlink():
                # Regular file.
                # is_file() follows symlinks, but we already excluded
                # symlinks above, so this is a genuine file.
                files_to_unlink.append(entry)
            else:
                raise RuntimeError(
                    f"Cleanup refused: unexpected filesystem entry type: {entry}"
                )

    walk(resolved_root)

    # Sort for safe deletion order: deepest paths first.
    files_to_unlink.sort(key=lambda p: len(p.parts), reverse=True)
    symlinks_to_unlink.sort(key=lambda p: len(p.parts), reverse=True)
    dirs_to_rmdir.sort(key=lambda p: len(p.parts), reverse=True)

    # ------------------------------------------------------------------
    # Phase 2: Execute the validated deletion plan.
    # ------------------------------------------------------------------
    # Unlink regular files first (deepest first).
    for path in files_to_unlink:
        path.unlink()

    # Unlink symlink links themselves (never their resolved targets).
    # is_symlink() returns True for dangling symlinks, so they are
    # correctly unlinked even though exists() returns False.
    for path in symlinks_to_unlink:
        path.unlink()

    # Remove real directories (deepest first, after contents are gone).
    for dir_path in dirs_to_rmdir:
        dir_path.rmdir()

    # Remove the temp_root directory itself.
    resolved_root.rmdir()


def reset_test_database(db_path: str | Path, temp_root: str | Path) -> None:
    """Issue a fresh single-use capability and reset the database.

    Args:
        db_path: Canonical path to the test-owned database file.
        temp_root: Canonical path to the test-owned temporary root directory.

    Raises:
        RuntimeError: If capability issuance or reset validation fails.
    """
    capability = _issue_reset_capability(db_path, temp_root)
    try:
        reset_db(db_path, capability=capability)
    finally:
        _revoke_reset_capability(capability)
