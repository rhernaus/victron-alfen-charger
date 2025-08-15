from typing import Any, Dict
from unittest.mock import ANY, MagicMock

import pytest

from alfen_driver.config import (
    Config,
    DefaultsConfig,
    ModbusConfig,
    RegistersConfig,
    ScheduleConfig,
)
from alfen_driver.dbus_utils import (
    EVC_CHARGE,
    EVC_MODE,
    get_current_ess_strategy,
    register_dbus_service,
)


def _make_config() -> Config:
    return Config(
        modbus=ModbusConfig(
            ip="192.168.1.100", port=502, socket_slave_id=1, station_slave_id=200
        ),
        device_instance=0,
        registers=RegistersConfig(),
        defaults=DefaultsConfig(intended_set_current=6.0, station_max_current=32.0),
        schedule=ScheduleConfig(items=[]),
    )


def test_register_dbus_service_registers_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    service_mock = MagicMock()
    service_cls = MagicMock(return_value=service_mock)

    # Patch VeDbusService symbol used inside module
    import alfen_driver.dbus_utils as dbus_utils_mod

    monkeypatch.setattr(dbus_utils_mod, "VeDbusService", service_cls)

    cfg = _make_config()

    def mode_cb(path: str, value: int) -> bool:
        return True

    def startstop_cb(path: str, value: int) -> bool:
        return True

    def set_current_cb(path: str, value: float) -> bool:
        return True

    service = register_dbus_service(
        "com.victronenergy.evcharger.ttyXR",
        cfg,
        EVC_MODE.MANUAL,
        EVC_CHARGE.ENABLED,
        6.0,
        [],
        mode_cb,
        startstop_cb,
        set_current_cb,
    )

    # Service constructed and registered
    service_cls.assert_called_once_with(
        "com.victronenergy.evcharger.ttyXR", register=False
    )
    service_mock.register.assert_called_once()

    # A few representative paths
    service_mock.add_path.assert_any_call(
        "/Mgmt/ProcessName", ANY, writeable=False, onchangecallback=None
    )
    service_mock.add_path.assert_any_call(
        "/Mode", EVC_MODE.MANUAL, writeable=True, onchangecallback=mode_cb
    )
    service_mock.add_path.assert_any_call(
        "/StartStop", EVC_CHARGE.ENABLED, writeable=True, onchangecallback=startstop_cb
    )
    service_mock.add_path.assert_any_call(
        "/SetCurrent", 6.0, writeable=True, onchangecallback=set_current_cb
    )

    assert service is service_mock


def test_get_current_ess_strategy_buying(monkeypatch: pytest.MonkeyPatch) -> None:
    # Mock dbus chain to return grid power > threshold
    class Obj:
        def GetValue(self) -> Dict[str, Any]:  # noqa: N802
            return {
                "Ac/Grid/L1/Power": 300.0,
                "Ac/Grid/L2/Power": 0.0,
                "Ac/Grid/L3/Power": 0.0,
                "Dc/Battery/Power": 0.0,
            }

    class Bus:
        def get_object(self, name: str, path: str) -> Obj:
            return Obj()

    import alfen_driver.dbus_utils as dbus_utils_mod

    monkeypatch.setattr(
        dbus_utils_mod, "dbus", MagicMock(SystemBus=MagicMock(return_value=Bus()))
    )

    assert get_current_ess_strategy() == "buying"


def test_get_current_ess_strategy_selling(monkeypatch: pytest.MonkeyPatch) -> None:
    class Obj:
        def GetValue(self) -> Dict[str, Any]:  # noqa: N802
            return {
                "Ac/Grid/L1/Power": -300.0,
                "Ac/Grid/L2/Power": -200.0,
                "Ac/Grid/L3/Power": -100.0,
                "Dc/Battery/Power": -400.0,
            }

    class Bus:
        def get_object(self, name: str, path: str) -> Obj:
            return Obj()

    import alfen_driver.dbus_utils as dbus_utils_mod

    monkeypatch.setattr(
        dbus_utils_mod, "dbus", MagicMock(SystemBus=MagicMock(return_value=Bus()))
    )

    assert get_current_ess_strategy() == "selling"


def test_get_current_ess_strategy_idle_and_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Idle (below thresholds)
    class Obj:
        def GetValue(self) -> Dict[str, Any]:  # noqa: N802
            return {
                "Ac/Grid/L1/Power": 100.0,
                "Ac/Grid/L2/Power": -50.0,
                "Ac/Grid/L3/Power": 50.0,
                "Dc/Battery/Power": 0.0,
            }

    class Bus:
        def get_object(self, name: str, path: str) -> Obj:
            return Obj()

    import alfen_driver.dbus_utils as dbus_utils_mod

    monkeypatch.setattr(
        dbus_utils_mod, "dbus", MagicMock(SystemBus=MagicMock(return_value=Bus()))
    )
    assert get_current_ess_strategy() == "idle"

    # Error path
    monkeypatch.setattr(
        dbus_utils_mod,
        "dbus",
        MagicMock(SystemBus=MagicMock(side_effect=RuntimeError("boom"))),
    )
    assert get_current_ess_strategy() == "idle"
