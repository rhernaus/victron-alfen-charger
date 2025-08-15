from datetime import datetime, timedelta

from alfen_driver.session_manager import ChargingSessionManager
from alfen_driver.constants import SessionDefaults


def test_session_start_confirmation_by_time(monkeypatch) -> None:
    mgr = ChargingSessionManager()

    # Begin charging but energy not yet increased
    mgr.update(power_w=1200, total_energy_kwh=0.0)
    assert mgr.current_session is None

    # Fast-forward time beyond confirmation window and still charging
    base = datetime.now()

    class FauxDatetime:
        @classmethod
        def now(cls):
            return base + timedelta(seconds=SessionDefaults.START_CONFIRMATION_SECONDS + 1)

    monkeypatch.setattr("alfen_driver.session_manager.datetime", FauxDatetime)
    mgr.update(power_w=1200, total_energy_kwh=0.0)
    assert mgr.current_session is not None


def test_session_end_after_grace_period(monkeypatch) -> None:
    mgr = ChargingSessionManager()

    # Start a session via time confirmation
    mgr.update(power_w=1200, total_energy_kwh=0.0)
    base = datetime.now()

    class FauxDatetimeStart:
        @classmethod
        def now(cls):
            return base + timedelta(seconds=SessionDefaults.START_CONFIRMATION_SECONDS + 1)

    monkeypatch.setattr("alfen_driver.session_manager.datetime", FauxDatetimeStart)
    mgr.update(power_w=1200, total_energy_kwh=0.0)
    assert mgr.current_session is not None

    # Stop charging: not ended immediately
    mgr.update(power_w=0.0, total_energy_kwh=0.0)
    assert mgr.current_session is not None

    # Advance time beyond grace and update again (> start_confirmation + end_delay)
    class FauxDatetimeEnd:
        @classmethod
        def now(cls):
            return base + timedelta(
                seconds=SessionDefaults.START_CONFIRMATION_SECONDS
                + SessionDefaults.SESSION_END_DELAY_SECONDS
                + 2
            )

    monkeypatch.setattr("alfen_driver.session_manager.datetime", FauxDatetimeEnd)
    mgr.update(power_w=0.0, total_energy_kwh=0.0)
    assert mgr.current_session is None
    assert mgr.last_session is not None


def test_state_persistence_roundtrip(monkeypatch) -> None:
    mgr = ChargingSessionManager()

    # Create a session and progress a bit
    mgr.update(power_w=1200, total_energy_kwh=1.0)
    # Confirm by time
    base = datetime.now()

    class FauxDatetime:
        @classmethod
        def now(cls):
            return base + timedelta(seconds=SessionDefaults.START_CONFIRMATION_SECONDS + 1)

        @staticmethod
        def fromisoformat(s: str):
            # Delegate to real datetime for restore_state
            return datetime.fromisoformat(s)

    monkeypatch.setattr("alfen_driver.session_manager.datetime", FauxDatetime)
    mgr.update(power_w=1200, total_energy_kwh=1.0)

    state = mgr.get_state()

    # Restore in a new manager
    mgr2 = ChargingSessionManager()
    mgr2.restore_state(state)
    # Active session restored if present
    stats = mgr2.get_session_stats()
    assert "total_sessions" in stats