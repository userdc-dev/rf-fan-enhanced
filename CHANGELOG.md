# 2.5.0 — 2026-10-28

Added the reusable visual RF Fan Remote card, persistent Android/Companion App loading, automatic device discovery, stronger mobile fan separation, always-visible physical timer controls, Safe OFF, backend countdown timers, configurable light-cycle presets and supported config-entry cleanup. Existing RF signals and entity identities are retained.

# 2.3.0 — 2026-10-28

Alphabetical configuration, remote dashboard generation, per-command replay, batch relearning, command library, safe export/import, state correction, timers and diagnostics. Existing learned codes and entity identities are retained. See README for installation and usage.

# Changelog

## 2.2.0

- Expanded first-time fan setup to include light style, optional colour and
  brightness buttons, timer buttons, direction, physical oscillation,
  swing/breeze and natural-wind controls.
- All selected controls are learned during the original guided setup; opening
  Configure afterward is no longer required to complete a typical fan remote.

## 2.1.0

- Added selectable fan power styles: one On/Off toggle, Off-only, or separate
  On and Off buttons.

## 2.0.0

First release of the enhanced fork maintained by
[@userdc-dev](https://github.com/userdc-dev).

- Added clear English setup and configuration menus.
- Added an eight-speed fan slider and support for learning more speeds later.
- Added optional timer, direction, physical oscillation, swing/breeze, light
  and light-colour controls.
- Added safe add, re-learn and remove flows without resetting the fan.
- Added a preselected checklist of learned optional controls; deselecting an
  item and confirming removes both the learned command and its entity cleanly.
- Added per-device raw or filtered RF replay to keep Goldair and other
  non-Mercator remotes reliable.
- Preserved existing learned commands and Home Assistant config entries during
  upgrades.

## Original project

This project is derived from
[clevrdavid/rf_fan](https://github.com/clevrdavid/rf_fan), created by
[@clevrdavid](https://github.com/clevrdavid), under the MIT License.
