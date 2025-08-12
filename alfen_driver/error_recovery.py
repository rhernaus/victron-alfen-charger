"""Error recovery utilities for robust operation."""

import logging
import time
from functools import wraps
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, TypeVar, Union

from .exceptions import AlfenDriverError, RetryExhaustedException

T = TypeVar("T")


def with_error_recovery(
    exception_types: Union[Type[Exception], Tuple[Type[Exception], ...]],
    max_retries: int = 3,
    retry_delay: float = 1.0,
    backoff_multiplier: float = 2.0,
    default_return: Optional[Any] = None,
    log_errors: bool = True,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator for automatic error recovery with exponential backoff.

    Args:
        exception_types: Exception type(s) to catch and retry on
        max_retries: Maximum number of retry attempts
        retry_delay: Initial delay between retries in seconds
        backoff_multiplier: Multiplier for exponential backoff
        default_return: Value to return if all retries fail
        log_errors: Whether to log errors during retries

    Returns:
        Decorated function with error recovery
    """
    if not isinstance(exception_types, tuple):
        exception_types = (exception_types,)

    def decorator(func: Callable[..., T]) -> Callable[..., Union[T, Any]]:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Union[T, Any]:
            logger = logging.getLogger(f"alfen_driver.retry.{func.__name__}")
            last_exception = None
            current_delay = retry_delay

            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exception_types as e:
                    last_exception = e
                    if log_errors:
                        if attempt < max_retries:
                            logger.warning(
                                f"Attempt {attempt + 1}/{max_retries + 1} failed: {e}. "
                                f"Retrying in {current_delay:.1f}s..."
                            )
                        else:
                            logger.error(f"All {max_retries + 1} attempts failed: {e}")

                    if attempt < max_retries:
                        time.sleep(current_delay)
                        current_delay *= backoff_multiplier

            if default_return is not None:
                logger.info(f"Returning default value: {default_return}")
                return default_return

            raise RetryExhaustedException(
                func.__name__, max_retries + 1, last_exception
            )

        return wrapper

    return decorator


class CircuitBreaker:
    """Circuit breaker pattern for preventing cascading failures."""

    def __init__(
        self,
        failure_threshold: int = 5,
        timeout: float = 60.0,
        expected_exception: Type[Exception] = Exception,
    ) -> None:
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            timeout: Time in seconds before attempting to reset
            expected_exception: Exception type that triggers the circuit breaker
        """
        self.failure_threshold = failure_threshold
        self.timeout = timeout
        self.expected_exception = expected_exception
        self.failure_count = 0
        self.last_failure_time: Optional[float] = None
        self.state = "CLOSED"  # CLOSED, OPEN, HALF_OPEN
        self.logger = logging.getLogger("alfen_driver.circuit_breaker")

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Call function through circuit breaker.

        Args:
            func: Function to call
            *args: Function arguments
            **kwargs: Function keyword arguments

        Returns:
            Function result

        Raises:
            Exception: If circuit is open or function fails
        """
        if self.state == "OPEN":
            if self._should_attempt_reset():
                self.state = "HALF_OPEN"
                self.logger.info("Circuit breaker moving to HALF_OPEN state")
            else:
                raise AlfenDriverError(
                    f"Circuit breaker is OPEN. Timeout: {self.timeout}s",
                    f"Last failure: {self.failure_count} failures",
                )

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise e

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt reset."""
        return (
            self.last_failure_time is not None
            and time.time() - self.last_failure_time >= self.timeout
        )

    def _on_success(self) -> None:
        """Handle successful function call."""
        if self.state == "HALF_OPEN":
            self.state = "CLOSED"
            self.failure_count = 0
            self.logger.info("Circuit breaker reset to CLOSED state")

    def _on_failure(self) -> None:
        """Handle function call failure."""
        self.failure_count += 1
        self.last_failure_time = time.time()

        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"
            self.logger.warning(
                f"Circuit breaker opened after {self.failure_count} failures"
            )


class ErrorAggregator:
    """Collects and reports on error patterns."""

    def __init__(self, max_errors: int = 100) -> None:
        """Initialize error aggregator."""
        self.max_errors = max_errors
        self.errors: List[Dict[str, Any]] = []
        self.logger = logging.getLogger("alfen_driver.error_aggregator")

    def record_error(
        self,
        error: Exception,
        context: Optional[str] = None,
        operation: Optional[str] = None,
    ) -> None:
        """Record an error with context."""
        error_info = {
            "timestamp": time.time(),
            "error_type": type(error).__name__,
            "message": str(error),
            "context": context,
            "operation": operation,
        }

        self.errors.append(error_info)

        # Keep only recent errors
        if len(self.errors) > self.max_errors:
            self.errors = self.errors[-self.max_errors :]

    def get_error_summary(self, last_minutes: int = 60) -> Dict[str, Any]:
        """Get summary of errors from the last N minutes."""
        cutoff_time = time.time() - (last_minutes * 60)
        recent_errors = [e for e in self.errors if e["timestamp"] >= cutoff_time]

        error_types: Dict[str, Dict[str, Any]] = {}
        for error in recent_errors:
            error_type = error["error_type"]
            if error_type not in error_types:
                error_types[error_type] = {
                    "count": 0,
                    "last_occurrence": 0.0,
                    "contexts": set(),
                }
            error_types[error_type]["count"] += 1
            error_types[error_type]["last_occurrence"] = error["timestamp"]
            if error["context"]:
                error_types[error_type]["contexts"].add(error["context"])

        return {
            "total_errors": len(recent_errors),
            "error_types": error_types,
            "time_window_minutes": last_minutes,
        }

    def log_error_summary(self, last_minutes: int = 60) -> None:
        """Log error summary."""
        summary = self.get_error_summary(last_minutes)
        if summary["total_errors"] == 0:
            return

        self.logger.info(f"Error summary (last {last_minutes} minutes):")
        self.logger.info(f"Total errors: {summary['total_errors']}")

        for error_type, info in summary["error_types"].items():
            contexts = ", ".join(info["contexts"]) if info["contexts"] else "N/A"
            self.logger.info(
                f"  {error_type}: {info['count']} occurrences, contexts: {contexts}"
            )


# Global error aggregator instance
error_aggregator = ErrorAggregator()
