"""Shared base entity for the BILRESA updater."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN
from .coordinator import BilresaManager


class BilresaEntity(Entity):
    """Base entity tied to a single BILRESA node."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: BilresaManager, node_id: int) -> None:
        """Initialize the entity."""
        self._manager = manager
        self._node_id = node_id

        matter_identifier = manager.get_matter_device_identifier(node_id)
        if matter_identifier is not None:
            # Attach to the existing Matter device so the name, firmware version
            # and area are inherited from the device the user already sees.
            self._attr_device_info = DeviceInfo(identifiers={matter_identifier})
        else:
            # Fallback: stand up our own device entry with the best metadata we
            # can read directly from the node.
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, str(node_id))},
                manufacturer=manager.get_manufacturer(node_id),
                model=manager.get_product_name(node_id) or "BILRESA",
                name=manager.get_node_name(node_id),
                serial_number=manager.get_serial(node_id),
                sw_version=manager.get_software_version_string(node_id),
            )

    async def async_added_to_hass(self) -> None:
        """Subscribe to node updates."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self._manager.subscribe_node(self._node_id, self._handle_node_update)
        )

    @callback
    def _handle_node_update(self) -> None:
        """Handle a node update pushed by the manager."""
        self.async_write_ha_state()
