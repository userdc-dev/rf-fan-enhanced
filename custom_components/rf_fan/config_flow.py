"""Config and options flows for rf_fan.

Setup is split into two screens:
  Screen 1 – device name, primary entity type, RF transmitter & frequency.
  Screen 2 – entity-specific options (speeds for fan, stop button for cover, etc.).
Then the wizard walks through learning each button.

The options flow (Configure) lets you re-learn existing buttons or add new
sub-entities (fan / cover / light / button) to an existing device.
"""

from __future__ import annotations

from copy import deepcopy
import json

import asyncio
import logging
import re
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.helpers import entity_registry as er
from homeassistant.util import slugify

from .manager import async_snapshot, export_commands, import_commands, MODES
from .command import async_send_stored
from .const import (
    CLEAN_REPEAT,
    CMD_CLOSE,
    CMD_LIGHT,
    CMD_OFF,
    CMD_ON,
    CMD_OPEN,
    CMD_STOP,
    CONF_COMMANDS,
    CONF_CUSTOMS,
    CONF_DIRECT,
    CONF_ENTITY_TYPE,
    CONF_FREQUENCY,
    CONF_FAN_POWER_MODE,
    CONF_HAS_ON,
    CONF_HAS_STOP,
    CONF_LIGHT_MODE,
    CONF_NAME,
    CONF_LIGHT_CYCLE,
    CONF_LIGHT_CYCLE_CLICKS,
    CONF_SAFE_OFF,
    CONF_SPEED_COUNT,
    CONF_SUB_ENTITIES,
    CONF_TRANSMITTER,
    DEFAULT_FREQUENCY,
    DOMAIN,
    ENTITY_TYPE_BUTTON,
    ENTITY_TYPE_COVER,
    ENTITY_TYPE_FAN,
    ENTITY_TYPE_LIGHT,
    FAN_POWER_MODE_OFF_ONLY,
    FAN_POWER_MODE_ON_OFF,
    FAN_POWER_MODE_TOGGLE,
    LIGHT_MODE_ON_OFF,
    LIGHT_MODE_TOGGLE,
)
from .rf import async_capture_packet, async_sweep_frequency, find_rf_devices

from .extra_buttons import EXTRA_BUTTONS

_LOGGER = logging.getLogger(__name__)

# Frequencies are transmitter-specific. Never advertise universal 868/915 MHz support.
# This list is a conservative fallback for Broadlink hardware whose capabilities
# are not exposed through the radio_frequency entity.
FREQ_OPTIONS = [
    selector.SelectOptionDict(value=str(DEFAULT_FREQUENCY), label="433.92 MHz (433 MHz band)"),
]


def _frequency_options(hass, transmitter: str):
    """Use explicit frequency capabilities when the transmitter exposes them.

    radio_frequency integrations do not have a universal capability schema.
    Only accept a declared list of individual frequencies; never infer support
    from the entity name, manufacturer or generic RF device type.
    """
    state = hass.states.get(transmitter)
    if state is None:
        return FREQ_OPTIONS
    declared = state.attributes.get("supported_frequencies")
    if not isinstance(declared, (list, tuple)):
        return FREQ_OPTIONS
    options = []
    for item in declared:
        try:
            value = float(item)
        except (TypeError, ValueError):
            continue
        # Support Hz and MHz representations without allowing arbitrary bands.
        hz = round(value * 1_000_000) if value < 1000 else round(value)
        if 100_000_000 <= hz <= 1_000_000_000:
            options.append(selector.SelectOptionDict(
                value=str(hz), label=f"{hz / 1_000_000:g} MHz"
            ))
    return options or FREQ_OPTIONS

ENTITY_TYPE_OPTIONS = [
    selector.SelectOptionDict(value=ENTITY_TYPE_FAN,    label="Ceiling Fan"),
    selector.SelectOptionDict(value=ENTITY_TYPE_COVER,  label="Blind / Awning"),
    selector.SelectOptionDict(value=ENTITY_TYPE_LIGHT,  label="Light"),
    selector.SelectOptionDict(value=ENTITY_TYPE_BUTTON, label="Individual Button"),
]

LIGHT_MODE_OPTIONS = [
    selector.SelectOptionDict(value=LIGHT_MODE_TOGGLE, label="One button (On / Off toggle)"),
    selector.SelectOptionDict(value=LIGHT_MODE_ON_OFF, label="Two buttons (separate On and Off)"),
]

FAN_POWER_MODE_OPTIONS = [
    selector.SelectOptionDict(value=FAN_POWER_MODE_TOGGLE, label="One button (On / Off toggle)"),
    selector.SelectOptionDict(value=FAN_POWER_MODE_OFF_ONLY, label="Off button only (speeds turn fan on)"),
    selector.SelectOptionDict(value=FAN_POWER_MODE_ON_OFF, label="Two buttons (separate On and Off)"),
]

FAN_LIGHT_MODE_NONE = "none"
FAN_LIGHT_MODE_OPTIONS = [
    selector.SelectOptionDict(value=FAN_LIGHT_MODE_NONE, label="No fan light"),
    selector.SelectOptionDict(value=LIGHT_MODE_TOGGLE, label="One light button (On / Off toggle)"),
    selector.SelectOptionDict(value=LIGHT_MODE_ON_OFF, label="Two light buttons (separate On and Off)"),
]
FAN_LIGHT_EXTRA_KEYS = [
    "light_colour", "light_warm", "light_neutral", "light_cool",
    "brightness_up", "brightness_down",
]
FAN_TIMER_KEYS = [
    "timer_30m", "timer_1h", "timer_2h", "timer_4h", "timer_6h",
    "timer_8h", "timer_cycle",
]


async def _delayed_capture(device: Any) -> dict[str, Any]:
    """Brief settle, then capture the packet (sweep flow, phase 2)."""
    await asyncio.sleep(1)
    return await async_capture_packet(device)


def _unique_custom_key(name: str, taken: set[str]) -> str:
    """Build a unique custom_<slug> key for a button name."""
    base = f"custom_{slugify(name)}" if slugify(name) else "custom"
    key = base
    suffix = 2
    while key in taken:
        key = f"{base}_{suffix}"
        suffix += 1
    return key


def _sub_key_prefix(name: str, taken: set[str]) -> str:
    """Build a unique sub-entity key prefix from the entity name."""
    base = slugify(name) or "entity"
    key = base
    suffix = 2
    while key in taken:
        key = f"{base}_{suffix}"
        suffix += 1
    return key


def _build_queue(
    entity_type: str,
    *,
    speed_count: int = 0,
    has_on: bool = False,
    fan_power_mode: str = FAN_POWER_MODE_OFF_ONLY,
    has_stop: bool = False,
    light_mode: str = LIGHT_MODE_TOGGLE,
    customs: list[dict] = [],
    key_prefix: str = "",
) -> list[str]:
    """Return the ordered list of command keys to learn for an entity."""

    def pk(cmd: str) -> str:
        return f"{key_prefix}_{cmd}" if key_prefix else cmd

    queue: list[str] = []
    if entity_type == ENTITY_TYPE_FAN:
        queue.append(pk(CMD_OFF))
        queue += [pk(f"speed_{i}") for i in range(1, speed_count + 1)]
        if fan_power_mode == FAN_POWER_MODE_ON_OFF or has_on:
            queue.append(pk(CMD_ON))
    elif entity_type == ENTITY_TYPE_COVER:
        queue.append(pk(CMD_OPEN))
        queue.append(pk(CMD_CLOSE))
        if has_stop:
            queue.append(pk(CMD_STOP))
    elif entity_type == ENTITY_TYPE_LIGHT:
        if light_mode == LIGHT_MODE_TOGGLE:
            queue.append(pk(CMD_LIGHT))
        else:
            queue.append(pk(CMD_ON))
            queue.append(pk(CMD_OFF))
    # ENTITY_TYPE_BUTTON: no queue from entity itself; buttons come from customs
    queue += [c["key"] for c in customs]
    return queue


class _LearnFlowMixin:
    """Shared two-phase learning steps for both the config and options flows."""

    _device: Any
    _current_key: str | None
    _task: Any
    _pending: dict[str, Any] | None
    _learn_error: str | None
    _commands: dict[str, Any]
    _transmitter: str
    _frequency: int
    _custom_names: dict[str, str]
    _speed_count: int
    _direct: bool
    _entity_type: str
    _fan_power_mode: str

    def _label(self, key: str | None) -> str:
        """Human-friendly label for a command key."""
        if key is None:
            return ""
        if self._commands and self._commands.get(key, {}).get("label"):
            return self._commands[key]["label"]
        if key in EXTRA_BUTTONS:
            return EXTRA_BUTTONS[key]
        # Strip key_prefix if present
        bare = key.split("_", 1)[1] if "_" in key and not key.startswith("speed_") and not key.startswith("custom_") else key
        if bare == CMD_OFF or key == CMD_OFF:
            return "Fan power toggle" if self._fan_power_mode == FAN_POWER_MODE_TOGGLE else "Off"
        if bare == CMD_ON or key == CMD_ON:
            return "On"
        if bare == CMD_LIGHT or key == CMD_LIGHT:
            return "Light toggle"
        if key == "fan_light_on":
            return "Light On"
        if key == "fan_light_off":
            return "Light Off"
        if bare == CMD_OPEN or key == CMD_OPEN:
            return "Open"
        if bare == CMD_CLOSE or key == CMD_CLOSE:
            return "Close"
        if bare == CMD_STOP or key == CMD_STOP:
            return "Stop"
        if key.startswith("speed_") or bare.startswith("speed_"):
            raw = key if key.startswith("speed_") else bare
            number = raw.removeprefix("speed_")
            total = self._speed_count
            hint = ""
            if number == "1":
                hint = " (lowest)"
            elif number == str(total):
                hint = " (highest)"
            return f"Speed {number} of {total}{hint}"
        return self._custom_names.get(key, key)

    def _reset_learn_state(self) -> None:
        """Reset per-button learning state."""
        self._task = None
        self._pending = None
        self._learn_error = None

    async def _async_after_confirm(self) -> ConfigFlowResult:
        """Called after a code is kept. Implemented by the concrete flow."""
        raise NotImplementedError

    async def async_step_learn(self, user_input=None) -> ConfigFlowResult:
        """Learn a button - either direct capture or the two-phase sweep."""
        label = self._label(self._current_key)

        if self._direct:
            # Direct capture: listen at the chosen frequency, single press.
            if self._task is None:
                self._task = self.hass.async_create_task(
                    async_capture_packet(self._device, self._frequency / 1_000_000)
                )
                return self.async_show_progress(
                    step_id="learn",
                    progress_action="press",
                    description_placeholders={"button": label},
                    progress_task=self._task,
                )
            try:
                self._pending = await self._task
                self._learn_error = None
            except Exception as err:  # noqa: BLE001 - surface any capture failure
                self._learn_error = str(err)
                self._task = None
                return self.async_show_progress_done(next_step_id="learn_error")
            self._task = None
            return self.async_show_progress_done(next_step_id="learn_result")

        # Sweep flow, phase 1: hold the button while the carrier is found.
        if self._task is None:
            self._task = self.hass.async_create_task(
                async_sweep_frequency(self._device)
            )
            return self.async_show_progress(
                step_id="learn",
                progress_action="sweep",
                description_placeholders={"button": label},
                progress_task=self._task,
            )
        try:
            await self._task
        except Exception as err:  # noqa: BLE001 - surface any capture failure
            self._learn_error = str(err)
            self._task = None
            return self.async_show_progress_done(next_step_id="learn_error")
        self._task = None
        return self.async_show_progress_done(next_step_id="learn_press")

    async def async_step_learn_press(self, user_input=None) -> ConfigFlowResult:
        """Sweep flow, between phases: release and press again."""
        label = self._label(self._current_key)
        if user_input is not None:
            self._task = self.hass.async_create_task(_delayed_capture(self._device))
            return self.async_show_progress(
                step_id="learn_capture",
                progress_action="capture",
                description_placeholders={"button": label},
                progress_task=self._task,
            )
        return self.async_show_form(
            step_id="learn_press",
            data_schema=vol.Schema({}),
            description_placeholders={"button": label},
        )

    async def async_step_learn_capture(self, user_input=None) -> ConfigFlowResult:
        """Sweep flow, phase 2 done: store the captured code (or report failure)."""
        try:
            self._pending = await self._task
            self._learn_error = None
        except Exception as err:  # noqa: BLE001 - surface any capture failure
            self._learn_error = str(err)
            self._task = None
            return self.async_show_progress_done(next_step_id="learn_error")
        self._task = None
        return self.async_show_progress_done(next_step_id="learn_result")

    async def async_step_learn_result(self, user_input=None) -> ConfigFlowResult:
        """Offer to test, keep, or retry the just-learned code."""
        return self.async_show_menu(
            step_id="learn_result",
            menu_options=["confirm", "test", "choose_test_mode", "retry", "skip"],
            description_placeholders={
                "button": self._label(self._current_key),
                "pulses": str(self._pending["length"] if self._pending else 0),
            },
        )

    async def async_step_confirm(self, user_input=None) -> ConfigFlowResult:
        """Store the learned code and continue."""
        assert self._pending is not None
        self._commands[self._current_key] = {
            **self._commands.get(self._current_key, {}),
            "timings": self._pending["timings"],
            "repeat": self._pending["repeat"],
            "b64": self._pending["b64"],
            **({"replay_mode": self._pending["replay_mode"]} if "replay_mode" in self._pending else {}),
        }
        return await self._async_after_confirm()

    async def async_step_skip(self, user_input=None):
        """Skip without replacing the previous code."""
        self._pending = None
        return await self._async_after_confirm()

    async def async_step_choose_test_mode(self, user_input=None):
        if user_input is not None:
            self._pending["replay_mode"] = user_input["mode"]
            return await self.async_step_test()
        return self.async_show_form(step_id="choose_test_mode", data_schema=vol.Schema({
            vol.Required("mode"): selector.SelectSelector(selector.SelectSelectorConfig(options=[
                {"value": "original", "label": "Original Broadlink packet"},
                {"value": "raw", "label": "Captured timings"},
                {"value": "clean", "label": "Mercator filtered"},
            ]))
        }))

    async def async_step_test(self, user_input=None) -> ConfigFlowResult:
        """Transmit the just-learned code so the user can verify it."""
        if user_input is not None:
            return await self.async_step_learn_result()
        if self._pending is not None:
            try:
                await async_send_stored(
                    self.hass,
                    self._transmitter,
                    {**self._pending, **({"replay_mode": self._commands[self._current_key]["replay_mode"]} if "replay_mode" not in self._pending and self._current_key in self._commands and "replay_mode" in self._commands[self._current_key] else {})},
                    self._frequency,
                    clean=(getattr(self, "_replay_mode", "legacy") == "clean" or (getattr(self, "_replay_mode", "legacy") == "legacy" and self._direct)),
                    repeat=CLEAN_REPEAT if (getattr(self, "_replay_mode", "legacy") == "clean" or (getattr(self, "_replay_mode", "legacy") == "legacy" and self._direct)) else 0,
                )
            except HomeAssistantError as err:
                return self.async_show_form(step_id="test", data_schema=vol.Schema({}), errors={"base": "send_failed"})
        return await self.async_step_learn_result()

    async def async_step_retry(self, user_input=None) -> ConfigFlowResult:
        """Re-learn the current button the same way."""
        self._reset_learn_state()
        return await self.async_step_learn()

    async def async_step_retry_direct(self, user_input=None) -> ConfigFlowResult:
        """Switch to direct capture (no sweep) and re-learn the current button."""
        self._direct = True
        self._reset_learn_state()
        return await self.async_step_learn()

    async def async_step_learn_error(self, user_input=None) -> ConfigFlowResult:
        """Offer to retry - and, if sweeping, to capture without the sweep."""
        menu_options = ["retry", "skip"]
        if not self._direct:
            menu_options.append("retry_direct")
        return self.async_show_menu(
            step_id="learn_error",
            menu_options=menu_options,
            description_placeholders={
                "button": self._label(self._current_key),
                "error": self._learn_error or "",
            },
        )


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------
class RfFanConfigFlow(_LearnFlowMixin, ConfigFlow, domain=DOMAIN):
    """Two-screen setup wizard + button-learning for an RF device."""

    VERSION = 2

    def __init__(self) -> None:
        """Initialise wizard state."""
        self._data: dict[str, Any] = {}
        self._commands: dict[str, Any] = {}
        self._customs: list[dict[str, str]] = []
        self._custom_names: dict[str, str] = {}
        self._queue: list[str] = []
        self._current_key: str | None = None
        self._device: Any = None
        self._task: Any = None
        self._pending: dict[str, Any] | None = None
        self._learn_error: str | None = None
        self._speed_count: int = 0
        self._transmitter: str = ""
        self._frequency: int = DEFAULT_FREQUENCY
        self._direct: bool = False
        self._entity_type: str = ENTITY_TYPE_FAN
        self._has_on: bool = False
        self._fan_power_mode: str = FAN_POWER_MODE_OFF_ONLY
        self._has_stop: bool = False
        self._light_mode: str = LIGHT_MODE_TOGGLE
        self._initial_sub_entities: list[dict[str, Any]] = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry) -> OptionsFlow:
        """Return the options flow (re-learn / add buttons)."""
        return RfFanOptionsFlow()

    # ------------------------------------------------------------------
    # Screen 1: name + entity type + transmitter + frequency
    # ------------------------------------------------------------------
    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Collect the device's name, transmitter and type."""
        devices = find_rf_devices(self.hass)
        if not devices:
            return self.async_abort(reason="no_rf_device")

        if user_input is not None:
            self._entity_type = user_input[CONF_ENTITY_TYPE]
            self._transmitter = user_input[CONF_TRANSMITTER]
            reg = er.async_get(self.hass).async_get(self._transmitter)
            self._device = getattr(self.hass.data.get("broadlink"), "devices", {}).get(reg.config_entry_id) if reg else None
            if self._device is None or self._device not in devices.values():
                # Transmission-only RF devices need a different learning backend.
                # Do not offer a setup that will fail at the first learn step.
                return self.async_abort(reason="no_rf_device")
            self._data = {
                CONF_NAME: user_input[CONF_NAME],
                CONF_ENTITY_TYPE: self._entity_type,
                CONF_TRANSMITTER: self._transmitter,
            }
            return await self.async_step_frequency()

        schema = vol.Schema({
            vol.Required(CONF_NAME): selector.TextSelector(),
            vol.Required(CONF_ENTITY_TYPE, default=ENTITY_TYPE_FAN): selector.SelectSelector(
                selector.SelectSelectorConfig(options=ENTITY_TYPE_OPTIONS)
            ),
            vol.Required(CONF_TRANSMITTER): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="radio_frequency")
            ),
        })
        return self.async_show_form(step_id="user", data_schema=schema)

    async def async_step_frequency(self, user_input=None) -> ConfigFlowResult:
        """Show frequencies only after the RF transmitter is known."""
        options = _frequency_options(self.hass, self._transmitter)
        allowed = {option["value"] for option in options}
        if user_input is not None:
            chosen = str(user_input[CONF_FREQUENCY])
            if chosen in allowed:
                self._frequency = int(chosen)
                self._data[CONF_FREQUENCY] = self._frequency
                return await self.async_step_entity_options()
        default = str(DEFAULT_FREQUENCY)
        if default not in allowed:
            default = options[0]["value"]
        return self.async_show_form(
            step_id="frequency",
            errors={"base": "invalid_frequency"} if user_input is not None else {},
            data_schema=vol.Schema({
                vol.Required(CONF_FREQUENCY, default=default): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options)
                ),
            }),
        )

    # ------------------------------------------------------------------
    # Screen 2: entity-type-specific options
    # ------------------------------------------------------------------
    async def async_step_entity_options(self, user_input=None) -> ConfigFlowResult:
        entity_type = self._entity_type

        if user_input is not None:
            self._direct = user_input.get(CONF_DIRECT, False)

            if entity_type == ENTITY_TYPE_FAN:
                self._speed_count = int(user_input[CONF_SPEED_COUNT])
                self._fan_power_mode = user_input.get(CONF_FAN_POWER_MODE, FAN_POWER_MODE_TOGGLE)
                self._has_on = self._fan_power_mode == FAN_POWER_MODE_ON_OFF
                fan_light_mode = user_input.get("fan_light_mode", FAN_LIGHT_MODE_NONE)
                light_extras = list(user_input.get("fan_light_extras", []))
                timer_buttons = list(user_input.get("fan_timer_buttons", []))
                feature_buttons = [
                    key
                    for key, selected in (
                        ("direction_toggle", user_input.get("has_direction", False)),
                        ("swing", user_input.get("has_oscillation", False)),
                        ("swing_air", user_input.get("has_swing_air", False)),
                        ("natural_breeze", user_input.get("has_natural_breeze", False)),
                    )
                    if selected
                ]
                selected_extras = list(dict.fromkeys(light_extras + timer_buttons + feature_buttons))
                self._data.update({
                    CONF_SPEED_COUNT: self._speed_count,
                    CONF_HAS_ON: self._has_on,
                    CONF_FAN_POWER_MODE: self._fan_power_mode,
                    "dc_extras": selected_extras,
                    "has_light": fan_light_mode == LIGHT_MODE_TOGGLE,
                })
                self._initial_sub_entities = []
                if fan_light_mode == LIGHT_MODE_ON_OFF:
                    self._initial_sub_entities.append({
                        "entity_type": ENTITY_TYPE_LIGHT,
                        "name": "Light",
                        "key_prefix": "fan_light",
                        "light_mode": LIGHT_MODE_ON_OFF,
                    })
            elif entity_type == ENTITY_TYPE_COVER:
                self._has_stop = user_input.get(CONF_HAS_STOP, False)
                self._data[CONF_HAS_STOP] = self._has_stop
            elif entity_type == ENTITY_TYPE_LIGHT:
                self._light_mode = user_input.get(CONF_LIGHT_MODE, LIGHT_MODE_TOGGLE)
                self._data[CONF_LIGHT_MODE] = self._light_mode
            # BUTTON: no extra options beyond custom button names

            # Parse custom button names
            self._customs = []
            self._custom_names = {}

            self._data[CONF_DIRECT] = self._direct

            self._queue = _build_queue(
                entity_type,
                speed_count=self._speed_count,
                has_on=self._has_on,
                fan_power_mode=self._fan_power_mode,
                has_stop=self._has_stop,
                light_mode=self._light_mode,
                customs=self._customs,
            )
            if entity_type == ENTITY_TYPE_FAN:
                if fan_light_mode == LIGHT_MODE_TOGGLE:
                    self._queue.append(CMD_LIGHT)
                elif fan_light_mode == LIGHT_MODE_ON_OFF:
                    self._queue.extend(["fan_light_on", "fan_light_off"])
                self._queue.extend(selected_extras)
            return await self.async_step_next()

        # Build the schema depending on entity type
        fields: dict = {}
        if entity_type == ENTITY_TYPE_FAN:
            fields[vol.Required(CONF_SPEED_COUNT, default=8)] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=8, step=1, mode=selector.NumberSelectorMode.SLIDER)
            )
            fields[vol.Required(CONF_FAN_POWER_MODE, default=FAN_POWER_MODE_TOGGLE)] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=FAN_POWER_MODE_OPTIONS)
            )
            fields[vol.Required("fan_light_mode", default=FAN_LIGHT_MODE_NONE)] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=FAN_LIGHT_MODE_OPTIONS)
            )
            fields[vol.Optional("fan_light_extras", default=[])] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[selector.SelectOptionDict(value=key, label=EXTRA_BUTTONS[key]) for key in FAN_LIGHT_EXTRA_KEYS],
                    multiple=True,
                )
            )
            fields[vol.Optional("fan_timer_buttons", default=[])] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[selector.SelectOptionDict(value=key, label=EXTRA_BUTTONS[key]) for key in FAN_TIMER_KEYS],
                    multiple=True,
                )
            )
            fields[vol.Required("has_direction", default=False)] = selector.BooleanSelector()
            fields[vol.Required("has_oscillation", default=False)] = selector.BooleanSelector()
            fields[vol.Required("has_swing_air", default=False)] = selector.BooleanSelector()
            fields[vol.Required("has_natural_breeze", default=False)] = selector.BooleanSelector()
        elif entity_type == ENTITY_TYPE_COVER:
            fields[vol.Required(CONF_HAS_STOP, default=True)] = selector.BooleanSelector()
        elif entity_type == ENTITY_TYPE_LIGHT:
            fields[vol.Required(CONF_LIGHT_MODE, default=LIGHT_MODE_TOGGLE)] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=LIGHT_MODE_OPTIONS)
            )
        # Shared
        fields[vol.Required(CONF_DIRECT, default=False)] = selector.BooleanSelector()
        return self.async_show_form(
            step_id="entity_options",
            data_schema=vol.Schema(fields),
            description_placeholders={"entity_type": entity_type},
        )

    async def async_step_next(self, user_input=None) -> ConfigFlowResult:
        """Advance to the next button to learn, or finish."""
        if not self._queue:
            return self.async_create_entry(
                title=self._data[CONF_NAME],
                data={
                    **self._data,
                    CONF_COMMANDS: self._commands,
                    CONF_CUSTOMS: self._customs,
                    CONF_SUB_ENTITIES: self._initial_sub_entities,
                },
            )
        self._current_key = self._queue.pop(0)
        self._reset_learn_state()
        return await self.async_step_learn()

    async def _async_after_confirm(self) -> ConfigFlowResult:
        """Continue learning the queued buttons."""
        return await self.async_step_next()


# ---------------------------------------------------------------------------
# Options flow
# ---------------------------------------------------------------------------
MENU_LABELS = {
    "add_entity": "Add a new entity",
    "add_fan_speeds": "Add more fan speeds",
    "export_import": "Backup / export / import",
    "relearn": "Batch learn / relearn buttons",
    "command_library": "Command library",
    "create_dashboard": "Create / refresh remote dashboard",
    "fan_settings": "Fan and startup settings",
    "fan_power_settings": "Fan power button style",
    "add_dc_features": "Learn extra fan buttons",
    "remote_manager": "Remote command manager",
    "remove_commands": "Remove learned buttons",
    "diagnostics": "Signal diagnostics",
    "replay_settings": "Signal replay settings",
}


def _ordered_menu(options):
    """Sort the visible menu labels and keep the commit action last."""
    ordered = sorted((key for key in options if key != "finish"), key=lambda key: MENU_LABELS[key].casefold())
    return [*ordered, "finish"] if "finish" in options else ordered


class RfFanOptionsFlow(_LearnFlowMixin, OptionsFlow):
    """Re-learn existing buttons or add new sub-entities after setup."""

    def __init__(self) -> None:
        """Initialise options state (config entry is read lazily)."""
        self._commands: dict[str, Any] | None = None
        self._customs: list[dict[str, str]] = []
        self._custom_names: dict[str, str] = {}
        self._sub_entities: list[dict[str, Any]] = []
        self._transmitter: str = ""
        self._frequency: int = DEFAULT_FREQUENCY
        self._speed_count: int = 0
        self._direct: bool = False
        self._device: Any = None
        self._current_key: str | None = None
        self._task: Any = None
        self._pending: dict[str, Any] | None = None
        self._learn_error: str | None = None
        self._entity_type: str = ENTITY_TYPE_FAN
        self._fan_power_mode: str = FAN_POWER_MODE_OFF_ONLY
        # State for add-entity wizard
        self._adding_entity_type: str = ENTITY_TYPE_BUTTON
        self._adding_name: str = ""
        self._adding_key_prefix: str = ""
        self._adding_queue: list[str] = []
        self._extra_buttons: list[str] = []
        self._replay_mode: str = "legacy"
        self._removed_commands: set[str] = set()
        self._pending_removed_commands: set[str] = set()

    def _load_entry(self) -> None:
        entry = self.config_entry
        self._commands = deepcopy(entry.data[CONF_COMMANDS])
        self._customs = [dict(c) for c in entry.data.get(CONF_CUSTOMS, [])]
        self._custom_names = {c["key"]: c["name"] for c in self._customs}
        self._sub_entities = [dict(s) for s in entry.data.get(CONF_SUB_ENTITIES, [])]
        self._transmitter = entry.data[CONF_TRANSMITTER]
        self._frequency = entry.data[CONF_FREQUENCY]
        self._speed_count = entry.data.get(CONF_SPEED_COUNT, 0)
        self._direct = entry.data.get(CONF_DIRECT, False)
        self._entity_type = entry.data.get(CONF_ENTITY_TYPE, ENTITY_TYPE_FAN)
        self._fan_power_mode = entry.data.get(
            CONF_FAN_POWER_MODE,
            FAN_POWER_MODE_ON_OFF if entry.data.get(CONF_HAS_ON, False) else FAN_POWER_MODE_OFF_ONLY,
        )
        self._extra_buttons = list(entry.data.get("dc_extras", []))
        self._startup_state = entry.data.get("startup_state", "restore")
        self._safe_off_enabled = bool(entry.data.get(
            CONF_SAFE_OFF,
            "speed_1" in self._commands and CMD_OFF in self._commands,
        ))
        self._light_cycle_enabled = bool(entry.data.get(CONF_LIGHT_CYCLE, CMD_LIGHT in self._commands))
        self._light_cycle_clicks = {
            "cool": 1,
            "neutral": 3,
            "warm": 5,
            **entry.data.get(CONF_LIGHT_CYCLE_CLICKS, {}),
        }
        self._replay_mode = entry.data.get("replay_mode", "legacy")

    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        """Show the main configure menu."""
        if self._commands is None:
            self._load_entry()

        devices = find_rf_devices(self.hass)
        if not devices:
            return self.async_abort(reason="no_rf_device")
        reg = er.async_get(self.hass).async_get(self._transmitter)
        self._device = getattr(self.hass.data.get("broadlink"), "devices", {}).get(reg.config_entry_id) if reg else None
        if self._device is None:
            return self.async_abort(reason="no_rf_device")

        return self.async_show_menu(
            step_id="init",
            menu_options=_ordered_menu(
                [
                    "fan_settings",
                    "create_dashboard",
                    "diagnostics",
                    "remote_manager",
                    "command_library",
                    "export_import",
                    "relearn",
                    "fan_power_settings",
                    *(["add_fan_speeds"] if self._speed_count < 8 else []),
                    "add_dc_features",
                    *(
                        ["remove_commands"]
                        if any(
                            key in EXTRA_BUTTONS or key == CMD_ON
                            for key in self._commands
                        )
                        else []
                    ),
                    "replay_settings",
                    "add_entity",
                    "finish",
                ]
                if self._entity_type == ENTITY_TYPE_FAN
                else ["remote_manager", "create_dashboard", "command_library", "export_import", "relearn", "add_entity", "finish"]
            ),
        )

    # ------------------------------------------------------------------
    # Re-learn an existing button
    # ------------------------------------------------------------------

    async def async_step_fan_power_settings(self, user_input=None) -> ConfigFlowResult:
        """Choose whether fan power is toggle, Off-only, or separate On/Off."""
        if user_input is not None:
            self._fan_power_mode = user_input[CONF_FAN_POWER_MODE]
            if (
                self._fan_power_mode == FAN_POWER_MODE_ON_OFF
                and CMD_ON not in self._commands
            ):
                self._adding_queue = [CMD_ON]
                return await self.async_step_add_entity_learn()
            return await self.async_step_init()

        return self.async_show_form(
            step_id="fan_power_settings",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_FAN_POWER_MODE,
                    default=self._fan_power_mode,
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=FAN_POWER_MODE_OPTIONS)
                )
            }),
        )

    async def async_step_add_fan_speeds(self, user_input=None) -> ConfigFlowResult:
        """Learn additional discrete speeds while retaining existing commands."""
        if self._speed_count >= 8:
            return await self.async_step_init()

        if user_input is not None:
            target = int(user_input[CONF_SPEED_COUNT])
            target = max(self._speed_count + 1, min(8, target))
            self._adding_queue = [
                f"speed_{number}"
                for number in range(self._speed_count + 1, target + 1)
                if f"speed_{number}" not in self._commands
            ]
            self._speed_count = target
            return await self.async_step_add_entity_learn()

        return self.async_show_form(
            step_id="add_fan_speeds",
            data_schema=vol.Schema({
                vol.Required(CONF_SPEED_COUNT, default=8): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=self._speed_count + 1,
                        max=8,
                        step=1,
                        mode=selector.NumberSelectorMode.SLIDER,
                    )
                )
            }),
        )

    async def async_step_add_dc_features(self, user_input=None) -> ConfigFlowResult:
        """Learn additional buttons without replacing existing learned codes."""
        available = [key for key in EXTRA_BUTTONS if key not in self._commands]
        if user_input is not None:
            selected = [key for key in user_input.get("dc_extras", []) if key in available]
            self._extra_buttons.extend(key for key in selected if key not in self._extra_buttons)
            self._adding_queue = selected
            return await self.async_step_add_entity_learn()
        if not available:
            return await self.async_step_init()
        return self.async_show_form(
            step_id="add_dc_features",
            data_schema=vol.Schema({
                vol.Optional("dc_extras", default=[]): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[selector.SelectOptionDict(value=key, label=EXTRA_BUTTONS[key]) for key in available],
                        multiple=True,
                    )
                )
            }),
        )

    async def async_step_remove_commands(self, user_input=None) -> ConfigFlowResult:
        """Select which currently learned optional buttons should remain."""
        removable = [
            key
            for key in self._commands
            if key in EXTRA_BUTTONS or key == CMD_ON
        ]
        if not removable:
            return await self.async_step_init()

        if user_input is not None:
            kept = {
                key
                for key in user_input.get("keep_commands", [])
                if key in removable
            }
            self._pending_removed_commands = set(removable) - kept
            if not self._pending_removed_commands:
                return await self.async_step_init()
            return await self.async_step_confirm_remove_commands()

        return self.async_show_form(
            step_id="remove_commands",
            data_schema=vol.Schema({
                vol.Required("keep_commands", default=removable): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            selector.SelectOptionDict(
                                value=key,
                                label=self._label(key),
                            )
                            for key in removable
                        ],
                        multiple=True,
                    )
                )
            }),
        )

    async def async_step_confirm_remove_commands(
        self, user_input=None
    ) -> ConfigFlowResult:
        """Require confirmation before deleting learned command data."""
        if not self._pending_removed_commands:
            return await self.async_step_init()
        labels = ", ".join(
            self._label(key) for key in sorted(self._pending_removed_commands)
        )
        return self.async_show_menu(
            step_id="confirm_remove_commands",
            menu_options=["confirm_remove", "cancel_remove"],
            description_placeholders={"buttons": labels},
        )

    async def async_step_confirm_remove(self, user_input=None) -> ConfigFlowResult:
        """Apply the confirmed removals to the pending options-flow data."""
        selected = set(self._pending_removed_commands)
        for key in selected:
            self._commands.pop(key, None)
        self._removed_commands.update(selected)
        self._pending_removed_commands.clear()
        self._extra_buttons = [
            key for key in self._extra_buttons if key not in selected
        ]
        return await self.async_step_init()

    async def async_step_cancel_remove(self, user_input=None) -> ConfigFlowResult:
        """Cancel the pending deletion and leave all commands untouched."""
        self._pending_removed_commands.clear()
        return await self.async_step_init()

    async def async_step_replay_settings(self, user_input=None) -> ConfigFlowResult:
        """Keep learning and signal processing as independent settings."""
        if user_input is not None:
            self._replay_mode = user_input["replay_mode"]
            return await self.async_step_init()
        return self.async_show_form(
            step_id="replay_settings",
            data_schema=vol.Schema({
                vol.Required("replay_mode", default=self._replay_mode): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=[
                        selector.SelectOptionDict(value="legacy", label="Original behavior"),
                        selector.SelectOptionDict(value="raw", label="Captured signal (no Mercator filter)"),
                        selector.SelectOptionDict(value="clean", label="Mercator filtered frame"),
                    ])
                )
            }),
        )

    async def async_step_relearn(self, user_input=None) -> ConfigFlowResult:
        """Pick an existing button to re-learn."""
        if user_input is not None:
            self._adding_queue = list(user_input.get("buttons", []))
            return await self.async_step_add_entity_learn()
        options = [
            selector.SelectOptionDict(value=key, label=self._label(key))
            for key in self._commands
        ]
        return self.async_show_form(
            step_id="relearn",
            data_schema=vol.Schema(
                {
                    vol.Optional("buttons", default=[]): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options, multiple=True)
                    )
                }
            ),
        )

    # ------------------------------------------------------------------
    # Add a new sub-entity (screen A: type + name)
    # ------------------------------------------------------------------
    async def async_step_add_entity(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            self._adding_entity_type = user_input[CONF_ENTITY_TYPE]
            self._adding_name = (user_input.get(CONF_NAME) or "").strip()
            if not self._adding_name:
                return await self.async_step_init()
            return await self.async_step_add_entity_options()

        schema = vol.Schema({
            vol.Required(CONF_ENTITY_TYPE, default=ENTITY_TYPE_BUTTON): selector.SelectSelector(
                selector.SelectSelectorConfig(options=ENTITY_TYPE_OPTIONS)
            ),
            vol.Required(CONF_NAME): selector.TextSelector(),
        })
        return self.async_show_form(step_id="add_entity", data_schema=schema)

    # ------------------------------------------------------------------
    # Add a new sub-entity (screen B: type-specific options)
    # ------------------------------------------------------------------
    async def async_step_add_entity_options(self, user_input=None) -> ConfigFlowResult:
        entity_type = self._adding_entity_type

        if user_input is not None:
            # Build a unique key_prefix
            taken_prefixes = {s["key_prefix"] for s in self._sub_entities}
            self._adding_key_prefix = _sub_key_prefix(self._adding_name, taken_prefixes)

            speed_count = 0
            has_on = False
            fan_power_mode = FAN_POWER_MODE_OFF_ONLY
            has_stop = False
            light_mode = LIGHT_MODE_TOGGLE
            customs: list[dict] = []
            custom_names: dict[str, str] = {}

            if entity_type == ENTITY_TYPE_FAN:
                speed_count = int(user_input.get(CONF_SPEED_COUNT, 8))
                fan_power_mode = user_input.get(CONF_FAN_POWER_MODE, FAN_POWER_MODE_TOGGLE)
                has_on = fan_power_mode == FAN_POWER_MODE_ON_OFF
            elif entity_type == ENTITY_TYPE_COVER:
                has_stop = user_input.get(CONF_HAS_STOP, True)
            elif entity_type == ENTITY_TYPE_LIGHT:
                light_mode = user_input.get(CONF_LIGHT_MODE, LIGHT_MODE_TOGGLE)

            # Register the sub-entity
            self._sub_entities.append({
                "entity_type": entity_type,
                "name": self._adding_name,
                "key_prefix": self._adding_key_prefix,
                "speed_count": speed_count,
                "has_on": has_on,
                "fan_power_mode": fan_power_mode,
                "has_stop": has_stop,
                "light_mode": light_mode,
            })
            # Merge new customs
            self._customs.extend(customs)
            self._custom_names.update(custom_names)

            # Build and enqueue the learning queue for this sub-entity
            self._adding_queue = _build_queue(
                entity_type,
                speed_count=speed_count,
                has_on=has_on,
                fan_power_mode=fan_power_mode,
                has_stop=has_stop,
                light_mode=light_mode,
                customs=customs,
                key_prefix=self._adding_key_prefix,
            )
            # Update speed_count context for label formatting if it's a fan
            if entity_type == ENTITY_TYPE_FAN:
                self._speed_count = speed_count
            return await self.async_step_add_entity_learn()

        # Build schema
        fields: dict = {}
        if entity_type == ENTITY_TYPE_FAN:
            fields[vol.Required(CONF_SPEED_COUNT, default=8)] = selector.NumberSelector(
                selector.NumberSelectorConfig(min=1, max=8, step=1, mode=selector.NumberSelectorMode.SLIDER)
            )
            fields[vol.Required(CONF_FAN_POWER_MODE, default=FAN_POWER_MODE_TOGGLE)] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=FAN_POWER_MODE_OPTIONS)
            )
        elif entity_type == ENTITY_TYPE_COVER:
            fields[vol.Required(CONF_HAS_STOP, default=True)] = selector.BooleanSelector()
        elif entity_type == ENTITY_TYPE_LIGHT:
            fields[vol.Required(CONF_LIGHT_MODE, default=LIGHT_MODE_TOGGLE)] = selector.SelectSelector(
                selector.SelectSelectorConfig(options=LIGHT_MODE_OPTIONS)
            )
        return self.async_show_form(
            step_id="add_entity_options",
            data_schema=vol.Schema(fields),
            description_placeholders={
                "entity_type": entity_type,
                "name": self._adding_name,
            },
        )

    async def async_step_add_entity_learn(self, user_input=None) -> ConfigFlowResult:
        """Dequeue the next button for the new sub-entity."""
        if not self._adding_queue:
            return await self.async_step_init()
        self._current_key = self._adding_queue.pop(0)
        self._reset_learn_state()
        return await self.async_step_learn()

    async def _async_after_confirm(self) -> ConfigFlowResult:
        """After each code is kept: continue the adding queue, or back to menu."""
        if self._adding_queue:
            return await self.async_step_add_entity_learn()
        return await self.async_step_init()

    # ------------------------------------------------------------------
    # Finish: persist changes
    # ------------------------------------------------------------------
    async def async_step_finish(self, user_input=None) -> ConfigFlowResult:
        """Persist the updated commands and reload the entry."""
        if user_input is None:
            return self.async_show_form(step_id="finish", data_schema=vol.Schema({}), description_placeholders={
                "summary": f"{len(self._commands)} learned commands; {self._speed_count} fan speeds. Existing signal data is retained unless you explicitly relearned, replaced or removed it."
            })
        await async_snapshot(self.hass, self.config_entry)
        self.hass.config_entries.async_update_entry(
            self.config_entry,
            data={
                **self.config_entry.data,
                CONF_COMMANDS: self._commands,
                CONF_CUSTOMS: self._customs,
                CONF_SUB_ENTITIES: self._sub_entities,
                CONF_DIRECT: self._direct,
                CONF_SPEED_COUNT: self._speed_count,
                CONF_HAS_ON: (
                    self._fan_power_mode == FAN_POWER_MODE_ON_OFF
                    and CMD_ON in self._commands
                ),
                CONF_FAN_POWER_MODE: self._fan_power_mode,
                "dc_extras": self._extra_buttons,
                "replay_mode": self._replay_mode,
                "startup_state": self._startup_state,
                CONF_SAFE_OFF: self._safe_off_enabled,
                CONF_LIGHT_CYCLE: self._light_cycle_enabled,
                CONF_LIGHT_CYCLE_CLICKS: self._light_cycle_clicks,
                "has_light": CMD_LIGHT in self._commands,
            },
        )

        # Removing a platform entity from setup does not automatically remove
        # its registry record. Delete the matching records so the device page
        # does not retain unavailable ghost controls.
        entity_registry = er.async_get(self.hass)
        for key in self._removed_commands:
            if key == CMD_LIGHT:
                domain = "light"
                unique_id = f"{self.config_entry.entry_id}_light"
            elif key in EXTRA_BUTTONS:
                domain = "button"
                unique_id = f"{self.config_entry.entry_id}_{key}"
            else:
                continue
            entity_id = entity_registry.async_get_entity_id(
                domain,
                DOMAIN,
                unique_id,
            )
            if entity_id is not None:
                entity_registry.async_remove(entity_id)

        return self.async_create_entry(title="", data={})

    async def async_step_remote_manager(self, user_input=None):
        if user_input is not None:
            self._managed_key = user_input["command"]
            return await self.async_step_manage_command()
        return self.async_show_form(step_id="remote_manager", data_schema=vol.Schema({
            vol.Required("command"): selector.SelectSelector(selector.SelectSelectorConfig(options=[
                {"value": key, "label": f"{self._label(key)} ({value.get('replay_mode', self._replay_mode)})"}
                for key, value in self._commands.items()
            ]))
        }))

    async def async_step_manage_command(self, user_input=None):
        key = self._managed_key
        if user_input is not None:
            action = user_input["action"]
            mode = user_input["mode"]
            if action == "test":
                try:
                    await async_send_stored(self.hass, self._transmitter,
                        self._commands[key], self._frequency, mode=mode,
                        clean=(self._replay_mode == "clean" or (self._replay_mode == "legacy" and self._direct)),
                        repeat=CLEAN_REPEAT if self._direct or self._replay_mode == "clean" else 0)
                except HomeAssistantError:
                    return self.async_show_form(step_id="manage_command", data_schema=self._manage_schema(), errors={"base": "send_failed"})
                return await self.async_step_manage_command()
            if action == "save_mode":
                self._commands[key] = {**self._commands[key], "replay_mode": mode}
                return await self.async_step_remote_manager()
            if action == "relearn":
                self._adding_queue = [key]
                return await self.async_step_add_entity_learn()
            if action == "copy":
                return await self.async_step_reassign()
            if action == "rename":
                return await self.async_step_rename_command()
            return await self.async_step_init()
        return self.async_show_form(step_id="manage_command", data_schema=self._manage_schema())

    def _manage_schema(self):
        return vol.Schema({
            vol.Required("mode", default=self._commands[self._managed_key].get("replay_mode", self._replay_mode)):
                selector.SelectSelector(selector.SelectSelectorConfig(options=[
                    {"value": "legacy", "label": "Original integration behavior (preserved)"},
                    {"value": "original", "label": "Original Broadlink packet"},
                    {"value": "raw", "label": "Captured timings"},
                    {"value": "clean", "label": "Mercator filtered"}])),
            vol.Required("action"): selector.SelectSelector(selector.SelectSelectorConfig(options=[
                {"value": "test", "label": "Test selected mode (sends RF now)"},
                {"value": "save_mode", "label": "Keep selected mode after confirming response"},
                {"value": "relearn", "label": "Relearn this button"},
                {"value": "copy", "label": "Reassign / copy to another button"},
                {"value": "rename", "label": "Rename button label"},
                {"value": "back", "label": "Back"}]))
        })

    async def async_step_reassign(self, user_input=None):
        errors = {}
        if user_input is not None:
            target = user_input["target"]
            if target in self._commands and not user_input.get("overwrite", False):
                errors["base"] = "overwrite_required"
            else:
                self._commands[target] = deepcopy(self._commands[self._managed_key])
                if target in EXTRA_BUTTONS and target not in self._extra_buttons:
                    self._extra_buttons.append(target)
                return await self.async_step_init()
        return self.async_show_form(step_id="reassign", errors=errors, data_schema=vol.Schema({
            vol.Required("target"): selector.SelectSelector(selector.SelectSelectorConfig(options=list(dict.fromkeys([
                *self._commands, *_build_queue(self._entity_type, speed_count=self._speed_count), *EXTRA_BUTTONS
            ])))),
            vol.Optional("overwrite", default=False): selector.BooleanSelector()
        }))

    async def async_step_rename_command(self, user_input=None):
        if user_input is not None:
            name = user_input["name"].strip()
            if name:
                self._commands[self._managed_key] = {**self._commands[self._managed_key], "label": name[:100]}
            return await self.async_step_remote_manager()
        return self.async_show_form(step_id="rename_command", data_schema=vol.Schema({
            vol.Required("name"): selector.TextSelector()
        }))

    async def async_step_command_library(self, user_input=None):
        if user_input is not None:
            self._library_entry = user_input["source_entry"]
            return await self.async_step_library_copy()
        entries = self.hass.config_entries.async_entries(DOMAIN)
        return self.async_show_form(step_id="command_library", data_schema=vol.Schema({
            vol.Required("source_entry"): selector.SelectSelector(selector.SelectSelectorConfig(options=[
                {"value": e.entry_id, "label": e.title} for e in entries if e.data.get("frequency") == self._frequency
            ]))
        }))

    async def async_step_library_copy(self, user_input=None):
        entry = self.hass.config_entries.async_get_entry(self._library_entry)
        errors = {}
        if entry is None or entry.domain != DOMAIN or entry.data.get("frequency") != self._frequency:
            return self.async_abort(reason="invalid_import")
        if user_input is not None:
            key = user_input["source_command"]
            target = user_input["target"]
            if target in self._commands and not user_input.get("overwrite", False):
                errors["base"] = "overwrite_required"
            elif not re.fullmatch(r"[a-zA-Z0-9_]{1,100}", target):
                errors["base"] = "invalid_import"
            else:
                source = deepcopy(entry.data[CONF_COMMANDS][key])
                if "replay_mode" not in source:
                    source["replay_mode"] = entry.data.get("replay_mode", "legacy")
                    if source["replay_mode"] == "legacy":
                        source["legacy_clean"] = entry.data.get(CONF_DIRECT, False)
                self._commands[target] = source
                if target in EXTRA_BUTTONS and target not in self._extra_buttons:
                    self._extra_buttons.append(target)
                if target not in EXTRA_BUTTONS and target not in _build_queue(self._entity_type, speed_count=self._speed_count):
                    if not any(s.get("key_prefix") == target for s in self._sub_entities):
                        self._sub_entities.append({"entity_type": ENTITY_TYPE_BUTTON, "name": target, "key_prefix": target})
                return await self.async_step_init()
        return self.async_show_form(step_id="library_copy", errors=errors, data_schema=vol.Schema({
            vol.Required("source_command"): selector.SelectSelector(selector.SelectSelectorConfig(options=list(entry.data[CONF_COMMANDS]))),
            vol.Required("target"): selector.TextSelector(),
            vol.Optional("overwrite", default=False): selector.BooleanSelector()
        }))

    async def async_step_export_import(self, user_input=None):
        errors = {}
        if user_input is not None:
            text = user_input.get("import_json", "").strip()
            if not text:
                return await self.async_step_init()
            try:
                commands = import_commands(text, self._frequency)
                collisions = set(commands) & set(self._commands)
                if collisions and not user_input.get("overwrite", False):
                    errors["base"] = "overwrite_required"
                else:
                    self._commands.update(commands)
                    for key in commands:
                        if key in EXTRA_BUTTONS and key not in self._extra_buttons:
                            self._extra_buttons.append(key)
                        elif key not in EXTRA_BUTTONS and key not in self.config_entry.data[CONF_COMMANDS]:
                            if not key.startswith("speed_") and key not in (CMD_ON, CMD_OFF, CMD_LIGHT, CMD_OPEN, CMD_CLOSE, CMD_STOP):
                                if not any(sub.get("key_prefix") == key for sub in self._sub_entities):
                                    self._sub_entities.append({"entity_type": ENTITY_TYPE_BUTTON, "name": key, "key_prefix": key})
                    return await self.async_step_init()
            except (ValueError, TypeError, KeyError):
                errors["base"] = "invalid_import"
        return self.async_show_form(step_id="export_import", errors=errors, data_schema=vol.Schema({
            vol.Optional("export_json", default=export_commands({"frequency": self._frequency, "commands": self._commands, "replay_mode": self._replay_mode, "direct_capture": self._direct})):
                selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            vol.Optional("import_json"): selector.TextSelector(selector.TextSelectorConfig(multiline=True)),
            vol.Optional("overwrite", default=False): selector.BooleanSelector()
        }))


    async def async_step_fan_settings(self, user_input=None):
        errors = {}
        if user_input is not None:
            count = int(user_input["speed_count"])
            if any(f"speed_{i}" not in self._commands for i in range(1, count + 1)):
                errors["base"] = "missing_speeds"
            else:
                self._speed_count = count
                self._startup_state = user_input["startup_state"]
                self._safe_off_enabled = bool(user_input[CONF_SAFE_OFF])
                if CMD_LIGHT in self._commands:
                    self._light_cycle_enabled = bool(user_input[CONF_LIGHT_CYCLE])
                    self._light_cycle_clicks = {
                        "cool": int(user_input["light_cycle_cool_clicks"]),
                        "neutral": int(user_input["light_cycle_neutral_clicks"]),
                        "warm": int(user_input["light_cycle_warm_clicks"]),
                    }
                return await self.async_step_init()
        fields = {
            vol.Required("speed_count", default=self._speed_count): selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=8, step=1)),
            vol.Required("startup_state", default=self._startup_state): selector.SelectSelector(selector.SelectSelectorConfig(options=[
                {"value": "restore", "label": "Restore last assumed state"},
                {"value": "unknown", "label": "Unknown until a command or correction"},
                {"value": "off", "label": "Assume off (does not send RF)"}])),
            vol.Required(CONF_SAFE_OFF, default=self._safe_off_enabled): selector.BooleanSelector(),
        }
        if CMD_LIGHT in self._commands:
            fields.update({
                vol.Required(CONF_LIGHT_CYCLE, default=self._light_cycle_enabled): selector.BooleanSelector(),
                vol.Required("light_cycle_cool_clicks", default=self._light_cycle_clicks["cool"]): selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=12, step=1)),
                vol.Required("light_cycle_neutral_clicks", default=self._light_cycle_clicks["neutral"]): selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=12, step=1)),
                vol.Required("light_cycle_warm_clicks", default=self._light_cycle_clicks["warm"]): selector.NumberSelector(selector.NumberSelectorConfig(min=1, max=12, step=1)),
            })
        return self.async_show_form(step_id="fan_settings", errors=errors, data_schema=vol.Schema(fields))

    async def async_step_diagnostics(self, user_input=None):
        if user_input is not None:
            return await self.async_step_init()
        from .diagnostics import command_summary
        summary = json.dumps(command_summary({"commands": self._commands}), indent=2)
        return self.async_show_form(step_id="diagnostics", data_schema=vol.Schema({}), description_placeholders={"summary": summary})

    async def async_step_create_dashboard(self, user_input=None):
        if user_input is not None:
            from .dashboard import async_create_dashboard
            try:
                await async_create_dashboard(self.hass)
            except (HomeAssistantError, OSError):
                return self.async_show_form(step_id="create_dashboard", data_schema=vol.Schema({}), errors={"base": "dashboard_failed"})
            return self.async_show_form(step_id="dashboard_ready", data_schema=vol.Schema({}))
        return self.async_show_form(step_id="create_dashboard", data_schema=vol.Schema({}))

    async def async_step_dashboard_ready(self, user_input=None):
        return await self.async_step_init()
