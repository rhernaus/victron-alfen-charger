#!/usr/bin/env python3
"""Demonstration of structured logging features in the Alfen driver.

This script shows how to use the enhanced structured logging system
with proper context management, performance metrics, and domain-specific
logging methods.
"""

import time

from alfen_driver.config import LoggingConfig
from alfen_driver.logging_utils import (
    LogContext,
    get_logger,
    log_context,
    setup_root_logging,
)


def demo_basic_logging():
    """Demonstrate basic structured logging features."""
    print("=== Basic Structured Logging ===")

    # Create configuration
    config_mock = type("Config", (), {})()
    config_mock.logging = LoggingConfig(
        level="DEBUG",
        file="/tmp/alfen_demo.log",
        format="structured",
        console_output=True,
    )

    # Set up logging
    setup_root_logging(config_mock)
    logger = get_logger("demo.basic", config_mock)

    # Set context for this operation
    logger.set_context(
        LogContext(operation="demo_session", component="demo", session_id="demo123")
    )

    # Basic logging with extra data
    logger.info("Starting basic logging demo", demo_version="1.0", user="demo_user")

    logger.debug(
        "Debug message with structured data",
        debug_info="detailed information",
        counter=42,
    )

    logger.warning("Warning message", issue="simulated warning", severity="low")

    print("\n")


def demo_domain_specific_logging():
    """Demonstrate domain-specific logging methods."""
    print("=== Domain-Specific Logging ===")

    config_mock = type("Config", (), {})()
    config_mock.logging = LoggingConfig(level="DEBUG", console_output=True)

    logger = get_logger("demo.domain", config_mock)
    logger.set_context(
        LogContext(
            component="charging_controller", device_instance=1, modbus_slave_id=200
        )
    )

    # Modbus operation logging
    start_time = time.time()
    time.sleep(0.025)  # Simulate operation
    duration = (time.time() - start_time) * 1000

    logger.log_modbus_operation(
        "read_holding_registers",
        slave_id=200,
        address=1201,
        success=True,
        duration_ms=duration,
        register_count=5,
        values=[1, 2, 3, 4, 5],
    )

    # Charging event logging
    logger.log_charging_event(
        "current_updated",
        current=16.0,
        power=3680.0,
        status="charging",
        mode="AUTO",
        phase_count=3,
    )

    # Performance metrics
    logger.log_performance(
        "data_processing",
        duration_ms=12.5,
        success=True,
        records_processed=150,
        cache_hits=120,
    )

    # Error recovery logging
    logger.log_error_recovery(
        "modbus_reconnect",
        attempt=2,
        max_attempts=3,
        success=True,
        previous_error="Connection timeout",
    )

    # Configuration event
    logger.log_config_event(
        "config_reloaded",
        source="file_change",
        config_version="1.2.3",
        changes=["logging.level", "schedule.items"],
    )

    print("\n")


def demo_context_management():
    """Demonstrate logging context management."""
    print("=== Context Management ===")

    config_mock = type("Config", (), {})()
    config_mock.logging = LoggingConfig(level="INFO", console_output=True)

    logger = get_logger("demo.context", config_mock)

    # Using context manager
    with log_context(operation="charge_session", correlation_id="sess_456"):
        logger.info("Starting new charging session")

        with log_context(component="modbus_reader", modbus_slave_id=1):
            logger.info("Reading voltage data", voltages=[230.5, 229.8, 231.2])

        with log_context(component="current_controller"):
            logger.info(
                "Updating charging current",
                old_current=12.0,
                new_current=16.0,
                reason="solar_excess_available",
            )

    logger.info("Session completed")

    print("\n")


def demo_error_and_exception_logging():
    """Demonstrate error and exception logging."""
    print("=== Error and Exception Logging ===")

    config_mock = type("Config", (), {})()
    config_mock.logging = LoggingConfig(level="DEBUG", console_output=True)

    logger = get_logger("demo.errors", config_mock)
    logger.set_context(LogContext(operation="error_demo", component="error_handler"))

    # Regular error logging
    logger.error(
        "Modbus communication failed",
        error_code="E001",
        device_address="192.168.1.100",
        slave_id=200,
        retry_count=3,
    )

    # Exception logging
    try:
        # Simulate an exception
        raise ValueError("Invalid current value: -5.0 (must be positive)")
    except ValueError:
        logger.exception(
            "Current validation failed",
            attempted_value=-5.0,
            valid_range="0.0-64.0",
            source="user_input",
        )

    print("\n")


def demo_data_sanitization():
    """Demonstrate sensitive data sanitization."""
    print("=== Data Sanitization ===")

    config_mock = type("Config", (), {})()
    config_mock.logging = LoggingConfig(level="INFO", console_output=True)

    logger = get_logger("demo.security", config_mock)

    # Logging with sensitive data (will be automatically sanitized)
    logger.info(
        "User authentication",
        username="demo_user",
        password="secret123",  # This will be redacted
        api_key="api_key_abc123",  # This will be redacted
        host="192.168.1.100",  # This will remain
        timestamp=time.time(),
    )

    # Nested sensitive data
    logger.info(
        "Configuration loaded",
        config={
            "database": {
                "host": "localhost",
                "password": "db_secret",  # This will be redacted
                "port": 5432,
            },
            "auth": {
                "secret_key": "jwt_secret",  # This will be redacted
                "algorithm": "HS256",
            },
        },
    )

    print("\n")


def main():
    """Run all logging demonstrations."""
    print("Alfen Driver - Structured Logging Demonstration")
    print("=" * 50)
    print()

    demo_basic_logging()
    demo_domain_specific_logging()
    demo_context_management()
    demo_error_and_exception_logging()
    demo_data_sanitization()

    print("=" * 50)
    print("Demo completed! Check /tmp/alfen_demo.log for file output.")
    print()
    print("Key features demonstrated:")
    print("- Structured logging with context information")
    print("- Domain-specific logging methods (Modbus, charging, performance)")
    print("- Hierarchical context management")
    print("- Automatic sensitive data sanitization")
    print("- Performance metrics and error recovery tracking")
    print("- Both human-readable and structured output formats")


if __name__ == "__main__":
    main()
