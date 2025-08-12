"""Tests for error recovery utilities."""

import time
from unittest.mock import Mock, patch

import pytest

from alfen_driver.error_recovery import (
    CircuitBreaker,
    ErrorAggregator,
    error_aggregator,
    with_error_recovery,
)
from alfen_driver.exceptions import AlfenDriverError, RetryExhaustedException


class TestWithErrorRecovery:
    """Tests for the with_error_recovery decorator."""

    def test_successful_function_call(self):
        """Test decorator with function that succeeds immediately."""

        @with_error_recovery(ValueError, max_retries=3)
        def successful_function():
            return "success"

        result = successful_function()
        assert result == "success"

    def test_function_succeeds_after_retries(self):
        """Test function that succeeds after some failures."""
        call_count = 0

        @with_error_recovery(ValueError, max_retries=3, retry_delay=0.01)
        def flaky_function():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError(f"Failure {call_count}")
            return "success"

        with patch("time.sleep"):
            result = flaky_function()

        assert result == "success"
        assert call_count == 3

    def test_function_fails_all_retries(self):
        """Test function that fails all retry attempts."""

        @with_error_recovery(ValueError, max_retries=2, retry_delay=0.01)
        def always_fails():
            raise ValueError("Persistent failure")

        with patch("time.sleep"):
            with pytest.raises(RetryExhaustedException) as exc_info:
                always_fails()

        assert exc_info.value.operation == "always_fails"
        assert exc_info.value.attempts == 3  # max_retries + 1

    def test_function_with_default_return(self):
        """Test function with default return value."""

        @with_error_recovery(
            ValueError, max_retries=1, default_return="default", retry_delay=0.01
        )
        def always_fails():
            raise ValueError("Failure")

        with patch("time.sleep"):
            result = always_fails()

        assert result == "default"

    def test_exponential_backoff(self):
        """Test exponential backoff timing."""

        @with_error_recovery(
            ValueError, max_retries=3, retry_delay=0.1, backoff_multiplier=2.0
        )
        def always_fails():
            raise ValueError("Failure")

        with patch("time.sleep") as mock_sleep:
            with pytest.raises(RetryExhaustedException):
                always_fails()

        # Should have slept with increasing delays: 0.1, 0.2, 0.4
        expected_calls = [((0.1,),), ((0.2,),), ((0.4,),)]
        assert mock_sleep.call_args_list == expected_calls

    def test_multiple_exception_types(self):
        """Test decorator with multiple exception types."""

        @with_error_recovery((ValueError, TypeError), max_retries=2, retry_delay=0.01)
        def multi_exception_function(exception_type):
            raise exception_type("Test error")

        with patch("time.sleep"):
            # Should catch ValueError
            with pytest.raises(RetryExhaustedException):
                multi_exception_function(ValueError)

            # Should catch TypeError
            with pytest.raises(RetryExhaustedException):
                multi_exception_function(TypeError)

            # Should not catch RuntimeError
            with pytest.raises(RuntimeError):
                multi_exception_function(RuntimeError)

    def test_logging_disabled(self):
        """Test decorator with logging disabled."""

        @with_error_recovery(
            ValueError, max_retries=1, log_errors=False, retry_delay=0.01
        )
        def failing_function():
            raise ValueError("Test error")

        with patch("time.sleep"):
            with patch("logging.getLogger") as mock_logger_get:
                mock_logger = Mock()
                mock_logger_get.return_value = mock_logger

                with pytest.raises(RetryExhaustedException):
                    failing_function()

                # Logger should not have been called
                mock_logger.warning.assert_not_called()
                mock_logger.error.assert_not_called()


class TestCircuitBreaker:
    """Tests for the CircuitBreaker class."""

    def test_successful_call_closed_state(self):
        """Test successful call when circuit is closed."""
        breaker = CircuitBreaker(failure_threshold=3, timeout=1.0)

        def successful_function():
            return "success"

        result = breaker.call(successful_function)
        assert result == "success"
        assert breaker.state == "CLOSED"
        assert breaker.failure_count == 0

    def test_circuit_opens_after_threshold(self):
        """Test circuit opens after failure threshold."""
        breaker = CircuitBreaker(
            failure_threshold=2, timeout=1.0, expected_exception=ValueError
        )

        def failing_function():
            raise ValueError("Test failure")

        # First failure
        with pytest.raises(ValueError):
            breaker.call(failing_function)
        assert breaker.state == "CLOSED"
        assert breaker.failure_count == 1

        # Second failure - should open circuit
        with pytest.raises(ValueError):
            breaker.call(failing_function)
        assert breaker.state == "OPEN"
        assert breaker.failure_count == 2

    def test_circuit_stays_open(self):
        """Test circuit stays open and rejects calls."""
        breaker = CircuitBreaker(
            failure_threshold=1, timeout=1.0, expected_exception=ValueError
        )

        def failing_function():
            raise ValueError("Test failure")

        # Trigger circuit opening
        with pytest.raises(ValueError):
            breaker.call(failing_function)

        assert breaker.state == "OPEN"

        # Next call should be rejected
        with pytest.raises(AlfenDriverError) as exc_info:
            breaker.call(failing_function)

        assert "Circuit breaker is OPEN" in str(exc_info.value)

    def test_circuit_half_open_after_timeout(self):
        """Test circuit moves to half-open after timeout."""
        breaker = CircuitBreaker(
            failure_threshold=1, timeout=0.1, expected_exception=ValueError
        )

        def failing_function():
            raise ValueError("Test failure")

        # Open the circuit
        with pytest.raises(ValueError):
            breaker.call(failing_function)

        assert breaker.state == "OPEN"

        # Wait for timeout
        time.sleep(0.15)

        # Next call should move to half-open
        with pytest.raises(ValueError):
            breaker.call(failing_function)

        # Should have been in half-open state during the call
        assert breaker.state == "OPEN"  # Back to open after failure

    def test_circuit_resets_after_success(self):
        """Test circuit resets to closed after success in half-open."""
        breaker = CircuitBreaker(
            failure_threshold=1, timeout=0.1, expected_exception=ValueError
        )

        def failing_function():
            raise ValueError("Test failure")

        def successful_function():
            return "success"

        # Open the circuit
        with pytest.raises(ValueError):
            breaker.call(failing_function)

        # Wait for timeout
        time.sleep(0.15)

        # Successful call should reset circuit
        result = breaker.call(successful_function)

        assert result == "success"
        assert breaker.state == "CLOSED"
        assert breaker.failure_count == 0

    def test_unexpected_exception_not_counted(self):
        """Test that unexpected exceptions don't affect circuit breaker."""
        breaker = CircuitBreaker(
            failure_threshold=2, timeout=1.0, expected_exception=ValueError
        )

        def runtime_error_function():
            raise RuntimeError("Unexpected error")

        # RuntimeError should not be caught by circuit breaker
        with pytest.raises(RuntimeError):
            breaker.call(runtime_error_function)

        assert breaker.state == "CLOSED"
        assert breaker.failure_count == 0


class TestErrorAggregator:
    """Tests for the ErrorAggregator class."""

    def test_record_error(self):
        """Test recording an error."""
        aggregator = ErrorAggregator(max_errors=10)

        error = ValueError("Test error")
        aggregator.record_error(
            error, context="test_context", operation="test_operation"
        )

        assert len(aggregator.errors) == 1
        error_info = aggregator.errors[0]
        assert error_info["error_type"] == "ValueError"
        assert error_info["message"] == "Test error"
        assert error_info["context"] == "test_context"
        assert error_info["operation"] == "test_operation"
        assert "timestamp" in error_info

    def test_max_errors_limit(self):
        """Test that error list is limited to max_errors."""
        aggregator = ErrorAggregator(max_errors=3)

        # Add 5 errors
        for i in range(5):
            error = ValueError(f"Error {i}")
            aggregator.record_error(error)

        # Should only keep the last 3
        assert len(aggregator.errors) == 3
        messages = [e["message"] for e in aggregator.errors]
        assert messages == ["Error 2", "Error 3", "Error 4"]

    def test_get_error_summary(self):
        """Test getting error summary."""
        aggregator = ErrorAggregator()

        # Add some recent errors
        aggregator.record_error(ValueError("Error 1"), context="context1")
        aggregator.record_error(ValueError("Error 2"), context="context1")
        aggregator.record_error(TypeError("Error 3"), context="context2")

        summary = aggregator.get_error_summary(last_minutes=60)

        assert summary["total_errors"] == 3
        assert len(summary["error_types"]) == 2

        # Check ValueError summary
        value_error_info = summary["error_types"]["ValueError"]
        assert value_error_info["count"] == 2
        assert "context1" in value_error_info["contexts"]

        # Check TypeError summary
        type_error_info = summary["error_types"]["TypeError"]
        assert type_error_info["count"] == 1
        assert "context2" in type_error_info["contexts"]

    def test_get_error_summary_time_window(self):
        """Test error summary with time window filtering."""
        aggregator = ErrorAggregator()

        # Add an old error (simulate by manipulating timestamp)
        old_error_info = {
            "timestamp": time.time() - 3600,  # 1 hour ago
            "error_type": "ValueError",
            "message": "Old error",
            "context": None,
            "operation": None,
        }
        aggregator.errors.append(old_error_info)

        # Add a recent error
        aggregator.record_error(TypeError("Recent error"))

        # Get summary for last 30 minutes
        summary = aggregator.get_error_summary(last_minutes=30)

        # Should only include the recent error
        assert summary["total_errors"] == 1
        assert "TypeError" in summary["error_types"]
        assert "ValueError" not in summary["error_types"]

    def test_log_error_summary(self):
        """Test logging error summary."""
        aggregator = ErrorAggregator()

        # Add some errors
        aggregator.record_error(ValueError("Error 1"), context="test")
        aggregator.record_error(ValueError("Error 2"), context="test")

        with patch("logging.getLogger") as mock_logger_get:
            mock_logger = Mock()
            mock_logger_get.return_value = mock_logger

            aggregator.log_error_summary(last_minutes=60)

            # Should have logged summary information
            assert (
                mock_logger.info.call_count >= 2
            )  # At least summary + error type info

    def test_log_error_summary_no_errors(self):
        """Test logging when there are no errors."""
        aggregator = ErrorAggregator()

        with patch("logging.getLogger") as mock_logger_get:
            mock_logger = Mock()
            mock_logger_get.return_value = mock_logger

            aggregator.log_error_summary(last_minutes=60)

            # Should not log anything when there are no errors
            mock_logger.info.assert_not_called()


class TestGlobalErrorAggregator:
    """Tests for the global error aggregator instance."""

    def test_global_error_aggregator_exists(self):
        """Test that global error aggregator instance exists."""
        assert error_aggregator is not None
        assert isinstance(error_aggregator, ErrorAggregator)

    def test_global_error_aggregator_usage(self):
        """Test using the global error aggregator."""
        # Clear any existing errors
        error_aggregator.errors.clear()

        # Record an error
        test_error = RuntimeError("Global test error")
        error_aggregator.record_error(test_error, context="global_test")

        # Verify it was recorded
        assert len(error_aggregator.errors) >= 1

        # Find our error (there might be others from other tests)
        our_error = None
        for error_info in error_aggregator.errors:
            if error_info["message"] == "Global test error":
                our_error = error_info
                break

        assert our_error is not None
        assert our_error["error_type"] == "RuntimeError"
        assert our_error["context"] == "global_test"

        # Clean up
        error_aggregator.errors.clear()
