"""Tibber API integration for dynamic electricity pricing."""

import asyncio
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, Optional, Tuple, cast

from .config import TibberConfig
from .logging_utils import get_logger


class PriceLevel(Enum):
    """Tibber price levels."""

    VERY_CHEAP = "VERY_CHEAP"
    CHEAP = "CHEAP"
    NORMAL = "NORMAL"
    EXPENSIVE = "EXPENSIVE"
    VERY_EXPENSIVE = "VERY_EXPENSIVE"


class TibberClient:
    """Client for Tibber API interactions."""

    GRAPHQL_URL = "https://api.tibber.com/v1-beta/gql"

    def __init__(self, config: TibberConfig):
        """Initialize Tibber client.

        Args:
            config: Tibber configuration with access token.
        """
        self.config = config
        self.logger = get_logger("alfen_driver.tibber")
        self._cache: Dict[str, Any] = {}
        self._cache_time: float = 0
        self._cache_ttl: int = 300  # Cache for 5 minutes
        self._cache_next_refresh: float = 0.0  # Absolute epoch when we should refresh

    def _fetch_graphql_sync(self, query: str) -> Optional[Dict[str, Any]]:
        """Synchronous GraphQL POST using standard library (fallback if aiohttp is missing)."""
        headers = {
            "Authorization": f"Bearer {self.config.access_token.strip()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "victron-alfen-charger/1.0 (+https://github.com/)",
        }
        payload = {"query": query, "variables": {}}
        data_bytes = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.GRAPHQL_URL,
            data=data_bytes,
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                status_code = getattr(response, "status", None) or response.getcode()
                if int(status_code) != 200:
                    self.logger.error(f"Tibber API error: {status_code}")
                    return None
                body = response.read().decode("utf-8")
                return cast(Dict[str, Any], json.loads(body))
        except urllib.error.HTTPError as e:  # pragma: no cover - network dependent
            # Try to extract error body for diagnostics
            err_body = ""
            try:
                err_body = e.read().decode("utf-8")
            except Exception:
                err_body = ""
            if err_body:
                # Try to parse and surface GraphQL errors if present
                try:
                    parsed = json.loads(err_body)
                    errors = parsed.get("errors")
                    if errors:
                        first = errors[0]
                        message = first.get("message", "")
                        self.logger.error(
                            f"Tibber API HTTP error: {e.code} {e.reason} - {message}"
                        )
                    else:
                        self.logger.error(
                            f"Tibber API HTTP error: {e.code} {e.reason} - Body: {err_body[:200]}"
                        )
                except Exception:
                    self.logger.error(
                        f"Tibber API HTTP error: {e.code} {e.reason} - Body: {err_body[:200]}"
                    )
            else:
                self.logger.error(f"Tibber API HTTP error: {e.code} {e.reason}")
            return None
        except urllib.error.URLError as e:  # pragma: no cover - network dependent
            self.logger.error(f"Tibber API URL error: {e.reason}")
            return None
        except Exception as e:  # pragma: no cover - safety net
            self.logger.error(f"Tibber API request failed: {e}")
            return None

    async def get_current_price_level(self) -> Optional[PriceLevel]:
        """Get the current electricity price level.

        Returns:
            Current price level or None if unavailable.
        """
        if not self.config.enabled or not self.config.access_token:
            return None

        # Check cache using dynamic next refresh time if available
        now = time.time()
        # If we have a next refresh set and we're before it, prefer cached data or skip request
        if self._cache_next_refresh and now < self._cache_next_refresh:
            price_info = self._cache.get("current_price") if self._cache else None
            if price_info:
                return PriceLevel(price_info.get("level", "NORMAL"))
            # No cached data yet, respect backoff and avoid hammering the API
            return None

        try:
            # Query Tibber API including next slot to know when to refresh
            query = """
            query PriceInfoQuery {
                viewer {
                    homes {
                        id
                        currentSubscription {
                            priceInfo {
                                current {
                                    total
                                    level
                                    startsAt
                                }
                                today {
                                    total
                                    level
                                    startsAt
                                }
                                tomorrow {
                                    total
                                    level
                                    startsAt
                                }
                            }
                        }
                    }
                }
            }
            """

            data: Optional[Dict[str, Any]] = None

            # Try optional aiohttp via importlib; fall back to urllib if unavailable
            aiohttp_available = False
            aiohttp_mod: Any = None
            try:
                import importlib

                aiohttp_mod = importlib.import_module("aiohttp")
                aiohttp_available = True
            except Exception as e:  # pragma: no cover - optional dependency
                self.logger.debug(f"aiohttp not available, using urllib fallback: {e}")

            if aiohttp_available and aiohttp_mod is not None:
                headers = {
                    "Authorization": f"Bearer {self.config.access_token.strip()}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "User-Agent": "victron-alfen-charger/1.0 (+https://github.com/)",
                }
                async with aiohttp_mod.ClientSession() as session:
                    async with session.post(
                        self.GRAPHQL_URL,
                        json={"query": query, "variables": {}},
                        headers=headers,
                        timeout=aiohttp_mod.ClientTimeout(total=10),
                    ) as response:
                        if response.status != 200:
                            # Try to extract JSON error detail
                            body_text = ""
                            try:
                                body_text = await response.text()
                            except Exception:
                                body_text = ""
                            detail = ""
                            if body_text:
                                try:
                                    parsed = json.loads(body_text)
                                    errors = parsed.get("errors")
                                    if errors:
                                        detail = errors[0].get("message", "")
                                except Exception as parse_error:
                                    self.logger.debug(
                                        f"Failed to parse Tibber error body as JSON: {parse_error}"
                                    )
                            msg = f"Tibber API error: {response.status}" + (
                                f" - {detail}" if detail else ""
                            )
                            self.logger.error(msg)
                            # Short backoff to avoid hammering on failure
                            self._cache_next_refresh = max(
                                self._cache_next_refresh, now + 60
                            )
                            return None
                        data = await response.json()
            else:
                # Fallback to standard library in a thread to avoid blocking
                data = await asyncio.to_thread(self._fetch_graphql_sync, query)

            if not data:
                # Short backoff to avoid hammering on failure
                self._cache_next_refresh = max(self._cache_next_refresh, now + 60)
                return None

            # Parse response
            # If GraphQL-level errors are present, log and back off
            if isinstance(data, dict) and data.get("errors"):
                first_error = data["errors"][0]
                message = first_error.get("message", "GraphQL error")
                self.logger.error(f"Tibber API GraphQL error: {message}")
                self._cache_next_refresh = max(self._cache_next_refresh, now + 60)
                return None

            homes = data.get("data", {}).get("viewer", {}).get("homes", [])
            if not homes:
                self.logger.warning("No homes found in Tibber account")
                return None

            # Use specified home or first home
            target_home = None
            if self.config.home_id:
                for home in homes:
                    if home.get("id") == self.config.home_id:
                        target_home = home
                        break
            else:
                target_home = homes[0]

            if not target_home:
                self.logger.warning(f"Home {self.config.home_id} not found")
                return None

            # Get current and upcoming price info
            price_info = (
                target_home.get("currentSubscription", {})
                .get("priceInfo", {})
                .get("current", {})
            )
            price_info_container = target_home.get("currentSubscription", {}).get(
                "priceInfo", {}
            )
            prices_today = price_info_container.get("today", []) or []
            prices_tomorrow = price_info_container.get("tomorrow", []) or []

            if not price_info:
                self.logger.warning("No price info available")
                return None

            # Update cache (store current and a computed next if available)
            self._cache = {"current_price": price_info}
            self._cache_time = now

            # Determine next refresh time by finding the next slot in today/tomorrow lists
            next_refresh: float = 0.0
            try:
                # Build a combined chronological list of all upcoming starts
                upcoming_starts: list[float] = []
                for entry in [*prices_today, *prices_tomorrow]:
                    starts_at = entry.get("startsAt")
                    if isinstance(starts_at, str) and starts_at:
                        starts_str = starts_at.replace("Z", "+00:00")
                        try:
                            dt = datetime.fromisoformat(starts_str)
                            if dt.tzinfo is None:
                                dt = dt.replace(tzinfo=timezone.utc)
                            ts = dt.timestamp()
                            upcoming_starts.append(ts)
                        except Exception as parse_err:
                            self.logger.debug(
                                f"Failed to parse startsAt '{starts_at}': {parse_err}"
                            )
                            continue
                upcoming_starts.sort()

                # Prefer the next period strictly after current.startsAt (or now if missing)
                cur_start_ts = None
                try:
                    cur_starts = price_info.get("startsAt")
                    if isinstance(cur_starts, str) and cur_starts:
                        cur_str = cur_starts.replace("Z", "+00:00")
                        cur_dt = datetime.fromisoformat(cur_str)
                        if cur_dt.tzinfo is None:
                            cur_dt = cur_dt.replace(tzinfo=timezone.utc)
                        cur_start_ts = cur_dt.timestamp()
                except Exception:
                    cur_start_ts = None

                # Find the first upcoming start that is greater than current start (or now)
                baseline = cur_start_ts or now
                for ts in upcoming_starts:
                    if ts > baseline + 1e-6:
                        next_refresh = ts
                        break
            except Exception:
                next_refresh = 0.0

            # Fallbacks if next refresh is not available
            if not next_refresh:
                # If we have current startsAt, assume hourly price and refresh at +3600
                try:
                    current_starts_at = price_info.get("startsAt")
                    if isinstance(current_starts_at, str) and current_starts_at:
                        starts_str = current_starts_at.replace("Z", "+00:00")
                        dtc = datetime.fromisoformat(starts_str)
                        if dtc.tzinfo is None:
                            dtc = dtc.replace(tzinfo=timezone.utc)
                        next_refresh = dtc.timestamp() + 3600.0
                except Exception:
                    next_refresh = 0.0
            if not next_refresh:
                # As a last resort, refresh in 15 minutes
                next_refresh = now + 900.0

            # Add a small safety margin to avoid racing the boundary
            self._cache_next_refresh = max(next_refresh + 1.0, now + 5.0)

            level_str = price_info.get("level", "NORMAL")
            self.logger.info(
                f"Current Tibber price level: {level_str} "
                f"(price: {price_info.get('total', 0):.4f})"
            )

            return PriceLevel(level_str)

        except asyncio.TimeoutError:
            self.logger.error("Tibber API timeout")
            # Short backoff to avoid hammering on failure
            self._cache_next_refresh = max(self._cache_next_refresh, now + 60)
            return None
        except Exception as e:
            self.logger.error(f"Error fetching Tibber price: {e}")
            # Short backoff to avoid hammering on failure
            self._cache_next_refresh = max(self._cache_next_refresh, now + 60)
            return None

    def should_charge(self, price_level: Optional[PriceLevel]) -> bool:
        """Determine if charging should be enabled based on price level.

        Args:
            price_level: Current price level.

        Returns:
            True if charging should be enabled.
        """
        if not price_level:
            return False

        if price_level == PriceLevel.VERY_CHEAP and self.config.charge_on_very_cheap:
            return True
        if price_level == PriceLevel.CHEAP and self.config.charge_on_cheap:
            return True

        return False


# Shared client to persist cache across schedule checks
_SHARED_CLIENT: Optional[TibberClient] = None
_SHARED_CLIENT_KEY: Optional[Tuple[str, str]] = None


def _get_shared_client(config: TibberConfig) -> TibberClient:
    global _SHARED_CLIENT, _SHARED_CLIENT_KEY
    key = (config.access_token, config.home_id)
    if _SHARED_CLIENT is None or _SHARED_CLIENT_KEY != key:
        _SHARED_CLIENT = TibberClient(config)
        _SHARED_CLIENT_KEY = key
    return _SHARED_CLIENT


def check_tibber_schedule(config: TibberConfig) -> Tuple[bool, str]:
    """Check if charging should be enabled based on Tibber pricing.

    This is a synchronous wrapper for use in the main code.

    Args:
        config: Tibber configuration.

    Returns:
        Tuple of (should_charge, explanation_string).
    """
    if not config.enabled:
        return False, "Tibber integration disabled"

    if not config.access_token:
        return False, "No Tibber access token configured"

    # Create event loop if needed
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # Reuse shared client to leverage cross-call caching
    client = _get_shared_client(config)

    # Run async function
    try:
        price_level = loop.run_until_complete(client.get_current_price_level())
    except Exception as e:
        logger = get_logger("alfen_driver.tibber")
        logger.error(f"Error checking Tibber price: {e}")
        return False, f"Tibber API error: {str(e)}"

    if not price_level:
        return False, "Could not fetch Tibber price"

    should_charge = client.should_charge(price_level)

    if should_charge:
        return True, f"Tibber price is {price_level.value} - charging enabled"
    else:
        return False, f"Tibber price is {price_level.value} - waiting for cheaper price"
