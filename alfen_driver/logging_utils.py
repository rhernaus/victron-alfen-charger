"""Structured logging utilities for the Alfen driver.

This module provides:
- Structured logging with consistent format
- Context management for log correlation
- Performance metrics logging
- Security-aware logging (sanitizes sensitive data)
- Proper log levels and categorization
"""

import dataclasses
import json
import logging
import logging.handlers
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Optional


@dataclasses.dataclass
class LogContext:
    """Context information for structured logging."""

    operation: Optional[str] = None
    component: Optional[str] = None
    session_id: Optional[str] = None
    device_instance: Optional[int] = None
    modbus_slave_id: Optional[int] = None
    correlation_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert context to dictionary for logging."""
        return {k: v for k, v in dataclasses.asdict(self).items() if v is not None}


class StructuredLogger:
    """Enhanced logger with structured logging capabilities."""

    def __init__(self, name: str, config: Optional[Any] = None):
        self.name = name
        self.logger = logging.getLogger(name)
        self._context_store = threading.local()
        self.config = config

        # Don't add handlers if root logger is already configured
        # The setup_root_logging() function handles this globally
        root_logger = logging.getLogger()
        if not root_logger.handlers and not self.logger.handlers:
            self._setup_logging()

    def _setup_logging(self) -> None:
        """Set up logging configuration."""
        # Create formatter for structured logging
        formatter = StructuredFormatter()

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)

        # File handler if configured
        if self.config and hasattr(self.config, "logging"):
            try:
                # Use rotating file handler to prevent log files from growing too large
                max_bytes = (
                    getattr(self.config.logging, "max_file_size_mb", 10) * 1024 * 1024
                )
                backup_count = getattr(self.config.logging, "backup_count", 5)
                file_handler = logging.handlers.RotatingFileHandler(
                    self.config.logging.file,
                    maxBytes=max_bytes,
                    backupCount=backup_count,
                )
                file_handler.setFormatter(formatter)
                self.logger.addHandler(file_handler)
            except (OSError, AttributeError) as e:
                # Fallback to console only if file logging fails
                console_handler.stream.write(
                    f"WARNING: Failed to set up file logging: {e}\n"
                )

        # Set level
        level = "INFO"
        if self.config and hasattr(self.config, "logging"):
            level = getattr(self.config.logging, "level", "INFO")

        self.logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    def set_context(self, context: LogContext) -> None:
        """Set logging context for current thread."""
        self._context_store.context = context

    def get_context(self) -> LogContext:
        """Get current logging context."""
        return getattr(self._context_store, "context", LogContext())

    def _log_with_context(
        self,
        level: int,
        message: str,
        extra_data: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> None:
        """Log message with structured context."""
        context = self.get_context()

        # Build structured log entry
        log_data = {
            "message": message,
            "timestamp": time.time(),
            "level": logging.getLevelName(level),
            "logger": self.name,
            **context.to_dict(),
        }

        # Add extra data
        if extra_data:
            # Sanitize sensitive data
            sanitized_data = self._sanitize_data(extra_data)
            log_data.update(sanitized_data)

        # Log the structured data
        self.logger._log(
            level, message, (), extra={"structured_data": log_data}, **kwargs
        )

    def _sanitize_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Sanitize sensitive information from log data."""
        sensitive_keys = {"password", "token", "key", "secret", "auth", "credential"}

        sanitized = {}
        for key, value in data.items():
            key_lower = key.lower()
            if any(sensitive in key_lower for sensitive in sensitive_keys):
                sanitized[key] = "***REDACTED***"
            elif isinstance(value, dict):
                sanitized[key] = self._sanitize_data(value)  # type: ignore
            else:
                sanitized[key] = value

        return sanitized

    # Convenience methods for different log levels
    def debug(self, message: str, **kwargs: Any) -> None:
        """Log debug message."""
        self._log_with_context(logging.DEBUG, message, kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log info message."""
        self._log_with_context(logging.INFO, message, kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log warning message."""
        self._log_with_context(logging.WARNING, message, kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """Log error message."""
        self._log_with_context(logging.ERROR, message, kwargs)

    def critical(self, message: str, **kwargs: Any) -> None:
        """Log critical message."""
        self._log_with_context(logging.CRITICAL, message, kwargs)

    def exception(self, message: str, **kwargs: Any) -> None:
        """Log exception with traceback."""
        self._log_with_context(logging.ERROR, message, kwargs, exc_info=True)

    # Domain-specific logging methods
    def log_modbus_operation(
        self,
        operation: str,
        slave_id: int,
        address: int,
        success: bool,
        duration_ms: float,
        **kwargs: Any,
    ) -> None:
        """Log Modbus operation with performance metrics."""
        level = logging.DEBUG if success else logging.WARNING
        self._log_with_context(
            level,
            f"Modbus {operation}",
            {
                "operation_type": "modbus",
                "operation": operation,
                "slave_id": slave_id,
                "address": address,
                "success": success,
                "duration_ms": duration_ms,
                **kwargs,
            },
        )

    def log_charging_event(
        self,
        event: str,
        current: Optional[float] = None,
        power: Optional[float] = None,
        status: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        """Log charging-related events."""
        # Build readable message with current value if provided
        message_parts = [f"Charging: {event}"]
        if current is not None:
            message_parts.append(f"current={current:.2f}A")
        if power is not None:
            message_parts.append(f"power={power:.1f}W")
        message = " ".join(message_parts)
        
        self._log_with_context(
            logging.INFO,
            message,
            {
                "operation_type": "charging",
                "event": event,
                "current": current,
                "power": power,
                "status": status,
                **kwargs,
            },
        )

    def log_config_event(
        self, event: str, source: Optional[str] = None, **kwargs: Any
    ) -> None:
        """Log configuration events."""
        self._log_with_context(
            logging.INFO,
            f"Config: {event}",
            {
                "operation_type": "configuration",
                "event": event,
                "source": source,
                **kwargs,
            },
        )

    def log_dbus_event(
        self,
        event: str,
        path: Optional[str] = None,
        value: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        """Log D-Bus related events."""
        self._log_with_context(
            logging.DEBUG,
            f"D-Bus: {event}",
            {
                "operation_type": "dbus",
                "event": event,
                "path": path,
                "value": value,
                **kwargs,
            },
        )

    def log_performance(
        self, operation: str, duration_ms: float, success: bool = True, **kwargs: Any
    ) -> None:
        """Log performance metrics."""
        level = logging.DEBUG if success else logging.WARNING
        self._log_with_context(
            level,
            f"Performance: {operation}",
            {
                "operation_type": "performance",
                "operation": operation,
                "duration_ms": duration_ms,
                "success": success,
                **kwargs,
            },
        )

    def log_error_recovery(
        self,
        operation: str,
        attempt: int,
        max_attempts: int,
        success: bool,
        error: Optional[str] = None,
        **kwargs,
    ) -> None:
        """Log error recovery attempts."""
        if success:
            level = logging.INFO if attempt > 1 else logging.DEBUG
            message = (
                f"Recovery: {operation} succeeded on attempt {attempt}/{max_attempts}"
            )
        else:
            level = logging.ERROR if attempt >= max_attempts else logging.WARNING
            message = (
                f"Recovery: {operation} failed on attempt {attempt}/{max_attempts}"
            )

        self._log_with_context(
            level,
            message,
            {
                "operation_type": "error_recovery",
                "operation": operation,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "success": success,
                "error": error,
                **kwargs,
            },
        )


class StructuredFormatter(logging.Formatter):
    """Custom formatter for structured logging."""

    def __init__(self):
        # Set consistent datetime format
        super().__init__(datefmt='%Y-%m-%d %H:%M:%S')

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with structured data."""
        # Basic formatting with consistent timestamp
        formatted_time = self.formatTime(record)

        # Get structured data if available
        structured_data = getattr(record, "structured_data", {})

        # Create base log entry
        log_entry = {
            "timestamp": formatted_time,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Add structured data
        if structured_data:
            log_entry.update(structured_data)

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Format based on environment
        if self._should_use_json():
            return json.dumps(log_entry, default=str)
        else:
            return self._format_human_readable(log_entry, record)

    def _should_use_json(self) -> bool:
        """Determine if JSON format should be used."""
        # Use JSON in production/containerized environments
        return False  # For now, use human-readable format

    def _format_human_readable(
        self, log_entry: Dict[str, Any], record: logging.LogRecord
    ) -> str:
        """Format log entry in human-readable format."""
        # Base message
        base = f"{log_entry['timestamp']} [{log_entry['level']:8}] {log_entry['logger']}: {log_entry['message']}"

        # Add context if available
        context_parts = []
        for key in ["component", "operation", "session_id", "correlation_id"]:
            if key in log_entry:
                context_parts.append(f"{key}={log_entry[key]}")

        if context_parts:
            base += f" [{', '.join(context_parts)}]"

        # Add performance metrics if available
        if "duration_ms" in log_entry:
            base += f" (took {log_entry['duration_ms']:.1f}ms)"

        # Add exception if present
        if "exception" in log_entry:
            base += f"\n{log_entry['exception']}"

        return base


@contextmanager
def log_context(**context_kwargs):
    """Context manager for setting logging context."""
    # Get current logger (this is a simplified approach)
    # In practice, you'd want to manage this more carefully
    context = LogContext(**context_kwargs)

    # Store context in thread-local storage for the duration
    current_thread = threading.current_thread()
    old_context = getattr(current_thread, "_log_context", None)
    current_thread._log_context = context

    try:
        yield context
    finally:
        current_thread._log_context = old_context


def get_logger(name: str, config: Optional[Any] = None) -> StructuredLogger:
    """Get or create a structured logger."""
    return StructuredLogger(name, config)


def setup_root_logging(config: Optional[Any] = None) -> None:
    """Set up root logging configuration."""
    # Configure root logger to use structured formatting
    root_logger = logging.getLogger()

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add structured formatter
    formatter = StructuredFormatter()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Set level
    level = "INFO"
    if config and hasattr(config, "logging"):
        level = getattr(config.logging, "level", "INFO")

    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
