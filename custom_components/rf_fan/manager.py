"""Validated command exchange and private, atomic configuration snapshots."""
from __future__ import annotations

from base64 import b64decode
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from uuid import uuid4

MODES = ("legacy", "original", "raw", "clean")


def validate_commands(commands):
    if not isinstance(commands, dict) or len(commands) > 256:
        raise ValueError("Expected at most 256 commands")
    for key, command in commands.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-zA-Z0-9_]{1,100}", key):
            raise ValueError("Invalid command key")
        if not isinstance(command, dict):
            raise ValueError("Invalid command record")
        ts = command.get("timings")
        if not isinstance(ts, list) or not 2 <= len(ts) <= 32768:
            raise ValueError("Invalid timing count")
        if any(type(t) is not int or not 0 < abs(t) <= 2_000_000 for t in ts):
            raise ValueError("Invalid timing value")
        if command.get("replay_mode", "legacy") not in MODES:
            raise ValueError("Invalid replay mode")
        if "b64" in command:
            raw = b64decode(command["b64"], validate=True)
            if not 5 <= len(raw) <= 65540 or raw[0] not in (0xB0, 0xB1, 0xB2, 0xB4, 0xD7):
                raise ValueError("Invalid original RF packet")
            if int.from_bytes(raw[2:4], "little") > len(raw) - 4:
                raise ValueError("Truncated original RF packet")
    return deepcopy(commands)


def export_commands(data):
    payload = {"format": "rf_fan_commands", "version": 1,
               "frequency": data["frequency"], "commands": data["commands"],
               "replay_mode": data.get("replay_mode", "legacy"),
               "direct_capture": data.get("direct_capture", False)}
    payload["sha256"] = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return json.dumps(payload, indent=2)


def import_commands(text, frequency):
    if len(text) > 4_000_000:
        raise ValueError("Import exceeds 4 MB")
    payload = json.loads(text)
    if payload.get("format") != "rf_fan_commands" or payload.get("version") != 1:
        raise ValueError("Unsupported command export")
    checksum = payload.pop("sha256", None)
    if checksum != hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest():
        raise ValueError("Export checksum does not match")
    if payload.get("frequency") != frequency:
        raise ValueError("Carrier frequency differs from this remote")
    commands = validate_commands(payload["commands"])
    mode = payload.get("replay_mode", "legacy")
    if mode not in MODES:
        raise ValueError("Invalid replay mode")
    if mode == "legacy":
        mode = "clean" if payload.get("direct_capture") else "raw"
        # Legacy filtering permits captured fallback; retain that behavior below.
        if payload.get("direct_capture"):
            mode = "legacy_clean"
    for command in commands.values():
        if "replay_mode" not in command:
            if mode == "legacy_clean":
                command["replay_mode"] = "legacy"
                command["legacy_clean"] = True
            else:
                command["replay_mode"] = mode
    return commands


async def async_snapshot(hass, entry):
    """Keep a private snapshot before every options commit; never edit .storage."""
    snapshot = {"entry_id": entry.entry_id, "version": entry.version,
                "data": dict(entry.data), "options": dict(entry.options)}
    folder = Path(hass.config.path("rf_fan_safety"))
    def write():
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        name = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}_{entry.entry_id}_{uuid4().hex}.json"
        path = folder / name
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(snapshot, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        return str(path)
    return await hass.async_add_executor_job(write)
