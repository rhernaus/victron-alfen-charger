"""Charging session management for Alfen driver."""

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from .constants import SessionDefaults

logger = logging.getLogger(__name__)


class ChargingSession:
    """Represents a single charging session."""

    def __init__(self, start_energy_kwh: float):
        self.start_time = datetime.now()
        self.start_energy_kwh = start_energy_kwh
        self.end_time: Optional[datetime] = None
        self.end_energy_kwh: Optional[float] = None

    @property
    def duration_seconds(self) -> float:
        """Get session duration in seconds."""
        end = self.end_time or datetime.now()
        return (end - self.start_time).total_seconds()

    @property
    def energy_delivered_kwh(self) -> float:
        """Get energy delivered during session."""
        if self.end_energy_kwh is None:
            return 0.0
        return self.end_energy_kwh - self.start_energy_kwh

    def end(self, end_energy_kwh: float) -> None:
        """End the charging session."""
        self.end_time = datetime.now()
        self.end_energy_kwh = end_energy_kwh


class ChargingSessionManager:
    """Manages charging sessions and statistics."""

    def __init__(self) -> None:
        self.current_session: Optional[ChargingSession] = None
        self.last_session: Optional[ChargingSession] = None
        self.total_sessions = 0
        self.total_energy_kwh = 0.0
        self._last_power = 0.0
        self._last_energy_kwh = 0.0

    def update(self, power_w: float, total_energy_kwh: float) -> None:
        """Update session state based on power and energy readings."""
        # Consider charging if power > 100W (to avoid noise)
        charging = power_w > 100

        # Don't start a session on the very first update
        if self._last_energy_kwh == 0.0 and total_energy_kwh > 0:
            # First reading, just store the baseline
            self._last_energy_kwh = total_energy_kwh
            self._last_power = power_w
            return

        if charging and self.current_session is None:
            # Start new session only if energy has actually increased
            energy_delta = total_energy_kwh - self._last_energy_kwh
            if energy_delta > SessionDefaults.ENERGY_THRESHOLD_KWH:
                self._start_session(total_energy_kwh)

        elif not charging and self.current_session is not None:
            # End current session
            self._end_session(total_energy_kwh)

        self._last_power = power_w
        self._last_energy_kwh = total_energy_kwh

    def _start_session(self, start_energy_kwh: float) -> None:
        """Start a new charging session."""
        logger.info(f"Starting new charging session at {start_energy_kwh:.2f} kWh")
        self.current_session = ChargingSession(start_energy_kwh)
        self.total_sessions += 1

    def _end_session(self, end_energy_kwh: float) -> None:
        """End the current charging session."""
        if self.current_session is None:
            return

        self.current_session.end(end_energy_kwh)
        energy_delivered = self.current_session.energy_delivered_kwh
        duration = self.current_session.duration_seconds

        logger.info(
            f"Ending charging session: {energy_delivered:.2f} kWh delivered "
            f"in {duration/60:.1f} minutes"
        )

        self.total_energy_kwh += energy_delivered
        self.last_session = self.current_session
        self.current_session = None

    def get_session_stats(self) -> Dict[str, Any]:
        """Get current session statistics."""
        stats = {
            "total_sessions": self.total_sessions,
            "total_energy_kwh": self.total_energy_kwh,
            "session_active": self.current_session is not None,
        }

        if self.current_session:
            stats.update(
                {
                    "session_duration_min": self.current_session.duration_seconds / 60,
                    "session_energy_kwh": self.current_session.energy_delivered_kwh,
                }
            )

        if self.last_session:
            stats.update(
                {
                    "last_session_duration_min": self.last_session.duration_seconds
                    / 60,
                    "last_session_energy_kwh": self.last_session.energy_delivered_kwh,
                }
            )

        return stats

    def restore_state(self, state: Dict[str, Any]) -> None:
        """Restore session manager state from persisted data."""
        self.total_sessions = state.get("total_sessions", 0)
        self.total_energy_kwh = state.get("total_energy_kwh", 0.0)
        self._last_energy_kwh = state.get("last_energy_kwh", 0.0)

    def get_state(self) -> Dict[str, Any]:
        """Get state for persistence."""
        return {
            "total_sessions": self.total_sessions,
            "total_energy_kwh": self.total_energy_kwh,
            "last_energy_kwh": self._last_energy_kwh,
        }
