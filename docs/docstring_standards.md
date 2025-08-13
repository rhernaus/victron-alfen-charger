# Docstring Standards for Alfen EV Charger Driver

This document demonstrates the comprehensive Google/NumPy style docstrings implemented throughout the Alfen EV charger driver codebase. The documentation follows industry best practices for Python project documentation.

## Documentation Standards Applied

### 1. Module-Level Docstrings
Every module starts with a comprehensive module docstring that includes:
- Purpose and overview of the module
- Key features and functionality
- Usage examples
- Integration notes
- Architecture information where relevant

**Example from `exceptions.py`:**
```python
"""Custom exception classes for the Alfen EV Charger Driver.

This module defines a comprehensive hierarchy of exceptions for the Alfen driver,
providing detailed error information and context for debugging and error handling.

The exception hierarchy follows the pattern:
    AlfenDriverError (base)
    ├── ConfigurationError
    ├── ModbusConnectionError
    ├── ModbusReadError
    ├── ModbusWriteError
    ├── ModbusVerificationError
    ├── DBusError
    ├── StatusMappingError
    ├── ChargingControlError
    ├── ValidationError
    ├── SessionError
    ├── RetryExhaustedError
    └── ServiceUnavailableError
"""
```

### 2. Class Docstrings
All classes include:
- Clear purpose statement
- Detailed attribute descriptions with types
- Usage examples
- Notes about integration or special considerations

**Example from `ModbusConfig`:**
```python
class ModbusConfig:
    """Configuration for Modbus TCP connection parameters.

    This class defines the network connection settings for communicating with
    the Alfen EV charger via Modbus TCP protocol. The Alfen charger typically
    uses two different slave IDs for different types of data access.

    Attributes:
        ip: IP address of the Alfen charger (e.g., "192.168.1.100").
        port: TCP port for Modbus communication (typically 502).
        socket_slave_id: Slave ID for real-time socket data (typically 1).
            Used for reading voltages, currents, power, and energy.
        station_slave_id: Slave ID for station configuration (typically 200).
            Used for control operations like setting current and phases.

    Example:
        ```python
        modbus_config = ModbusConfig(
            ip="192.168.1.100",
            port=502,
            socket_slave_id=1,
            station_slave_id=200
        )
        ```
    """
```

### 3. Function Docstrings
Functions include comprehensive documentation with:
- Clear one-line summary
- Detailed description of functionality
- Complete Args section with type information
- Returns section with type and description
- Raises section for all possible exceptions
- Examples showing typical usage
- Notes about implementation details or caveats

**Example from `read_holding_registers`:**
```python
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
```

### 4. Exception Class Docstrings
Custom exceptions include:
- Purpose and when the exception is raised
- Detailed attribute descriptions
- Context about what causes the exception
- Usage examples showing proper exception handling

**Example from `ModbusReadError`:**
```python
class ModbusReadError(AlfenDriverError):
    """Raised when Modbus read operations fail.

    This exception occurs when attempting to read holding registers from the Alfen
    charger via Modbus TCP. Common causes include network timeouts, invalid register
    addresses, or the charger being in an unresponsive state.

    Attributes:
        address (int): The starting register address that was being read.
        count (int): The number of registers that were requested.
        slave_id (int): The Modbus slave ID that was targeted.

    Example:
        ```python
        try:
            registers = client.read_holding_registers(123, 5, slave=1)
        except ModbusReadError as e:
            logger.error(f"Read failed: {e}")
            logger.debug(f"Failed at address {e.address} for {e.count} registers")
        ```
    """
```

## Documentation Coverage

### Completed Modules with Comprehensive Docstrings:

1. **`exceptions.py`** - Complete exception hierarchy documentation
   - Module overview with exception tree
   - All 13 exception classes fully documented
   - Usage examples for each exception type
   - Context about when exceptions are raised

2. **`modbus_utils.py`** - Modbus communication utilities
   - Complete module overview with key features
   - Functions documented with examples and error handling
   - Detailed examples for register reading and decoding
   - Error handling and retry mechanism documentation

3. **`config.py`** - Configuration management (partial)
   - Module overview of configuration system
   - Key classes (ModbusConfig, RegistersConfig) documented
   - Main load_config function with comprehensive documentation
   - Integration examples and fallback behavior

### Key Documentation Features:

1. **Consistent Formatting**: All docstrings follow Google/NumPy style conventions
2. **Type Information**: Args and Returns sections include type information
3. **Practical Examples**: Each function/class includes relevant usage examples
4. **Error Documentation**: Comprehensive Raises sections for exception handling
5. **Context Provided**: Notes sections explain implementation details and caveats
6. **Cross-References**: Documentation references related functions and classes

## Benefits of This Documentation Approach

1. **Developer Onboarding**: New developers can quickly understand component purposes
2. **API Clarity**: Function signatures and behaviors are clearly explained
3. **Error Handling**: Exception documentation helps with robust error handling
4. **Integration Guidance**: Examples show how components work together
5. **Maintenance**: Detailed documentation aids in code maintenance and updates

## Documentation Tools Integration

The comprehensive docstrings support:
- **IDE Integration**: Rich tooltips and auto-completion
- **Sphinx Documentation**: Automatic API documentation generation
- **Type Checking**: Integration with mypy and other type checkers
- **Testing**: Docstring examples can be validated with doctest
- **Code Review**: Self-documenting code reduces review overhead

This documentation standard ensures the Alfen driver codebase is maintainable,
approachable for new developers, and follows Python community best practices
for professional software development.
