"""Fan platform for rf_fan - an optimistic, discrete-speed RF fan."""

from __future__ import annotations

import asyncio
import math
from datetime import datetime, timedelta, timezone
import voluptuous as vol
from homeassistant.core import callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.event import async_track_point_in_utc_time, async_track_time_interval
from homeassistant.helpers import config_validation as cv
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util.percentage import (
    percentage_to_ranged_value,
    ranged_value_to_percentage,
)

from .command import async_send_stored
from .const import (
    CLEAN_REPEAT,
    CMD_OFF,
    CMD_ON,
    CONF_COMMANDS,
    CONF_DIRECT,
    CONF_FREQUENCY,
    CONF_FAN_POWER_MODE,
    CONF_HAS_ON,
    CONF_NAME,
    CONF_LIGHT_CYCLE,
    CONF_LIGHT_CYCLE_CLICKS,
    CONF_SAFE_OFF,
    CONF_SPEED_COUNT,
    CONF_SUB_ENTITIES,
    CONF_TRANSMITTER,
    DOMAIN,
    ENTITY_TYPE_FAN,
    FAN_POWER_MODE_OFF_ONLY,
    FAN_POWER_MODE_TOGGLE,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the fan entity from a config entry."""
    platform = entity_platform.async_get_current_platform()
    platform.async_register_entity_service("set_timer", {vol.Required("minutes"): vol.All(vol.Coerce(float), vol.Range(min=0))}, "async_set_timer")
    platform.async_register_entity_service("safe_off", {}, "async_safe_off")
    platform.async_register_entity_service("send_command", {vol.Required("command"): cv.string}, "async_send_command")
    platform.async_register_entity_service("set_light_cycle", {
        vol.Required("mode"): vol.In(["cool", "neutral", "warm"]),
    }, "async_set_light_cycle")
    platform.async_register_entity_service("set_assumed_state", {
        vol.Required("state"): vol.In(["on", "off", "unknown"]),
        vol.Optional("percentage"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
        vol.Optional("direction"): vol.In(["forward", "reverse", "unknown"]),
    }, "async_set_assumed_state")
    data = entry.data
    entities: list[FanEntity] = []
    operation_lock = asyncio.Lock()
    sub_fan_prefixes = {
        sub.get("key_prefix")
        for sub in data.get(CONF_SUB_ENTITIES, [])
        if sub.get("entity_type") == ENTITY_TYPE_FAN and sub.get("key_prefix")
    }

    primary_type = data.get("entity_type", ENTITY_TYPE_FAN)
    if primary_type == ENTITY_TYPE_FAN:
        entities.append(RfFan(
            entry,
            name=data[CONF_NAME],
            key_prefix="",
            speed_count=data.get(CONF_SPEED_COUNT, 3),
            has_on=data.get(CONF_HAS_ON, False),
            power_mode=data.get(CONF_FAN_POWER_MODE, "on_off" if data.get(CONF_HAS_ON) else FAN_POWER_MODE_OFF_ONLY),
            sub_fan_prefixes=sub_fan_prefixes,
            operation_lock=operation_lock,
        ))

    for sub in data.get(CONF_SUB_ENTITIES, []):
        if sub.get("entity_type") == ENTITY_TYPE_FAN:
            entities.append(RfFan(
                entry,
                name=sub["name"],
                key_prefix=sub["key_prefix"],
                speed_count=sub.get("speed_count", 3),
                has_on=sub.get("has_on", False),
                power_mode=sub.get("fan_power_mode", FAN_POWER_MODE_OFF_ONLY),
                sub_fan_prefixes=sub_fan_prefixes,
                operation_lock=operation_lock,
            ))

    if entities:
        async_add_entities(entities)


class RfFan(FanEntity, RestoreEntity):
    """A learned RF fan with discrete speeds and optimistic state.

    RF is one-way, so we cannot read the fan's real state - we assume it matches
    the last command we sent. Because every speed maps to its own discrete code
    (and there's a dedicated Off), that assumption stays accurate without drift.
    """

    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_assumed_state = True

    def __init__(
        self,
        entry: ConfigEntry,
        name: str,
        key_prefix: str,
        speed_count: int,
        has_on: bool,
        power_mode: str,
        sub_fan_prefixes: set[str],
        operation_lock: asyncio.Lock | None = None,
    ) -> None:
        """Initialise from the config entry's learned commands."""
        data = entry.data
        self._transmitter: str = data[CONF_TRANSMITTER]
        self._frequency: int = data[CONF_FREQUENCY]
        self._commands: dict[str, Any] = data[CONF_COMMANDS]
        self._speed_count = speed_count
        self._has_on = has_on
        self._power_mode = power_mode
        self._direct: bool = data.get(CONF_DIRECT, False)
        self._replay_mode = data.get("replay_mode", "legacy")
        self._entry_id = entry.entry_id
        self._key_prefix = key_prefix
        self._sub_fan_prefixes = sub_fan_prefixes
        self._attr_name = name if key_prefix else None
        uid_suffix = f"fan_{key_prefix}" if key_prefix else "fan"

        self._attr_unique_id = f"{entry.entry_id}_{uid_suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=data[CONF_NAME],
            manufacturer="RF Device (Broadlink Learning)",
        )
        self._attr_supported_features = (
            FanEntityFeature.SET_SPEED
            | FanEntityFeature.TURN_ON
            | FanEntityFeature.TURN_OFF
        )
        self._attr_speed_count = speed_count
        self._startup_state = data.get("startup_state", "restore")
        self._timer_end = None
        self._timer_cancel = None
        self._timer_tick_cancel = None
        # All fan entities belonging to one learned remote share the same
        # queue, so a sub-fan cannot interleave with another fan's Safe OFF.
        self._operation_lock = operation_lock or asyncio.Lock()
        self._safe_off_active = False
        self._removed = False
        self._timer_generation = 0
        self._timer_missed = False
        self._safe_off_enabled = bool(
            data.get(CONF_SAFE_OFF, self._pk("speed_1") in self._commands and self._pk(CMD_OFF) in self._commands)
        )
        self._light_cycle_enabled = bool(
            data.get(CONF_LIGHT_CYCLE, self._pk("light") in self._commands)
        )
        self._light_cycle_clicks = {
            "cool": 1,
            "neutral": 3,
            "warm": 5,
            **data.get(CONF_LIGHT_CYCLE_CLICKS, {}),
        }
        self._state_uncertain = True
        self._attr_is_on = None
        self._attr_percentage = None
        self._attr_current_direction = None
        if self._pk("direction_toggle") in self._commands:
            self._attr_supported_features |= FanEntityFeature.DIRECTION
        if self._pk("swing") in self._commands:
            self._attr_supported_features |= FanEntityFeature.OSCILLATE
        self._attr_oscillating = None

    @property
    def is_on(self):
        return self._attr_is_on

    def _pk(self, cmd: str) -> str:
        return f"{self._key_prefix}_{cmd}" if self._key_prefix else cmd

    async def async_added_to_hass(self) -> None:
        """Restore the last assumed state across restarts."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if self._startup_state == "off":
            self._attr_is_on, self._attr_percentage = False, 0
        if last is not None and self._startup_state == "restore":
            self._attr_is_on = {"on": True, "off": False}.get(last.state)
            if (pct := last.attributes.get("percentage")) is not None:
                self._attr_percentage = pct
            self._attr_current_direction = last.attributes.get("direction")
            self._attr_oscillating = last.attributes.get("oscillating")
            deadline = last.attributes.get("timer_end")
            if deadline:
                try:
                    parsed = datetime.fromisoformat(deadline)
                    if parsed > datetime.now(timezone.utc):
                        self._schedule_timer(parsed)
                    else:
                        # A timer that elapsed while Home Assistant was stopped is
                        # not safe to replay after startup.  Record it for the UI
                        # and require the user to start a fresh timer.
                        self._timer_missed = True
                except ValueError:
                    pass

    async def _send(self, key: str) -> None:
        """Transmit a learned command by key."""
        command = self._commands.get(key)
        if not command:
            raise HomeAssistantError(
                f"Command '{key}' not learned."
            )
        self._state_uncertain = True
        await async_send_stored(
            self.hass,
            self._transmitter,
            command,
            self._frequency,
            clean=(self._replay_mode == "clean" or (self._replay_mode == "legacy" and self._direct)),
            repeat=CLEAN_REPEAT if (self._replay_mode == "clean" or (self._replay_mode == "legacy" and self._direct)) else 0,
        )

    def _speed_from_percentage(self, percentage: int) -> int:
        """Map a percentage to a discrete speed 1..speed_count."""
        speed = math.ceil(percentage_to_ranged_value((1, self._speed_count), percentage))
        return max(1, min(self._speed_count, speed))

    def _owns_command(self, command: str) -> bool:
        """Return whether a stored command belongs to this fan entity."""
        if command not in self._commands:
            return False
        if self._key_prefix:
            return command.startswith(f"{self._key_prefix}_")
        return not any(command.startswith(f"{prefix}_") for prefix in self._sub_fan_prefixes)

    def _bare_command(self, command: str) -> str:
        """Remove this entity's command prefix."""
        return command[len(self._key_prefix) + 1:] if self._key_prefix else command

    async def _async_set_percentage_locked(self, percentage: int) -> None:
        if percentage == 0:
            await self._async_turn_off_locked()
            return
        speed = self._speed_from_percentage(percentage)
        await self._send(self._pk(f"speed_{speed}"))
        self._attr_is_on = True
        self._attr_percentage = ranged_value_to_percentage((1, self._speed_count), speed)
        self.async_write_ha_state()

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the fan speed by percentage."""
        async with self._operation_lock:
            await self._async_set_percentage_locked(percentage)

    async def _async_turn_on_locked(self, percentage: int | None = None) -> None:
        if percentage is not None:
            await self._async_set_percentage_locked(percentage)
            return
        if self._power_mode == FAN_POWER_MODE_TOGGLE:
            if not self._attr_is_on:
                await self._send(self._pk(CMD_OFF))
                self._attr_is_on = True
                if not self._attr_percentage:
                    self._attr_percentage = ranged_value_to_percentage((1, self._speed_count), 1)
            self.async_write_ha_state()
            return
        on_key = self._pk(CMD_ON)
        if self._has_on and on_key in self._commands:
            await self._send(on_key)
            self._attr_is_on = True
            if not self._attr_percentage:
                self._attr_percentage = ranged_value_to_percentage((1, self._speed_count), 1)
        else:
            speed = self._speed_from_percentage(self._attr_percentage or 1)
            await self._send(self._pk(f"speed_{speed}"))
            self._attr_is_on = True
            self._attr_percentage = ranged_value_to_percentage((1, self._speed_count), speed)
        self.async_write_ha_state()

    async def async_turn_on(
        self,
        percentage: int | None = None,
        preset_mode: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Turn the fan on, optionally at a given speed."""
        async with self._operation_lock:
            await self._async_turn_on_locked(percentage)

    async def _async_turn_off_locked(self) -> None:
        self._cancel_timer()
        if self._power_mode != FAN_POWER_MODE_TOGGLE or self._attr_is_on is not False:
            await self._send(self._pk(CMD_OFF))
        self._attr_is_on = False
        self._attr_percentage = 0
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        async with self._operation_lock:
            await self._async_turn_off_locked()

    async def _async_safe_off_locked(self) -> None:
        await self._send(self._pk("speed_1"))
        await asyncio.sleep(1)
        await self._send(self._pk(CMD_OFF))
        self._cancel_timer()
        self._attr_is_on = False
        self._attr_percentage = 0
        self._state_uncertain = True
        self.async_write_ha_state()

    async def async_safe_off(self) -> None:
        """Reliably stop a compatible fan without claiming RF feedback."""
        if not self._safe_off_enabled:
            raise HomeAssistantError("Safe OFF is disabled for this fan")
        if self._safe_off_active:
            return
        self._safe_off_active = True
        try:
            async with self._operation_lock:
                await self._async_safe_off_locked()
        finally:
            self._safe_off_active = False

    async def async_send_command(self, command: str) -> None:
        """Send one configured command for reusable remote controls."""
        if not self._owns_command(command):
            raise HomeAssistantError("Command does not belong to this fan")
        async with self._operation_lock:
            await self._send(command)
            await self._apply_command_state_locked(command)

    async def _async_update_light_state(self, toggle_count: int) -> None:
        """Keep the optional optimistic light entity aligned with RF toggles."""
        if toggle_count % 2 == 0:
            return
        unique_id = f"{self._entry_id}_light"
        if self._key_prefix:
            unique_id = f"{self._entry_id}_light_{self._key_prefix}"
        entity_id = er.async_get(self.hass).async_get_entity_id("light", DOMAIN, unique_id)
        state = self.hass.states.get(entity_id) if entity_id else None
        if state is None or state.state not in ("on", "off"):
            return
        await self.hass.services.async_call(
            DOMAIN,
            "set_light_assumed_state",
            {"state": "off" if state.state == "on" else "on"},
            target={"entity_id": entity_id},
            blocking=True,
        )

    async def _apply_command_state_locked(self, command: str) -> None:
        """Apply only state changes that are known from a sent command."""
        bare = self._bare_command(command)
        if bare.startswith("speed_"):
            try:
                speed = int(bare.removeprefix("speed_"))
            except ValueError:
                speed = 0
            if 1 <= speed <= self._speed_count:
                self._attr_is_on = True
                self._attr_percentage = ranged_value_to_percentage((1, self._speed_count), speed)
        elif bare == CMD_ON:
            self._attr_is_on = True
            if not self._attr_percentage:
                self._attr_percentage = ranged_value_to_percentage((1, self._speed_count), 1)
        elif bare == CMD_OFF:
            if self._power_mode == FAN_POWER_MODE_TOGGLE:
                if self._attr_is_on is not None:
                    self._attr_is_on = not self._attr_is_on
                    self._attr_percentage = 0 if not self._attr_is_on else (
                        self._attr_percentage or ranged_value_to_percentage((1, self._speed_count), 1)
                    )
            else:
                self._cancel_timer()
                self._attr_is_on = False
                self._attr_percentage = 0
        elif bare == "direction_toggle" and self._attr_current_direction in ("forward", "reverse"):
            self._attr_current_direction = "reverse" if self._attr_current_direction == "forward" else "forward"
        elif bare == "swing" and self._attr_oscillating is not None:
            self._attr_oscillating = not self._attr_oscillating
        elif bare == "light":
            await self._async_update_light_state(1)
        self.async_write_ha_state()

    async def async_set_light_cycle(self, mode: str) -> None:
        """Replay the existing light toggle a configured number of times."""
        key = self._pk("light")
        if not self._light_cycle_enabled or key not in self._commands:
            raise HomeAssistantError("Light-cycle presets are disabled for this fan")
        clicks = int(self._light_cycle_clicks[mode])
        async with self._operation_lock:
            for index in range(clicks):
                await self._send(key)
                if index + 1 < clicks:
                    await asyncio.sleep(0.35)
            await self._async_update_light_state(clicks)
            self._state_uncertain = True
            self.async_write_ha_state()

    async def _async_timer_expired(self, generation: int) -> None:
        """Use Safe OFF when enabled, otherwise retain normal turn-off."""
        async with self._operation_lock:
            if self._removed or generation != self._timer_generation:
                return
            if self._safe_off_enabled:
                await self._async_safe_off_locked()
            else:
                await self._async_turn_off_locked()


    @property
    def extra_state_attributes(self):
        remaining = max(0, int((self._timer_end - datetime.now(timezone.utc)).total_seconds())) if self._timer_end else 0
        return {"state_uncertain": self._state_uncertain,
                "safe_off_enabled": self._safe_off_enabled,
                "timer_missed": self._timer_missed,
                "timer_end": self._timer_end.isoformat() if self._timer_end else None,
                "timer_remaining": str(timedelta(seconds=remaining)),
                "timer_remaining_seconds": remaining,
                "last_transmission": self.hass.data.get("rf_fan_runtime", {}).get("last_send", {}).get(self._transmitter)}

    async def async_set_assumed_state(self, state, percentage=None, direction=None):
        async with self._operation_lock:
            self._attr_is_on = {"on": True, "off": False, "unknown": None}[state]
            if state == "off":
                self._attr_percentage = 0
            elif percentage is not None:
                self._attr_percentage = percentage
            elif state == "unknown":
                self._attr_percentage = None
            if direction is not None:
                self._attr_current_direction = None if direction == "unknown" else direction
            self._state_uncertain = state == "unknown"
            self.async_write_ha_state()

    async def async_set_direction(self, direction):
        if direction not in ("forward", "reverse"):
            raise HomeAssistantError("Invalid fan direction")
        async with self._operation_lock:
            if self._attr_current_direction is None:
                raise HomeAssistantError("Confirm the current direction with rf_fan.set_assumed_state first")
            if direction != self._attr_current_direction:
                await self._send(self._pk("direction_toggle"))
                self._attr_current_direction = direction
                self._state_uncertain = True
                self.async_write_ha_state()

    async def async_oscillate(self, oscillating):
        async with self._operation_lock:
            if self._attr_oscillating is None or oscillating != self._attr_oscillating:
                await self._send(self._pk("swing"))
                self._attr_oscillating = oscillating
                self._state_uncertain = True
                self.async_write_ha_state()

    def _schedule_timer(self, deadline):
        self._cancel_timer()
        self._timer_end = deadline
        self._timer_missed = False
        generation = self._timer_generation
        @callback
        def expire(now):
            if generation != self._timer_generation:
                return
            self._timer_cancel = None
            if self._timer_tick_cancel:
                self._timer_tick_cancel()
                self._timer_tick_cancel = None
            self._timer_end = None
            self.async_write_ha_state()
            self.hass.async_create_task(self._async_timer_expired(generation))
        @callback
        def tick(now):
            self.async_write_ha_state()
        self._timer_cancel = async_track_point_in_utc_time(self.hass, expire, deadline)
        self._timer_tick_cancel = async_track_time_interval(self.hass, tick, timedelta(seconds=1))

    def _cancel_timer(self):
        self._timer_generation += 1
        if self._timer_cancel:
            self._timer_cancel()
            self._timer_cancel = None
        if self._timer_tick_cancel:
            self._timer_tick_cancel()
            self._timer_tick_cancel = None
        self._timer_end = None

    async def async_set_timer(self, minutes):
        async with self._operation_lock:
            self._cancel_timer()
            self._timer_missed = False
            if minutes:
                self._schedule_timer(datetime.now(timezone.utc) + timedelta(minutes=minutes))
            self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        self._removed = True
        self._cancel_timer()
        await super().async_will_remove_from_hass()
