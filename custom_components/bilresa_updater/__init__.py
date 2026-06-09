"""The IKEA BILRESA Firmware Updater integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_FALLBACK_INTERVAL,
    CONF_URL,
    DEFAULT_KEEP_AWAKE_FALLBACK_INTERVAL,
    DEFAULT_MATTER_URL,
)
from .coordinator import BilresaConnectionError, BilresaManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.SENSOR,
]

type BilresaConfigEntry = ConfigEntry[BilresaManager]


async def async_setup_entry(hass: HomeAssistant, entry: BilresaConfigEntry) -> bool:
    """Set up IKEA BILRESA Firmware Updater from a config entry."""
    url = entry.data.get(CONF_URL, DEFAULT_MATTER_URL)
    fallback_interval = entry.options.get(
        CONF_FALLBACK_INTERVAL, DEFAULT_KEEP_AWAKE_FALLBACK_INTERVAL
    )
    manager = BilresaManager(hass, url, fallback_interval=fallback_interval)

    try:
        await manager.async_connect()
    except BilresaConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = manager
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: BilresaConfigEntry) -> None:
    """Reload the entry when options change so the new interval takes effect."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: BilresaConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_disconnect()
    return unloaded
