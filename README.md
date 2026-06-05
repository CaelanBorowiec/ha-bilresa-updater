# IKEA BILRESA Firmware Updater (HACS)

A Home Assistant custom integration that updates **IKEA BILRESA** Matter-over-Thread
remotes (dual-button and scroll-wheel variants) and reliably finishes the firmware
transfer **without making you hold down a button on the remote**.

It works for any firmware version your device reports an update for (for example
`1.8.5 -> 1.9.15`), because it resolves the latest applicable image from the CSA
Distributed Compliance Ledger (DCL) rather than hard-coding versions.

## Why this exists

The BILRESA is a battery-powered Matter **Intermittently Connected Device (ICD) /
Sleepy End Device**. To save power it polls its Thread router slowly. During a
firmware update it must be in **active mode** (fast polling) so the
Block Data Exchange (BDX) transfer can run.

Home Assistant's built-in Matter updater simply announces the OTA Provider and
waits. On a sleepy ICD the device drops back to slow polling and the underlying
`python-matter-server` aborts the update on the `querying -> idle` transition.
The common workaround is to repeatedly press the remote, which fires the Matter
**User Active Mode Trigger** and forces active mode.

This integration automates that. While the update runs it issues the Matter
**`StayActiveRequest`** command (ICD Management cluster `0x0046`) on a timer,
re-arming before each `PromisedActiveDuration` expires — the button-free
equivalent of holding the remote awake.

## How it works

```
Home Assistant (this integration)
  └─ websocket client ─► Matter Server (python-matter-server)
                              ├─ check_node_update  ─► CSA DCL (find latest .ota)
                              ├─ update_node        ─► ephemeral OTA Provider + BDX
                              └─ StayActiveRequest   ─► keeps the BILRESA awake
                                        (issued on a loop by this integration)
```

The integration connects to your existing Matter Server as a *second* websocket
client (the server is designed for multiple consumers), so it shares the same
fabric and OTA Provider as Home Assistant's own Matter integration.

## Requirements

- Home Assistant 2024.12 or newer.
- The official **Matter (BETA)** integration set up and working, with a Thread
  border router (HA Connect ZBT-1, a Thread radio, or a Dirigera hub acting as a
  border router) and IPv6 enabled on the Home Assistant host.
- The BILRESA must already be **commissioned to Home Assistant's Matter fabric**
  (it shows up as a device under the Matter integration).

## Installation (HACS)

1. In HACS, add this repository as a **custom repository** (category: *Integration*).
2. Install **IKEA BILRESA Firmware Updater** and restart Home Assistant.
3. Go to **Settings -> Devices & Services -> Add Integration** and search for
   *IKEA BILRESA Firmware Updater*.
4. Confirm the Matter Server URL (it is pre-filled from your Matter integration).

## What you get

For each discovered BILRESA remote:

- An **Update** entity. Press *Install* to update; progress is read live from the
  device's OTA Requestor cluster and the keep-awake loop runs automatically.
- Diagnostic **sensors**: OTA update state, ICD operating mode (SIT/LIT), and the
  last promised active duration.
- **Buttons**: *Keep awake now* (send a single `StayActiveRequest`) and
  *Retry firmware update* (re-announce the OTA Provider).

## Limitations & notes

- `StayActiveRequest` is optional in the Matter spec. If a device does not accept
  it, the integration logs which button to press (from the device's
  `UserActiveModeTriggerInstruction`) and falls back to the normal flow.
- Only one firmware update runs at a time (enforced by both this integration and
  the Matter Server).
- Thread / mDNS / IPv6 problems can block OTA entirely. If an update fails, check
  that the device is reachable and that the Matter Server can serve the image.

## Disclaimer

Firmware updates carry inherent risk. This is a community project and is not
affiliated with or endorsed by IKEA or the Connectivity Standards Alliance. Use
at your own risk.
