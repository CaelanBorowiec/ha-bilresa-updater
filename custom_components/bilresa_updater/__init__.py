"""The IKEA BILRESA Firmware Updater integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .const import CONF_URL, DEFAULT_MATTER_URL
from .coordinator import BilresaConnectionError, BilresaManager

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BUTTON, Platform.SENSOR, Platform.UPDATE]

type BilresaConfigEntry = ConfigEntry[BilresaManager]


async def async_setup_entry(hass: HomeAssistant, entry: BilresaConfigEntry) -> bool:
    """Set up IKEA BILRESA Firmware Updater from a config entry."""
    url = entry.data.get(CONF_URL, DEFAULT_MATTER_URL)
    manager = BilresaManager(hass, url)

    try:
        await manager.async_connect()
    except BilresaConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: BilresaConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_disconnect()
    return unloaded
