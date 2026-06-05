"""Update platform for the IKEA BILRESA Firmware Updater."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BilresaConfigEntry
from .entity import BilresaEntity

_LOGGER = logging.getLogger(__name__)

# DCL lookups are cheap but not free; poll a few times per day.
SCAN_INTERVAL = timedelta(hours=6)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BilresaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up update entities for discovered BILRESA remotes."""
    manager = entry.runtime_data
    async_add_entities(
        BilresaUpdateEntity(manager, node_id)
        for node_id in manager.get_bilresa_node_ids()
    )


class BilresaUpdateEntity(BilresaEntity, UpdateEntity):
    """Firmware update entity backed by the Matter OTA flow."""

    _attr_should_poll = True
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.SPECIFIC_VERSION
    )

    def __init__(self, manager: Any, node_id: int) -> None:
        """Initialize the update entity."""
        super().__init__(manager, node_id)
        self._attr_unique_id = f"{node_id}_firmware"
        self._software_update: Any = None

    @property
    def installed_version(self) -> str | None:
        """Return the currently installed firmware version."""
        return self._manager.get_software_version_string(self._node_id)

    @property
    def in_progress(self) -> bool:
        """Return whether an update is currently in progress."""
        return self._manager.is_installing(self._node_id)

    @property
    def update_percentage(self) -> float | None:
        """Return the download progress percentage."""
        return self._manager.get_progress(self._node_id)

    async def async_update(self) -> None:
        """Check the DCL for the latest applicable firmware."""
        update = await self._manager.check_update(self._node_id)
        if update is None:
            self._software_update = None
            self._attr_latest_version = self.installed_version
            self._attr_release_summary = None
            self._attr_release_url = None
            return

        self._software_update = update
        self._attr_latest_version = update.software_version_string
        self._attr_release_url = getattr(update, "release_notes_url", None)

    async def async_install(
        self, version: str | None, backup: bool, **kwargs: Any
    ) -> None:
        """Install firmware, keeping the sleepy device awake throughout."""
        target: int | str | None
        if version is not None:
            target = version
        elif self._software_update is not None:
            target = self._software_update.software_version
        else:
            target = None
        await self._manager.install(self._node_id, target)
