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
    UpdateError,
)
from matter_server.common.models import EventType

from chip.clusters import Objects as clusters

from .const import (
    CONF_URL,
    ICD_OPERATING_MODE_NAMES,
    IKEA_VENDOR_ID,
    KEEP_AWAKE_DURATION_MS,
    KEEP_AWAKE_FALLBACK_INTERVAL,
    KEEP_AWAKE_MIN_INTERVAL,
    KEEP_AWAKE_REARM_RATIO,
    LISTEN_TASK_NAME,
    MATTER_DOMAIN,
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
        self._install_lock = asyncio.Lock()
        self._installing: set[int] = set()
        self._progress: dict[int, float | None] = {}
        self._last_promised: dict[int, int | None] = {}
        self._listeners: dict[int, list[Callable[[], None]]] = {}

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
        """Tear down the connection."""
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
        """Forward Matter attribute changes to interested entities."""
        node_id = _extract_node_id(data)
        if node_id is None:
            return
        # Keep our cached download progress in sync for in-progress installs.
        if node_id in self._installing:
            self._progress[node_id] = self._read_update_progress(node_id)
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
        # Match BILRESA by name when available, but still accept IKEA OTA
        # capable nodes whose name is blank so future revisions keep working.
        return PRODUCT_NAME_MATCH in product or product == ""

    @staticmethod
    def _ota_endpoint(node: Any) -> int | None:
        for ep_id, endpoint in node.endpoints.items():
            if endpoint.has_cluster(clusters.OtaSoftwareUpdateRequestor):
                return ep_id
        return None

    def _get_node(self, node_id: int) -> Any:
        if self.client is None:
            raise BilresaConnectionError("Matter Server not connected")
        return self.client.get_node(node_id)

    # ------------------------------------------------------------------
    # Device metadata helpers
    # ------------------------------------------------------------------
    def get_product_name(self, node_id: int) -> str | None:
        try:
            info = self._get_node(node_id).device_info
        except (NodeNotExists, BilresaConnectionError):
            return None
        return getattr(info, "productName", None) if info else None

    def get_node_name(self, node_id: int) -> str:
        product = self.get_product_name(node_id)
        return product or f"BILRESA {node_id}"

    def get_serial(self, node_id: int) -> str | None:
        try:
            info = self._get_node(node_id).device_info
        except (NodeNotExists, BilresaConnectionError):
            return None
        return getattr(info, "serialNumber", None) if info else None

    def get_software_version_string(self, node_id: int) -> str | None:
        try:
            info = self._get_node(node_id).device_info
        except (NodeNotExists, BilresaConnectionError):
            return None
        return getattr(info, "softwareVersionString", None) if info else None

    def get_software_version_int(self, node_id: int) -> int | None:
        try:
            info = self._get_node(node_id).device_info
        except (NodeNotExists, BilresaConnectionError):
            return None
        return getattr(info, "softwareVersion", None) if info else None

    # ------------------------------------------------------------------
    # OTA / ICD status helpers (read from the live node cache)
    # ------------------------------------------------------------------
    def is_installing(self, node_id: int) -> bool:
        return node_id in self._installing

    def get_progress(self, node_id: int) -> float | None:
        if node_id not in self._installing:
            return None
        return self._progress.get(node_id) or self._read_update_progress(node_id)

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

    def _read_update_progress(self, node_id: int) -> float | None:
        node = self._safe_node(node_id)
        if node is None:
            return None
        value = self._read_attribute(
            node_id,
            self._ota_endpoint(node),
            clusters.OtaSoftwareUpdateRequestor,
            clusters.OtaSoftwareUpdateRequestor.Attributes.UpdateStateProgress,
        )
        if value is None:
            return None
        return float(value)

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
    # Update check / install
    # ------------------------------------------------------------------
    async def check_update(self, node_id: int) -> Any:
        """Query the DCL (and local sources) for the latest applicable image."""
        if self.client is None:
            raise BilresaConnectionError("Matter Server not connected")
        return await self.client.check_node_update(node_id=node_id)

    async def install(self, node_id: int, software_version: int | str | None) -> None:
        """Run an OTA update while keeping the sleepy device awake.

        ``update_node`` performs the heavy lifting on the server (ephemeral OTA
        Provider + BDX transfer). We run a ``StayActiveRequest`` loop alongside
        it so a SIT ICD does not drop back to slow polling mid-transfer.
        """
        if self.client is None:
            raise BilresaConnectionError("Matter Server not connected")
        if software_version is None:
            update = await self.check_update(node_id)
            if update is None:
                raise HomeAssistantError("No firmware update available for this device")
            software_version = update.software_version

        async with self._install_lock:
            self._installing.add(node_id)
            self._progress[node_id] = None
            self._notify(node_id)
            stop_event = asyncio.Event()
            keep_task = self.hass.async_create_task(
                self._keep_awake_loop(node_id, stop_event),
                f"{LISTEN_TASK_NAME}_keepawake_{node_id}",
            )
            try:
                await self.client.update_node(
                    node_id=node_id, software_version=software_version
                )
            except UpdateError as err:
                raise HomeAssistantError(
                    await self._describe_update_failure(node_id, err)
                ) from err
            finally:
                stop_event.set()
                await keep_task
                self._installing.discard(node_id)
                self._progress.pop(node_id, None)
                self._notify(node_id)

    async def _describe_update_failure(self, node_id: int, err: Exception) -> str:
        """Augment an update failure with reachability diagnostics."""
        detail = str(err)
        if self.client is None:
            return detail
        try:
            results = await self.client.ping_node(node_id)
        except (MatterError, NodeNotReady):
            return detail
        reachable = [addr for addr, ok in results.items() if ok]
        if not reachable:
            return (
                f"{detail} (device {node_id} is not reachable on any address - "
                "check Thread/IPv6 connectivity)"
            )
        return f"{detail} (device reachable at: {', '.join(reachable)})"

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
            return False
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
            return None
        promised = getattr(response, "promisedActiveDuration", None)
        self._last_promised[node_id] = promised
        self._notify(node_id)
        return promised

    async def _keep_awake_loop(self, node_id: int, stop_event: asyncio.Event) -> None:
        """Hold the device in active mode until ``stop_event`` is set."""
        if not await self.supports_stay_active(node_id):
            instruction = self.get_user_active_mode_instruction(node_id)
            _LOGGER.warning(
                "Node %s does not support StayActiveRequest; the firmware "
                "transfer may stall. Tap the remote's active-mode button to "
                "keep it awake (instruction hint: %s)",
                node_id,
                instruction,
            )
            return

        while not stop_event.is_set():
            promised = await self.keep_awake_once(node_id)
            if promised:
                interval = max(
                    KEEP_AWAKE_MIN_INTERVAL,
                    (promised / 1000) * KEEP_AWAKE_REARM_RATIO,
                )
            else:
                interval = KEEP_AWAKE_FALLBACK_INTERVAL
            try:
                async with asyncio.timeout(interval):
                    await stop_event.wait()
            except asyncio.TimeoutError:
                continue


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
