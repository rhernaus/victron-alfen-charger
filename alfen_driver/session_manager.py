"""Charging session management for Alfen driver."""

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from .constants import SessionDefaults

logger = logging.getLogger(__name__)


class ChargingSession:
    """Represents a single charging session."""

    def __init__(self, start_energy_kwh: float, start_time: Optional[datetime] = None):
        self.start_time = start_time or datetime.now()
        self.start_energy_kwh = start_energy_kwh
        self.end_time: Optional[datetime] = None
        self.end_energy_kwh: Optional[float] = None
        self.current_energy_kwh: float = start_energy_kwh  # Track current energy

    @property
    def duration_seconds(self) -> float:
        """Get session duration in seconds."""
        end = self.end_time or datetime.now()
        return (end - self.start_time).total_seconds()

    @property
    def energy_delivered_kwh(self) -> float:
        """Get energy delivered during session."""
        if self.end_energy_kwh is not None:
            # Session ended, use final energy
            return self.end_energy_kwh - self.start_energy_kwh
        else:
            # Session active, use current energy
            return max(0.0, self.current_energy_kwh - self.start_energy_kwh)

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
        # Candidate session start tracking
        self._candidate_start_energy_kwh: Optional[float] = None
        self._candidate_start_time: Optional[datetime] = None
        # Graceful end tracking to avoid flapping
        self._not_charging_since: Optional[datetime] = None

    def update(self, power_w: float, total_energy_kwh: float) -> None:
        """Update session state based on power and energy readings."""
        # Consider charging if power > 100W (to avoid noise)
        charging = power_w > 100

        # Initialize baseline energy if first reading but do not return early
        if self._last_energy_kwh == 0.0 and total_energy_kwh > 0:
            self._last_energy_kwh = total_energy_kwh
            self._last_power = power_w
            # Continue to allow candidate start logic to run on first tick

        now = datetime.now()

        if charging:
            # Reset end delay timer
            self._not_charging_since = None

            if self.current_session is None:
                # Establish a candidate start point if not already set
                if self._candidate_start_energy_kwh is None:
                    self._candidate_start_energy_kwh = total_energy_kwh
                    self._candidate_start_time = now

                # Confirm start if enough energy has accumulated OR enough time has passed
                energy_since_candidate = (
                    total_energy_kwh - (self._candidate_start_energy_kwh or total_energy_kwh)
                )
                time_since_candidate = (
                    (now - self._candidate_start_time).total_seconds()
                    if self._candidate_start_time is not None
                    else 0.0
                )
                if (
                    energy_since_candidate >= SessionDefaults.ENERGY_THRESHOLD_KWH
                    or time_since_candidate >= SessionDefaults.START_CONFIRMATION_SECONDS
                ):
                    # Start the session at the candidate energy snapshot
                    self._start_session(self._candidate_start_energy_kwh or total_energy_kwh)
                    # Clear candidate once session is active
                    self._candidate_start_energy_kwh = None
                    self._candidate_start_time = None

        else:
            # Not charging: clear any candidate start
            self._candidate_start_energy_kwh = None
            self._candidate_start_time = None

            # End session after a grace period to avoid flapping
            if self.current_session is not None:
                if self._not_charging_since is None:
                    self._not_charging_since = now
                elif (
                    now - self._not_charging_since
                ).total_seconds() >= SessionDefaults.SESSION_END_DELAY_SECONDS:
                    self._end_session(total_energy_kwh)
                    self._not_charging_since = None

        # Update current energy for active session
        if self.current_session is not None:
            self.current_session.current_energy_kwh = total_energy_kwh

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

        # Restore active session if it was persisted
        if state.get("active_session"):
            session_data = state["active_session"]
            start_time = datetime.fromisoformat(session_data["start_time"])
            self.current_session = ChargingSession(
                session_data["start_energy_kwh"], start_time
            )
            self.current_session.current_energy_kwh = session_data.get(
                "current_energy_kwh", session_data["start_energy_kwh"]
            )
            logger.info(
                f"Restored active session: started {start_time}, "
                f"energy delivered: {self.current_session.energy_delivered_kwh:.2f} kWh"
            )

    def get_state(self) -> Dict[str, Any]:
        """Get state for persistence."""
        state: Dict[str, Any] = {
            "total_sessions": self.total_sessions,
            "total_energy_kwh": self.total_energy_kwh,
            "last_energy_kwh": self._last_energy_kwh,
        }

        # Persist active session if present
        if self.current_session:
            state["active_session"] = {
                "start_time": self.current_session.start_time.isoformat(),
                "start_energy_kwh": self.current_session.start_energy_kwh,
                "current_energy_kwh": self.current_session.current_energy_kwh,
            }

        return state
