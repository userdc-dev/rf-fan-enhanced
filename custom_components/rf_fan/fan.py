"""Fan platform for rf_fan - an optimistic, discrete-speed RF fan."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
import voluptuous as vol
from homeassistant.core import callback
from homeassistant.helpers import entity_platform
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers import config_validation as cv
from typing import Any

from homeassistant.components.fan import FanEntity, FanEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
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
    platform.async_register_entity_service("set_timer", {vol.Required("minutes"): vol.All(vol.Coerce(int), vol.Range(min=0, max=1440))}, "async_set_timer")
    platform.async_register_entity_service("set_assumed_state", {
        vol.Required("state"): vol.In(["on", "off", "unknown"]),
        vol.Optional("percentage"): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
        vol.Optional("direction"): vol.In(["forward", "reverse", "unknown"]),
    }, "async_set_assumed_state")
    data = entry.data
    entities: list[FanEntity] = []

    primary_type = data.get("entity_type", ENTITY_TYPE_FAN)
    if primary_type == ENTITY_TYPE_FAN:
        entities.append(RfFan(
            entry,
            name=data[CONF_NAME],
            key_prefix="",
            speed_count=data.get(CONF_SPEED_COUNT, 3),
            has_on=data.get(CONF_HAS_ON, False),
            power_mode=data.get(CONF_FAN_POWER_MODE, "on_off" if data.get(CONF_HAS_ON) else FAN_POWER_MODE_OFF_ONLY),
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
        self._key_prefix = key_prefix
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
                    self._schedule_timer(datetime.fromisoformat(deadline))
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

    async def async_set_percentage(self, percentage: int) -> None:
        """Set the fan speed by percentage."""
        if percentage == 0:
            await self.async_turn_off()
            return
        speed = self._speed_from_percentage(percentage)
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
        if percentage is not None:
            await self.async_set_percentage(percentage)
            return
        if self._power_mode == FAN_POWER_MODE_TOGGLE:
            if not self._attr_is_on:
                await self._send(self._pk(CMD_OFF))
                self._attr_is_on = True
                if not self._attr_percentage:
                    self._attr_percentage = ranged_value_to_percentage(
                        (1, self._speed_count), 1
                    )
            self.async_write_ha_state()
            return
        on_key = self._pk(CMD_ON)
        if self._has_on and on_key in self._commands:
            await self._send(on_key)
            self._attr_is_on = True
            if not self._attr_percentage:
                self._attr_percentage = ranged_value_to_percentage(
                    (1, self._speed_count), 1
                )
        else:
            # No dedicated On button: resume the last speed, or speed 1.
            speed = self._speed_from_percentage(self._attr_percentage or 1)
            await self._send(self._pk(f"speed_{speed}"))
            self._attr_is_on = True
            self._attr_percentage = ranged_value_to_percentage(
                (1, self._speed_count), speed
            )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fan off."""
        if self._timer_cancel:
            self._timer_cancel()
            self._timer_cancel = None
        self._timer_end = None
        if self._power_mode != FAN_POWER_MODE_TOGGLE or self._attr_is_on is not False:
            await self._send(self._pk(CMD_OFF))
        self._attr_is_on = False
        self._attr_percentage = 0
        self.async_write_ha_state()


    @property
    def extra_state_attributes(self):
        return {"state_uncertain": self._state_uncertain,
                "timer_end": self._timer_end.isoformat() if self._timer_end else None,
                "last_transmission": self.hass.data.get("rf_fan_runtime", {}).get("last_send", {}).get(self._transmitter)}

    async def async_set_assumed_state(self, state, percentage=None, direction=None):
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
        if self._attr_current_direction is None:
            raise HomeAssistantError("Confirm the current direction with rf_fan.set_assumed_state first")
        if direction != self._attr_current_direction:
            await self._send(self._pk("direction_toggle"))
            self._attr_current_direction = direction
            self._state_uncertain = True
            self.async_write_ha_state()

    async def async_oscillate(self, oscillating):
        if self._attr_oscillating is None or oscillating != self._attr_oscillating:
            await self._send(self._pk("swing"))
            self._attr_oscillating = oscillating
            self._state_uncertain = True
            self.async_write_ha_state()

    def _schedule_timer(self, deadline):
        if self._timer_cancel:
            self._timer_cancel()
        self._timer_end = deadline
        @callback
        def expire(now):
            self._timer_cancel = None
            self._timer_end = None
            self.hass.async_create_task(self.async_turn_off())
        self._timer_cancel = async_track_point_in_utc_time(self.hass, expire, deadline)

    async def async_set_timer(self, minutes):
        if self._timer_cancel:
            self._timer_cancel()
            self._timer_cancel = None
        self._timer_end = None
        if minutes:
            self._schedule_timer(datetime.now(timezone.utc) + timedelta(minutes=minutes))
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self):
        if self._timer_cancel:
            self._timer_cancel()
            self._timer_cancel = None
        await super().async_will_remove_from_hass()
