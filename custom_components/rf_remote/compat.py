"""Compatibility helpers for the legacy rf_fan service namespace."""

from __future__ import annotations

from homeassistant.helpers.service import async_register_entity_service

from .const import LEGACY_DOMAIN


def async_register_legacy_entity_service(platform, name, schema, func) -> None:
    """Keep existing rf_fan.* automations working after the domain migration."""
    hass = platform.hass
    if hass.services.has_service(LEGACY_DOMAIN, name):
        return
    async_register_entity_service(
        hass,
        LEGACY_DOMAIN,
        name,
        entities=platform.domain_platform_entities,
        func=func,
        job_type=None,
        schema=schema,
    )
