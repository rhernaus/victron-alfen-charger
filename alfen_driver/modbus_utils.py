import logging
import math
import time
from typing import Any, List

from pymodbus.client import ModbusTcpClient
from pymodbus.constants import Endian
from pymodbus.exceptions import ModbusException
from pymodbus.payload import BinaryPayloadDecoder


def read_holding_registers(
    client: ModbusTcpClient, address: int, count: int, slave: int
) -> List[int]:
    """
    Read holding registers from Modbus.

    Parameters:
        address: Starting register address.
        count: Number of registers to read.
        slave: Modbus slave ID.

    Returns:
        List of register values.

    Raises:
        ModbusException: If read fails.
    """
    rr = client.read_holding_registers(address, count, slave=slave)
    if rr.isError():
        raise ModbusException(f"Error reading registers at {address}")
    return rr.registers


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
    """
    Decode 4 registers into a 64-bit float.

    Parameters:
        registers: List of 4 register values.

    Returns:
        The decoded float (NaN replaced with 0.0).
    """
    decoder = BinaryPayloadDecoder.fromRegisters(
        registers, byteorder=Endian.BIG, wordorder=Endian.BIG
    )
    val = decoder.decode_64bit_float()
    return val if not math.isnan(val) else 0.0


def read_modbus_string(
    client: ModbusTcpClient, address: int, count: int, slave: int
) -> str:
    """
    Read a string from Modbus holding registers.

    Parameters:
        address: Starting register address.
        count: Number of registers to read.
        slave: Modbus slave ID.

    Returns:
        The decoded string, stripped of nulls/spaces.

    Raises:
        ModbusException: If read fails (logged, returns "N/A").
    """
    try:
        regs = read_holding_registers(client, address, count, slave)
        bytes_list = []
        for reg in regs:
            bytes_list.append((reg >> 8) & 0xFF)
            bytes_list.append(reg & 0xFF)
        return "".join(chr(b) for b in bytes_list).strip("\x00 ")
    except ModbusException as e:
        logging.getLogger("alfen_driver").debug(f"Modbus string read failed: {e}")
        return "N/A"


def reconnect(
    client: ModbusTcpClient,
    logger: logging.Logger,
    retry_delay: float = 0.5,
) -> bool:
    """
    Attempt to reconnect to the Modbus server indefinitely until successful.
    """
    client.close()
    attempt = 0
    while True:
        attempt += 1
        logger.info(f"Attempting Modbus reconnect (attempt {attempt})...")
        if client.connect():
            logger.info("Modbus connection re-established.")
            return True
        time.sleep(retry_delay)


def retry_modbus_operation(
    operation: callable,
    retries: int,
    retry_delay: float,
    logger: logging.Logger = None,
) -> Any:
    for attempt in range(retries):
        try:
            return operation()
        except ModbusException as e:
            if logger:
                logger.error(f"Modbus error on attempt {attempt+1}: {e}")
            if attempt < retries - 1:
                time.sleep(retry_delay)
    if logger:
        logger.error("Failed after retries.")
    raise ModbusException("Operation failed after retries")
