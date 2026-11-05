RF Remote Enhanced 3.0.0 introduces the new `rf_remote` Home Assistant integration domain.

- Adds a guided migration from existing `rf_fan` config entries.
- Creates a private backup before migration.
- Transfers learned RF commands and config-entry options without relearning.
- Moves entity-registry and device-registry ownership to `rf_remote`, preserving existing entity IDs, device IDs and saved one-fan card selections.
- Keeps `rf_fan.*` service aliases for existing automations.
- Updates existing Lovelace resources from the legacy frontend URL to `/rf_remote_static/`, while preserving the card type and dashboard route.
- Retains the dashboard storage key, private snapshot folder and command-export format.

The RF learning and replay backend remains Broadlink-based. This release does not add ESPHome, Tuya or other transmitter backends. No RF protocol or command encoding changes are included.
