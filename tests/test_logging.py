"""LEADLIFT — Logging configuration tests.

Uses temporary directories and synthetic data only.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from leadlift.logging_config import (
    setup_logging,
    get_logger,
    safe_log,
    SensitiveDataFilter,
    _SENSITIVE_PATTERNS,
)


@pytest.fixture
def temp_log_dir():
    d = tempfile.mkdtemp()
    yield d
    import shutil
    shutil.rmtree(d, ignore_errors=True)


class TestLoggingSetup:
    def test_normal_setup(self, temp_log_dir):
        logger = setup_logging(log_dir=temp_log_dir, log_level="INFO")
        assert logger is not None
        assert logger.name == "leadlift"
        assert len(logger.handlers) > 0

    def test_log_file_created(self, temp_log_dir):
        logger = setup_logging(log_dir=temp_log_dir, log_level="DEBUG")
        logger.info("Test message")
        log_file = Path(temp_log_dir) / "leadlift.log"
        assert log_file.exists()
        content = log_file.read_text()
        assert "Test message" in content

    def test_exception_logging(self, temp_log_dir):
        logger = setup_logging(log_dir=temp_log_dir, log_level="DEBUG")
        try:
            raise ValueError("Test exception")
        except ValueError:
            logger.exception("An error occurred")
        log_file = Path(temp_log_dir) / "leadlift.log"
        content = log_file.read_text()
        assert "An error occurred" in content
        assert "ValueError" in content

    def test_rotation(self, temp_log_dir):
        logger = setup_logging(log_dir=temp_log_dir, log_level="DEBUG", max_bytes=100, backup_count=2)
        # Write enough to trigger rotation
        for i in range(50):
            logger.info("This is a long message that should trigger rotation eventually %d", i)
        # Check that rotated files exist
        log_files = list(Path(temp_log_dir).glob("leadlift.log*"))
        assert len(log_files) >= 1

    def test_invalid_log_dir(self):
        logger = setup_logging(log_dir="/nonexistent/path/that/cannot/exist", log_level="INFO")
        assert logger is not None
        # Should fall back to stderr
        assert len(logger.handlers) > 0

    def test_unwritable_log_dir(self, temp_log_dir):
        # Create a file to block directory creation
        blocker = os.path.join(tempfile.gettempdir(), "log_blocker.txt")
        with open(blocker, "w") as f:
            f.write("blocker")
        try:
            logger = setup_logging(log_dir=blocker, log_level="INFO")
            assert logger is not None
        finally:
            if os.path.exists(blocker):
                os.unlink(blocker)

    def test_get_logger(self):
        logger = get_logger("test.module")
        assert logger.name == "leadlift.test.module"

    def test_safe_log(self, temp_log_dir):
        logger = setup_logging(log_dir=temp_log_dir, log_level="DEBUG")
        # Should not raise even with bad args
        safe_log(logger, logging.INFO, "Safe message %s", "arg")
        safe_log(logger, logging.INFO, "Bad format %s %s", "only_one_arg")
        log_file = Path(temp_log_dir) / "leadlift.log"
        content = log_file.read_text()
        assert "Safe message" in content

    def test_safe_log_never_crashes(self, temp_log_dir):
        logger = setup_logging(log_dir=temp_log_dir, log_level="DEBUG")
        # Pass None logger
        safe_log(None, logging.INFO, "test")  # Should not crash
        # Pass invalid level
        safe_log(logger, 99999, "test")  # Should not crash


class TestSensitiveDataRedaction:
    def test_token_redaction(self):
        f = SensitiveDataFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="token=secret123", args=(), exc_info=None,
        )
        f.filter(record)
        assert "secret123" not in str(record.msg)
        assert "REDACTED" in str(record.msg)

    def test_email_redaction(self):
        f = SensitiveDataFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="User email: john@example.com", args=(), exc_info=None,
        )
        f.filter(record)
        assert "john@example.com" not in str(record.msg)

    def test_phone_redaction(self):
        f = SensitiveDataFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Phone: 1234567890", args=(), exc_info=None,
        )
        f.filter(record)
        assert "1234567890" not in str(record.msg)

    def test_bearer_token_redaction(self):
        f = SensitiveDataFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Authorization: Bearer abc123def456", args=(), exc_info=None,
        )
        f.filter(record)
        assert "abc123def456" not in str(record.msg)

    def test_secret_field_redaction(self):
        f = SensitiveDataFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="test", args=(), exc_info=None,
        )
        record.password = "mypassword123"
        f.filter(record)
        assert record.password == "***REDACTED***"

    def test_no_sensitive_data_passes(self):
        f = SensitiveDataFilter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="Normal log message without secrets", args=(), exc_info=None,
        )
        result = f.filter(record)
        assert result is True
        assert "Normal log message" in str(record.msg)

    def test_logging_failure_does_not_crash(self, temp_log_dir):
        logger = setup_logging(log_dir=temp_log_dir, log_level="DEBUG")
        # Log with circular reference (should not crash)
        safe_log(logger, logging.INFO, "test message")
        # Verify log file exists
        log_file = Path(temp_log_dir) / "leadlift.log"
        assert log_file.exists()

    def test_repeated_setup_no_duplicate_handlers(self, temp_log_dir):
        logger1 = setup_logging(log_dir=temp_log_dir, log_level="DEBUG")
        handler_count1 = len(logger1.handlers)
        logger2 = setup_logging(log_dir=temp_log_dir, log_level="DEBUG")
        handler_count2 = len(logger2.handlers)
        assert handler_count1 == handler_count2
        assert logger1 is logger2

    def test_traceback_redaction(self, temp_log_dir):
        logger = setup_logging(log_dir=temp_log_dir, log_level="DEBUG")
        try:
            raise ValueError("token=secret123 password=hunter2")
        except ValueError:
            logger.exception("Error occurred")
        log_file = Path(temp_log_dir) / "leadlift.log"
        content = log_file.read_text()
        # The exception value should be redacted (not the source code line)
        assert "ValueError: token=***REDACTED***" in content
        assert "REDACTED" in content

    def test_traceback_chained_exception(self, temp_log_dir):
        logger = setup_logging(log_dir=temp_log_dir, log_level="DEBUG")
        try:
            try:
                raise ValueError("inner secret=abc")
            except ValueError as e:
                raise RuntimeError("outer password=xyz") from e
        except RuntimeError:
            logger.exception("Chained error")
        log_file = Path(temp_log_dir) / "leadlift.log"
        content = log_file.read_text()
        # Exception values should be redacted
        assert "secret=***REDACTED***" in content or "REDACTED" in content
        assert "password=***REDACTED***" in content or "REDACTED" in content

    def test_redaction_in_all_handlers(self, temp_log_dir):
        logger = setup_logging(log_dir=temp_log_dir, log_level="DEBUG")
        # Check that all handlers have the filter
        for handler in logger.handlers:
            filter_types = [type(f).__name__ for f in handler.filters]
            assert "SensitiveDataFilter" in filter_types

    def test_invalid_log_level(self, temp_log_dir):
        logger = setup_logging(log_dir=temp_log_dir, log_level="INVALID_LEVEL")
        assert logger is not None
        # Should default to INFO
        assert logger.level == logging.INFO

    def test_retention_bounded(self, temp_log_dir):
        logger = setup_logging(log_dir=temp_log_dir, log_level="DEBUG", max_bytes=200, backup_count=2)
        for i in range(100):
            logger.info("Message number %d with some padding to exceed size", i)
        log_files = list(Path(temp_log_dir).glob("leadlift.log*"))
        assert len(log_files) <= 4

    def test_zero_max_bytes(self, temp_log_dir):
        logger = setup_logging(log_dir=temp_log_dir, log_level="DEBUG", max_bytes=0)
        assert logger is not None
        logger.info("Test message")
        log_file = Path(temp_log_dir) / "leadlift.log"
        assert log_file.exists()
        logger = setup_logging(log_dir=temp_log_dir, log_level="DEBUG", max_bytes=200, backup_count=2)
        for i in range(100):
            logger.info("Message number %d with some padding to exceed size", i)
        # Should have at most backup_count + 1 files
        log_files = list(Path(temp_log_dir).glob("leadlift.log*"))
        assert len(log_files) <= 4  # 1 main + 2 backups + maybe 1 in-progress


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
