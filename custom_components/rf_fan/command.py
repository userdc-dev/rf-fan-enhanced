"""Build and send learned RF codes through the radio_frequency platform."""

from __future__ import annotations

from typing import Any
from base64 import b64decode
from datetime import datetime, timezone
import asyncio

from rf_protocols import ModulationType, RadioFrequencyCommand

from homeassistant.components.radio_frequency import async_send_command
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .mercator import clean_frame


class CapturedCommand(RadioFrequencyCommand):
    """A learned RF code, replayed as raw OOK timings.

    We subclass the stable, top-level ``rf_protocols.RadioFrequencyCommand`` (the
    same import Home Assistant core uses) and set the attributes the transmitter
    reads directly, rather than importing the library's internal command modules
    - their layout differs between rf_protocols releases (``commands`` is a
    module in the shipped version, a package on ``main``). The Broadlink
    transmitter only consumes ``frequency``, ``repeat_count`` and
    ``get_raw_timings()``; ``modulation`` gates the transmitter-support check.
    We deliberately skip ``super().__init__`` to stay immune to constructor
    changes across releases.
    """

    def __init__(
        self, *, frequency: int, timings: list[int], repeat_count: int = 0
    ) -> None:
        """Initialise from decoded raw timings."""
        self.frequency = frequency
        self.modulation = ModulationType.OOK
        self.repeat_count = repeat_count
        self.symbol_rate = None
        self.output_power = None
        self._timings = timings

    def get_raw_timings(self) -> list[int]:
        """Return the signed alternating microsecond timings."""
        return self._timings


# Drop leading/trailing gaps longer than this (microseconds). Direct captures can
# include tens of milliseconds (up to seconds) of idle before/after the real code;
# transmitting that desyncs some receivers - notably the Mercator FRM97 - and it
# isn't part of the signal. Real inter-frame gaps are well under this.
_IDLE_TRIM_US = 20000


def _trim_idle(timings: list[int]) -> list[int]:
    """Drop huge leading/trailing idle gaps from a captured pulse train."""
    ts = [int(t) for t in timings]
    while ts and abs(ts[0]) > _IDLE_TRIM_US:
        ts.pop(0)
    while ts and abs(ts[-1]) > _IDLE_TRIM_US:
        ts.pop()
    return ts


async def async_send_stored(
    hass: HomeAssistant,
    transmitter: str,
    data: dict[str, Any],
    frequency: int,
    *,
    clean: bool = False,
    repeat: int = 0,
    mode: str | None = None,
) -> None:
    """Send a stored command dict (``{"timings"}``) via a transmitter.

    Normally we send the (idle-trimmed) captured train once - it already holds
    several frame repeats. With ``clean=True`` we instead send a single de-noised
    consensus frame, which the Broadlink repeats ``repeat`` times: needed for
    fussy Manchester remotes (e.g. Mercator FRM97) whose raw captures contain
    noisy frames.
    """
    mode = mode or data.get("replay_mode")
    if mode == "legacy" and "legacy_clean" in data:
        clean = bool(data["legacy_clean"])
        repeat = 10 if clean else 0
    if mode not in (None, "legacy", "original", "raw", "clean"):
        raise HomeAssistantError("Unknown RF replay mode")
    if mode == "original":
        if not data.get("b64"):
            raise HomeAssistantError("Original Broadlink packet is unavailable; use Captured")
        try:
            packet = b64decode(data["b64"], validate=True)
        except (ValueError, TypeError) as err:
            raise HomeAssistantError("Invalid original Broadlink packet") from err
        if len(packet) < 5 or packet[0] not in (0xB0, 0xB1, 0xB2, 0xB4, 0xD7):
            raise HomeAssistantError("Invalid RF packet header")
        if int.from_bytes(packet[2:4], "little") > len(packet) - 4:
            raise HomeAssistantError("Truncated original RF packet")
        registry_entry = er.async_get(hass).async_get(transmitter)
        devices = getattr(hass.data.get("broadlink"), "devices", {})
        device = devices.get(registry_entry.config_entry_id) if registry_entry else None
        if device is None:
            raise HomeAssistantError("Selected Broadlink transmitter is unavailable")
        async def send():
            await device.async_request(device.api.send_data, packet)
    else:
        if mode == "raw":
            clean, repeat = False, 0
        elif mode == "clean":
            clean, repeat = True, 10
        timings = list(data.get("timings", []))
        if not timings or any(type(t) is not int or not 0 < abs(t) <= 2_000_000 for t in timings):
            raise HomeAssistantError("Invalid captured RF timings")
        if clean:
            timings = clean_frame(timings)
            if not timings:
                # Legacy entries historically fell back to captured timings.
                if mode == "clean":
                    raise HomeAssistantError("No Mercator consensus frame found; use Captured")
                timings = _trim_idle(data["timings"])
        else:
            timings = _trim_idle(timings)
        if not timings:
            raise HomeAssistantError("RF capture contains only idle gaps")
        command = CapturedCommand(frequency=frequency, timings=timings, repeat_count=repeat)
        async def send():
            await async_send_command(hass, transmitter, command)
    runtime = hass.data.setdefault("rf_fan_runtime", {})
    lock = runtime.setdefault("locks", {}).setdefault(transmitter, asyncio.Lock())
    status = {"mode": mode or "legacy", "time": datetime.now(timezone.utc).isoformat()}
    try:
        async with lock:
            await send()
    except Exception as err:
        status["result"] = "failed"
        status["error_type"] = type(err).__name__
        raise HomeAssistantError(f"RF transmission failed: {type(err).__name__}") from err
    else:
        status["result"] = "sent_unconfirmed"
    finally:
        runtime.setdefault("last_send", {})[transmitter] = status
    return
