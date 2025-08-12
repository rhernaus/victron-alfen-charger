"""Tests for Modbus utilities."""

from unittest.mock import Mock, patch

import pytest
from pymodbus.exceptions import ModbusException

from alfen_driver.exceptions import (
    ModbusConnectionError,
    ModbusReadError,
    RetryExhaustedException,
)
from alfen_driver.modbus_utils import (
    decode_64bit_float,
    decode_floats,
    read_holding_registers,
    read_modbus_string,
    reconnect,
    retry_modbus_operation,
)


class TestReadHoldingRegisters:
    """Tests for read_holding_registers function."""

    def test_successful_read(self, mock_modbus_client):
        """Test successful register read."""
        # Setup mock response
        mock_response = Mock()
        mock_response.isError.return_value = False
        mock_response.registers = [100, 200, 300]
        mock_modbus_client.read_holding_registers.return_value = mock_response

        result = read_holding_registers(mock_modbus_client, 123, 3, 1)

        assert result == [100, 200, 300]
        mock_modbus_client.read_holding_registers.assert_called_once_with(
            123, 3, slave=1
        )

    def test_error_response(self, mock_modbus_client):
        """Test handling of error response."""
        # Setup mock error response
        mock_response = Mock()
        mock_response.isError.return_value = True
        mock_modbus_client.read_holding_registers.return_value = mock_response

        with pytest.raises(ModbusReadError) as exc_info:
            read_holding_registers(mock_modbus_client, 123, 3, 1)

        assert exc_info.value.address == 123
        assert exc_info.value.count == 3
        assert exc_info.value.slave_id == 1

    def test_modbus_exception(self, mock_modbus_client):
        """Test handling of Modbus exception."""
        mock_modbus_client.read_holding_registers.side_effect = ModbusException(
            "Connection failed"
        )

        with pytest.raises(ModbusException):
            read_holding_registers(mock_modbus_client, 123, 3, 1)


class TestDecodeFloats:
    """Tests for decode_floats function."""

    def test_decode_single_float(self):
        """Test decoding a single float."""
        # 32-bit float for 230.0: 0x4366 0x0000 in big endian
        registers = [0x4366, 0x0000]
        result = decode_floats(registers, 1)

        assert len(result) == 1
        assert abs(result[0] - 230.0) < 0.1  # Allow small floating point error

    def test_decode_multiple_floats(self):
        """Test decoding multiple floats."""
        # Two floats: 230.0 and 12.5
        registers = [0x4366, 0x0000, 0x4148, 0x0000]
        result = decode_floats(registers, 2)

        assert len(result) == 2
        assert abs(result[0] - 230.0) < 0.1
        assert abs(result[1] - 12.5) < 0.1

    def test_decode_nan_handling(self):
        """Test that NaN values are replaced with 0.0."""
        # Create registers that would decode to NaN
        with patch(
            "alfen_driver.modbus_utils.BinaryPayloadDecoder"
        ) as mock_decoder_class:
            mock_decoder = Mock()
            mock_decoder.decode_32bit_float.return_value = float("nan")
            mock_decoder_class.fromRegisters.return_value = mock_decoder

            result = decode_floats([0x7FC0, 0x0000], 1)

            assert len(result) == 1
            assert result[0] == 0.0

    def test_decode_zero_count(self):
        """Test decoding with zero count."""
        result = decode_floats([0x4366, 0x0000], 0)
        assert result == []


class TestDecode64BitFloat:
    """Tests for decode_64bit_float function."""

    def test_decode_64bit_float(self):
        """Test decoding 64-bit float."""
        # Mock the decoder to return a known value
        with patch(
            "alfen_driver.modbus_utils.BinaryPayloadDecoder"
        ) as mock_decoder_class:
            mock_decoder = Mock()
            mock_decoder.decode_64bit_float.return_value = 12345.6789
            mock_decoder_class.fromRegisters.return_value = mock_decoder

            result = decode_64bit_float([0x1234, 0x5678, 0x9ABC, 0xDEF0])

            assert result == 12345.6789

    def test_decode_64bit_nan_handling(self):
        """Test that 64-bit NaN values are replaced with 0.0."""
        with patch(
            "alfen_driver.modbus_utils.BinaryPayloadDecoder"
        ) as mock_decoder_class:
            mock_decoder = Mock()
            mock_decoder.decode_64bit_float.return_value = float("nan")
            mock_decoder_class.fromRegisters.return_value = mock_decoder

            result = decode_64bit_float([0x7FF8, 0x0000, 0x0000, 0x0000])

            assert result == 0.0


class TestReadModbusString:
    """Tests for read_modbus_string function."""

    def test_read_simple_string(self, mock_modbus_client):
        """Test reading a simple string."""
        # Setup mock to return registers representing "TEST"
        mock_response = Mock()
        mock_response.isError.return_value = False
        mock_response.registers = [0x5445, 0x5354]  # "TE" + "ST" in ASCII
        mock_modbus_client.read_holding_registers.return_value = mock_response

        result = read_modbus_string(mock_modbus_client, 100, 2, 1)

        assert result == "TEST"

    def test_read_string_with_nulls(self, mock_modbus_client):
        """Test reading string with null terminators."""
        # Setup mock to return registers with null padding
        mock_response = Mock()
        mock_response.isError.return_value = False
        mock_response.registers = [0x4142, 0x0000]  # "AB" + null padding
        mock_modbus_client.read_holding_registers.return_value = mock_response

        result = read_modbus_string(mock_modbus_client, 100, 2, 1)

        assert result == "AB"

    def test_read_string_with_spaces(self, mock_modbus_client):
        """Test reading string with space padding."""
        # Setup mock to return registers with space padding
        mock_response = Mock()
        mock_response.isError.return_value = False
        mock_response.registers = [0x4142, 0x2020]  # "AB" + "  "
        mock_modbus_client.read_holding_registers.return_value = mock_response

        result = read_modbus_string(mock_modbus_client, 100, 2, 1)

        assert result == "AB"

    def test_read_string_modbus_error(self, mock_modbus_client):
        """Test handling of Modbus error during string read."""
        mock_modbus_client.read_holding_registers.side_effect = ModbusReadError(
            100, 2, 1
        )

        with patch("logging.getLogger") as mock_logger_get:
            mock_logger = Mock()
            mock_logger_get.return_value = mock_logger

            result = read_modbus_string(mock_modbus_client, 100, 2, 1)

            assert result == "N/A"
            mock_logger.debug.assert_called_once()

    def test_read_string_unexpected_error(self, mock_modbus_client):
        """Test handling of unexpected error during string read."""
        mock_modbus_client.read_holding_registers.side_effect = RuntimeError(
            "Unexpected error"
        )

        with patch("logging.getLogger") as mock_logger_get:
            mock_logger = Mock()
            mock_logger_get.return_value = mock_logger

            result = read_modbus_string(mock_modbus_client, 100, 2, 1)

            assert result == "N/A"
            mock_logger.warning.assert_called_once()


class TestReconnect:
    """Tests for reconnect function."""

    def test_successful_reconnect(self, mock_modbus_client, mock_logger):
        """Test successful reconnection."""
        mock_modbus_client.connect.return_value = True

        result = reconnect(mock_modbus_client, mock_logger, retry_delay=0.01)

        assert result is True
        mock_modbus_client.close.assert_called_once()
        mock_modbus_client.connect.assert_called()
        mock_logger.info.assert_called()

    def test_reconnect_with_max_attempts(self, mock_modbus_client, mock_logger):
        """Test reconnection with max attempts limit."""
        mock_modbus_client.connect.return_value = False
        mock_modbus_client.host = "test_host"
        mock_modbus_client.port = 502

        with pytest.raises(ModbusConnectionError) as exc_info:
            reconnect(mock_modbus_client, mock_logger, retry_delay=0.01, max_attempts=2)

        assert exc_info.value.host == "test_host"
        assert exc_info.value.port == 502
        assert "Failed to connect after 2 attempts" in str(exc_info.value)

        # Should have attempted connection twice
        assert mock_modbus_client.connect.call_count == 2

    def test_reconnect_with_connection_exception(self, mock_modbus_client, mock_logger):
        """Test reconnection when connection raises exception."""
        mock_modbus_client.connect.side_effect = [
            ConnectionError("Network error"),
            True,  # Second attempt succeeds
        ]

        result = reconnect(mock_modbus_client, mock_logger, retry_delay=0.01)

        assert result is True
        mock_logger.warning.assert_called()  # For the failed attempt
        mock_logger.info.assert_called()  # For the successful attempt

    @patch("time.sleep")
    def test_reconnect_retry_delay(self, mock_sleep, mock_modbus_client, mock_logger):
        """Test that retry delay is respected."""
        mock_modbus_client.connect.side_effect = [False, True]

        result = reconnect(mock_modbus_client, mock_logger, retry_delay=1.5)

        assert result is True
        mock_sleep.assert_called_once_with(1.5)


class TestRetryModbusOperation:
    """Tests for retry_modbus_operation function."""

    def test_successful_operation(self, mock_logger):
        """Test successful operation on first try."""
        operation = Mock(return_value="success")

        result = retry_modbus_operation(operation, 3, 0.1, mock_logger)

        assert result == "success"
        operation.assert_called_once()
        mock_logger.error.assert_not_called()

    def test_operation_succeeds_after_retries(self, mock_logger):
        """Test operation succeeding after some failures."""
        operation = Mock(
            side_effect=[
                ModbusException("First failure"),
                ModbusException("Second failure"),
                "success",
            ]
        )

        with patch("time.sleep"):
            result = retry_modbus_operation(operation, 3, 0.1, mock_logger)

        assert result == "success"
        assert operation.call_count == 3
        assert mock_logger.error.call_count == 2  # Two failure logs

    def test_operation_fails_all_retries(self, mock_logger):
        """Test operation failing all retry attempts."""
        operation = Mock(side_effect=ModbusException("Persistent failure"))

        with patch("time.sleep"):
            with pytest.raises(RetryExhaustedException) as exc_info:
                retry_modbus_operation(operation, 2, 0.1, mock_logger)

        # The operation name will be something like "Mock" or empty
        assert exc_info.value.operation in ["<lambda>", "Mock", "unknown", ""]
        assert exc_info.value.attempts == 2
        assert isinstance(exc_info.value.last_error, ModbusException)

        # The function tries 2 retries + 1 extra attempt to get the last error
        assert operation.call_count == 3
        mock_logger.error.assert_called()

    def test_operation_with_non_modbus_exception(self, mock_logger):
        """Test operation failing with non-Modbus exception."""
        operation = Mock(side_effect=ValueError("Not a Modbus error"))

        # Non-Modbus exceptions should not be retried
        with pytest.raises(ValueError):
            retry_modbus_operation(operation, 3, 0.1, mock_logger)

        operation.assert_called_once()

    def test_operation_without_logger(self):
        """Test operation retry without logger."""
        operation = Mock(side_effect=[ModbusException("Failure"), "success"])

        with patch("time.sleep"):
            result = retry_modbus_operation(operation, 2, 0.1, None)

        assert result == "success"
        assert operation.call_count == 2

    @patch("time.sleep")
    def test_retry_delay_timing(self, mock_sleep, mock_logger):
        """Test that retry delays are respected."""
        operation = Mock(
            side_effect=[
                ModbusException("First failure"),
                ModbusException("Second failure"),
                "success",
            ]
        )

        result = retry_modbus_operation(operation, 3, 0.5, mock_logger)

        assert result == "success"
        # Should have slept twice (after first two failures)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_called_with(0.5)

    def test_retry_exhausted_with_function_name(self, mock_logger):
        """Test RetryExhaustedException includes function name when available."""

        def named_operation():
            raise ModbusException("Test failure")

        with patch("time.sleep"):
            with pytest.raises(RetryExhaustedException) as exc_info:
                retry_modbus_operation(named_operation, 1, 0.1, mock_logger)

        assert exc_info.value.operation == "named_operation"
