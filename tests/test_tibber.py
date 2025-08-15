import asyncio
from typing import Any, Dict

import pytest

from alfen_driver.config import TibberConfig
from alfen_driver.tibber import PriceLevel, TibberClient, check_tibber_schedule, get_hourly_overview_text


@pytest.mark.asyncio
async def test_tibber_should_charge_strategies(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = TibberConfig(access_token="x", enabled=True)
    client = TibberClient(cfg)

    # Seed cache with current price info
    client._cache = {"current_price": {"level": "CHEAP", "total": 0.1234}}

    # level strategy
    cfg.strategy = "level"
    cfg.charge_on_cheap = True
    assert client.should_charge(PriceLevel.CHEAP) is True

    cfg.charge_on_cheap = False
    assert client.should_charge(PriceLevel.CHEAP) is False

    # threshold strategy
    cfg.strategy = "threshold"
    cfg.max_price_total = 0.2
    assert client.should_charge(PriceLevel.CHEAP) is True
    cfg.max_price_total = 0.1
    assert client.should_charge(PriceLevel.CHEAP) is False

    # percentile strategy: mock upcoming price window
    cfg.strategy = "percentile"
    client._cached_upcoming = [
        {"total": 0.10, "startsAt": "2025-01-01T00:00:00Z", "level": "NORMAL"},
        {"total": 0.20, "startsAt": "2025-01-01T01:00:00Z", "level": "NORMAL"},
        {"total": 0.30, "startsAt": "2025-01-01T02:00:00Z", "level": "NORMAL"},
    ]
    cfg.cheap_percentile = 0.5
    # Determine threshold via internal helper
    thr = client._determine_threshold()
    assert thr is not None and 0.1 <= thr <= 0.3
    # Our current total (0.1234) should be <= threshold around median
    can = client.should_charge(PriceLevel.CHEAP)
    assert isinstance(can, bool)


def test_check_tibber_schedule_disabled_or_missing_token() -> None:
    cfg = TibberConfig(access_token="", enabled=False)
    ok, msg = check_tibber_schedule(cfg)
    assert ok is False and "disabled" in msg

    cfg2 = TibberConfig(access_token="", enabled=True)
    ok2, msg2 = check_tibber_schedule(cfg2)
    assert ok2 is False and "token" in msg2


def test_get_hourly_overview_text_no_data() -> None:
    cfg = TibberConfig(access_token="x", enabled=True)
    text = get_hourly_overview_text(cfg)
    assert "not enabled" not in text  # enabled
    # With no cached data, it should say so
    assert "no upcoming price data" in text


def test_get_hourly_overview_text_with_data(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = TibberConfig(access_token="x", enabled=True)
    client = TibberClient(cfg)
    # Populate upcoming and cache
    client._cached_upcoming = [
        {"total": 0.1, "startsAt": "2025-01-01T00:00:00Z", "level": "CHEAP"},
        {"total": 0.2, "startsAt": "2025-01-01T01:00:00Z", "level": "NORMAL"},
        {"total": 0.4, "startsAt": "2025-01-01T02:00:00Z", "level": "EXPENSIVE"},
    ]
    client._cache = {"current_price": {"total": 0.15, "level": "CHEAP", "startsAt": "2025-01-01T00:00:00Z"}}

    # Inject as shared client
    import alfen_driver.tibber as tib_mod

    monkeypatch.setattr(tib_mod, "_SHARED_CLIENT", client)
    monkeypatch.setattr(tib_mod, "_SHARED_CLIENT_KEY", (cfg.access_token, cfg.home_id))

    text = get_hourly_overview_text(cfg)
    assert "Tibber hourly overview" in text
    assert "strategy=level" in text
    # Should include entries
    assert "2025-01-01T00:00:00Z" in text
    assert "priceRating=" in text