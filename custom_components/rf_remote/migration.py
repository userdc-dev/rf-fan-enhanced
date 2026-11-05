"""Safe migration from the legacy rf_fan config-entry domain."""

from __future__ import annotations

from copy import deepcopy
import logging
from typing import Any

LEGACY_DOMAIN = "rf_fan"
DOMAIN = "rf_remote"
LEGACY_ENTRY_KEY = "_legacy_rf_fan_entry_id"
_LOGGER = logging.getLogger(__name__)


def migrate_legacy_data(data: dict[str, Any], version: int) -> dict[str, Any]:
    """Normalize legacy config-entry data without changing learned RF commands."""
    migrated = deepcopy(dict(data))
    if version == 1:
        legacy_customs = migrated.pop("customs", []) or []
        sub_entities = list(migrated.get("sub_entities", []) or [])
        commands = migrated.get("commands", {}) or {}
        for custom in legacy_customs:
            if (
                isinstance(custom, dict)
                and custom.get("key") in commands
                and custom.get("name")
            ):
                sub_entities.append(
                    {
                        "entity_type": "button",
                        "name": custom["name"],
                        "key_prefix": custom["key"],
                    }
                )
        migrated["sub_entities"] = sub_entities
    return migrated


async def async_migrate_legacy_entry(hass, entry) -> None:
    """Move one rf_fan config entry to rf_remote, preserving entity IDs and device."""
    from homeassistant.config_entries import ConfigEntryState
    from homeassistant.exceptions import ConfigEntryNotReady
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    legacy_entry_id = entry.data.get(LEGACY_ENTRY_KEY)
    if not legacy_entry_id:
        return
    if not isinstance(legacy_entry_id, str):
        raise ConfigEntryNotReady("The legacy RF entry reference is invalid")

    legacy_entry = hass.config_entries.async_get_entry(legacy_entry_id)
    if legacy_entry is not None and legacy_entry.domain != LEGACY_DOMAIN:
        raise ConfigEntryNotReady("The selected legacy entry is not an rf_fan entry")

    # Keep a private recovery snapshot before moving any registry records.
    if legacy_entry is not None:
        from .manager import async_snapshot

        try:
            await async_snapshot(hass, legacy_entry)
        except Exception as err:  # noqa: BLE001 - do not migrate without a backup
            raise ConfigEntryNotReady(
                f"Could not create a pre-migration backup: {type(err).__name__}"
            ) from err

        if legacy_entry.state == ConfigEntryState.LOADED:
            if not await hass.config_entries.async_unload(legacy_entry_id):
                raise ConfigEntryNotReady(
                    "The legacy RF Fan entry could not be unloaded safely"
                )
        elif (
            legacy_entry.state in (
                ConfigEntryState.SETUP_IN_PROGRESS,
                ConfigEntryState.UNLOAD_IN_PROGRESS,
            )
            or not legacy_entry.state.recoverable
        ):
            raise ConfigEntryNotReady(
                "The legacy RF Fan entry is not in a safe state to migrate; restart Home Assistant and try again"
            )

    entity_registry = er.async_get(hass)
    legacy_entities = [
        row
        for row in entity_registry.entities.values()
        if row.platform == LEGACY_DOMAIN and row.config_entry_id == legacy_entry_id
    ]

    # Validate every unique ID before changing anything. Entity IDs themselves are
    # retained, so automations and dashboards continue to point at the same entities.
    entity_updates = []
    for row in legacy_entities:
        prefix = f"{legacy_entry_id}_"
        if not row.unique_id.startswith(prefix):
            raise ConfigEntryNotReady(
                f"Cannot safely map the unique ID for {row.entity_id}"
            )
        new_unique_id = f"{entry.entry_id}_{row.unique_id[len(prefix):]}"
        conflict = entity_registry.async_get_entity_id(
            row.domain, DOMAIN, new_unique_id
        )
        if conflict and conflict != row.entity_id:
            raise ConfigEntryNotReady(
                f"Entity registry conflict while migrating {row.entity_id}"
            )
        entity_updates.append((row, new_unique_id))

    device_registry = dr.async_get(hass)
    legacy_devices = [
        device
        for device in device_registry.devices.values()
        if device.config_entry_id == legacy_entry_id
    ]

    for row, new_unique_id in entity_updates:
        entity_registry.async_update_entity_platform(
            row.entity_id,
            DOMAIN,
            new_config_entry_id=entry.entry_id,
            new_unique_id=new_unique_id,
            new_device_id=row.device_id,
        )

    for device in legacy_devices:
        identifiers = set(device.identifiers)
        identifiers.discard((LEGACY_DOMAIN, legacy_entry_id))
        identifiers.add((DOMAIN, entry.entry_id))
        device_registry.async_update_device(
            device.id,
            new_config_entry_id=entry.entry_id,
            new_identifiers=identifiers,
        )

    # The old entry can now be removed without deleting the moved entity/device
    # registry records. If it was already absent, this also supports a safe retry.
    if legacy_entry is not None:
        await hass.config_entries.async_remove(legacy_entry_id)

    data = dict(entry.data)
    data.pop(LEGACY_ENTRY_KEY, None)
    hass.config_entries.async_update_entry(entry, data=data)

    _LOGGER.info(
        "Migrated legacy rf_fan entry %s to rf_remote entry %s; preserved %d entities",
        legacy_entry_id,
        entry.entry_id,
        len(entity_updates),
    )
