"""Create a native remote dashboard from this installation's entity registry."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

URL_PATH = "rf-fan-remotes"
FILENAME = "rf_fan_remote_dashboard.yaml"


def build_dashboard(entries, entities):
    """Use one live card so device changes never require rewriting Lovelace."""
    return {
        "title": "RF Fan remotes",
        "views": [{
            "title": "RF Fan remotes",
            "path": "remote-1",
            "icon": "mdi:remote",
            "cards": [{"type": "custom:rf-fan-remote-card", "device_id": "all"}],
        }],
    }


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
    store = Store(hass, 1, "rf_fan_dashboard")
    saved = await store.async_load() or {}
    content = json.dumps(config, indent=2)
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    def save():
        folder = Path(hass.config.path("rf_fan_safety"))
        def prune_snapshots():
            if not folder.exists():
                return
            snapshots = sorted(folder.glob("dashboard_*.yaml"), reverse=True)
            for stale in snapshots[5:]:
                stale.unlink()
        if path.exists():
            existing_content = path.read_text(encoding="utf-8")
            existing_hash = hashlib.sha256(existing_content.encode("utf-8")).hexdigest()
            if existing_hash == content_hash:
                prune_snapshots()
                return
            if saved.get("content_hash") and existing_hash != saved["content_hash"]:
                raise HomeAssistantError(
                    "The generated RF Fan dashboard was changed outside this integration; it was not overwritten"
                )
            folder.mkdir(mode=0o700, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            shutil.copy2(path, folder / f"dashboard_{stamp}.yaml")
            prune_snapshots()
        temporary = path.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    await hass.async_add_executor_job(save)
    async_register_dashboard(hass)
    await store.async_save({"enabled": True, "content_hash": content_hash})
    await data.dashboards[URL_PATH].async_load(True)


async def async_restore_dashboard(hass):
    saved = await Store(hass, 1, "rf_fan_dashboard").async_load()
    if saved and saved.get("enabled"):
        try:
            async_register_dashboard(hass)
        except HomeAssistantError:
            # A renamed/removed panel must never stop the fan integration loading.
            return
