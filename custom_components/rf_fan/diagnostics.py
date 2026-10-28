"""Diagnostics without replayable packets, credentials or hardware addresses."""
import hashlib
import json


def command_summary(data):
    return {key: {"pulses": len(command.get("timings", [])),
                  "original_available": bool(command.get("b64")),
                  "mode": command.get("replay_mode", data.get("replay_mode", "legacy")),
                  "sha256": hashlib.sha256(json.dumps(command, sort_keys=True).encode()).hexdigest()}
            for key, command in data.get("commands", {}).items()}


async def async_get_config_entry_diagnostics(hass, entry):
    return {"version": 1, "speed_count": entry.data.get("speed_count"),
            "direct_capture": entry.data.get("direct_capture", False),
            "commands": command_summary(entry.data),
            "last_transmission": hass.data.get("rf_fan_runtime", {}).get("last_send", {}).get(entry.data.get("transmitter")),
            "physical_response": "not_observable_without_feedback"}
