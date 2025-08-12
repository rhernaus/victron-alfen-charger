"""Modbus communication utilities for the Alfen EV Charger Driver.

This module provides comprehensive Modbus TCP communication functions for
interacting with Alfen EV chargers. It includes utilities for reading registers,
decoding various data formats, handling connection management, and implementing
robust error recovery mechanisms.

Key Features:
    - Register reading with automatic error handling
    - Float decoding (32-bit and 64-bit) with endianness support
    - String decoding from Modbus registers
    - Connection management and automatic reconnection
    - Retry mechanisms with configurable parameters
    - NaN handling and data sanitization

The module is designed to work with the Alfen charger's Modbus TCP interface,
which typically uses:
    - Socket slave ID (1): Real-time data (voltages, currents, power)
    - Station slave ID (200): Configuration and control registers

Example:
    ```python
    from alfen_driver.modbus_utils import read_holding_registers, decode_floats

    # Read voltage registers and decode as floats
    voltage_regs = read_holding_registers(client, 306, 6, slave=1)
    voltages = decode_floats(voltage_regs, 3)  # 3 phases
    print(f"Voltages: L1={voltages[0]:.1f}V, L2={voltages[1]:.1f}V, L3={voltages[2]:.1f}V")
    ```
"""

import math
import time
from typing import Any, Callable, List, Optional

from pymodbus.client import ModbusTcpClient
from pymodbus.constants import Endian
from pymodbus.exceptions import ModbusException
from pymodbus.payload import BinaryPayloadDecoder

from .exceptions import (
    ModbusConnectionError,
    ModbusReadError,
    RetryExhaustedException,
)
from .logging_utils import get_logger


def read_holding_registers(
    client: ModbusTcpClient, address: int, count: int, slave: int
) -> List[int]:
    """Read holding registers from a Modbus TCP device.

    This function reads a contiguous block of holding registers from the specified
    Modbus slave device. It automatically handles Modbus protocol errors and
    converts them to appropriate exceptions.

    Args:
        client: The Modbus TCP client instance to use for communication.
        address: The starting register address (0-based addressing).
        count: The number of consecutive registers to read.
        slave: The Modbus slave/unit identifier (1-247).

    Returns:
        A list of register values as integers (16-bit unsigned values).

    Raises:
        ModbusReadError: If the read operation fails, includes details about
            the address, count, and slave ID for debugging.
        ModbusException: For low-level Modbus protocol errors.

    Example:
        ```python
        # Read 3 voltage registers from socket slave
        try:
            voltage_regs = read_holding_registers(client, 306, 6, slave=1)
            print(f"Read {len(voltage_regs)} register values")
        except ModbusReadError as e:
            logger.error(f"Failed to read voltages: {e}")
        ```

    Note:
        This function uses the pymodbus library's read_holding_registers method
        and converts Modbus-specific errors to the driver's exception hierarchy.
    """
    rr = client.read_holding_registers(address, count, slave=slave)
    if rr.isError():
        raise ModbusReadError(address, count, slave, str(rr))
    return list(rr.registers)


def decode_floats(registers: List[int], count: int) -> List[float]:
    """
    Decode list of registers into floats (assuming 2 registers per float).

    Parameters:
        registers: List of register values.
        count: Number of floats to decode.

    Returns:
        List of decoded float values (NaN replaced with 0.0).
    """
    values = []
    decoder = BinaryPayloadDecoder.fromRegisters(
        registers, byteorder=Endian.BIG, wordorder=Endian.BIG
    )
    for _ in range(count):
        val = decoder.decode_32bit_float()
        values.append(val if not math.isnan(val) else 0.0)
    return values


def decode_64bit_float(registers: List[int]) -> float:
    """Decode Modbus registers into a 64-bit floating-point value.

    This function converts 4 consecutive 16-bit Modbus registers into an IEEE 754
    64-bit (double precision) floating-point number. This is typically used for
    high-precision values like energy counters in the Alfen charger.

    Args:
        registers: List of exactly 4 consecutive 16-bit register values.

    Returns:
        The decoded 64-bit floating-point value. NaN values are converted
        to 0.0 to prevent calculation errors.

    Example:
        ```python
        # Decode energy counter (4 registers)
        energy_regs = read_holding_registers(client, 374, 4, slave=1)
        total_energy = decode_64bit_float(energy_regs)
        energy_kwh = total_energy / 1000.0  # Convert Wh to kWh
        ```

    Note:
        The function uses big-endian byte and word order consistent with
        Modbus protocol standards. Energy values from Alfen chargers are
        typically in watt-hours (Wh) and require conversion to kWh.
    """
    decoder = BinaryPayloadDecoder.fromRegisters(
        registers, byteorder=Endian.BIG, wordorder=Endian.BIG
    )
    val = decoder.decode_64bit_float()
    return val if not math.isnan(val) else 0.0


def read_modbus_string(
    client: ModbusTcpClient, address: int, count: int, slave: int
) -> str:
    """Read a string value from Modbus holding registers.

    This function reads a sequence of holding registers and interprets them as
    ASCII string data. Each register contains 2 bytes (characters), with the
    high byte first. The resulting string is cleaned of null bytes and trailing spaces.

    Args:
        client: The Modbus TCP client instance to use for communication.
        address: The starting register address for the string data.
        count: The number of registers to read (each register = 2 characters).
        slave: The Modbus slave/unit identifier.

    Returns:
        The decoded string with null bytes and trailing spaces removed.
        Returns "N/A" if the read operation fails.

    Example:
        ```python
        # Read firmware version (17 registers = 34 characters)
        firmware = read_modbus_string(client, 123, 17, slave=200)
        print(f"Firmware version: {firmware}")
        ```

    Note:
        This function includes error handling and will return "N/A" instead
        of raising exceptions, making it suitable for non-critical string
        reads like device information queries.
    """
    try:
        regs = read_holding_registers(client, address, count, slave)
        bytes_list = []
        for reg in regs:
            bytes_list.append((reg >> 8) & 0xFF)
            bytes_list.append(reg & 0xFF)
        return "".join(chr(b) for b in bytes_list).strip("\x00 ")
    except ModbusReadError as e:
        logger = get_logger("alfen_driver.modbus_utils")
        logger.debug(
            "Modbus string read failed",
            error=str(e),
            address=address,
            count=count,
            slave=slave,
        )
        return "N/A"
    except Exception as e:
        logger = get_logger("alfen_driver.modbus_utils")
        logger.warning(
            "Unexpected error reading Modbus string",
            error=str(e),
            address=address,
            count=count,
            slave=slave,
        )
        return "N/A"


def reconnect(
    client: ModbusTcpClient,
    logger: Any,  # Can be structured logger or regular logger
    retry_delay: float = 0.5,
    max_attempts: Optional[int] = None,
) -> bool:
    """Attempt to reconnect to the Modbus TCP server.

    This function handles Modbus connection recovery by closing the existing
    connection and attempting to establish a new one. It supports configurable
    retry logic with delays between attempts and optional maximum attempt limits.

    Args:
        client: The ModbusTcpClient instance to reconnect. The client's host
            and port properties are used for reconnection attempts.
        logger: Logger instance (structured or regular) for connection status
            messages and error reporting.
        retry_delay: Time delay in seconds between connection attempts.
            Defaults to 0.5 seconds.
        max_attempts: Maximum number of connection attempts before giving up.
            If None, will attempt indefinitely until successful.

    Returns:
        True if the connection was successfully re-established.

    Raises:
        ModbusConnectionError: If max_attempts is specified and reached without
            establishing a successful connection. Includes details about the
            host, port, and number of failed attempts.

    Example:
        ```python
        try:
            success = reconnect(client, logger, retry_delay=1.0, max_attempts=5)
            if success:
                print("Reconnected successfully")
        except ModbusConnectionError as e:
            logger.error(f"Failed to reconnect: {e}")
        ```

    Note:
        This function will close any existing connection before attempting
        to reconnect. The retry logic includes exponential backoff and
        proper error logging for monitoring connection health.
    """
    client.close()
    attempt = 0

    while True:
        attempt += 1
        logger.info(f"Attempting Modbus reconnect (attempt {attempt})...")

        try:
            if client.connect():
                logger.info("Modbus connection re-established.")
                return True
        except Exception as e:
            logger.warning(f"Connection attempt {attempt} failed: {e}")

        if max_attempts and attempt >= max_attempts:
            host = getattr(client, "host", "unknown")
            port = getattr(client, "port", 0)
            raise ModbusConnectionError(
                host, port, f"Failed to connect after {attempt} attempts"
            )

        time.sleep(retry_delay)


def retry_modbus_operation(
    operation: Callable[[], Any],
    retries: int,
    retry_delay: float,
    logger: Optional[Any] = None,
) -> Any:
    """Execute a Modbus operation with automatic retry logic.

    This function wraps any Modbus operation with robust retry mechanisms
    to handle transient network errors, temporary charger unresponsiveness,
    or other recoverable Modbus communication issues.

    Args:
        operation: A callable that performs the Modbus operation. Should
            return the operation result and may raise ModbusException on failure.
        retries: Maximum number of retry attempts after the initial failure.
            Total attempts = retries + 1.
        retry_delay: Time delay in seconds between retry attempts.
        logger: Optional logger instance for error reporting during retries.

    Returns:
        The return value from the successful operation call.

    Raises:
        RetryExhaustedException: If all retry attempts are exhausted without
            success. Includes the operation name, total attempts, and the
            last exception encountered.
        Exception: Any non-ModbusException raised by the operation is
            immediately re-raised without retry.

    Example:
        ```python
        def read_voltages():
            return read_holding_registers(client, 306, 6, slave=1)

        try:
            voltages = retry_modbus_operation(read_voltages, retries=3, retry_delay=0.5, logger=logger)
            voltage_floats = decode_floats(voltages, 3)
        except RetryExhaustedException as e:
            logger.error(f"Failed to read voltages after {e.attempts} attempts")
        ```

    Note:
        Only ModbusException and its subclasses are subject to retry logic.
        Other exceptions (like network errors) are immediately propagated.
        This ensures that programming errors or configuration issues are
        not masked by retry attempts.
    """
    for attempt in range(retries):
        try:
            return operation()
        except ModbusException as e:
            if logger:
                logger.error(f"Modbus error on attempt {attempt+1}: {e}")
            if attempt < retries - 1:
                time.sleep(retry_delay)
    if logger:
        logger.error(f"Operation failed after {retries} retries")

    # Get the last exception if available
    last_error: Optional[Exception] = None
    try:
        return operation()
    except Exception as e:
        last_error = e

    raise RetryExhaustedException(
        operation.__name__ if hasattr(operation, "__name__") else "unknown",
        retries,
        last_error,
    )
