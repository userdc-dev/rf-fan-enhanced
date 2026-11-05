RF Remote Enhanced 2.5.6 updates the published integration name while preserving compatibility with existing installations.

- Changes the HACS listing name and Home Assistant integration display name to **RF Remote Enhanced**.
- Keeps the internal integration domain as `rf_fan`. Existing config entries, entity IDs, services, dashboards and learned RF commands are not migrated or recreated.
- Keeps the current Broadlink-based RF learning and replay backend. ESPHome, Tuya and other transmitter backends are not added by this branding release.
- Configures HACS to render the repository README as its information page.

No RF protocol, command format, or transmitter behavior changes in this release.
