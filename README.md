# RF Fan Enhanced

A Home Assistant custom integration for learning and replaying ceiling-fan RF
remote controls through an RF-capable Broadlink device.

This fork is maintained by [@userdc-dev](https://github.com/userdc-dev). It
extends the original `clevrdavid/rf_fan` integration with improvements created
for userdc-dev's Home Assistant fan setup:

- English setup and configuration screens
- Up to eight learned fan speeds with slider control
- Optional timer, direction, swing, breeze and light-related buttons
- Fan lights default to one learned On/Off toggle command, matching most fan
  remotes; separate On and Off commands are also supported when the remote has
  two distinct light buttons
- Fan power supports one On/Off toggle button, an Off-only button with speeds
  used to turn on, or separate On and Off buttons
- The initial fan wizard can learn light controls, colour and brightness
  buttons, timers, direction, oscillation, swing air and natural breeze in one
  guided setup; the Configure menu remains available for later changes
- Safe addition and re-learning of individual controls
- A preselected learned-button checklist with confirmed, clean removal
- Per-device raw or filtered RF replay so non-Mercator fans remain reliable
- Preservation of existing learned commands during upgrades and configuration

## Installation with HACS

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=userdc-dev&repository=rf-fan-enhanced&category=integration)

After downloading and restarting Home Assistant:

[![Add RF Fan Enhanced to Home Assistant](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=rf_fan)

1. Install HACS in Home Assistant.
2. Open **HACS → Integrations → Custom repositories**.
3. Add `https://github.com/userdc-dev/rf-fan-enhanced` with category
   **Integration**.
4. Open **RF Fan Enhanced**, select **Download**, and restart Home Assistant.
5. Open **Settings → Devices & services → Add integration**, search for
   **RF Fan Enhanced**, and follow the learning wizard.

The repository follows the standard HACS layout and is checked automatically
with both the HACS validator and Home Assistant's hassfest validator.

## Backup and disaster recovery

The GitHub repository permanently preserves the integration itself, so it can
be reinstalled on a completely new Home Assistant system using the steps above.

Learned RF commands are specific to your remotes and are stored in Home
Assistant's configuration entries, not in GitHub. To restore those commands
without relearning them, keep a regular Home Assistant backup that includes
the `.storage` configuration data. If no Home Assistant backup survives, the
integration can still be reinstalled from GitHub, but each remote command must
be learned again.

## Requirements

- Home Assistant 2026.5 or newer
- An RF-capable Broadlink already configured in Home Assistant

## Attribution

The original integration was created by
[@clevrdavid](https://github.com/clevrdavid) at
[clevrdavid/rf_fan](https://github.com/clevrdavid/rf_fan). This enhanced fork
is maintained by [@userdc-dev](https://github.com/userdc-dev) and remains
distributed under the original MIT License.

