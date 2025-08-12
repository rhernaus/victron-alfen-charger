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
    ACTIVE_POWER_TOTAL = 344
    ACTIVE_POWER_L1 = 346
    ACTIVE_POWER_L2 = 348
    ACTIVE_POWER_L3 = 350
    METER_ACTIVE_ENERGY_TOTAL = 374

    # Station registers (slave ID 200)
    STATUS = 1201
    SET_CURRENT = 1210
    ACTUAL_CURRENT = 1211
    MODBUS_MAX_CURRENT = 1212
    ACTIVE_MAX_CURRENT = 1213
    ACTIVE_PHASES = 1215
    REAL_STATE = 1220
    ERROR = 1221
    STATION_ACTIVE_MAX_CURRENT = 1100

    # Version and serial registers
    VERSION_MAJOR = 1301
    VERSION_MINOR = 1302
    VERSION_PATCH = 1303
    BOOTLOADER_VERSION = 1304
    SERIAL_NUMBER_START = 1500
    SERIAL_NUMBER_LENGTH = 10

    # Manufacturer string registers
    MANUFACTURER_START = 2000
    MANUFACTURER_LENGTH = 8


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
