# RF Fan Enhanced

A Home Assistant custom integration for learning and replaying ceiling-fan RF
remote controls through an RF-capable Broadlink device.

This fork is maintained by [@userdc-dev](https://github.com/userdc-dev). It
extends the original `clevrdavid/rf_fan` integration with improvements created
for userdc-dev's Home Assistant fan setup:

- English setup and configuration screens
- Up to eight learned fan speeds with slider control
- Optional timer, direction, swing, breeze and light-related buttons
- Safe addition and re-learning of individual controls
- A preselected learned-button checklist with confirmed, clean removal
- Per-device raw or filtered RF replay so non-Mercator fans remain reliable
- Preservation of existing learned commands during upgrades and configuration

## Installation with HACS

Add this repository to HACS as a custom repository with category
**Integration**, install **RF Fan Enhanced**, and restart Home Assistant.

## Requirements

- Home Assistant 2026.5 or newer
- An RF-capable Broadlink already configured in Home Assistant

## Attribution

The original integration was created by
[@clevrdavid](https://github.com/clevrdavid) at
[clevrdavid/rf_fan](https://github.com/clevrdavid/rf_fan). This enhanced fork
is maintained by [@userdc-dev](https://github.com/userdc-dev) and remains
distributed under the original MIT License.

