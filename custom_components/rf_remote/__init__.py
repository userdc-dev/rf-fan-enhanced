"""RF Remote Enhanced Home Assistant integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN, EVENT_UPDATED, EVENT_UPDATED_LEGACY
from .dashboard import async_restore_dashboard
from .frontend import async_setup_frontend
from .migration import async_migrate_legacy_entry, migrate_legacy_data

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.FAN, Platform.COVER, Platform.LIGHT, Platform.BUTTON]


def _fire_updated(hass: HomeAssistant) -> None:
    """Fire the current event and its legacy alias for existing listeners."""
    hass.bus.async_fire(EVENT_UPDATED)
    hass.bus.async_fire(EVENT_UPDATED_LEGACY)


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Migrate config-entry data from earlier RF Remote Enhanced versions."""
    if entry.version == 1:
        data = migrate_legacy_data(entry.data, entry.version)
        hass.config_entries.async_update_entry(entry, data=data, version=2)
        _LOGGER.info("rf_remote: migrated config entry '%s' to version 2", entry.title)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up an RF remote, migrating a legacy rf_fan entry first if requested."""
    await async_migrate_legacy_entry(hass, entry)
    await async_setup_frontend(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_restore_dashboard(hass)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _fire_updated(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload an RF remote."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        _fire_updated(hass)
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Notify the frontend after permanent removal."""
    _fire_updated(hass)


async def async_remove_config_entry_device(hass, config_entry, device_entry) -> bool:
    """A device is one RF remote; deleting it removes its owning entry."""
    if (DOMAIN, config_entry.entry_id) not in device_entry.identifiers:
        return False
    await hass.config_entries.async_remove(config_entry.entry_id)
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the entry when its options change (e.g. after re-learning)."""
    await hass.config_entries.async_reload(entry.entry_id)
    _fire_updated(hass)
