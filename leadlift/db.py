"""LEADLIFT — SQLite database layer with initialization, migration, and connection management."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
import sqlcipher3.dbapi2 as sqlcipher
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from leadlift.config import get_settings
from leadlift.logging_config import get_logger, safe_log
from leadlift.key_management import get_db_key, get_secure_backend, is_secure_backend
import logging

_logger = get_logger("db")


SCHEMA_VERSION = 4


# ---------------------------------------------------------------------------
# Canonical database-path safety policy
# ---------------------------------------------------------------------------

# Protected persistent databases that must never be destroyed or migrated
# by test or destructive operations. Paths are resolved canonically to
# defeat .., relative paths, symlinks, and non-existing-path bypasses.
# The candidate database path is derived dynamically from this module's
# location; known absolute paths for other protected locations are listed
# as additional defence in depth.
_CANDIDATE_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "leadlift.db"

_PROTECTED_DB_BASES: tuple[Path, ...] = (
    _CANDIDATE_DB_PATH,
    Path("C:/Users/OGUNYEYE SOLOMON/Projects/Test/leadlift/data/leadlift.db"),
    Path("C:/Users/OGUNYEYE SOLOMON/Projects/Test/leadlift_recovery/leadlift/data/leadlift.db"),
    Path("C:/Temp/LEADLIFT_Forensic_Snapshot_20261107-113130/leadlift/data/leadlift.db"),
)

# Each protected database and all of its sidecars (plaintext backup,
# encrypted variant, WAL, SHM, journal) are protected.
_PROTECTED_SUFFIXES: tuple[str, ...] = (
    "",
    ".plaintext.bak",
    ".encrypted",
    ".db-wal",
    ".db-shm",
    ".db-journal",
)


def _build_protected_paths() -> frozenset[Path]:
    """Build the full set of protected paths including sidecars."""
    protected: set[Path] = set()
    for base in _PROTECTED_DB_BASES:
        resolved_base = base.resolve()
        for suffix in _PROTECTED_SUFFIXES:
            protected.add(Path(str(resolved_base) + suffix))
    return frozenset(protected)


PROTECTED_PATHS: frozenset[Path] = _build_protected_paths()


# System temporary directory, resolved canonically. All test-owned
# temporary roots must reside within this hierarchy to prevent
# capabilities from being issued against persistent application data.
_SYSTEM_TEMP_DIR: Path = Path(tempfile.gettempdir()).resolve()


# Private registry of legitimately issued reset capabilities.
# Keyed by capability token; value is the capability object itself.
# This registry is the sole source of truth for reset authorization:
# merely being an instance of _DatabaseResetCapability never authorizes
# a reset. Only capabilities present in this registry are valid.
_ISSUED_RESET_CAPABILITIES: dict[bytes, _DatabaseResetCapability] = {}


def _resolve_db_path(path: str | Path) -> Path:
    """Resolve a database path canonically.

    Handles relative paths, ``..`` components, symlinks, and
    non-existing paths. Returns the absolute resolved path.
    """
    return Path(path).resolve()


def _assert_not_protected(db_path: str | Path) -> None:
    """Reject destructive operations against protected persistent databases.

    Raises RuntimeError if db_path resolves to a protected path or any of
    its sidecars.
    """
    resolved = _resolve_db_path(db_path)
    for protected in PROTECTED_PATHS:
        if resolved == protected:
            raise RuntimeError(
                "Destructive database operation blocked: "
                f"{db_path} resolves to a protected persistent "
                f"database or sidecar: {protected}"
            )


class _DatabaseResetCapability:
    """Capability object authorizing a destructive database reset.

    Bound to a specific canonical target path and canonical temporary
    root directory. The constructor is public so that
    _issue_reset_capability() can create instances, but instance
    construction alone does NOT confer authorization. A capability is
    valid only when it is present in _ISSUED_RESET_CAPABILITIES and has
    not been consumed. reset_db() validates against this registry before
    any destructive action.

    Trust boundary: this mechanism protects against accidental misuse
    and ordinary caller forgery. It is not a security boundary against
    malicious code executing with unrestricted access inside the same
    Python process — Python has no truly inaccessible members.
    """

    __slots__ = ("_token", "_target_path", "_temp_root")

    def __init__(self, token: bytes, target_path: Path, temp_root: Path):
        self._token = token
        self._target_path = target_path
        self._temp_root = temp_root

    @property
    def target_path(self) -> Path:
        return self._target_path

    @property
    def temp_root(self) -> Path:
        return self._temp_root


def _issue_reset_capability(target_path: str | Path, temp_root: str | Path) -> _DatabaseResetCapability:
    """Issue a capability authorizing reset of target_path within temp_root.

    Only function that can create a valid capability. Resolves all paths
    canonically, validates that target is within temp_root, and that neither
    is a protected path. Does not perform any destructive action.
    """
    resolved_target = _resolve_db_path(target_path)
    resolved_root = _resolve_db_path(temp_root)

    _assert_not_protected(resolved_target)
    _assert_not_protected(resolved_root)

    if not resolved_root.exists():
        raise RuntimeError(
            f"Temporary root {temp_root} does not exist"
        )
    if not resolved_root.is_dir():
        raise RuntimeError(
            f"Temporary root {temp_root} is not a directory"
        )

    # The temporary root must reside within the system temporary
    # directory to prevent capabilities from being issued against
    # persistent application data directories.
    try:
        resolved_root.relative_to(_SYSTEM_TEMP_DIR)
    except ValueError:
        raise RuntimeError(
            f"Temporary root {temp_root} is not within the system "
            f"temporary directory {_SYSTEM_TEMP_DIR}"
        )

    try:
        resolved_target.relative_to(resolved_root)
    except ValueError:
        raise RuntimeError(
            f"Target {target_path} is not within temporary root {temp_root}"
        )

    token = secrets.token_bytes(32)
    capability = _DatabaseResetCapability(token, resolved_target, resolved_root)
    _ISSUED_RESET_CAPABILITIES[token] = capability
    return capability


def _revoke_reset_capability(capability: _DatabaseResetCapability) -> None:
    """Revoke a capability from the issuance registry.

    Safe to call on already-consumed capabilities. Only revokes the
    exact object instance registered under its token. Never performs
    any database deletion.
    """
    if capability is None or not isinstance(capability, _DatabaseResetCapability):
        return
    registered = _ISSUED_RESET_CAPABILITIES.get(capability._token)
    if registered is capability:
        del _ISSUED_RESET_CAPABILITIES[capability._token]


def _connect_db(path) -> sqlite3.Connection:
    """Open an encrypted SQLite database."""
    key = get_db_key()
    conn = sqlcipher.connect(str(path))
    conn.execute('PRAGMA key = "x\'{}\'"'.format(key))
    conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    conn.row_factory = sqlcipher.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _migrate_plaintext_db_if_needed() -> None:
    """Migrate an existing plaintext database to encrypted storage."""
    settings = get_settings()
    db_path = Path(settings.database_path)
    if not db_path.exists():
        return
    try:
        conn = _connect_db(db_path)
        conn.close()
        return  # Already encrypted; no migration needed.
    except Exception:
        pass
    # Migration is required: this performs destructive file operations.
    # Validate the path before any deletion or rename.
    _assert_not_protected(db_path)
    encrypted_path = db_path.with_name(db_path.name + ".encrypted")
    if encrypted_path.exists():
        encrypted_path.unlink()
    plain_conn = sqlite3.connect(str(db_path))
    plain_conn.row_factory = sqlite3.Row
    enc_conn = sqlcipher.connect(str(encrypted_path))
    enc_conn.execute('PRAGMA key = "x\'{}\'"'.format(get_db_key()))
    for statement in plain_conn.iterdump():
        if statement.strip():
            enc_conn.execute(statement)
    enc_conn.commit()
    verify_conn = _connect_db(encrypted_path)
    verify_conn.close()
    plain_conn.close()
    enc_conn.close()
    backup_path = db_path.with_name(db_path.name + ".plaintext.bak")
    if backup_path.exists():
        backup_path.unlink()
    db_path.rename(backup_path)
    encrypted_path.rename(db_path)


def get_connection() -> sqlite3.Connection:
    """Get a connection to the SQLite database."""
    settings = get_settings()
    settings.ensure_db_dir()
    _migrate_plaintext_db_if_needed()
    conn = _connect_db(settings.database_path)
    return conn


@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
    """Context manager for database connections."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


def get_schema_version() -> int:
    """Get the current schema version from the database."""
    with get_db() as conn:
        try:
            row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
            return row["version"] if row else 0
        except sqlite3.OperationalError:
            return 0


def set_schema_version(version: int) -> None:
    """Set the schema version in the database."""
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO schema_version (id, version, updated_at)
            VALUES (1, ?, datetime('now'))
        """, (version,))
        conn.commit()


def _init_db_schema(conn: sqlite3.Connection) -> None:
    """Run schema initialization on an open connection."""
    schema_path = Path(__file__).parent / "schema.sql"
    with open(schema_path, "r", encoding="utf-8") as f:
        schema_sql = f.read()
    conn.executescript(schema_sql)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_version (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            version INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.commit()


def init_db(db_path: str | Path | None = None) -> None:
    """Initialize the database from schema.sql.

    If db_path is None, uses settings.database_path (production behaviour).
    If db_path is provided, initializes that specific database after
    validating it is not a protected persistent database.
    """
    if db_path is not None:
        _assert_not_protected(db_path)
        path = _resolve_db_path(db_path)
        conn = _connect_db(path)
        try:
            _init_db_schema(conn)
        finally:
            conn.close()
    else:
        with get_db() as conn:
            _init_db_schema(conn)


def _get_schema_version_from_conn(conn: sqlite3.Connection) -> int:
    """Get schema version from an open connection."""
    try:
        row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
        return row["version"] if row else 0
    except sqlite3.OperationalError:
        return 0


def _set_schema_version_on_conn(conn: sqlite3.Connection, version: int) -> None:
    """Set schema version on an open connection."""
    conn.execute("""
        INSERT OR REPLACE INTO schema_version (id, version, updated_at)
        VALUES (1, ?, datetime('now'))
    """, (version,))
    conn.commit()


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Run migrations on an open connection."""
    current_version = _get_schema_version_from_conn(conn)

    if current_version >= SCHEMA_VERSION:
        return

    if current_version < 2:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS response_drafts (
                draft_id TEXT PRIMARY KEY,
                inquiry_id TEXT NOT NULL,
                draft_text TEXT NOT NULL,
                generated_by TEXT NOT NULL,
                model TEXT,
                status TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (inquiry_id) REFERENCES inquiries(inquiry_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_response_drafts_inquiry ON response_drafts(inquiry_id)
        """)

    if current_version < 3:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(inquiries)").fetchall()]
        if "text_fingerprint" not in cols:
            conn.execute("""
                ALTER TABLE inquiries ADD COLUMN text_fingerprint TEXT
            """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_inquiries_text_fingerprint ON inquiries(text_fingerprint)
        """)

    if current_version < 4:
        collisions = conn.execute("""
            SELECT source, source_message_id, COUNT(*) as c
            FROM inquiries
            WHERE source_message_id IS NOT NULL
            GROUP BY source, source_message_id
            HAVING c > 1
        """).fetchall()
        if collisions:
            raise RuntimeError(
                "Migration v3->v4 failed: " + str(len(collisions)) + " duplicate pairs found."
            )
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_inquiries_source_message
            ON inquiries(source, source_message_id)
            WHERE source_message_id IS NOT NULL AND source_message_id != ''
        """)

    _set_schema_version_on_conn(conn, SCHEMA_VERSION)


def migrate_db(db_path: str | Path | None = None) -> None:
    """Run migrations to bring database to current schema version.

    If db_path is None, uses settings.database_path (production behaviour).
    If db_path is provided, migrates that specific database after
    validating it is not a protected persistent database.
    """
    if db_path is not None:
        _assert_not_protected(db_path)
        path = _resolve_db_path(db_path)
        conn = _connect_db(path)
        try:
            _run_migrations(conn)
        finally:
            conn.close()
    else:
        with get_db() as conn:
            _run_migrations(conn)


def init_db_with_migrations(db_path: str | Path | None = None) -> None:
    """Initialize database and run migrations.

    If db_path is None, uses settings.database_path (production behaviour).
    If db_path is provided, initializes and migrates that specific database
    after validating it is not a protected persistent database.
    """
    init_db(db_path=db_path)
    migrate_db(db_path=db_path)


def reset_db(
    db_path: str | Path,
    *,
    capability: _DatabaseResetCapability | None = None,
) -> None:
    """Reset the database (for testing). Deletes and reinitializes.

    Requires an explicit db_path and a valid, single-use
    _DatabaseResetCapability issued by _issue_reset_capability() for
    that exact path within its authorized temporary directory.

    The capability is validated against the private issuance registry
    and consumed (removed from the registry) before any destructive
    file operation. A consumed capability cannot authorize a second
    reset.

    Rejects protected persistent databases and sidecars before any
    destructive action.
    """
    # 1. Validate capability type.
    if capability is None or not isinstance(capability, _DatabaseResetCapability):
        raise RuntimeError(
            "Destructive database reset requires a valid "
            "_DatabaseResetCapability issued by _issue_reset_capability()."
        )

    # 2. Validate registry membership and exact object identity.
    # Merely constructing a _DatabaseResetCapability does not authorize
    # a reset; the capability must be present in the private registry.
    registered = _ISSUED_RESET_CAPABILITIES.get(capability._token)
    if registered is None or registered is not capability:
        raise RuntimeError(
            "Destructive database reset failed: capability is not registered "
            "or has already been consumed."
        )

    # 3. Validate canonical target path and temporary-root containment
    # using the registered bindings (not the caller-supplied object).
    path = _resolve_db_path(db_path)
    if path != registered.target_path:
        raise RuntimeError(
            f"Capability is bound to {registered.target_path}, not {path}"
        )
    try:
        path.relative_to(registered.temp_root)
    except ValueError:
        raise RuntimeError(
            f"Path {path} is not within capability's temporary root "
            f"{registered.temp_root}"
        )

    # 4. Validate protected-path conditions for the target and every
    # sidecar that would be deleted.
    _assert_not_protected(path)
    for suffix in ("", ".plaintext.bak", ".encrypted", ".db-wal", ".db-shm", ".db-journal"):
        sidecar_path = Path(str(path) + suffix)
        _assert_not_protected(sidecar_path)

    # 5. Consume the capability before any destructive file operation.
    # Removal happens before unlink so a failed deletion cannot
    # accidentally restore authorization.
    del _ISSUED_RESET_CAPABILITIES[capability._token]

    # 6. Perform the authorized reset.
    # Remove every sidecar that could accompany the database file so a
    # freshly initialized database starts clean with no stale WAL,
    # SHM, or journal state.
    for suffix in ("", ".plaintext.bak", ".encrypted", ".db-wal", ".db-shm", ".db-journal"):
        sidecar_path = Path(str(path) + suffix)
        if sidecar_path.exists():
            sidecar_path.unlink()

    init_db_with_migrations(db_path=path)


def create_backup() -> dict:
    """Create a verified backup of the SQLite database.

    Uses SQLite's online backup API for safe concurrent access.
    Returns a dict with success status, path, integrity results, and any error.
    """
    import shutil
    from datetime import datetime

    settings = get_settings()
    db_path = Path(settings.database_path)

    if not db_path.exists():
        safe_log(_logger, logging.WARNING, "Database file does not exist: %s", db_path)
        return {"success": False, "error": "Database file does not exist"}

    # Determine backup directory
    try:
        backup_dir = Path(settings.backup_dir)
        backup_dir.mkdir(parents=True, exist_ok=True)
    except (OSError, FileExistsError) as e:
        return {"success": False, "error": f"Cannot create backup directory: {e}"}

    # Generate unique timestamped filename
    import uuid
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    unique_suffix = uuid.uuid4().hex[:8]
    backup_name = f"leadlift_backup_{timestamp}_{unique_suffix}.db"
    backup_path = backup_dir / backup_name

    # Ensure no overwrite (shouldn't happen with uuid, but be safe)
    counter = 1
    while backup_path.exists():
        backup_path = backup_dir / f"leadlift_backup_{timestamp}_{unique_suffix}_{counter}.db"
        counter += 1

    # Write to temp file first (unique name to avoid concurrent collisions)
    tmp_path = backup_dir / f".tmp_{backup_name}_{unique_suffix}"
    if tmp_path.exists():
        tmp_path.unlink()

    source_conn = None
    dest_conn = None
    try:
        source_conn = _connect_db(db_path)
        dest_conn = sqlcipher.connect(str(tmp_path))
        dest_conn.execute('PRAGMA key = "x\'{}\'"'.format(get_db_key()))
        source_conn.backup(dest_conn)
        dest_conn.commit()

        # Verify integrity
        integrity_result = dest_conn.execute("PRAGMA integrity_check").fetchone()
        integrity_ok = integrity_result[0] == "ok" if integrity_result else False

        fk_result = dest_conn.execute("PRAGMA foreign_key_check").fetchall()
        fk_ok = len(fk_result) == 0

        if not integrity_ok or not fk_ok:
            # Close connection before cleanup
            if dest_conn:
                dest_conn.close()
                dest_conn = None
            # Remove temp file
            if tmp_path.exists():
                tmp_path.unlink()
            errors = []
            if not integrity_ok:
                errors.append(f"integrity_check: {integrity_result[0] if integrity_result else 'unknown'}")
            if not fk_ok:
                errors.append(f"foreign_key_check: {len(fk_result)} violations")
            return {"success": False, "error": "; ".join(errors)}

        # Promote temp file to final name
        # Close dest connection before rename (Windows file locking)
        if dest_conn:
            dest_conn.close()
            dest_conn = None

        tmp_path.rename(backup_path)

        # Apply retention (failure here does not invalidate the backup)
        retention_warning = None
        try:
            _apply_backup_retention(backup_dir, settings.backup_retention)
        except Exception as e:
            retention_warning = str(e)
            safe_log(_logger, logging.WARNING, "Backup retention failed: %s", e)

        safe_log(_logger, logging.INFO, "Backup created: %s", backup_path.name)

        result = {
            "success": True,
            "path": str(backup_path),
            "filename": backup_path.name,
            "integrity": "ok",
            "foreign_keys": "ok",
        }
        if retention_warning:
            result["retention_warning"] = retention_warning
        return result

    except Exception as e:
        # Close connections before cleanup
        if dest_conn:
            try:
                dest_conn.close()
            except Exception:
                pass
            dest_conn = None
        # Clean up temp file
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except OSError:
            pass
        safe_log(_logger, logging.ERROR, "Backup failed: %s", e)
        return {"success": False, "error": str(e)}

    finally:
        if source_conn:
            source_conn.close()
        if dest_conn:
            dest_conn.close()


def _apply_backup_retention(backup_dir: Path, retention: int) -> None:
    """Remove old backups beyond retention limit."""
    if retention <= 0:
        return
    backups = sorted(
        backup_dir.glob("leadlift_backup_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old_backup in backups[retention:]:
        try:
            old_backup.unlink()
        except OSError:
            pass


def list_backups() -> list:
    """List available backup files."""
    settings = get_settings()
    backup_dir = Path(settings.backup_dir)
    if not backup_dir.exists():
        return []
    backups = sorted(
        backup_dir.glob("leadlift_backup_*.db"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    result = []
    for b in backups:
        result.append({
            "filename": b.name,
            "path": str(b),
            "size_bytes": b.stat().st_size,
        })

def check_database_health() -> dict:
    """Perform a read-only health check on the database.

    Returns a dict with:
        - status: "healthy", "unhealthy", or "unable_to_check"
        - integrity: "ok" or error message
        - foreign_keys: "ok" or list of violations
        - schema_version: int or None
        - error: error message if unable to check
        - details: additional diagnostic information
    """
    settings = get_settings()
    db_path = Path(settings.database_path)

    result = {
        "status": "unable_to_check",
        "integrity": None,
        "foreign_keys": None,
        "schema_version": None,
        "error": None,
        "details": {},
    }

    # Check if database file exists
    if not db_path.exists():
        result["error"] = "Database file does not exist"
        return result

    conn = None
    try:
        conn = _connect_db(db_path)

        # Check integrity
        try:
            integrity_row = conn.execute("PRAGMA integrity_check").fetchone()
            integrity_result = integrity_row[0] if integrity_row else "unknown"
            result["integrity"] = integrity_result
        except sqlite3.Error as e:
            result["error"] = f"Integrity check failed: {e}"
            return result

        # Check foreign keys
        try:
            fk_violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            result["foreign_keys"] = "ok" if len(fk_violations) == 0 else f"{len(fk_violations)} violations"
            result["details"]["fk_violations"] = [dict(r) for r in fk_violations] if fk_violations else []
        except sqlite3.Error as e:
            result["error"] = f"Foreign key check failed: {e}"
            return result

        # Check schema version
        try:
            row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
            result["schema_version"] = row["version"] if row else None
        except sqlite3.OperationalError:
            result["schema_version"] = None
            result["details"]["schema_version_error"] = "schema_version table not found"

        # Determine overall status
        if integrity_result == "ok" and len(fk_violations) == 0:
            result["status"] = "healthy"
        else:
            result["status"] = "unhealthy"

        return result

    except sqlite3.Error as e:
        result["error"] = f"Database connection failed: {e}"
        return result
    except Exception as e:
        result["error"] = f"Unexpected error: {e}"
        return result
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

