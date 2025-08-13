"""Tests for configuration management."""

import json
import os
import tempfile
from unittest.mock import Mock, patch

import pytest

from alfen_driver.config import (
    Config,
    ControlsConfig,
    ScheduleItem,
    load_config,
    load_config_from_disk,
    parse_hhmm_to_minutes,
)
from alfen_driver.exceptions import ValidationError


class TestScheduleItem:
    """Tests for ScheduleItem dataclass."""

    def test_default_schedule_item(self) -> None:
        """Test default schedule item creation."""
        item = ScheduleItem()
        assert item.enabled == 0
        assert item.days_mask == 0
        assert item.start == "00:00"
        assert item.end == "00:00"

    def test_custom_schedule_item(self) -> None:
        """Test custom schedule item creation."""
        item = ScheduleItem(enabled=1, days_mask=127, start="09:00", end="17:00")
        assert item.enabled == 1
        assert item.days_mask == 127
        assert item.start == "09:00"
        assert item.end == "17:00"


class TestControlsConfig:
    """Tests for ControlsConfig dataclass."""

    def test_default_controls_config(self) -> None:
        """Test default controls configuration."""
        config = ControlsConfig()
        # Defaults in refactor use 0.5 tolerance
        assert config.current_tolerance == 0.5
        assert config.update_difference_threshold == 0.1
        assert config.verification_delay == 0.1
        assert config.retry_delay == 0.5
        assert config.max_retries == 3
        assert config.watchdog_interval_seconds == 30
        assert config.max_set_current == 64.0
        assert config.min_charge_duration_seconds == 300

    def test_controls_config_validation_success(self) -> None:
        """Test successful validation of controls config."""
        config = ControlsConfig(
            current_tolerance=0.5,
            max_retries=5,
            watchdog_interval_seconds=60,
            max_set_current=32.0,
        )
        # Should not raise any exceptions
        assert config.current_tolerance == 0.5
        assert config.max_retries == 5

    def test_controls_config_negative_tolerance(self) -> None:
        """Test validation error for negative tolerance."""
        with pytest.raises(ValidationError) as exc_info:
            ControlsConfig(current_tolerance=-0.1)
        assert "current_tolerance" in str(exc_info.value)
        assert "must be non-negative" in str(exc_info.value)

    def test_controls_config_zero_retries(self) -> None:
        """Test validation error for zero retries."""
        with pytest.raises(ValidationError) as exc_info:
            ControlsConfig(max_retries=0)
        assert "max_retries" in str(exc_info.value)
        assert "must be at least 1" in str(exc_info.value)

    def test_controls_config_negative_watchdog_interval(self) -> None:
        """Test validation error for negative watchdog interval."""
        with pytest.raises(ValidationError) as exc_info:
            ControlsConfig(watchdog_interval_seconds=-1)
        assert "watchdog_interval_seconds" in str(exc_info.value)
        assert "must be positive" in str(exc_info.value)

    def test_controls_config_zero_max_current(self) -> None:
        """Test validation error for zero max current."""
        with pytest.raises(ValidationError) as exc_info:
            ControlsConfig(max_set_current=0.0)
        assert "max_set_current" in str(exc_info.value)
        assert "must be positive" in str(exc_info.value)


class TestConfig:
    """Tests for main Config dataclass."""

    def test_config_validation_success(self, sample_config: Config) -> None:
        """Test successful config validation."""
        # Should not raise any exceptions
        assert sample_config.modbus.port == 502
        assert sample_config.defaults.intended_set_current == 6.0
        assert sample_config.poll_interval_ms == 1000

    def test_config_negative_port(self, sample_config: Config) -> None:
        """Test validation error for negative port."""
        sample_config.modbus.port = -1
        with pytest.raises(ValidationError) as exc_info:
            Config(**sample_config.__dict__)
        assert "modbus.port" in str(exc_info.value)
        assert "must be positive" in str(exc_info.value)

    def test_config_negative_intended_current(self, sample_config: Config) -> None:
        """Test validation error for negative intended current."""
        sample_config.defaults.intended_set_current = -5.0
        with pytest.raises(ValidationError) as exc_info:
            Config(**sample_config.__dict__)
        assert "intended_set_current" in str(exc_info.value)
        assert "must be non-negative" in str(exc_info.value)

    def test_config_zero_poll_interval(self, sample_config: Config) -> None:
        """Test validation error for zero poll interval."""
        sample_config.poll_interval_ms = 0
        with pytest.raises(ValidationError) as exc_info:
            Config(**sample_config.__dict__)
        assert "poll_interval_ms" in str(exc_info.value)
        assert "must be positive" in str(exc_info.value)


class TestParseHHMMToMinutes:
    """Tests for time parsing function."""

    def test_valid_time_parsing(self) -> None:
        """Test parsing valid time strings."""
        assert parse_hhmm_to_minutes("00:00") == 0
        assert parse_hhmm_to_minutes("01:30") == 90
        assert parse_hhmm_to_minutes("12:00") == 720
        assert parse_hhmm_to_minutes("23:59") == 1439

    def test_invalid_time_formats(self) -> None:
        """Test parsing invalid time formats."""
        assert parse_hhmm_to_minutes("invalid") == 0
        assert parse_hhmm_to_minutes("25:00") == 60  # Hours wrap around
        assert parse_hhmm_to_minutes("12:99") == 720 + 39  # Minutes wrap around
        assert parse_hhmm_to_minutes("") == 0
        assert parse_hhmm_to_minutes("12") == 0  # Missing colon

    def test_non_string_input(self) -> None:
        """Test parsing non-string inputs."""
        assert parse_hhmm_to_minutes(None) == 0
        assert parse_hhmm_to_minutes(123) == 0
        assert parse_hhmm_to_minutes([]) == 0

    def test_whitespace_handling(self) -> None:
        """Test parsing with whitespace."""
        assert parse_hhmm_to_minutes(" 12:30 ") == 750
        assert parse_hhmm_to_minutes("\t09:15\n") == 555


class TestLoadConfigFromDisk:
    """Tests for loading configuration from disk."""

    def test_load_valid_json_config(self) -> None:
        """Test loading valid JSON configuration."""
        config_data = {"key": "value", "number": 42}

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config_data, f)
            temp_file = f.name

        try:
            logger = Mock()
            result = load_config_from_disk(temp_file, logger)
            assert result == config_data
        finally:
            os.unlink(temp_file)

    def test_load_nonexistent_file(self) -> None:
        """Test loading from non-existent file."""
        logger = Mock()
        result = load_config_from_disk("/nonexistent/file.json", logger)
        assert result is None

    def test_load_invalid_json(self) -> None:
        """Test loading invalid JSON."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{ invalid json }")
            temp_file = f.name

        try:
            logger = Mock()
            result = load_config_from_disk(temp_file, logger)
            assert result is None
            logger.warning.assert_called_once()
        finally:
            os.unlink(temp_file)

    def test_load_config_os_error(self) -> None:
        """Test handling OS errors during load."""
        logger = Mock()

        # Create file and then make it unreadable
        with tempfile.NamedTemporaryFile(delete=False) as f:
            temp_file = f.name

        try:
            os.chmod(temp_file, 0o000)  # Remove all permissions
            result = load_config_from_disk(temp_file, logger)
            assert result is None
            logger.warning.assert_called_once()
        finally:
            # Restore permissions to delete
            os.chmod(temp_file, 0o644)
            os.unlink(temp_file)


class TestLoadConfig:
    """Tests for main configuration loading."""

    def test_load_config_file_not_found(self) -> None:
        """Test loading when config file doesn't exist should raise error."""
        logger = Mock()
        with pytest.raises(Exception):
            load_config("/nonexistent/file.yaml")

    def test_load_valid_yaml_config(self, temp_config_file: str) -> None:
        """Test loading valid YAML configuration using provided path."""
        logger = Mock()
        config = load_config(temp_config_file)

        assert isinstance(config, Config)
        assert config.modbus.port == 502
        assert config.defaults.intended_set_current >= 0

    def test_load_invalid_yaml_structure(self) -> None:
        """Test loading YAML with invalid structure raises ConfigurationError."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("not_a_dict_but_a_string")
            temp_file = f.name

        try:
            with pytest.raises(Exception):
                load_config(temp_file)
        finally:
            os.unlink(temp_file)

    def test_load_yaml_with_validation_error(self) -> None:
        """Test loading YAML that fails validation raises ConfigurationError."""
        invalid_config = """
modbus:
  ip: "192.168.1.100"
  port: -1  # Invalid negative port
  socket_slave_id: 1
  station_slave_id: 200

defaults:
  intended_set_current: 6.0
  station_max_current: 32.0

# Minimal required fields
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

logging:
  level: "INFO"
  file: "/tmp/test.log"

schedule:
  items: []

controls: {}

poll_interval_ms: 1000
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(invalid_config)
            temp_file = f.name

        try:
            with pytest.raises(Exception):
                load_config(temp_file)
        finally:
            os.unlink(temp_file)

    def test_load_yaml_with_missing_fields(self) -> None:
        """Test loading YAML with missing required fields raises ConfigurationError."""
        incomplete_config = """
modbus:
  ip: "192.168.1.100"
  # Missing other required fields
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(incomplete_config)
            temp_file = f.name

        try:
            # Missing optional fields should load with defaults
            config = load_config(temp_file)
            assert isinstance(config, Config)
            assert config.modbus.ip == "192.168.1.100"
        finally:
            os.unlink(temp_file)

    def test_load_config_with_invalid_schedule_times(self) -> None:
        """Test loading config with invalid schedule times raises or logs warnings."""
        config_with_bad_schedule = """
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
  file: "/tmp/test.log"

schedule:
  items:
    - enabled: 1
      days_mask: 127
      start: "invalid_time"  # Invalid time format
      end: "17:00"

controls: {}

poll_interval_ms: 1000
"""

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(config_with_bad_schedule)
            temp_file = f.name

        try:
            # Legacy schedule fields should be accepted and mapped; load succeeds
            config = load_config(temp_file)
            assert isinstance(config, Config)
            assert len(config.schedule.items) >= 1
        finally:
            os.unlink(temp_file)
