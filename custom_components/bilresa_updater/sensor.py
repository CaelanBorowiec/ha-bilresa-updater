"""Diagnostic sensors for the IKEA BILRESA Firmware Updater."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BilresaConfigEntry
from .const import ICD_OPERATING_MODE_NAMES, OTA_UPDATE_STATE_NAMES
from .coordinator import BilresaManager
from .entity import BilresaEntity


@dataclass(frozen=True, kw_only=True)
class BilresaSensorDescription(SensorEntityDescription):
    """Describes a BILRESA diagnostic sensor."""

    value_fn: Callable[[BilresaManager, int], Any]


SENSORS: tuple[BilresaSensorDescription, ...] = (
    BilresaSensorDescription(
        key="ota_state",
        translation_key="ota_state",
        device_class=SensorDeviceClass.ENUM,
        options=list(OTA_UPDATE_STATE_NAMES.values()),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda manager, node_id: manager.get_update_state_name(node_id),
    ),
    BilresaSensorDescription(
        key="icd_mode",
        translation_key="icd_mode",
        device_class=SensorDeviceClass.ENUM,
        options=list(ICD_OPERATING_MODE_NAMES.values()),
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda manager, node_id: manager.get_operating_mode(node_id),
    ),
    BilresaSensorDescription(
        key="promised_active",
        translation_key="promised_active",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MILLISECONDS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda manager, node_id: manager.get_last_promised_duration(node_id),
    ),
    BilresaSensorDescription(
        key="battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda manager, node_id: manager.get_battery_percent(node_id),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BilresaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up diagnostic sensors for discovered BILRESA remotes."""
    manager = entry.runtime_data
    async_add_entities(
        BilresaSensor(manager, node_id, description)
        for node_id in manager.get_bilresa_node_ids()
        for description in SENSORS
    )


class BilresaSensor(BilresaEntity, SensorEntity):
    """A diagnostic sensor reflecting OTA / ICD state."""

    entity_description: BilresaSensorDescription

    def __init__(
        self,
        manager: BilresaManager,
        node_id: int,
        description: BilresaSensorDescription,
    ) -> None:
        """Initialize the sensor."""
        super().__init__(manager, node_id)
        self.entity_description = description
        self._attr_unique_id = f"{node_id}_{description.key}"

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self._manager, self._node_id)
