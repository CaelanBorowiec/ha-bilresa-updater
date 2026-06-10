<p align="center">
  <img src="icon.png" alt="IKEA BILRESA Firmware Updater" width="200">
</p>

# IKEA BILRESA Firmware Updater (HACS)

<p align="center">
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg" alt="HACS Custom repository"></a>
  <a href="https://github.com/CaelanBorowiec/ha-bilresa-updater/releases"><img src="https://img.shields.io/github/release/CaelanBorowiec/ha-bilresa-updater.svg" alt="GitHub Release"></a>
  <a href="https://github.com/CaelanBorowiec/ha-bilresa-updater/actions/workflows/validate.yml"><img src="https://github.com/CaelanBorowiec/ha-bilresa-updater/actions/workflows/validate.yml/badge.svg" alt="Validate"></a>
</p>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=CaelanBorowiec&repository=ha-bilresa-updater&category=integration">
    <img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.">
  </a>
  <a href="https://my.home-assistant.io/redirect/config_flow_start/?domain=bilresa_updater">
    <img src="https://my.home-assistant.io/badges/config_flow_start.svg" alt="Add Integration">
  </a>
</p>

This Home Assistant custom integration assists firmware updates to IKEA BILRESA
Matter-over-Thread remotes (dual-button and scroll-wheel variants) and reliably
finishes the firmware transfer without making you press buttons to prevent the
remote sleeping.

It works for any firmware version your device reports an update for (for example
`1.8.5` → `1.9.15`). Home Assistant's built-in Matter integration already
handles the OTA download from the CSA Distributed Compliance Ledger (DCL); this
integration keeps the sleepy remote awake so that transfer can complete.

<p align="center">
  <img src="bilresa-product.png" alt="IKEA BILRESA remote" width="320">
</p>

## The problem

BILRESA firmware updates from Home Assistant usually stall or fail because the
remote falls asleep mid-transfer. The common workaround is to stand there
pressing buttons on the remote for the entire update. This integration does that
for you digitally: it notices an update starting (from HA, Apple Home, Google
Home, or anywhere else) and keeps the remote awake until the update finishes.

Install it once and forget it; it sits idle until a firmware update begins.

## Features

Verified on real BILRESA hardware (Long Idle Time ICD mode):

- Hands-free end-to-end firmware update via the native HA **Firmware** Update
  entity
- Detects updates the moment they start, from any controller, and stops
  keep-awake when the device returns to idle
- Picks up updates already in progress after an HA restart
- Survives transient Thread radio dropouts and retries through a transfer
- **Keep awake now** button for manual nudging or testing
- Feature-detected `StayActiveRequest` support (falls back to button-press
  instructions if the device does not accept the command)

## How it works

This integration does **not** add its own update button. The official Matter
integration already exposes a working **Firmware** Update entity for the BILRESA.
The only thing it lacks is keeping the sleepy device awake.

This integration runs in the background: it connects to your Matter Server as a
second websocket client, watches each BILRESA's OTA `UpdateState`, and the
moment an update starts it fires a `StayActiveRequest` keep-awake loop until
the transfer finishes.

```
You press the native "Firmware" Update button (or any controller starts an OTA)
        │
        ▼
Matter Server runs the OTA Provider + BDX transfer
        │  OTA UpdateState → querying / downloading / applying
        ▼
this integration (watching UpdateState) ──► StayActiveRequest loop ──► BILRESA
                                            (re-armed until state returns to idle)
```

The BILRESA is a battery-powered Matter **Intermittently Connected Device (ICD)**.
To save power it polls slowly; during a firmware update it must stay in **active
mode** (fast polling) so the Block Data Exchange transfer can run. While an
update runs, this integration issues the Matter **`StayActiveRequest`** command
(ICD Management cluster `0x0046`) on a timer, re-arming before each
`PromisedActiveDuration` expires: the button-free equivalent of holding the
remote awake.

## Requirements

- Home Assistant 2024.12 or newer
- The official **Matter (BETA)** integration set up and working, with a Thread
  border router (HA Connect ZBT-1, a Thread radio, or a Dirigera hub acting as a
  border router) and IPv6 enabled on the Home Assistant host
- The BILRESA must already be **commissioned to Home Assistant's Matter fabric**
  (it shows up as a device under the Matter integration)

## Installation

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=CaelanBorowiec&repository=ha-bilresa-updater&category=integration)

1. In HACS, add this repository as a **custom repository** (category:
   _Integration_), or install from the HACS default store once listed.
2. Install **IKEA BILRESA Firmware Updater** and restart Home Assistant.
3. Go to **Settings → Devices & Services → Add Integration** and search for
   _IKEA BILRESA Firmware Updater_.
4. Confirm the Matter Server URL (pre-filled from your Matter integration).

## Usage

To update firmware, use the **Firmware** Update entity that the official Matter
integration already exposes on the device. This integration adds, on that same
device:

- **Keep-awake active** binary sensor: on while holding the device awake for an
  OTA
- Diagnostic sensors: OTA update state, ICD operating mode (SIT/LIT), and last
  promised active duration
- **Keep awake now** button: sends a single `StayActiveRequest` for manual
  nudging or testing

### Tip: stay close to your parent Thread router

For the fastest, most reliable update, move the BILRESA within a metre or two
of the Thread router it is currently attached to before you start the firmware
update. The transfer is a large download over a low-power mesh; a strong,
direct link to the parent router means fewer hops, higher throughput, and fewer
radio dropouts.

To find which router your remote is using:

1. In Home Assistant, go to **Settings → Apps → Matter Server** (or **Settings →
   Add-ons → Matter Server** on older installs) and open the **Web UI**.
2. Select the **Thread** tab to open the mesh topology map.
3. Click your BILRESA node in the graph. The details panel shows its **parent
   router** (for example your HA Connect ZBT-1/ZBT-2, IKEA Dirigera hub, Google
   Nest Hub, or Apple HomePod) and the link quality to that router.
4. Place the remote next to that router for the duration of the update, then
   return it to its normal location when the OTA state returns to idle.

The map is built from Matter neighbor data and can be incomplete or slow to
refresh. If your device does not appear, reload the Thread view or check
**Settings → Matter → Devices**, select the BILRESA, and confirm **Network
type** is Thread. If you run the **OpenThread Border Router** add-on, you can
also open its web UI (enable the Web UI and REST API ports in the add-on
configuration) and use the **Topology** page as an alternative view.

## Configuration

In the integration's **Configure** dialog you can tune the keep-awake re-send
interval (default: 15 seconds, range 4–60). This is how often the integration
re-sends `StayActiveRequest` during an update when the device does not report a
usable `PromisedActiveDuration`. Field reports suggest 10–15 s is more reliable
than longer intervals; lower values use slightly more battery.

## Testing notes

Verified against a complete end-to-end update (`1.8.5` → `1.9.15`) driven by the
native Matter Update entity:

- The BILRESA advertises `StayActiveRequest` (command `0x03`) in its ICD
  Management `AcceptedCommandList`.
- The device returns a `PromisedActiveDuration` of **30 seconds** per request.
  The keep-awake loop re-arms at 50% of that promise (~15 s) to avoid gaps in
  active-mode coverage.
- OTA state transitions are detected in real time via per-node Matter Server
  event subscriptions.
- During sustained BDX transfer activity, individual `StayActiveRequest` sends
  can fail while the download continues; the loop retries through these failures
  instead of giving up.
- A Thread radio dropout mid-transfer can still abort a download (the device
  then restarts from 0% on the next retry). Keep-awake greatly reduces but cannot
  fully eliminate this.

## Limitations

- `StayActiveRequest` is optional in the Matter spec. If a device does not accept
  it, the integration logs which button to press (from the device's
  `UserActiveModeTriggerInstruction`) and falls back to the normal flow.
- Only one firmware update runs at a time (enforced by both this integration and
  the Matter Server).
- Thread / mDNS / IPv6 problems can block OTA entirely. If an update fails,
  check that the device is reachable and that the Matter Server can serve the
  image.

## License

This project is licensed under the [MIT License](LICENSE).

## Disclaimer

Firmware updates carry inherent risk. This is a community project and is not
affiliated with or endorsed by IKEA or the Connectivity Standards Alliance. Use
at your own risk.
