"""Frontend registration and data API for the reusable RF remote card."""
from __future__ import annotations

from pathlib import Path
import voluptuous as vol

from homeassistant.components import frontend, websocket_api
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .const import (
    CONF_COMMANDS,
    CONF_LIGHT_CYCLE,
    CONF_LIGHT_CYCLE_CLICKS,
    CONF_SAFE_OFF,
    CONF_SPEED_COUNT,
    CONF_SUB_ENTITIES,
    DOMAIN,
)
from .extra_buttons import EXTRA_BUTTONS

CARD_PATH = "/rf_fan_static/rf-fan-remote-card.js"
CARD_URL = f"{CARD_PATH}?v=2.5.0"


def _category(key: str) -> str:
    if key.startswith("timer_"):
        return "physical_timer"
    if key == "light" or key.startswith("light_") or key.startswith("brightness_"):
        return "lighting"
    if key in {"direction_toggle", "natural_breeze", "swing_air", "swing"}:
        return "mode"
    if key in {"on", "off"} or key.startswith("speed_"):
        return "fan"
    return "other"


def _label(key: str, command: dict) -> str:
    return command.get("label") or EXTRA_BUTTONS.get(key) or key.replace("_", " ").title()


def _devices(hass: HomeAssistant) -> list[dict]:
    entity_registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    result = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        data = entry.data
        commands = data.get(CONF_COMMANDS, {})
        device = device_registry.async_get_device(identifiers={(DOMAIN, entry.entry_id)})
        registry_rows = er.async_entries_for_config_entry(entity_registry, entry.entry_id)
        by_unique = {(row.domain, row.unique_id): row.entity_id for row in registry_rows if not row.disabled_by}
        sub_prefixes = {
            sub.get("key_prefix") for sub in data.get(CONF_SUB_ENTITIES, []) if sub.get("key_prefix")
        }
        fan_defs = [{"name": entry.title, "prefix": "", "speed_count": data.get(CONF_SPEED_COUNT, 3)}]
        fan_defs.extend(
            {"name": sub.get("name", entry.title), "prefix": sub.get("key_prefix", ""), "speed_count": sub.get("speed_count", 3)}
            for sub in data.get(CONF_SUB_ENTITIES, []) if sub.get("entity_type") == "fan"
        )
        fans = []
        for item in fan_defs:
            prefix = item["prefix"]
            unique = f"{entry.entry_id}_fan_{prefix}" if prefix else f"{entry.entry_id}_fan"
            fan_entity = by_unique.get(("fan", unique))
            if not fan_entity:
                continue
            available = []
            for full_key, command in commands.items():
                if prefix:
                    marker = f"{prefix}_"
                    if not full_key.startswith(marker):
                        continue
                    key = full_key[len(marker):]
                else:
                    if any(full_key.startswith(f"{candidate}_") for candidate in sub_prefixes):
                        continue
                    key = full_key
                available.append({
                    "command": full_key,
                    "key": key,
                    "label": _label(key, command),
                    "category": _category(key),
                })
            light_unique = f"{entry.entry_id}_light_{prefix}" if prefix else f"{entry.entry_id}_light"
            fans.append({
                **item,
                "entity_id": fan_entity,
                "light_entity_id": by_unique.get(("light", light_unique)),
                "safe_off_enabled": bool(data.get(CONF_SAFE_OFF, any(c["key"] == "speed_1" for c in available) and any(c["key"] == "off" for c in available))),
                "light_cycle": ({"cool": 1, "neutral": 3, "warm": 5, **data.get(CONF_LIGHT_CYCLE_CLICKS, {})}
                    if data.get(CONF_LIGHT_CYCLE, any(c["key"] == "light" for c in available)) and any(c["key"] == "light" for c in available)
                    else None),
                "commands": available,
            })
        result.append({
            "device_id": device.id if device else entry.entry_id,
            "entry_id": entry.entry_id,
            "name": entry.title,
            "fans": fans,
        })
    return result


@websocket_api.websocket_command({vol.Required("type"): "rf_fan/devices"})
@websocket_api.async_response
async def websocket_devices(hass, connection, msg):
    """Return safe metadata only; RF packets never leave the backend."""
    connection.send_result(msg["id"], _devices(hass))


async def _async_register_persistent_resource(hass: HomeAssistant) -> bool:
    """Register through Lovelace after it has finished starting."""
    from homeassistant.components.lovelace.const import LOVELACE_DATA

    lovelace = hass.data.get(LOVELACE_DATA)
    if lovelace is None or not hasattr(lovelace.resources, "async_create_item"):
        return False
    resources = lovelace.resources
    await resources.async_get_info()
    existing = next(
        (item for item in resources.async_items() if item.get("url", "").split("?", 1)[0] == CARD_PATH),
        None,
    )
    if existing is None:
        await resources.async_create_item({"url": CARD_URL, "res_type": "module"})
    elif existing.get("url") != CARD_URL or existing.get("type") != "module":
        await resources.async_update_item(existing["id"], {"url": CARD_URL, "res_type": "module"})
    frontend.remove_extra_js_url(hass, CARD_URL)
    return True


async def async_setup_frontend(hass: HomeAssistant) -> None:
    """Serve and register the card once per Home Assistant run."""
    state = hass.data.setdefault(DOMAIN, {})
    if state.get("frontend_setup"):
        return
    folder = Path(__file__).parent / "frontend"
    await hass.http.async_register_static_paths([
        StaticPathConfig("/rf_fan_static", str(folder), cache_headers=False)
    ])
    # The fallback covers startup before Lovelace exists. The persistent
    # resource below is what fresh Android Companion App WebViews consume.
    frontend.add_extra_js_url(hass, CARD_URL)
    await _async_register_persistent_resource(hass)
    @callback
    def retry_resource(_now):
        hass.async_create_task(_async_register_persistent_resource(hass))
    async_call_later(hass, 5, retry_resource)
    websocket_api.async_register_command(hass, websocket_devices)
    state["frontend_setup"] = True
