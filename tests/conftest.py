"""Pytest configuration and shared fixtures."""

import logging
import os
import sys
import tempfile
from typing import Generator
from unittest.mock import MagicMock, Mock

import pytest
from pymodbus.client import ModbusTcpClient

# Mock dbus and other system dependencies that aren't available in test environment
sys.modules["dbus"] = MagicMock()
sys.modules["dbus.mainloop"] = MagicMock()
sys.modules["dbus.mainloop.glib"] = MagicMock()
sys.modules["gi"] = MagicMock()
sys.modules["gi.repository"] = MagicMock()
sys.modules["gi.repository.GLib"] = MagicMock()
sys.modules["vedbus"] = MagicMock()

from alfen_driver.config import (  # noqa: E402
    Config,
    ControlsConfig,
    DefaultsConfig,
    LoggingConfig,
    ModbusConfig,
    RegistersConfig,
    ScheduleConfig,
    ScheduleItem,
)

# Disable logging during tests unless explicitly enabled
logging.disable(logging.CRITICAL)


@pytest.fixture
def mock_modbus_client() -> Mock:
    """Mock Modbus client for testing."""
    client = Mock(spec=ModbusTcpClient)
    client.host = "test_host"
    client.port = 502
    client.is_socket_open.return_value = True
    client.connect.return_value = True
    client.close.return_value = None

    # Mock successful register response
    mock_response = Mock()
    mock_response.isError.return_value = False
    mock_response.registers = [0, 0, 0, 0, 0]
    client.read_holding_registers.return_value = mock_response

    # Mock successful write response
    write_response = Mock()
    write_response.isError.return_value = False
    client.write_registers.return_value = write_response

    return client


@pytest.fixture
def sample_config() -> Config:
    """Sample configuration for testing."""
    return Config(
        modbus=ModbusConfig(
            ip="192.168.1.100", port=502, socket_slave_id=1, station_slave_id=200
        ),
        device_instance=0,
        registers=RegistersConfig(
            voltages=306,
            currents=320,
            power=344,
            energy=374,
            status=1201,
            amps_config=1210,
            phases=1215,
            firmware_version=123,
            firmware_version_count=17,
            station_serial=157,
            station_serial_count=11,
            manufacturer=117,
            manufacturer_count=5,
            platform_type=140,
            platform_type_count=17,
            station_max_current=1100,
        ),
        defaults=DefaultsConfig(intended_set_current=6.0, station_max_current=32.0),
        logging=LoggingConfig(level="INFO", file="/tmp/test_alfen_driver.log"),
        schedule=ScheduleConfig(
            items=[
                ScheduleItem(enabled=1, days_mask=127, start="09:00", end="17:00"),
                ScheduleItem(enabled=0, days_mask=0, start="00:00", end="00:00"),
                ScheduleItem(enabled=0, days_mask=0, start="00:00", end="00:00"),
            ]
        ),
        controls=ControlsConfig(
            current_tolerance=0.25,
            update_difference_threshold=0.1,
            verification_delay=0.1,
            retry_delay=0.5,
            max_retries=3,
            watchdog_interval_seconds=30,
            max_set_current=64.0,
            min_charge_duration_seconds=300,
        ),
        poll_interval_ms=1000,
        timezone="UTC",
    )


@pytest.fixture
def temp_config_file() -> Generator[str, None, None]:
    """Create a temporary configuration file."""
    config_content = """
modbus:
  ip: "192.168.1.100"
  port: 502
  socket_slave_id: 1
  station_slave_id: 200

device_instance: 0

registers:
  voltages: 306
  currents: 320
  power: 344
  energy: 374
  status: 1201
  amps_config: 1210
  phases: 1215
  firmware_version: 123
  firmware_version_count: 17
  station_serial: 157
  station_serial_count: 11
  manufacturer: 117
  manufacturer_count: 5
  platform_type: 140
  platform_type_count: 17
  station_max_current: 1100

defaults:
  intended_set_current: 6.0
  station_max_current: 32.0

logging:
  level: "INFO"
  file: "/tmp/test_alfen_driver.log"

schedule:
  items:
    - enabled: 1
      days_mask: 127
      start: "09:00"
      end: "17:00"

controls:
  current_tolerance: 0.25
  update_difference_threshold: 0.1
  verification_delay: 0.1
  retry_delay: 0.5
  max_retries: 3
  watchdog_interval_seconds: 30
  max_set_current: 64.0
  min_charge_duration_seconds: 300

poll_interval_ms: 1000
timezone: "UTC"
"""

    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
        f.write(config_content)
        temp_file = f.name

    yield temp_file

    # Cleanup
    if os.path.exists(temp_file):
        os.unlink(temp_file)


@pytest.fixture
def mock_dbus_service() -> Mock:
    """Mock D-Bus service for testing."""
    service = {}

    def setitem(key: str, value: object) -> None:
        service[key] = value

    def getitem(key: str) -> object:
        return service.get(key, 0)

    mock_service = Mock()
    mock_service.__setitem__ = Mock(side_effect=setitem)
    mock_service.__getitem__ = Mock(side_effect=getitem)
    mock_service.register = Mock()
    mock_service.add_path = Mock()

    return mock_service


@pytest.fixture
def mock_logger() -> Mock:
    """Mock logger for testing."""
    return Mock(spec=logging.Logger)


@pytest.fixture
def sample_register_data() -> dict[str, list[int]]:
    """Sample register data for testing."""
    return {
        "voltages": [0x4366, 0x0000, 0x4366, 0x0000, 0x4366, 0x0000],  # 230V floats
        "currents": [0x4140, 0x0000, 0x4120, 0x0000, 0x4100, 0x0000],  # 12A, 10A, 8A
        "power": [0x4461, 0x8000],  # 3600W
        "phases": [3],  # 3-phase
        "status_registers": [0x4332, 0x0000, 0x0000, 0x0000, 0x0000],  # "C2" status
    }


@pytest.fixture(autouse=True)
def cleanup_logging() -> Generator[None, None, None]:
    """Ensure clean logging state for each test."""
    yield
    # Reset logging
    logging.disable(logging.NOTSET)
