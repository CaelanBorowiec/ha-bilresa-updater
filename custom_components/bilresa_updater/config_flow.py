"""Config flow for the IKEA BILRESA Firmware Updater integration."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import CONF_URL, DEFAULT_MATTER_URL, DOMAIN
from .coordinator import (
    BilresaConnectionError,
    async_validate_connection,
    discover_matter_url,
)

_LOGGER = logging.getLogger(__name__)


class BilresaConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for IKEA BILRESA Firmware Updater."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step."""
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}

        if user_input is not None:
            url = user_input[CONF_URL]
            try:
                await async_validate_connection(self.hass, url)
            except BilresaConnectionError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - surface unexpected errors to the UI
                _LOGGER.exception("Unexpected error validating Matter Server")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title="IKEA BILRESA Firmware Updater",
                    data={CONF_URL: url},
                )

        default_url = (
            (user_input or {}).get(CONF_URL)
            or discover_matter_url(self.hass)
            or DEFAULT_MATTER_URL
        )
        schema = vol.Schema({vol.Required(CONF_URL, default=default_url): str})
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )
