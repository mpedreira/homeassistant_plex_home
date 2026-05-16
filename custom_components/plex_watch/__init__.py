from __future__ import annotations
import logging
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import entity_platform
from .const import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up the Plex Watch integration from YAML (should not be used)."""
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Plex Watch from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    from .coordinator import PlexDataUpdateCoordinator
    coordinator = PlexDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    hass.data[DOMAIN][entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_play_series(call):
        """Service to play a series on the active device."""
        series_rating_key = call.data.get("series_rating_key")
        client_identifier = call.data.get("client_identifier")
        result = await coordinator.api.play_series(coordinator.base_url, series_rating_key, client_identifier)
        if not result:
            _LOGGER.error("Failed to play series %s on client %s", series_rating_key, client_identifier)

    async def handle_get_current_series(call):
        """Service to get the current series being played."""
        series = await coordinator.api.get_current_series(coordinator.base_url)
        hass.states.async_set(f"{DOMAIN}.current_series", series)

    hass.services.async_register(
        DOMAIN, "play_series", handle_play_series,
    )
    hass.services.async_register(
        DOMAIN, "get_current_series", handle_get_current_series,
    )
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok
