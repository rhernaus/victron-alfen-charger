"""Tests for business logic module."""

import time
from datetime import datetime
from unittest.mock import Mock, patch

import pytest
import pytz

from alfen_driver.config import ScheduleItem
from alfen_driver.dbus_utils import EVC_CHARGE, EVC_MODE, EVC_STATUS
from alfen_driver.exceptions import StatusMappingError
from alfen_driver.logic import (
    apply_mode_specific_status,
    clamp_value,
    compute_effective_current,
    is_within_any_schedule,
    map_alfen_status,
)


class TestClampValue:
    """Tests for clamp_value utility function."""

    def test_value_within_bounds(self) -> None:
        """Test value within bounds."""
        assert clamp_value(5.0, 0.0, 10.0) == 5.0
        assert clamp_value(0.0, 0.0, 10.0) == 0.0
        assert clamp_value(10.0, 0.0, 10.0) == 10.0

    def test_value_clamped_to_minimum(self) -> None:
        """Test value clamped to minimum."""
        assert clamp_value(-5.0, 0.0, 10.0) == 0.0
        assert clamp_value(-100.0, -50.0, 50.0) == -50.0

    def test_value_clamped_to_maximum(self) -> None:
        """Test value clamped to maximum."""
        assert clamp_value(15.0, 0.0, 10.0) == 10.0
        assert clamp_value(100.0, -50.0, 50.0) == 50.0


class TestIsWithinAnySchedule:
    """Tests for schedule checking function."""

    def test_within_single_schedule(self) -> None:
        """Test time within a single schedule."""
        schedules = [
            ScheduleItem(
                enabled=1, days_mask=127, start="09:00", end="17:00"
            ),  # All days, 9AM-5PM
        ]

        # Create a time that should be within schedule (Tuesday 12:00)
        test_time = datetime(2023, 1, 3, 12, 0, 0, tzinfo=pytz.UTC)
        test_timestamp = test_time.timestamp()

        result = is_within_any_schedule(schedules, test_timestamp, "UTC")
        assert result is True

    def test_outside_single_schedule_time(self) -> None:
        """Test time outside schedule hours."""
        schedules = [
            ScheduleItem(
                enabled=1, days_mask=127, start="09:00", end="17:00"
            ),  # All days, 9AM-5PM
        ]

        # Create a time outside schedule (Tuesday 20:00)
        test_time = datetime(2023, 1, 3, 20, 0, 0, tzinfo=pytz.UTC)
        test_timestamp = test_time.timestamp()

        result = is_within_any_schedule(schedules, test_timestamp, "UTC")
        assert result is False

    def test_outside_single_schedule_day(self) -> None:
        """Test day outside schedule mask."""
        schedules = [
            ScheduleItem(
                enabled=1, days_mask=0b0111110, start="09:00", end="17:00"
            ),  # Mon-Fri only
        ]

        # Create a time on Saturday (which should be outside)
        test_time = datetime(2023, 1, 7, 12, 0, 0, tzinfo=pytz.UTC)  # Saturday
        test_timestamp = test_time.timestamp()

        result = is_within_any_schedule(schedules, test_timestamp, "UTC")
        assert result is False

    def test_disabled_schedule(self) -> None:
        """Test disabled schedule is ignored."""
        schedules = [
            ScheduleItem(
                enabled=0, days_mask=127, start="00:00", end="23:59"
            ),  # Disabled
        ]

        # Even though time would be within range, schedule is disabled
        test_time = datetime(2023, 1, 3, 12, 0, 0, tzinfo=pytz.UTC)
        test_timestamp = test_time.timestamp()

        result = is_within_any_schedule(schedules, test_timestamp, "UTC")
        assert result is False

    def test_multiple_schedules_any_match(self) -> None:
        """Test that any matching schedule returns True."""
        schedules = [
            ScheduleItem(
                enabled=1, days_mask=0b0111110, start="09:00", end="17:00"
            ),  # Mon-Fri
            ScheduleItem(
                enabled=1, days_mask=0b1000001, start="10:00", end="14:00"
            ),  # Sat-Sun
        ]

        # Test Saturday time that matches second schedule
        test_time = datetime(2023, 1, 7, 12, 0, 0, tzinfo=pytz.UTC)  # Saturday 12:00
        test_timestamp = test_time.timestamp()

        result = is_within_any_schedule(schedules, test_timestamp, "UTC")
        assert result is True

    def test_empty_schedules(self) -> None:
        """Test empty schedules list."""
        schedules = []

        test_time = datetime(2023, 1, 3, 12, 0, 0, tzinfo=pytz.UTC)
        test_timestamp = test_time.timestamp()

        result = is_within_any_schedule(schedules, test_timestamp, "UTC")
        assert result is False

    def test_timezone_handling(self) -> None:
        """Test timezone handling in schedules."""
        schedules = [
            ScheduleItem(enabled=1, days_mask=127, start="09:00", end="17:00"),
        ]

        # Create UTC time that would be 12:00 in Eastern time
        utc_time = datetime(
            2023, 1, 3, 17, 0, 0, tzinfo=pytz.UTC
        )  # 17:00 UTC = 12:00 EST
        test_timestamp = utc_time.timestamp()

        # Should be within schedule when interpreted in Eastern time
        result = is_within_any_schedule(schedules, test_timestamp, "America/New_York")
        assert result is True

        # Should be outside schedule when interpreted in UTC
        result = is_within_any_schedule(schedules, test_timestamp, "UTC")
        assert result is False


class TestComputeEffectiveCurrent:
    """Tests for compute_effective_current function."""

    def test_manual_mode_enabled(self, sample_config) -> None:
        """Test manual mode with charging enabled."""
        schedules = []

        current, explanation = compute_effective_current(
            EVC_MODE.MANUAL,
            EVC_CHARGE.ENABLED,
            10.0,  # intended_set_current
            32.0,  # station_max_current
            time.time(),
            schedules,
            0.0,  # ev_power
            "UTC",
            charging_start_time=0.0,
            min_charge_duration_seconds=300,
        )

        assert current == 10.0
        assert "MANUAL" in explanation

    def test_manual_mode_disabled(self, sample_config) -> None:
        """Test manual mode with charging disabled."""
        schedules = []

        current, explanation = compute_effective_current(
            EVC_MODE.MANUAL,
            EVC_CHARGE.DISABLED,
            10.0,
            32.0,
            time.time(),
            schedules,
            0.0,
            "UTC",
            charging_start_time=0.0,
            min_charge_duration_seconds=300,
        )

        assert current == 0.0
        assert "MANUAL" in explanation
        assert "disabled" in explanation.lower()

    def test_manual_mode_clamping(self, sample_config) -> None:
        """Test manual mode with current clamping."""
        schedules = []

        current, explanation = compute_effective_current(
            EVC_MODE.MANUAL,
            EVC_CHARGE.ENABLED,
            50.0,  # Higher than station max
            32.0,  # station_max_current
            time.time(),
            schedules,
            0.0,
            "UTC",
            charging_start_time=0.0,
            min_charge_duration_seconds=300,
        )

        assert current == 32.0  # Should be clamped to station max
        assert "clamped" in explanation.lower()

    def test_auto_mode_with_excess_power(self, sample_config) -> None:
        """Test auto mode with sufficient excess power."""
        schedules = []

        with patch("alfen_driver.logic.try_solar_current_calculation") as mock_solar:
            mock_solar.return_value = (12.0, 3, "Solar calculation: 12.0A")

            current, explanation = compute_effective_current(
                EVC_MODE.AUTO,
                EVC_CHARGE.ENABLED,
                10.0,
                32.0,
                time.time(),
                schedules,
                0.0,
                "UTC",
                charging_start_time=0.0,
                min_charge_duration_seconds=300,
            )

            assert current == 12.0
            assert "Solar" in explanation

    def test_scheduled_mode_within_schedule(self, sample_config) -> None:
        """Test scheduled mode when within active schedule."""
        schedules = [
            ScheduleItem(enabled=1, days_mask=127, start="00:00", end="23:59"),
        ]

        with patch("alfen_driver.logic.is_within_any_schedule", return_value=True):
            current, explanation = compute_effective_current(
                EVC_MODE.SCHEDULED,
                EVC_CHARGE.ENABLED,
                15.0,
                32.0,
                time.time(),
                schedules,
                0.0,
                "UTC",
                charging_start_time=0.0,
                min_charge_duration_seconds=300,
            )

            assert current == 15.0
            assert "Scheduled" in explanation
            assert "within schedule" in explanation.lower()

    def test_scheduled_mode_outside_schedule(self, sample_config) -> None:
        """Test scheduled mode when outside schedule."""
        schedules = [
            ScheduleItem(enabled=1, days_mask=127, start="09:00", end="17:00"),
        ]

        with patch("alfen_driver.logic.is_within_any_schedule", return_value=False):
            current, explanation = compute_effective_current(
                EVC_MODE.SCHEDULED,
                EVC_CHARGE.ENABLED,
                15.0,
                32.0,
                time.time(),
                schedules,
                0.0,
                "UTC",
                charging_start_time=0.0,
                min_charge_duration_seconds=300,
            )

            assert current == 0.0
            assert "Scheduled" in explanation
            assert "outside schedule" in explanation.lower()


class TestMapAlfenStatus:
    """Tests for map_alfen_status function."""

    def test_charging_status_c2(self, mock_modbus_client, sample_config) -> None:
        """Test mapping of C2 (charging) status."""
        with patch("alfen_driver.logic.read_holding_registers") as mock_read:
            # Setup registers to represent "C2" string
            mock_read.return_value = [
                0x4332,
                0x0000,
                0x0000,
                0x0000,
                0x0000,
            ]  # "C2" + padding

            status = map_alfen_status(mock_modbus_client, sample_config)

            assert status == 2  # Charging

    def test_charging_status_d2(self, mock_modbus_client, sample_config) -> None:
        """Test mapping of D2 (charging) status."""
        with patch("alfen_driver.logic.read_holding_registers") as mock_read:
            mock_read.return_value = [0x4432, 0x0000, 0x0000, 0x0000, 0x0000]  # "D2"

            status = map_alfen_status(mock_modbus_client, sample_config)

            assert status == 2  # Charging

    def test_connected_status_b1(self, mock_modbus_client, sample_config) -> None:
        """Test mapping of B1 (connected) status."""
        with patch("alfen_driver.logic.read_holding_registers") as mock_read:
            mock_read.return_value = [0x4231, 0x0000, 0x0000, 0x0000, 0x0000]  # "B1"

            status = map_alfen_status(mock_modbus_client, sample_config)

            assert status == 1  # Connected

    def test_connected_status_c1(self, mock_modbus_client, sample_config) -> None:
        """Test mapping of C1 (connected) status."""
        with patch("alfen_driver.logic.read_holding_registers") as mock_read:
            mock_read.return_value = [0x4331, 0x0000, 0x0000, 0x0000, 0x0000]  # "C1"

            status = map_alfen_status(mock_modbus_client, sample_config)

            assert status == 1  # Connected

    def test_disconnected_status_a1(self, mock_modbus_client, sample_config) -> None:
        """Test mapping of A1 (disconnected) status."""
        with patch("alfen_driver.logic.read_holding_registers") as mock_read:
            mock_read.return_value = [0x4131, 0x0000, 0x0000, 0x0000, 0x0000]  # "A1"

            status = map_alfen_status(mock_modbus_client, sample_config)

            assert status == 0  # Disconnected

    def test_unknown_status(self, mock_modbus_client, sample_config) -> None:
        """Test mapping of unknown status."""
        with patch("alfen_driver.logic.read_holding_registers") as mock_read:
            mock_read.return_value = [0x5858, 0x0000, 0x0000, 0x0000, 0x0000]  # "XX"

            with patch("logging.getLogger") as mock_logger_get:
                mock_logger = Mock()
                mock_logger_get.return_value = mock_logger

                status = map_alfen_status(mock_modbus_client, sample_config)

            assert status == 0  # Default to disconnected
            mock_logger.warning.assert_called_once()

    def test_empty_status_string(self, mock_modbus_client, sample_config) -> None:
        """Test mapping of empty status string."""
        with patch("alfen_driver.logic.read_holding_registers") as mock_read:
            mock_read.return_value = [
                0x0000,
                0x0000,
                0x0000,
                0x0000,
                0x0000,
            ]  # All nulls

            with patch("logging.getLogger") as mock_logger_get:
                mock_logger = Mock()
                mock_logger_get.return_value = mock_logger

                status = map_alfen_status(mock_modbus_client, sample_config)

            assert status == 0  # Default to disconnected
            mock_logger.warning.assert_called_once()

    def test_status_read_exception(self, mock_modbus_client, sample_config) -> None:
        """Test handling of exception during status read."""
        with patch("alfen_driver.logic.read_holding_registers") as mock_read:
            mock_read.side_effect = Exception("Read failed")

            with pytest.raises(StatusMappingError) as exc_info:
                map_alfen_status(mock_modbus_client, sample_config)

            assert "Failed to read status registers" in str(exc_info.value)


class TestApplyModeSpecificStatus:
    """Tests for apply_mode_specific_status function."""

    def test_manual_mode_enabled_connected(self, sample_config) -> None:
        """Test manual mode, enabled, connected."""
        schedules = []

        result = apply_mode_specific_status(
            EVC_MODE.MANUAL,
            True,  # connected
            EVC_CHARGE.ENABLED,
            10.0,  # intended_set_current
            schedules,
            EVC_STATUS.CONNECTED.value,  # raw_status
            "UTC",
        )

        # Manual mode with enabled charging should allow natural status
        assert result == EVC_STATUS.CONNECTED.value

    def test_manual_mode_disabled(self, sample_config) -> None:
        """Test manual mode with charging disabled."""
        schedules = []

        result = apply_mode_specific_status(
            EVC_MODE.MANUAL,
            True,  # connected
            EVC_CHARGE.DISABLED,
            10.0,
            schedules,
            EVC_STATUS.CHARGING.value,  # Would be charging but disabled
            "UTC",
        )

        # Should override to wait_start when disabled
        assert result == EVC_STATUS.WAIT_START.value

    def test_auto_mode_no_excess_power(self, sample_config) -> None:
        """Test auto mode with no excess power."""
        schedules = []

        with patch("alfen_driver.logic.try_solar_current_calculation") as mock_solar:
            mock_solar.return_value = (0.0, 3, "No excess power")

            result = apply_mode_specific_status(
                EVC_MODE.AUTO,
                True,  # connected
                EVC_CHARGE.ENABLED,
                10.0,
                schedules,
                EVC_STATUS.CONNECTED.value,
                "UTC",
            )

            # Should show wait_sun when no solar power
            assert result == EVC_STATUS.WAIT_SUN.value

    def test_scheduled_mode_outside_schedule(self, sample_config) -> None:
        """Test scheduled mode outside active schedule."""
        schedules = [
            ScheduleItem(enabled=1, days_mask=127, start="09:00", end="17:00"),
        ]

        with patch("alfen_driver.logic.is_within_any_schedule", return_value=False):
            result = apply_mode_specific_status(
                EVC_MODE.SCHEDULED,
                True,  # connected
                EVC_CHARGE.ENABLED,
                10.0,
                schedules,
                EVC_STATUS.CONNECTED.value,
                "UTC",
            )

            # Should show wait_start when outside schedule
            assert result == EVC_STATUS.WAIT_START.value

    def test_disconnected_overrides_mode(self, sample_config) -> None:
        """Test that disconnected status overrides mode logic."""
        schedules = []

        result = apply_mode_specific_status(
            EVC_MODE.MANUAL,
            False,  # Not connected
            EVC_CHARGE.ENABLED,
            10.0,
            schedules,
            EVC_STATUS.DISCONNECTED.value,
            "UTC",
        )

        # Should remain disconnected regardless of mode
        assert result == EVC_STATUS.DISCONNECTED.value
