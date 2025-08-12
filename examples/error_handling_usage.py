"""Examples of using the error handling system."""

import logging

from alfen_driver.error_recovery import (
    CircuitBreaker,
    error_aggregator,
    with_error_recovery,
)
from alfen_driver.exceptions import (
    ConfigurationError,
    ModbusReadError,
    ValidationError,
)

# Setup logging
logging.basicConfig(level=logging.INFO)


# Example 1: Using the retry decorator
@with_error_recovery(
    exception_types=(ModbusReadError, ConnectionError),
    max_retries=3,
    retry_delay=1.0,
    backoff_multiplier=2.0,
    default_return=None,
)
def read_modbus_data() -> dict:
    """Example function that might fail due to network issues."""
    # This would normally make a real Modbus call
    import random

    if random.random() < 0.7:  # 70% chance of failure
        raise ModbusReadError(123, 5, 1, "Connection timeout")

    return {"voltage": 230.5, "current": 12.3}


# Example 2: Using circuit breaker
def unreliable_operation():
    """Example of an operation that might fail frequently."""
    import random

    if random.random() < 0.8:  # 80% chance of failure
        raise ConnectionError("Service unavailable")
    return "Success"


def main():
    """Demonstrate error handling patterns."""

    # Example 1: Retry decorator
    print("=== Retry Decorator Example ===")
    try:
        result = read_modbus_data()
        print(f"Success: {result}")
    except Exception as e:
        print(f"Failed after retries: {e}")
        error_aggregator.record_error(e, context="modbus_read", operation="read_data")

    # Example 2: Circuit breaker
    print("\n=== Circuit Breaker Example ===")
    breaker = CircuitBreaker(
        failure_threshold=3, timeout=5.0, expected_exception=ConnectionError
    )

    for i in range(10):
        try:
            result = breaker.call(unreliable_operation)
            print(f"Attempt {i+1}: {result}")
        except Exception as e:
            print(f"Attempt {i+1}: Failed - {e}")
            error_aggregator.record_error(
                e, context="circuit_breaker", operation="unreliable_op"
            )

    # Example 3: Custom exceptions with context
    print("\n=== Custom Exceptions Example ===")
    try:
        # Simulate configuration validation
        current_value = -5.0
        if current_value < 0:
            raise ValidationError(
                "current_setting", current_value, "must be non-negative"
            )
    except ValidationError as e:
        print(f"Validation error: {e}")
        error_aggregator.record_error(e, context="config_validation")

    try:
        # Simulate configuration loading error
        raise ConfigurationError(
            "Invalid YAML structure", config_field="modbus.port", config_value="invalid"
        )
    except ConfigurationError as e:
        print(f"Configuration error: {e}")
        error_aggregator.record_error(e, context="config_loading")

    # Example 4: Error summary
    print("\n=== Error Summary ===")
    error_aggregator.log_error_summary(last_minutes=1)  # Last minute
    summary = error_aggregator.get_error_summary(last_minutes=1)
    print(f"Total errors in last minute: {summary['total_errors']}")


if __name__ == "__main__":
    main()
