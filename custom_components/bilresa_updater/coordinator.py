"""Connection manager and OTA orchestration for the BILRESA updater.

This module is the only place that talks to ``python-matter-server`` / the
``chip`` SDK. The rest of the integration consumes plain Python values through
the :class:`BilresaManager` API so that a missing Matter dependency only fails
the connection (not every platform import).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import aiohttp_client

from matter_server.client import MatterClient
from matter_server.common.errors import (
    MatterError,
    NodeNotExists,
    NodeNotReady,
)
from matter_server.common.models import EventType

from chip.clusters import Objects as clusters

from .const import (
    CONF_URL,
    ICD_OPERATING_MODE_NAMES,
    IDLE_OTA_STATES,
    IKEA_VENDOR_ID,
    KEEP_AWAKE_DURATION_MS,
    KEEP_AWAKE_FALLBACK_INTERVAL,
    KEEP_AWAKE_MIN_INTERVAL,
    KEEP_AWAKE_REARM_RATIO,
    LISTEN_TASK_NAME,
    MATTER_DOMAIN,
    MIN_BATTERY_PERCENT,
    OTA_UPDATE_STATE_NAMES,
    PRODUCT_NAME_MATCH,
    STAY_ACTIVE_REQUEST_COMMAND_ID,
)

_LOGGER = logging.getLogger(__name__)


class BilresaConnectionError(HomeAssistantError):
    """Raised when the Matter Server cannot be reached."""


class BilresaManager:
    """Owns a dedicated Matter Server websocket connection.

    The Matter Server explicitly supports multiple simultaneous consumers, so we
    connect as a second client alongside Home Assistant's own Matter
    integration. This keeps us decoupled from HA internals while sharing the
    same fabric and OTA Provider.
    """

    def __init__(self, hass: HomeAssistant, url: str) -> None:
        """Initialize the manager."""
        self.hass = hass
        self.url = url
        self.client: MatterClient | None = None
        self._listen_task: asyncio.Task[None] | None = None
        self._unsub_events: Callable[[], None] | None = None
        self._last_promised: dict[int, int | None] = {}
        self._listeners: dict[int, list[Callable[[], None]]] = {}
        # Node ids recognised as BILRESA remotes (populated on connect).
        self._bilresa_ids: set[int] = set()
        # Active keep-awake loops, keyed by node id.
        self._keepalive_tasks: dict[int, asyncio.Task[None]] = {}
        self._keepalive_stops: dict[int, asyncio.Event] = {}
        # #region agent log
        self._dbg_last_state: dict[int, str | None] = {}
        # #endregion

    # #region agent log
    def _dbg(self, location: str, message: str, data: dict, hypothesis: str) -> None:
        try:
            import json as _json, time as _time
            with open(self.hass.config.path("debug-81fb06.log"), "a", encoding="utf-8") as _f:
                _f.write(_json.dumps({"sessionId": "81fb06", "timestamp": int(_time.time() * 1000), "location": location, "message": message, "data": data, "hypothesisId": hypothesis}) + "\n")
        except Exception:
            pass
    # #endregion

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def async_connect(self) -> None:
        """Connect to the Matter Server and start listening for events."""
        session = aiohttp_client.async_get_clientsession(self.hass)
        self.client = MatterClient(self.url, session)
        try:
            await self.client.connect()
        except (MatterError, OSError, asyncio.TimeoutError) as err:
            raise BilresaConnectionError(
                f"Could not connect to Matter Server at {self.url}: {err}"
            ) from err

        init_ready = asyncio.Event()
        self._listen_task = self.hass.async_create_background_task(
            self._listen(init_ready), LISTEN_TASK_NAME
        )

        try:
            async with asyncio.timeout(30):
                await init_ready.wait()
        except asyncio.TimeoutError as err:
            await self.async_disconnect()
            raise BilresaConnectionError(
                "Timed out waiting for the Matter Server node list"
            ) from err

        self._unsub_events = self.client.subscribe_events(
            self._handle_event, event_filter=EventType.ATTRIBUTE_UPDATED
        )

        self._bilresa_ids = set(self.get_bilresa_node_ids())
        # #region agent log
        self._dbg("coordinator.py:async_connect", "connected; BILRESA nodes discovered", {"bilresa_ids": sorted(self._bilresa_ids), "states": {n: self.get_update_state_name(n) for n in self._bilresa_ids}, "sw_versions": {n: self.get_software_version_string(n) for n in self._bilresa_ids}}, "H-D")
        # #endregion
        # In case an update is already mid-flight when we start up, evaluate now.
        for node_id in self._bilresa_ids:
            self._evaluate_keepawake(node_id)

    async def _listen(self, init_ready: asyncio.Event) -> None:
        """Run the client listen loop (lives for the duration of the entry)."""
        assert self.client is not None
        try:
            await self.client.start_listening(init_ready)
        except asyncio.CancelledError:
            raise
        except MatterError as err:
            _LOGGER.error("Matter Server listener stopped: %s", err)

    async def async_disconnect(self) -> None:
        """Tear down the connection and stop any keep-awake loops."""
        if self._keepalive_tasks:
            _LOGGER.warning(
                "Disconnecting while keeping node(s) %s awake for a firmware "
                "update; the transfer may stall until it is retried",
                ", ".join(str(n) for n in self._keepalive_tasks),
            )
        for stop in list(self._keepalive_stops.values()):
            stop.set()
        for task in list(self._keepalive_tasks.values()):
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, MatterError):
                pass
        self._keepalive_tasks.clear()
        self._keepalive_stops.clear()

        if self._unsub_events is not None:
            self._unsub_events()
            self._unsub_events = None
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except (asyncio.CancelledError, MatterError):
                pass
            self._listen_task = None
        if self.client is not None:
            await self.client.disconnect()
            self.client = None

    # ------------------------------------------------------------------
    # Events / listeners
    # ------------------------------------------------------------------
    @callback
    def _handle_event(self, event: EventType, data: Any) -> None:
        """React to Matter attribute changes on BILRESA nodes."""
        node_id = _extract_node_id(data)
        if node_id is None:
            return
        # Start/stop the keep-awake loop based on the device's OTA state,
        # regardless of which controller (HA, Apple, Google...) kicked off the
        # update. This is the integration's core job now that the native Matter
        # update entity handles the actual download.
        if node_id in self._bilresa_ids:
            self._evaluate_keepawake(node_id)
        self._notify(node_id)

    @callback
    def subscribe_node(self, node_id: int, update_cb: Callable[[], None]) -> Callable[[], None]:
        """Register an entity callback for a node. Returns an unsubscribe."""
        self._listeners.setdefault(node_id, []).append(update_cb)

        @callback
        def _unsub() -> None:
            listeners = self._listeners.get(node_id)
            if listeners and update_cb in listeners:
                listeners.remove(update_cb)

        return _unsub

    @callback
    def _notify(self, node_id: int) -> None:
        for update_cb in list(self._listeners.get(node_id, [])):
            update_cb()

    # ------------------------------------------------------------------
    # Node discovery
    # ------------------------------------------------------------------
    def get_bilresa_node_ids(self) -> list[int]:
        """Return node ids that look like BILRESA remotes."""
        if self.client is None:
            return []
        return [
            node.node_id
            for node in self.client.get_nodes()
            if self._is_bilresa(node)
        ]

    def _is_bilresa(self, node: Any) -> bool:
        info = node.device_info
        if info is None:
            return False
        if getattr(info, "vendorID", None) != IKEA_VENDOR_ID:
            return False
        if self._ota_endpoint(node) is None:
            return False
        product = (getattr(info, "productName", "") or "").upper()
        # Only attach to nodes that positively identify as BILRESA. Accepting
        # blank/unknown product names could expose firmware actions on unrelated
        # IKEA Matter devices, so we require an explicit name match.
        return PRODUCT_NAME_MATCH in product

    @staticmethod
    def _ota_endpoint(node: Any) -> int | None:
        for ep_id, endpoint in node.endpoints.items():
            if endpoint.has_cluster(clusters.OtaSoftwareUpdateRequestor):
                return ep_id
        return None

    @staticmethod
    def _power_source_endpoint(node: Any) -> int | None:
        for ep_id, endpoint in node.endpoints.items():
            if endpoint.has_cluster(clusters.PowerSource):
                return ep_id
        return None

    def _get_node(self, node_id: int) -> Any:
        if self.client is None:
            raise BilresaConnectionError("Matter Server not connected")
        return self.client.get_node(node_id)

    # ------------------------------------------------------------------
    # Device metadata helpers
    # ------------------------------------------------------------------
    def _basic_info_value(self, node_id: int, attribute: Any) -> Any:
        """Read a BasicInformation attribute (endpoint 0) from the node cache.

        This uses the same access path as Home Assistant's own Matter
        integration (``get_attribute_value``) rather than the ``device_info``
        convenience property, which can return ``None`` for composed/bridged
        endpoints.
        """
        return self._read_attribute(node_id, 0, clusters.BasicInformation, attribute)

    def get_product_name(self, node_id: int) -> str | None:
        return _clean_name(
            self._basic_info_value(
                node_id, clusters.BasicInformation.Attributes.ProductName
            )
        )

    def get_node_label(self, node_id: int) -> str | None:
        return _clean_name(
            self._basic_info_value(
                node_id, clusters.BasicInformation.Attributes.NodeLabel
            )
        )

    def get_node_name(self, node_id: int) -> str:
        """Return a human friendly device name.

        Prefer the user-set NodeLabel, then the product name, and only fall
        back to the node id when neither is available.
        """
        for candidate in (self.get_node_label(node_id), self.get_product_name(node_id)):
            if candidate:
                return candidate
        return f"IKEA BILRESA (node {node_id})"

    def get_manufacturer(self, node_id: int) -> str | None:
        return _clean_name(
            self._basic_info_value(
                node_id, clusters.BasicInformation.Attributes.VendorName
            )
        ) or "IKEA of Sweden"

    def get_serial(self, node_id: int) -> str | None:
        serial = self._basic_info_value(
            node_id, clusters.BasicInformation.Attributes.SerialNumber
        )
        if serial and "test" not in str(serial).lower():
            return str(serial)
        return None

    def get_software_version_string(self, node_id: int) -> str | None:
        return self._basic_info_value(
            node_id, clusters.BasicInformation.Attributes.SoftwareVersionString
        )

    def get_software_version_int(self, node_id: int) -> int | None:
        return self._basic_info_value(
            node_id, clusters.BasicInformation.Attributes.SoftwareVersion
        )

    def get_matter_device_identifier(self, node_id: int) -> tuple[str, str] | None:
        """Return the device registry identifier the Matter integration uses.

        Linking our entities to that identifier attaches them to the existing
        Matter device (inheriting its name, firmware, area, etc.) instead of
        creating a confusing duplicate device.
        """
        if self.client is None or self.client.server_info is None:
            return None
        node = self._safe_node(node_id)
        if node is None:
            return None
        endpoint = node.endpoints.get(0)
        if endpoint is None:
            return None

        device_id: str | None = None
        try:
            from homeassistant.components.matter.const import ID_TYPE_DEVICE_ID
            from homeassistant.components.matter.helpers import get_device_id

            device_id = get_device_id(self.client.server_info, endpoint)
            return (MATTER_DOMAIN, f"{ID_TYPE_DEVICE_ID}_{device_id}")
        except Exception:  # noqa: BLE001 - fall back to manual computation
            pass

        try:
            fabric_hex = f"{self.client.server_info.compressed_fabric_id:016X}"
            node_hex = f"{node_id:016X}"
            device_id = f"{fabric_hex}-{node_hex}-MatterNodeDevice"
        except Exception:  # noqa: BLE001
            return None
        return (MATTER_DOMAIN, f"deviceid_{device_id}")

    def get_battery_percent(self, node_id: int) -> int | None:
        """Return the battery level (0-100) or None if unknown.

        Matter reports BatPercentRemaining in half-percent units (0-200), so we
        halve it to get a normal 0-100 percentage.
        """
        node = self._safe_node(node_id)
        if node is None:
            return None
        value = self._read_attribute(
            node_id,
            self._power_source_endpoint(node),
            clusters.PowerSource,
            clusters.PowerSource.Attributes.BatPercentRemaining,
        )
        if value is None:
            return None
        return int(value) // 2

    # ------------------------------------------------------------------
    # OTA / ICD status helpers (read from the live node cache)
    # ------------------------------------------------------------------
    def is_keeping_awake(self, node_id: int) -> bool:
        """Return True while we are actively holding the device in active mode."""
        return node_id in self._keepalive_tasks

    def get_update_state_name(self, node_id: int) -> str | None:
        node = self._safe_node(node_id)
        if node is None:
            return None
        value = self._read_attribute(
            node_id,
            self._ota_endpoint(node),
            clusters.OtaSoftwareUpdateRequestor,
            clusters.OtaSoftwareUpdateRequestor.Attributes.UpdateState,
        )
        if value is None:
            return None
        return OTA_UPDATE_STATE_NAMES.get(int(value), str(value))

    def get_operating_mode(self, node_id: int) -> str | None:
        value = self._read_attribute(
            node_id,
            0,
            clusters.IcdManagement,
            clusters.IcdManagement.Attributes.OperatingMode,
        )
        if value is None:
            return None
        return ICD_OPERATING_MODE_NAMES.get(int(value), str(value))

    def get_last_promised_duration(self, node_id: int) -> int | None:
        return self._last_promised.get(node_id)

    def _safe_node(self, node_id: int) -> Any | None:
        try:
            return self._get_node(node_id)
        except (NodeNotExists, BilresaConnectionError):
            return None

    def _read_attribute(
        self, node_id: int, endpoint: int | None, cluster: Any, attribute: Any
    ) -> Any:
        if endpoint is None:
            return None
        node = self._safe_node(node_id)
        if node is None:
            return None
        try:
            return node.get_attribute_value(endpoint, cluster, attribute)
        except (KeyError, AttributeError):
            return None

    # ------------------------------------------------------------------
    # Auto keep-awake driver (reacts to OTA UpdateState changes)
    # ------------------------------------------------------------------
    @callback
    def _evaluate_keepawake(self, node_id: int) -> None:
        """Start or stop the keep-awake loop based on the device's OTA state."""
        state = self.get_update_state_name(node_id)
        # #region agent log
        if self._dbg_last_state.get(node_id, "__unset__") != state:
            self._dbg("coordinator.py:_evaluate_keepawake", "OTA UpdateState changed", {"node_id": node_id, "state": state, "prev": self._dbg_last_state.get(node_id), "loop_running": node_id in self._keepalive_tasks, "operating_mode": self.get_operating_mode(node_id)}, "H-C,H-D")
            self._dbg_last_state[node_id] = state
        # #endregion
        in_progress = state is not None and state not in IDLE_OTA_STATES
        if in_progress and node_id not in self._keepalive_tasks:
            self._start_keepawake(node_id, state)
        elif not in_progress and node_id in self._keepalive_tasks:
            self._stop_keepawake(node_id)

    @callback
    def _start_keepawake(self, node_id: int, state: str | None) -> None:
        """Begin holding a node in active mode for the duration of an update."""
        _LOGGER.info(
            "Firmware update detected on BILRESA node %s (state: %s); starting "
            "keep-awake loop",
            node_id,
            state,
        )
        battery = self.get_battery_percent(node_id)
        if battery is not None and battery < MIN_BATTERY_PERCENT:
            _LOGGER.warning(
                "BILRESA node %s battery is low (%s%%) during a firmware update; "
                "a flash interrupted by a dying battery can brick the device",
                node_id,
                battery,
            )
        # #region agent log
        self._dbg("coordinator.py:_start_keepawake", "starting keep-awake loop", {"node_id": node_id, "state": state, "battery": battery, "sw_version": self.get_software_version_string(node_id)}, "H-C")
        # #endregion
        stop_event = asyncio.Event()
        self._keepalive_stops[node_id] = stop_event
        self._keepalive_tasks[node_id] = self.hass.async_create_background_task(
            self._keepawake_runner(node_id, stop_event),
            f"{LISTEN_TASK_NAME}_keepawake_{node_id}",
        )
        self._notify(node_id)

    @callback
    def _stop_keepawake(self, node_id: int) -> None:
        """Signal the keep-awake loop for a node to stop."""
        _LOGGER.info(
            "Firmware update on BILRESA node %s finished; stopping keep-awake loop",
            node_id,
        )
        # #region agent log
        self._dbg("coordinator.py:_stop_keepawake", "OTA returned to idle; stopping loop", {"node_id": node_id, "state": self.get_update_state_name(node_id), "sw_version": self.get_software_version_string(node_id)}, "H-C")
        # #endregion
        stop_event = self._keepalive_stops.get(node_id)
        if stop_event is not None:
            stop_event.set()

    async def _keepawake_runner(
        self, node_id: int, stop_event: asyncio.Event
    ) -> None:
        """Run the keep-awake loop and clean up bookkeeping when it ends."""
        try:
            await self._keep_awake_loop(node_id, stop_event)
        finally:
            self._keepalive_tasks.pop(node_id, None)
            self._keepalive_stops.pop(node_id, None)
            self._notify(node_id)

    # ------------------------------------------------------------------
    # Keep-awake (ICD StayActiveRequest)
    # ------------------------------------------------------------------
    async def supports_stay_active(self, node_id: int) -> bool:
        """Feature-detect StayActiveRequest via the ICD AcceptedCommandList."""
        node = self._safe_node(node_id)
        if node is None:
            return False
        endpoint = node.endpoints.get(0)
        if endpoint is None or not endpoint.has_cluster(clusters.IcdManagement):
            return False
        accepted = self._read_attribute(
            node_id,
            0,
            clusters.IcdManagement,
            clusters.IcdManagement.Attributes.AcceptedCommandList,
        )
        if not accepted:
            # #region agent log
            self._dbg("coordinator.py:supports_stay_active", "AcceptedCommandList empty/unreadable", {"node_id": node_id, "accepted": accepted}, "H-E")
            # #endregion
            return False
        # #region agent log
        self._dbg("coordinator.py:supports_stay_active", "AcceptedCommandList read", {"node_id": node_id, "accepted": list(accepted), "supports_stay_active": STAY_ACTIVE_REQUEST_COMMAND_ID in accepted}, "H-E")
        # #endregion
        return STAY_ACTIVE_REQUEST_COMMAND_ID in accepted

    def get_user_active_mode_instruction(self, node_id: int) -> Any:
        """Return the UserActiveModeTriggerInstruction (which button to press)."""
        return self._read_attribute(
            node_id,
            0,
            clusters.IcdManagement,
            clusters.IcdManagement.Attributes.UserActiveModeTriggerInstruction,
        )

    async def keep_awake_once(self, node_id: int) -> int | None:
        """Send a single StayActiveRequest. Returns PromisedActiveDuration (ms)."""
        if self.client is None:
            raise BilresaConnectionError("Matter Server not connected")
        try:
            response = await self.client.send_device_command(
                node_id=node_id,
                endpoint_id=0,
                command=clusters.IcdManagement.Commands.StayActiveRequest(
                    stayActiveDuration=KEEP_AWAKE_DURATION_MS
                ),
            )
        except (MatterError, NodeNotReady) as err:
            _LOGGER.debug("StayActiveRequest failed for node %s: %s", node_id, err)
            # #region agent log
            self._dbg("coordinator.py:keep_awake_once", "StayActiveRequest FAILED", {"node_id": node_id, "error": str(err), "error_type": type(err).__name__}, "H-B")
            # #endregion
            return None
        promised = getattr(response, "promisedActiveDuration", None)
        # #region agent log
        self._dbg("coordinator.py:keep_awake_once", "StayActiveRequest OK", {"node_id": node_id, "promised_active_duration_ms": promised, "requested_ms": KEEP_AWAKE_DURATION_MS, "operating_mode": self.get_operating_mode(node_id), "response_type": type(response).__name__, "response_repr": repr(response)[:400]}, "H-A,H-F")
        # #endregion
        self._last_promised[node_id] = promised
        self._notify(node_id)
        return promised

    async def _keep_awake_loop(self, node_id: int, stop_event: asyncio.Event) -> None:
        """Hold the device in active mode until ``stop_event`` is set."""
        if not await self.supports_stay_active(node_id):
            instruction = self.get_user_active_mode_instruction(node_id)
            # #region agent log
            self._dbg("coordinator.py:_keep_awake_loop", "StayActiveRequest NOT supported; loop exiting immediately", {"node_id": node_id, "instruction": str(instruction)}, "H-E")
            # #endregion
            _LOGGER.warning(
                "Node %s does not support StayActiveRequest; the firmware "
                "transfer may stall. Tap the remote's active-mode button to "
                "keep it awake (instruction hint: %s)",
                node_id,
                instruction,
            )
            return

        while not stop_event.is_set():
            try:
                promised = await self.keep_awake_once(node_id)
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - the loop must never die mid-OTA
                # If this loop stops, the sleepy device drops to slow polling and
                # the transfer stalls. Log and keep retrying instead of dying.
                _LOGGER.warning(
                    "Keep-awake request for node %s failed (%s); retrying in %ss",
                    node_id,
                    err,
                    KEEP_AWAKE_FALLBACK_INTERVAL,
                )
                promised = None
            if promised:
                interval = max(
                    KEEP_AWAKE_MIN_INTERVAL,
                    (promised / 1000) * KEEP_AWAKE_REARM_RATIO,
                )
            else:
                interval = KEEP_AWAKE_FALLBACK_INTERVAL
            # #region agent log
            self._dbg("coordinator.py:_keep_awake_loop", "loop iteration; re-arming", {"node_id": node_id, "promised_ms": promised, "next_interval_s": interval, "ota_state": self.get_update_state_name(node_id)}, "H-C")
            # #endregion
            try:
                async with asyncio.timeout(interval):
                    await stop_event.wait()
            except asyncio.TimeoutError:
                continue


def _clean_name(name: Any) -> str | None:
    """Strip null chars/whitespace from a Matter name, returning None if empty."""
    if not name:
        return None
    cleaned = str(name).replace("\x00", "").strip()
    return cleaned or None


@callback
def _extract_node_id(data: Any) -> int | None:
    """Best-effort extraction of the node id from an event payload."""
    if isinstance(data, (list, tuple)) and data:
        first = data[0]
        if isinstance(first, int):
            return first
    node_id = getattr(data, "node_id", None)
    if isinstance(node_id, int):
        return node_id
    return None


def discover_matter_url(hass: HomeAssistant) -> str | None:
    """Return the Matter Server URL from the official Matter config entry."""
    for entry in hass.config_entries.async_entries(MATTER_DOMAIN):
        url = entry.data.get(CONF_URL)
        if url:
            return url
    return None


async def async_validate_connection(hass: HomeAssistant, url: str) -> int:
    """Validate a Matter Server URL. Returns the number of BILRESA nodes."""
    manager = BilresaManager(hass, url)
    try:
        await manager.async_connect()
        return len(manager.get_bilresa_node_ids())
    finally:
        await manager.async_disconnect()
