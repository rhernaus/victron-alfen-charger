"""Tests for logging utilities module."""

import logging
from unittest.mock import MagicMock, patch

from alfen_driver.logging_utils import (
    LogContext,
    StructuredFormatter,
    _sanitize_data,
    get_context,
    get_logger,
    log_charging_event,
    log_config_event,
    log_context,
    log_dbus_event,
    log_modbus_operation,
    log_performance,
    set_context,
    setup_root_logging,
)


class TestLogContext:
    """Tests for LogContext class."""

    def test_to_dict_filters_none_values(self) -> None:
        """Test that to_dict only includes non-None values."""
        context = LogContext(
            operation="test_op",
            component="test_comp",
            session_id=None,
            device_instance=42,
        )

        result = context.to_dict()

        assert result == {
            "operation": "test_op",
            "component": "test_comp",
            "device_instance": 42,
        }
        assert "session_id" not in result

    def test_to_dict_empty_context(self) -> None:
        """Test that empty context returns empty dict."""
        context = LogContext()
        assert context.to_dict() == {}

    def test_default_context(self) -> None:
        """Test default context creation."""
        context = LogContext()
        assert context.operation is None
        assert context.component is None
        assert context.session_id is None
        assert context.device_instance is None
        assert context.modbus_slave_id is None
        assert context.correlation_id is None

    def test_custom_context(self) -> None:
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


class TestStructuredFormatter:
    """Tests for StructuredFormatter class."""

    def test_format_basic_message(self) -> None:
        """Test formatting a basic log message."""
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Test message",
            args=(),
            exc_info=None,
        )

        result = formatter.format(record)

        assert "Test message" in result
        assert "INFO" in result
        assert "test" in result  # logger name

    def test_format_with_structured_data(self) -> None:
        """Test formatting with structured data."""
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="",
            lineno=0,
            msg="Test with data",
            args=(),
            exc_info=None,
        )
        record.structured_data = {"key": "value", "number": 42}

        result = formatter.format(record)

        assert "Test with data" in result
        assert "DEBUG" in result

    def test_format_with_exception(self) -> None:
        """Test formatting with exception info."""
        formatter = StructuredFormatter()
        try:
            raise ValueError("Test error")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="Error occurred",
            args=(),
            exc_info=exc_info,
        )

        result = formatter.format(record)

        assert "Error occurred" in result
        assert "ERROR" in result
        assert "ValueError" in result
        assert "Test error" in result

    def test_format_with_context(self) -> None:
        """Test formatting with context information."""
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="Context test",
            args=(),
            exc_info=None,
        )
        record.structured_data = {
            "component": "test_comp",
            "operation": "test_op",
            "session_id": "sess_123",
        }

        result = formatter.format(record)

        assert "Context test" in result
        assert "component=test_comp" in result
        assert "operation=test_op" in result
        assert "session_id=sess_123" in result

    def test_format_with_performance_metrics(self) -> None:
        """Test formatting with performance metrics."""
        formatter = StructuredFormatter()
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG,
            pathname="",
            lineno=0,
            msg="Performance test",
            args=(),
            exc_info=None,
        )
        record.structured_data = {"duration_ms": 123.45}

        result = formatter.format(record)

        assert "Performance test" in result
        assert "took 123.5ms" in result


class TestSanitizeData:
    """Tests for data sanitization."""

    def test_sanitize_sensitive_keys(self) -> None:
        """Test that sensitive keys are redacted."""
        data = {
            "normal": "value",
            "password": "secret123",
            "auth_token": "bearer xyz",
            "api_key": "12345",
        }

        result = _sanitize_data(data)

        assert result["normal"] == "value"
        assert result["password"] == "***REDACTED***"
        assert result["auth_token"] == "***REDACTED***"
        assert result["api_key"] == "***REDACTED***"

    def test_sanitize_nested_data(self) -> None:
        """Test sanitization of nested dictionaries."""
        data = {
            "config": {
                "host": "localhost",
                "password": "secret",
                "nested": {"secret_key": "xyz"},
            }
        }

        result = _sanitize_data(data)

        assert result["config"]["host"] == "localhost"
        assert result["config"]["password"] == "***REDACTED***"
        assert result["config"]["nested"]["secret_key"] == "***REDACTED***"


class TestLoggingHelpers:
    """Tests for domain-specific logging helpers."""

    @patch("alfen_driver.logging_utils._log_with_context")
    def test_log_modbus_operation(self, mock_log: MagicMock) -> None:
        """Test Modbus operation logging."""
        logger = logging.getLogger("test")

        log_modbus_operation(
            logger,
            operation="read",
            slave_id=1,
            address=100,
            success=True,
            duration_ms=15.5,
            extra_field="value",
        )

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][0] == logger
        assert call_args[0][1] == logging.DEBUG  # Success = DEBUG level
        assert "Modbus read" in call_args[0][2]
        assert call_args[0][3]["operation"] == "read"
        assert call_args[0][3]["slave_id"] == 1
        assert call_args[0][3]["duration_ms"] == 15.5

    @patch("alfen_driver.logging_utils._log_with_context")
    def test_log_charging_event(self, mock_log: MagicMock) -> None:
        """Test charging event logging."""
        logger = logging.getLogger("test")

        log_charging_event(
            logger,
            event="started",
            current=16.5,
            power=3800.0,
            status="CHARGING",
        )

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][1] == logging.INFO
        assert "current=16.50A" in call_args[0][2]
        assert "power=3800.0W" in call_args[0][2]

    @patch("alfen_driver.logging_utils._log_with_context")
    def test_log_config_event(self, mock_log: MagicMock) -> None:
        """Test configuration event logging."""
        logger = logging.getLogger("test")

        log_config_event(
            logger,
            event="loaded",
            source="/etc/config.yaml",
            version="1.2.3",
        )

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][1] == logging.INFO
        assert "Config: loaded" in call_args[0][2]
        assert call_args[0][3]["source"] == "/etc/config.yaml"
        assert call_args[0][3]["version"] == "1.2.3"

    @patch("alfen_driver.logging_utils._log_with_context")
    def test_log_dbus_event(self, mock_log: MagicMock) -> None:
        """Test D-Bus event logging."""
        logger = logging.getLogger("test")

        log_dbus_event(
            logger,
            event="value_changed",
            path="/Status",
            value=2,
        )

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][1] == logging.DEBUG
        assert "D-Bus: value_changed" in call_args[0][2]
        assert call_args[0][3]["path"] == "/Status"
        assert call_args[0][3]["value"] == 2

    @patch("alfen_driver.logging_utils._log_with_context")
    def test_log_performance(self, mock_log: MagicMock) -> None:
        """Test performance metrics logging."""
        logger = logging.getLogger("test")

        log_performance(
            logger,
            operation="data_poll",
            duration_ms=125.5,
            success=False,
        )

        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args[0][1] == logging.WARNING  # Failed = WARNING
        assert call_args[0][3]["duration_ms"] == 125.5
        assert call_args[0][3]["success"] is False


class TestContextManagement:
    """Tests for logging context management."""

    def test_set_and_get_context(self) -> None:
        """Test setting and getting logging context."""
        context = LogContext(operation="test", component="unit")

        set_context(context)
        retrieved = get_context()

        assert retrieved.operation == "test"
        assert retrieved.component == "unit"

    def test_log_context_manager(self) -> None:
        """Test log context manager."""
        original_context = LogContext(operation="original")
        set_context(original_context)

        with log_context(operation="new", session_id="123") as ctx:
            assert ctx.operation == "new"
            assert ctx.session_id == "123"
            current = get_context()
            assert current.operation == "new"

        # Context should be restored
        restored = get_context()
        assert restored.operation == "original"

    def test_context_manager_exception_handling(self) -> None:
        """Test context manager restores context even on exception."""
        original_context = LogContext(operation="original")
        set_context(original_context)

        try:
            with log_context(operation="temporary"):
                raise ValueError("Test error")
        except ValueError:
            pass

        # Context should still be restored
        restored = get_context()
        assert restored.operation == "original"


class TestSetupRootLogging:
    """Tests for root logging setup."""

    @patch("logging.handlers.RotatingFileHandler")
    def test_setup_with_file_handler(self, mock_handler: MagicMock) -> None:
        """Test setup with file handler when config provided."""
        config = MagicMock()
        config.logging.file = "/tmp/test.log"
        config.logging.level = "DEBUG"
        config.logging.max_file_size_mb = 5
        config.logging.backup_count = 3

        with patch("logging.getLogger") as mock_get_logger:
            mock_root = MagicMock()
            mock_get_logger.return_value = mock_root
            mock_root.handlers = []

            setup_root_logging(config)

            # Should create file handler with correct params
            mock_handler.assert_called_once_with(
                "/tmp/test.log",
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
            )

    def test_setup_without_config(self) -> None:
        """Test setup without config defaults to console only."""
        with patch("logging.getLogger") as mock_get_logger:
            mock_root = MagicMock()
            mock_get_logger.return_value = mock_root
            mock_root.handlers = []

            setup_root_logging(None)

            # Should set INFO level by default
            mock_root.setLevel.assert_called_with(logging.INFO)

    @patch("os.makedirs")
    @patch("os.path.exists")
    def test_creates_log_directory(
        self, mock_exists: MagicMock, mock_makedirs: MagicMock
    ) -> None:
        """Test that log directory is created if it doesn't exist."""
        config = MagicMock()
        config.logging.file = "/var/log/subdir/test.log"
        config.logging.max_file_size_mb = 10
        config.logging.backup_count = 5
        config.logging.level = "INFO"

        mock_exists.return_value = False

        with patch("logging.handlers.RotatingFileHandler"):
            with patch("logging.getLogger") as mock_get_logger:
                mock_root = MagicMock()
                mock_get_logger.return_value = mock_root
                mock_root.handlers = []

                setup_root_logging(config)

                mock_makedirs.assert_called_once_with("/var/log/subdir", exist_ok=True)

    def test_handles_file_handler_error(self) -> None:
        """Test graceful handling of file handler creation errors."""
        config = MagicMock()
        config.logging.file = "/invalid/path/test.log"
        config.logging.level = "INFO"

        with patch(
            "logging.handlers.RotatingFileHandler",
            side_effect=OSError("Permission denied"),
        ):
            with patch("logging.getLogger") as mock_get_logger:
                mock_root = MagicMock()
                mock_get_logger.return_value = mock_root
                mock_root.handlers = []

                # Should not raise, just log warning
                setup_root_logging(config)

                # Should still set up console handler and level
                mock_root.setLevel.assert_called_with(logging.INFO)


class TestGetLogger:
    """Tests for get_logger function."""

    def test_returns_standard_logger(self) -> None:
        """Test that get_logger returns a standard Python logger."""
        logger = get_logger("test.module")

        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.module"

    def test_config_parameter_ignored(self) -> None:
        """Test that config parameter is ignored for backward compatibility."""
        config = MagicMock()
        logger = get_logger("test.module", config)

        assert isinstance(logger, logging.Logger)
        assert logger.name == "test.module"
