# RF Remote Enhanced developer handover

## Purpose

RF Remote Enhanced is a Home Assistant custom integration for learning and replaying
one-way RF ceiling-fan commands through Home Assistant's `radio_frequency`
platform. The active integration domain is `rf_remote`. Version 3.0.0 includes a guided migration from legacy `rf_fan` config entries. The migration transfers entry data/options, updates entity and device registry ownership, and preserves entity IDs and device IDs. Keep the legacy service aliases and stored command format until a future explicitly announced removal.

## Per-device Lovelace card

The reusable card is bundled with the integration at
`custom_components/rf_remote/frontend/rf-fan-remote-card.js`. Home Assistant serves
it from `/rf_fan_static/` and `frontend.py` registers it as a Lovelace module.
Users do not install a separate frontend repository or edit dashboard YAML.

The visual flow is:

1. Add card.
2. Choose **RF Fan Remote**.
3. Select one fan in the editor.
4. Save.

Normal cards store only `fan_id`, whose value is
`<config_entry_id>:<key_prefix-or-primary>`. This remains stable when a device or
entity is renamed. The integration-generated sidebar dashboard deliberately uses
`device_id: all`; that special value must not be offered by the normal card
editor.

The card obtains safe metadata through the `rf_remote/devices` WebSocket command in
`frontend.py`. Replayable RF data must never be returned to the browser. The
`rf_fan_updated` event refreshes cards and open editors when devices, commands or
names change. A manually placed card whose device was deleted remains in the
user's dashboard and displays an unavailable-device message.

## Backend/frontend contract

Each device response contains an array of fans. A fan record provides:

- stable `fan_id`;
- current fan and optional light entity IDs;
- configured speed count and Safe OFF availability;
- command labels/categories without RF packets;
- optional relative light-cycle configuration.

Keep command execution on the backend through Home Assistant services. Do not
copy timing data, Broadlink packets or replay logic into JavaScript.

## Packaging and installation

This is a HACS integration repository. Required release files are committed in
GitHub, including `hacs.json`, `manifest.json`, the frontend module, translations,
services and Python sources. A GitHub release/tag matching `manifest.json` is the
installable version. The frontend cache key in `frontend.py` must also change
whenever the JavaScript changes.

The release workflow runs HACS and Home Assistant validation. Before publishing:

1. Run `python -m compileall -q custom_components/rf_remote`.
2. Run `python tests/test_commands.py`.
3. Run `node --check custom_components/rf_remote/frontend/rf-fan-remote-card.js`.
4. Run `node tests/test_frontend_card.mjs`.
5. Run `git diff --check`.
6. Confirm `manifest.json`, `frontend.py` and release notes use the intended
   version.
7. Upgrade an existing installation without deleting its config entries and
   verify learned-command fingerprints are unchanged.

## Safety and compatibility

- RF state is assumed; a successful send is not physical confirmation.
- Preserve serialized fan operations and Safe OFF locking.
- Preserve command ownership between primary and sub-fans.
- Never edit Home Assistant `.storage` files directly.
- Device deletion may remove only integration-owned records. Shared Broadlink
  devices and user-created dashboards, scripts and automations are out of scope.
- Keep the generated all-fans dashboard separate from reusable one-fan cards.



## Domain migration (3.0.0)

The HACS repository contains one integration directory, now
`custom_components/rf_remote/`. Do not add a second top-level integration
folder for `rf_fan`: HACS manages only one integration directory per
repository. The migration wizard discovers old `rf_fan` config entries when
the user adds RF Remote Enhanced.

The migration first creates a private snapshot, unloads the legacy entry when
possible, changes each entity-registry platform and unique ID to the new domain
while keeping the existing entity ID, moves the device-registry entry to the
new config entry while keeping its device ID, and then removes the old config
entry. Never edit `.storage` directly. The old service namespace
`rf_fan.*` remains registered as an alias for existing automations; the
frontend's historic static URL and dashboard storage identifiers are also
retained deliberately.
