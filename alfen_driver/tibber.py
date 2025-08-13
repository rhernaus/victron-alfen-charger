"""Tibber API integration for dynamic electricity pricing."""

import asyncio
import time
from enum import Enum
from typing import Any, Dict, Optional, Tuple

import aiohttp

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

    async def get_current_price_level(self) -> Optional[PriceLevel]:
        """Get the current electricity price level.

        Returns:
            Current price level or None if unavailable.
        """
        if not self.config.enabled or not self.config.access_token:
            return None

        # Check cache
        now = time.time()
        if self._cache and (now - self._cache_time) < self._cache_ttl:
            price_info = self._cache.get("current_price")
            if price_info:
                return PriceLevel(price_info.get("level", "NORMAL"))

        try:
            # Query Tibber API
            query = """
            {
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
                            }
                        }
                    }
                }
            }
            """

            headers = {
                "Authorization": f"Bearer {self.config.access_token}",
                "Content-Type": "application/json",
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.GRAPHQL_URL,
                    json={"query": query},
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status != 200:
                        self.logger.error(f"Tibber API error: {response.status}")
                        return None

                    data = await response.json()

            # Parse response
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

            # Get current price info
            price_info = (
                target_home.get("currentSubscription", {})
                .get("priceInfo", {})
                .get("current", {})
            )

            if not price_info:
                self.logger.warning("No price info available")
                return None

            # Update cache
            self._cache = {"current_price": price_info}
            self._cache_time = now

            level_str = price_info.get("level", "NORMAL")
            self.logger.info(
                f"Current Tibber price level: {level_str} "
                f"(price: {price_info.get('total', 0):.4f})"
            )

            return PriceLevel(level_str)

        except asyncio.TimeoutError:
            self.logger.error("Tibber API timeout")
            return None
        except Exception as e:
            self.logger.error(f"Error fetching Tibber price: {e}")
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

    # Create client and check price
    client = TibberClient(config)

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
