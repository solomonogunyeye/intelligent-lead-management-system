"""LEADLIFT — Local logging configuration.

Provides bounded, rotating file-based logging with sensitive-data redaction.
No external services or paid dependencies.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import re
from pathlib import Path
from typing import Optional

# Sensitive patterns to redact from log messages
_SENSITIVE_PATTERNS = [
    # OAuth tokens, API keys, secrets
    (re.compile(r'(token|secret|password|api_key|apikey|credential|auth)[\s:=]+[^\s,}]+', re.IGNORECASE), r'\1=***REDACTED***'),
    # Email addresses (partial redaction)
    (re.compile(r'([a-zA-Z0-9._%+-]+)@([a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'), r'***@***.***'),
    # Phone numbers (sequences of 7+ digits)
    (re.compile(r'\b\d{7,}\b'), '***PHONE_REDACTED***'),
    # Bearer tokens
    (re.compile(r'Bearer\s+\S+', re.IGNORECASE), 'Bearer ***REDACTED***'),
]

# Fields that should never be logged in full
_SENSITIVE_FIELD_NAMES = {
    "password", "secret", "token", "api_key", "apikey", "credential",
    "access_token", "refresh_token", "app_secret", "client_secret",
    "oauth_token", "auth_token", "webhook_secret",
}


class SensitiveDataFilter(logging.Filter):
    """Filter that redacts sensitive data from log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Redact from message
        if isinstance(record.msg, str):
            for pattern, replacement in _SENSITIVE_PATTERNS:
                record.msg = pattern.sub(replacement, record.msg)

        # Redact from args
        if record.args:
            new_args = []
            for arg in (record.args if isinstance(record.args, tuple) else (record.args,)):
                if isinstance(arg, str):
                    for pattern, replacement in _SENSITIVE_PATTERNS:
                        arg = pattern.sub(replacement, arg)
                new_args.append(arg)
            record.args = tuple(new_args) if isinstance(record.args, tuple) else new_args

        # Redact from exception traceback text
        if record.exc_text:
            for pattern, replacement in _SENSITIVE_PATTERNS:
                record.exc_text = pattern.sub(replacement, record.exc_text)

        # Redact from exc_info (exception value)
        if record.exc_info and len(record.exc_info) >= 2 and record.exc_info[1] is not None:
            exc_val = record.exc_info[1]
            if hasattr(exc_val, 'args') and exc_val.args:
                new_args = []
                for arg in exc_val.args:
                    if isinstance(arg, str):
                        for pattern, replacement in _SENSITIVE_PATTERNS:
                            arg = pattern.sub(replacement, arg)
                    new_args.append(arg)
                try:
                    exc_val.args = tuple(new_args)
                except (AttributeError, TypeError):
                    pass

        # Redact extra fields
        for attr in list(record.__dict__.keys()):
            if attr.lower() in _SENSITIVE_FIELD_NAMES:
                setattr(record, attr, "***REDACTED***")

        return True


def _get_log_dir() -> Path:
    """Get the log directory from settings or default."""
    try:
        from leadlift.config import get_settings
        settings = get_settings()
        log_dir = getattr(settings, "log_dir", None)
        if log_dir:
            return Path(log_dir)
    except Exception:
        pass
    return Path("leadlift/data/logs")


def setup_logging(
    log_dir: Optional[str] = None,
    log_level: str = "INFO",
    max_bytes: int = 5 * 1024 * 1024,  # 5MB per file
    backup_count: int = 5,
) -> logging.Logger:
    """Configure LEADLIFT logging with rotation and sensitive-data redaction.

    Args:
        log_dir: Directory for log files. Defaults to settings or leadlift/data/logs.
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        max_bytes: Maximum size per log file before rotation.
        backup_count: Number of rotated log files to retain.

    Returns:
        The root LEADLIFT logger.
    """
    logger = logging.getLogger("leadlift")

    # Determine log directory
    if log_dir:
        log_path = Path(log_dir)
    else:
        log_path = _get_log_dir()

    # Identify managed rotating file handlers owned by this setup.
    managed_file_handlers = [
        h for h in logger.handlers
        if isinstance(h, logging.handlers.RotatingFileHandler)
    ]

    # Idempotent same-destination handling.
    if managed_file_handlers and Path(managed_file_handlers[0].baseFilename).parent == log_path:
        return logger

    # Remove only managed rotating handlers; preserve unrelated handlers.
    for handler in managed_file_handlers:
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    try:
        log_path.mkdir(parents=True, exist_ok=True)
    except (OSError, PermissionError):
        if not any(
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.handlers.RotatingFileHandler)
            for h in logger.handlers
        ):
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            ))
            handler.addFilter(SensitiveDataFilter())
            logger.addHandler(handler)
        logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        return logger

    # Rotating file handler for requested destination.
    log_file = log_path / "leadlift.log"
    try:
        file_handler = logging.handlers.RotatingFileHandler(
            str(log_file),
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        file_handler.addFilter(SensitiveDataFilter())
        logger.addHandler(file_handler)
    except (OSError, PermissionError):
        if not any(
            isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.handlers.RotatingFileHandler)
            for h in logger.handlers
        ):
            handler = logging.StreamHandler()
            handler.setFormatter(logging.Formatter(
                "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
            ))
            handler.addFilter(SensitiveDataFilter())
            logger.addHandler(handler)
        logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
        return logger

    # Ensure one stderr handler for warnings/errors.
    if not any(
        isinstance(h, logging.StreamHandler)
        and not isinstance(h, logging.handlers.RotatingFileHandler)
        for h in logger.handlers
    ):
        stderr_handler = logging.StreamHandler()
        stderr_handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        ))
        stderr_handler.addFilter(SensitiveDataFilter())
        stderr_handler.setLevel(logging.WARNING)
        logger.addHandler(stderr_handler)

    logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))
    return logger

def get_logger(name: str) -> logging.Logger:
    """Get a named logger under the leadlift namespace.

    Args:
        name: Logger name (e.g., "adapters.email", "db", "ui").

    Returns:
        A logger instance.
    """
    return logging.getLogger(f"leadlift.{name}")


def safe_log(logger: logging.Logger, level: int, message: str, *args, **kwargs) -> None:
    """Log a message safely — never raises, never exposes secrets.

    Args:
        logger: Logger instance.
        level: Logging level.
        message: Log message (should not contain sensitive data).
        *args: Positional args for message formatting.
        **kwargs: Keyword args.
    """
    try:
        logger.log(level, message, *args, **kwargs)
    except Exception:
        pass  # Logging must never crash the application
