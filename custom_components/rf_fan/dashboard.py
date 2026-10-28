"""Create a native remote dashboard from this installation's entity registry."""
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

URL_PATH = "rf-fan-remotes"
FILENAME = "rf_fan_remote_dashboard.yaml"


def build_dashboard(entries, entities):
    views = []
    for entry in entries:
        rows = [e for e in entities if e.config_entry_id == entry.entry_id and not e.disabled_by]
        fans = [e for e in rows if e.entity_id.startswith("fan.")]
        cards = [{"type": "markdown", "content": f"# {entry.title.strip()}\nRF state is assumed. Use **Configure** in RF Fan Enhanced to learn or manage buttons."}]
        for entity in fans:
            fan = entity.entity_id
            prefix = entity.unique_id.removeprefix(entry.entry_id + "_fan_") if entity.unique_id != entry.entry_id + "_fan" else ""
            sub = next((s for s in entry.data.get("sub_entities", []) if s.get("key_prefix") == prefix), {})
            count = sub.get("speed_count", entry.data.get("speed_count", 3))
            cards.extend([
                {"type": "tile", "entity": fan, "features": [{"type": "fan-speed"}]},
                {"type": "grid", "columns": 3, "square": False, "cards": [
                    {"type": "button", "name": f"Speed {i}", "icon": "mdi:fan", "tap_action": {
                        "action": "perform-action", "perform_action": "fan.set_percentage",
                        "target": {"entity_id": fan}, "data": {"percentage": int(i * 100 / count)}}}
                    for i in range(1, count + 1)]},
                {"type": "grid", "columns": 2, "square": False, "cards": [
                    {"type": "button", "name": name, "icon": icon, "tap_action": {
                        "action": "perform-action", "perform_action": "rf_fan.set_timer",
                        "target": {"entity_id": fan}, "data": {"minutes": minutes}}}
                    for name, icon, minutes in (("Software timer: 2 hours", "mdi:timer-outline", 120), ("Cancel software timer", "mdi:timer-off-outline", 0))]},
                {"type": "entities", "title": "Assumed state and timer", "show_header_toggle": False, "entities": [fan,
                    {"type": "attribute", "entity": fan, "attribute": "state_uncertain", "name": "State uncertain"},
                    {"type": "attribute", "entity": fan, "attribute": "timer_end", "name": "Software timer ends"}]},
            ])
        buttons = [e for e in rows if e.entity_id.startswith("button.")]
        if buttons:
            cards.append({"type": "grid", "columns": 3, "square": False, "cards": [
                {"type": "button", "entity": e.entity_id, "show_state": False, "tap_action": {"action": "toggle"}} for e in buttons]})
        others = [e.entity_id for e in rows if e.entity_id.startswith(("light.", "cover."))]
        if others:
            cards.append({"type": "entities", "entities": others, "show_header_toggle": False})
        views.append({"title": entry.title.strip(), "path": f"remote-{len(views) + 1}", "icon": "mdi:remote", "cards": cards})
    return {"title": "RF Fan remotes", "views": views}


def async_register_dashboard(hass):
    from homeassistant.components import frontend
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    from homeassistant.components.lovelace.dashboard import LovelaceYAML

    data = hass.data.get(LOVELACE_DATA)
    if data is None:
        raise HomeAssistantError("The Home Assistant dashboard frontend is not ready")
    if URL_PATH in data.dashboards:
        existing = data.dashboards[URL_PATH]
        if getattr(existing, "path", None) != hass.config.path(FILENAME):
            raise HomeAssistantError("Another dashboard already uses rf-fan-remotes; it was not overwritten")
        return
    if frontend.async_panel_exists(hass, URL_PATH):
        raise HomeAssistantError("Another panel already uses rf-fan-remotes")
    data.dashboards[URL_PATH] = LovelaceYAML(hass, URL_PATH, {"filename": FILENAME, "mode": "yaml"})
    frontend.async_register_built_in_panel(hass, "lovelace", frontend_url_path=URL_PATH,
        sidebar_title="RF Fan remotes", sidebar_icon="mdi:remote", config={"mode": "yaml"}, require_admin=False)


async def async_create_dashboard(hass):
    # Validate namespace ownership before writing a file or registering a panel.
    from homeassistant.components.lovelace.const import LOVELACE_DATA
    data = hass.data.get(LOVELACE_DATA)
    if data is None:
        raise HomeAssistantError("The Home Assistant dashboard frontend is not ready")
    existing = data.dashboards.get(URL_PATH)
    if existing is not None and getattr(existing, "path", None) != hass.config.path(FILENAME):
        raise HomeAssistantError("Another dashboard already uses rf-fan-remotes; it was not overwritten")
    registry = er.async_get(hass)
    config = build_dashboard(hass.config_entries.async_entries("rf_fan"), list(registry.entities.values()))
    path = Path(hass.config.path(FILENAME))
    def save():
        if path.exists():
            folder = Path(hass.config.path("rf_fan_safety"))
            folder.mkdir(mode=0o700, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            shutil.copy2(path, folder / f"dashboard_{stamp}.yaml")
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(config, indent=2), encoding="utf-8")
        temporary.replace(path)
    await hass.async_add_executor_job(save)
    async_register_dashboard(hass)
    await Store(hass, 1, "rf_fan_dashboard").async_save({"enabled": True})
    await data.dashboards[URL_PATH].async_load(True)


async def async_restore_dashboard(hass):
    saved = await Store(hass, 1, "rf_fan_dashboard").async_load()
    if saved and saved.get("enabled"):
        try:
            async_register_dashboard(hass)
        except HomeAssistantError:
            # A renamed/removed panel must never stop the fan integration loading.
            return
