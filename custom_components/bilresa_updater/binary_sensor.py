"""Binary sensor exposing the keep-awake state for BILRESA remotes."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BilresaConfigEntry
from .coordinator import BilresaManager
from .entity import BilresaEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BilresaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the keep-awake binary sensor for discovered BILRESA remotes."""
    manager = entry.runtime_data
    async_add_entities(
        BilresaKeepAwakeSensor(manager, node_id)
        for node_id in manager.get_bilresa_node_ids()
    )


class BilresaKeepAwakeSensor(BilresaEntity, BinarySensorEntity):
    """On while the integration is actively holding the device awake for an OTA."""

    _attr_translation_key = "keep_awake_active"
    _attr_device_class = BinarySensorDeviceClass.RUNNING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, manager: BilresaManager, node_id: int) -> None:
        """Initialize the binary sensor."""
        super().__init__(manager, node_id)
        self._attr_unique_id = f"{node_id}_keep_awake_active"

    @property
    def is_on(self) -> bool:
        """Return True while the keep-awake loop is running for this node."""
        return self._manager.is_keeping_awake(self._node_id)
