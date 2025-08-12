"""Tests for structured logging utilities."""

import logging
import threading
from unittest.mock import MagicMock, patch

import pytest

from alfen_driver.config import LoggingConfig
from alfen_driver.logging_utils import (
    LogContext,
    StructuredFormatter,
    StructuredLogger,
    get_logger,
    log_context,
    setup_root_logging,
)


class TestLogContext:
    """Tests for LogContext dataclass."""

    def test_default_context(self):
        """Test default context creation."""
        context = LogContext()
        assert context.operation is None
        assert context.component is None
        assert context.session_id is None
        assert context.device_instance is None
        assert context.modbus_slave_id is None
        assert context.correlation_id is None

    def test_custom_context(self):
        """Test custom context creation."""
        context = LogContext(
            operation="test_operation",
            component="test_component",
            session_id="test_session",
            device_instance=42,
            modbus_slave_id=1,
            correlation_id="corr_123",
        )
        assert context.operation == "test_operation"
        assert context.component == "test_component"
        assert context.session_id == "test_session"
        assert context.device_instance == 42
        assert context.modbus_slave_id == 1
        assert context.correlation_id == "corr_123"

    def test_to_dict(self):
        """Test context to dictionary conversion."""
        context = LogContext(
            operation="test_op",
            component="test_comp",
            session_id=None,  # This should be excluded
            device_instance=1,
        )
        result = context.to_dict()
        expected = {
            "operation": "test_op",
            "component": "test_comp",
            "device_instance": 1,
        }
        assert result == expected
        assert "session_id" not in result


class TestStructuredLogger:
    """Tests for StructuredLogger class."""

    def test_logger_initialization(self):
        """Test logger initialization."""
        logger = StructuredLogger("test_logger")
        assert logger.name == "test_logger"
        assert logger.logger is not None

    def test_context_management(self):
        """Test logging context management."""
        logger = StructuredLogger("test_logger")

        # Test setting and getting context
        context = LogContext(operation="test_op", component="test_comp")
        logger.set_context(context)

        retrieved_context = logger.get_context()
        assert retrieved_context.operation == "test_op"
        assert retrieved_context.component == "test_comp"

    def test_data_sanitization(self):
        """Test sensitive data sanitization."""
        logger = StructuredLogger("test_logger")

        # Test data with sensitive keys
        sensitive_data = {
            "username": "testuser",
            "password": "secret123",
            "api_key": "sensitive_key",
            "token": "auth_token",
            "normal_field": "normal_value",
        }

        sanitized = logger._sanitize_data(sensitive_data)

        assert sanitized["username"] == "testuser"
        assert sanitized["password"] == "***REDACTED***"
        assert sanitized["api_key"] == "***REDACTED***"
        assert sanitized["token"] == "***REDACTED***"
        assert sanitized["normal_field"] == "normal_value"

    def test_nested_data_sanitization(self):
        """Test sanitization of nested data structures."""
        logger = StructuredLogger("test_logger")

        nested_data = {
            "config": {"database_password": "secret", "host": "localhost"},
            "auth": {"secret_key": "sensitive"},
        }

        sanitized = logger._sanitize_data(nested_data)

        assert sanitized["config"]["database_password"] == "***REDACTED***"
        assert sanitized["config"]["host"] == "localhost"
        assert sanitized["auth"]["secret_key"] == "***REDACTED***"

    @patch("alfen_driver.logging_utils.StructuredLogger._log_with_context")
    def test_log_level_methods(self, mock_log):
        """Test convenience methods for different log levels."""
        logger = StructuredLogger("test_logger")

        logger.debug("debug message", extra_field="value")
        mock_log.assert_called_with(
            logging.DEBUG, "debug message", {"extra_field": "value"}
        )

        logger.info("info message", extra_field="value")
        mock_log.assert_called_with(
            logging.INFO, "info message", {"extra_field": "value"}
        )

        logger.warning("warning message", extra_field="value")
        mock_log.assert_called_with(
            logging.WARNING, "warning message", {"extra_field": "value"}
        )

        logger.error("error message", extra_field="value")
        mock_log.assert_called_with(
            logging.ERROR, "error message", {"extra_field": "value"}
        )

        logger.critical("critical message", extra_field="value")
        mock_log.assert_called_with(
            logging.CRITICAL, "critical message", {"extra_field": "value"}
        )

    @patch("alfen_driver.logging_utils.StructuredLogger._log_with_context")
    def test_domain_specific_methods(self, mock_log):
        """Test domain-specific logging methods."""
        logger = StructuredLogger("test_logger")

        # Test Modbus operation logging
        logger.log_modbus_operation("read_registers", 1, 123, True, 50.0, extra="value")
        expected_data = {
            "operation_type": "modbus",
            "operation": "read_registers",
            "slave_id": 1,
            "address": 123,
            "success": True,
            "duration_ms": 50.0,
            "extra": "value",
        }
        mock_log.assert_called_with(
            logging.DEBUG, "Modbus read_registers", expected_data
        )

        # Test charging event logging
        logger.log_charging_event(
            "current_set", current=12.0, power=2760.0, status="charging"
        )
        expected_data = {
            "operation_type": "charging",
            "event": "current_set",
            "current": 12.0,
            "power": 2760.0,
            "status": "charging",
        }
        mock_log.assert_called_with(
            logging.INFO, "Charging: current_set", expected_data
        )

    @patch("alfen_driver.logging_utils.StructuredLogger._log_with_context")
    def test_error_recovery_logging(self, mock_log):
        """Test error recovery logging."""
        logger = StructuredLogger("test_logger")

        # Test successful recovery
        logger.log_error_recovery("modbus_read", 2, 3, True)
        expected_data = {
            "operation_type": "error_recovery",
            "operation": "modbus_read",
            "attempt": 2,
            "max_attempts": 3,
            "success": True,
            "error": None,
        }
        mock_log.assert_called_with(
            logging.INFO,
            "Recovery: modbus_read succeeded on attempt 2/3",
            expected_data,
        )

        # Test failed recovery
        logger.log_error_recovery("modbus_read", 3, 3, False, "Connection timeout")
        expected_data = {
            "operation_type": "error_recovery",
            "operation": "modbus_read",
            "attempt": 3,
            "max_attempts": 3,
            "success": False,
            "error": "Connection timeout",
        }
        mock_log.assert_called_with(
            logging.ERROR, "Recovery: modbus_read failed on attempt 3/3", expected_data
        )


class TestStructuredFormatter:
    """Tests for StructuredFormatter class."""

    def test_format_basic_record(self):
        """Test formatting of basic log record."""
        formatter = StructuredFormatter()

        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=123,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        formatted = formatter.format(record)

        # Should contain basic log info
        assert "INFO" in formatted
        assert "test_logger" in formatted
        assert "Test message" in formatted

    def test_format_with_structured_data(self):
        """Test formatting with structured data."""
        formatter = StructuredFormatter()

        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=123,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        # Add structured data
        record.structured_data = {
            "component": "test_component",
            "operation": "test_operation",
            "duration_ms": 42.5,
        }

        formatted = formatter.format(record)

        # Should contain context information
        assert "component=test_component" in formatted
        assert "operation=test_operation" in formatted
        assert "took 42.5ms" in formatted

    def test_format_with_exception(self):
        """Test formatting with exception information."""
        formatter = StructuredFormatter()

        try:
            raise ValueError("Test exception")
        except ValueError:
            record = logging.LogRecord(
                name="test_logger",
                level=logging.ERROR,
                pathname="test.py",
                lineno=123,
                msg="Test message",
                args=(),
                exc_info=True,  # This will capture the current exception
            )

        formatted = formatter.format(record)

        # Should contain exception information
        assert "ValueError" in formatted
        assert "Test exception" in formatted


class TestLogContextManager:
    """Tests for log context manager."""

    def test_context_manager(self):
        """Test log context manager functionality."""
        with log_context(operation="test_op", component="test_comp") as context:
            assert context.operation == "test_op"
            assert context.component == "test_comp"

    def test_context_cleanup(self):
        """Test that context is cleaned up after manager exits."""
        original_context = getattr(threading.current_thread(), "_log_context", None)

        with log_context(operation="test_op"):
            # Context should be set during the block
            current_context = getattr(threading.current_thread(), "_log_context", None)
            assert current_context is not None
            assert current_context.operation == "test_op"

        # Context should be restored after the block
        restored_context = getattr(threading.current_thread(), "_log_context", None)
        assert restored_context == original_context


class TestLoggingIntegration:
    """Integration tests for the structured logging system."""

    def test_get_logger_function(self):
        """Test the get_logger convenience function."""
        logger = get_logger("test.module")
        assert isinstance(logger, StructuredLogger)
        assert logger.name == "test.module"

    def test_get_logger_with_config(self):
        """Test get_logger with configuration."""
        config = MagicMock()
        config.logging = LoggingConfig(level="DEBUG", file="/tmp/test.log")

        logger = get_logger("test.module", config)
        assert isinstance(logger, StructuredLogger)
        assert logger.config == config

    def test_setup_root_logging(self):
        """Test root logging setup."""
        config = MagicMock()
        config.logging = LoggingConfig(level="WARNING")

        # This should not raise any exceptions
        setup_root_logging(config)

        # Check that root logger level was set
        root_logger = logging.getLogger()
        assert root_logger.level == logging.WARNING


class TestLoggingConfiguration:
    """Tests for logging configuration validation."""

    def test_valid_logging_config(self):
        """Test valid logging configuration."""
        config = LoggingConfig(
            level="INFO",
            file="/tmp/test.log",
            format="structured",
            max_file_size_mb=20,
            backup_count=10,
            console_output=True,
            json_format=False,
        )

        # Should not raise any exceptions
        assert config.level == "INFO"
        assert config.max_file_size_mb == 20
        assert config.backup_count == 10

    def test_invalid_log_level(self):
        """Test invalid log level validation."""
        from alfen_driver.exceptions import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            LoggingConfig(level="INVALID_LEVEL")

        assert "logging.level" in str(exc_info.value)
        assert "must be one of" in str(exc_info.value)

    def test_invalid_file_size(self):
        """Test invalid file size validation."""
        from alfen_driver.exceptions import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            LoggingConfig(max_file_size_mb=-5)

        assert "logging.max_file_size_mb" in str(exc_info.value)
        assert "must be positive" in str(exc_info.value)

    def test_invalid_backup_count(self):
        """Test invalid backup count validation."""
        from alfen_driver.exceptions import ValidationError

        with pytest.raises(ValidationError) as exc_info:
            LoggingConfig(backup_count=-1)

        assert "logging.backup_count" in str(exc_info.value)
        assert "must be non-negative" in str(exc_info.value)
