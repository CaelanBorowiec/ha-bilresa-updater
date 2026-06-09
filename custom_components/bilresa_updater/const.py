"""Constants for the IKEA BILRESA Firmware Updater integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "bilresa_updater"

# Domain of the official Home Assistant Matter integration we piggy-back on.
MATTER_DOMAIN: Final = "matter"

# Config keys.
CONF_URL: Final = "url"

# Default Matter Server websocket URL (matches the official add-on default).
DEFAULT_MATTER_URL: Final = "ws://localhost:5580/ws"

# IKEA of Sweden Matter vendor id (0x117C).
IKEA_VENDOR_ID: Final = 0x117C

# Substring used to recognise BILRESA remotes by product name.
PRODUCT_NAME_MATCH: Final = "BILRESA"

# Matter cluster ids.
BASIC_INFORMATION_CLUSTER_ID: Final = 0x0028
OTA_REQUESTOR_CLUSTER_ID: Final = 0x002A
ICD_MANAGEMENT_CLUSTER_ID: Final = 0x0046

# ICD Management command ids.
STAY_ACTIVE_REQUEST_COMMAND_ID: Final = 0x03

# Minimum battery level (percent) required before starting a firmware update.
# A flash interrupted by a dying battery on a battery-powered remote can brick
# the device, so refuse to start below this threshold.
MIN_BATTERY_PERCENT: Final = 20

# Keep-awake tuning.
# Duration (ms) we ask the ICD to remain in active mode on each request.
KEEP_AWAKE_DURATION_MS: Final = 60_000
# Re-issue the request once this fraction of the promised duration has elapsed.
KEEP_AWAKE_REARM_RATIO: Final = 0.75
# Never re-arm faster than this (seconds) to avoid hammering a sleepy device.
KEEP_AWAKE_MIN_INTERVAL: Final = 4
# Used when the device does not return a usable PromisedActiveDuration.
KEEP_AWAKE_FALLBACK_INTERVAL: Final = 20

# Background task names.
LISTEN_TASK_NAME: Final = "bilresa_updater_matter_listen"

# Human readable names for the OtaSoftwareUpdateRequestor UpdateState enum.
OTA_UPDATE_STATE_NAMES: Final[dict[int, str]] = {
    0: "unknown",
    1: "idle",
    2: "querying",
    3: "delayed_on_query",
    4: "downloading",
    5: "applying",
    6: "rolling_back",
    7: "delayed_on_apply",
}

# UpdateState names that mean nothing is happening. Any state NOT in this set
# means a firmware transfer is underway and the sleepy device must be held in
# active mode. We deliberately treat "querying"/"delayed_on_query" as active so
# the keep-awake loop starts before the stall-prone querying -> idle window.
IDLE_OTA_STATES: Final[frozenset[str]] = frozenset({"unknown", "idle"})

# ICD Management OperatingMode enum.
ICD_OPERATING_MODE_NAMES: Final[dict[int, str]] = {
    0: "sit",
    1: "lit",
}
