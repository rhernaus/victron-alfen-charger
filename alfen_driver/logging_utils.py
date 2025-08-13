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
import os
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional, cast


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


class StructuredFormatter(logging.Formatter):
    """Custom formatter for structured logging."""

    def __init__(self) -> None:
        # Set consistent datetime format
        super().__init__(datefmt="%Y-%m-%d %H:%M:%S")

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
        base = (
            f"{log_entry['timestamp']} [{log_entry['level']:8}] "
            f"{log_entry['logger']}: {log_entry['message']}"
        )

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


# Thread-local storage for logging context
_context_store = threading.local()


def set_context(context: LogContext) -> None:
    """Set logging context for current thread."""
    _context_store.context = context


def get_context() -> LogContext:
    """Get current logging context."""
    return getattr(_context_store, "context", LogContext())


def _sanitize_data(data: Dict[str, Any]) -> Dict[str, Any]:
    """Sanitize sensitive information from log data."""
    sensitive_keys = {"password", "token", "key", "secret", "auth", "credential"}

    sanitized: Dict[str, Any] = {}
    for key, value in data.items():
        key_lower = key.lower()
        if any(sensitive in key_lower for sensitive in sensitive_keys):
            sanitized[key] = "***REDACTED***"
        elif isinstance(value, dict):
            sanitized[key] = _sanitize_data(cast(Dict[str, Any], value))
        else:
            sanitized[key] = value

    return sanitized


def _log_with_context(
    logger: logging.Logger,
    level: int,
    message: str,
    extra_data: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
) -> None:
    """Log message with structured context."""
    context = get_context()

    # Build structured log entry
    log_data = {
        "message": message,
        "timestamp": time.time(),
        "level": logging.getLevelName(level),
        "logger": logger.name,
        **context.to_dict(),
    }

    # Add extra data
    if extra_data:
        # Sanitize sensitive data
        sanitized_data = _sanitize_data(extra_data)
        log_data.update(sanitized_data)

    # Log the structured data
    logger._log(level, message, (), extra={"structured_data": log_data}, **kwargs)


# Domain-specific logging helper functions
def log_modbus_operation(
    logger: logging.Logger,
    operation: str,
    slave_id: int,
    address: int,
    success: bool,
    duration_ms: float,
    **kwargs: Any,
) -> None:
    """Log Modbus operation with performance metrics."""
    level = logging.DEBUG if success else logging.WARNING
    _log_with_context(
        logger,
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
    logger: logging.Logger,
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

    _log_with_context(
        logger,
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
    logger: logging.Logger, event: str, source: Optional[str] = None, **kwargs: Any
) -> None:
    """Log configuration events."""
    _log_with_context(
        logger,
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
    logger: logging.Logger,
    event: str,
    path: Optional[str] = None,
    value: Optional[Any] = None,
    **kwargs: Any,
) -> None:
    """Log D-Bus related events."""
    _log_with_context(
        logger,
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
    logger: logging.Logger,
    operation: str,
    duration_ms: float,
    success: bool = True,
    **kwargs: Any,
) -> None:
    """Log performance metrics."""
    level = logging.DEBUG if success else logging.WARNING
    _log_with_context(
        logger,
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


@contextmanager
def log_context(**context_kwargs: Any) -> Iterator[LogContext]:
    """Context manager for setting logging context."""
    # Store context in thread-local storage for the duration
    context = LogContext(**context_kwargs)
    old_context = get_context()
    set_context(context)

    try:
        yield context
    finally:
        set_context(old_context)


def get_logger(name: str, config: Optional[Any] = None) -> logging.Logger:
    """Get a standard logger instance.

    The config parameter is kept for backward compatibility but is not used.
    All configuration is done through setup_root_logging().
    """
    return logging.getLogger(name)


def setup_root_logging(config: Optional[Any] = None) -> None:
    """Set up root logging configuration with both console and file output."""
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

    # File handler if configured
    if config and hasattr(config, "logging"):
        try:
            log_file = getattr(config.logging, "file", "/var/log/alfen_driver.log")

            # Create directory if it doesn't exist
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)

            # Use rotating file handler to prevent log files from growing too large
            max_bytes = getattr(config.logging, "max_file_size_mb", 10) * 1024 * 1024
            backup_count = getattr(config.logging, "backup_count", 5)

            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=max_bytes,
                backupCount=backup_count,
            )
            file_handler.setFormatter(formatter)
            root_logger.addHandler(file_handler)

            # Log successful file handler setup
            root_logger.info(f"Log file configured: {log_file}")

        except (OSError, AttributeError) as e:
            # Fallback to console only if file logging fails
            root_logger.warning(f"Failed to set up file logging: {e}")

    # Set level
    level = "INFO"
    if config and hasattr(config, "logging"):
        level = getattr(config.logging, "level", "INFO")

    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Log initialization
    root_logger.info(
        "Logging initialized",
        extra={
            "structured_data": {
                "log_level": level,
                "handlers": [type(h).__name__ for h in root_logger.handlers],
            }
        },
    )
