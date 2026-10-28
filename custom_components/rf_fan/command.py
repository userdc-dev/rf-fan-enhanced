"""Build and send learned RF codes through the radio_frequency platform."""

from __future__ import annotations

from typing import Any

from rf_protocols import ModulationType, RadioFrequencyCommand

from homeassistant.components.radio_frequency import async_send_command
from homeassistant.core import HomeAssistant

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
) -> None:
    """Send a stored command dict (``{"timings"}``) via a transmitter.

    Normally we send the (idle-trimmed) captured train once - it already holds
    several frame repeats. With ``clean=True`` we instead send a single de-noised
    consensus frame, which the Broadlink repeats ``repeat`` times: needed for
    fussy Manchester remotes (e.g. Mercator FRM97) whose raw captures contain
    noisy frames.
    """
    if clean:
        timings = clean_frame(data["timings"]) or _trim_idle(data["timings"])
    else:
        timings = _trim_idle(data["timings"])
    command = CapturedCommand(
        frequency=frequency, timings=timings, repeat_count=repeat
    )
    await async_send_command(hass, transmitter, command)
