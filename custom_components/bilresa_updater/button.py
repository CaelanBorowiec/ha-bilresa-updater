"""Buttons for manual recovery actions on BILRESA remotes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import BilresaConfigEntry
from .coordinator import BilresaManager
from .entity import BilresaEntity


@dataclass(frozen=True, kw_only=True)
class BilresaButtonDescription(ButtonEntityDescription):
    """Describes a BILRESA action button."""

    press_fn: Callable[[BilresaManager, int], Awaitable[None]]


async def _keep_awake(manager: BilresaManager, node_id: int) -> None:
    await manager.keep_awake_once(node_id)


BUTTONS: tuple[BilresaButtonDescription, ...] = (
    BilresaButtonDescription(
        key="keep_awake",
        translation_key="keep_awake",
        entity_category=EntityCategory.CONFIG,
        press_fn=_keep_awake,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: BilresaConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up action buttons for discovered BILRESA remotes."""
    manager = entry.runtime_data
    async_add_entities(
        BilresaButton(manager, node_id, description)
        for node_id in manager.get_bilresa_node_ids()
        for description in BUTTONS
    )


class BilresaButton(BilresaEntity, ButtonEntity):
    """A manual recovery button."""

    entity_description: BilresaButtonDescription

    def __init__(
        self,
        manager: BilresaManager,
        node_id: int,
        description: BilresaButtonDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(manager, node_id)
        self.entity_description = description
        self._attr_unique_id = f"{node_id}_{description.key}"

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.entity_description.press_fn(self._manager, self._node_id)
