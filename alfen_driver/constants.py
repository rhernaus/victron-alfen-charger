"""Centralized constants for the Alfen driver."""


class ModbusRegisters:
    """Modbus register addresses."""

    # Socket registers (slave ID 1)
    VOLTAGES_L1 = 306
    VOLTAGES_L2 = 308
    VOLTAGES_L3 = 310
    CURRENTS_L1 = 320
    CURRENTS_L2 = 322
    CURRENTS_L3 = 324
    ACTIVE_POWER_TOTAL = 338  # Start from Real Power L1
    METER_ACTIVE_ENERGY_TOTAL = 374

    # Station registers (slave ID 200)
    STATION_ACTIVE_MAX_CURRENT = 1100  # 2 registers, FLOAT32
    STATION_TEMPERATURE = 1102  # 2 registers, FLOAT32
    STATION_OCPP_STATE = 1104  # 1 register, UNSIGNED16
    STATION_NR_OF_SOCKETS = 1105  # 1 register, UNSIGNED16

    # Product identification registers (slave ID 200)
    PRODUCT_NAME_START = 100  # 17 registers, STRING "ALF_1000"
    PRODUCT_NAME_LENGTH = 17
    MANUFACTURER_START = 117  # 5 registers, STRING "Alfen NV"
    MANUFACTURER_LENGTH = 5
    MODBUS_TABLE_VERSION = 122  # 1 register, SIGNED16
    FIRMWARE_VERSION_START = 123  # 17 registers, STRING
    FIRMWARE_VERSION_LENGTH = 17
    PLATFORM_TYPE_START = 140  # 17 registers, STRING "NG910"
    PLATFORM_TYPE_LENGTH = 17
    SERIAL_NUMBER_START = 157  # 11 registers, STRING
    SERIAL_NUMBER_LENGTH = 11

    # Socket registers (slave ID 1 or 2)
    SOCKET_AVAILABILITY = 1200  # 1 register, 1=operative, 0=inoperative
    SOCKET_MODE3_STATE = (
        1201  # 5 registers, STRING - "A", "B1", "B2", "C1", "C2", "D1", "D2", "E", "F"
    )
    SOCKET_MAX_CURRENT = 1206  # 2 registers, FLOAT32
    SOCKET_VALID_TIME = 1208  # 2 registers, UNSIGNED32
    SOCKET_MODBUS_MAX_CURRENT = 1210  # 2 registers, FLOAT32 (R/W)
    SOCKET_SAFE_CURRENT = 1212  # 2 registers, FLOAT32
    SOCKET_SETPOINT_ACCOUNTED = 1214  # 1 register, 1=Yes, 0=No
    SOCKET_PHASES = 1215  # 1 register (R/W), 1=1phase, 3=3phase
    ACTIVE_PHASES = 1215  # Alias for SOCKET_PHASES


class ChargingLimits:
    """Charging current and voltage limits."""

    MIN_CURRENT = 6.0
    MAX_CURRENT = 64.0
    DEFAULT_CURRENT = 6.0
    NOMINAL_VOLTAGE = 230.0


class PollingIntervals:
    """Polling intervals in milliseconds."""

    DEFAULT = 1000
    ACTIVE_MIN = 500
    ACTIVE_MAX = 2000
    IDLE_MIN = 2000
    IDLE_MAX = 10000


class TimeoutDefaults:
    """Timeout defaults in seconds."""

    MODBUS_CONNECTION = 10.0
    MODBUS_OPERATION = 5.0
    CURRENT_VERIFICATION_DELAY = 2.0


class RetryDefaults:
    """Retry configuration defaults."""

    MAX_ATTEMPTS = 3
    DELAY_SECONDS = 1.0
    CURRENT_UPDATE_MAX_ATTEMPTS = 10


class SessionDefaults:
    """Session tracking defaults."""

    ENERGY_THRESHOLD_KWH = 0.01
    SESSION_END_DELAY_SECONDS = 30
