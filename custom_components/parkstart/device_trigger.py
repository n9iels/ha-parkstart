"""Device triggers: "parking session started/ended" on a permit device.

They wrap the parkstart_session_started/ended events, filtered on the permit, so automations can be made
in the UI without typing an event name. The event data is available as trigger.event.data.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.device_automation import DEVICE_TRIGGER_BASE_SCHEMA
from homeassistant.components.device_automation.exceptions import InvalidDeviceAutomationConfig
from homeassistant.components.homeassistant.triggers import event as event_trigger
from homeassistant.const import CONF_DEVICE_ID, CONF_DOMAIN, CONF_PLATFORM, CONF_TYPE
from homeassistant.core import CALLBACK_TYPE, HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.trigger import TriggerActionType, TriggerInfo
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    EVENT_SESSION_ENDED,
    EVENT_SESSION_STARTED,
    SOURCE_EXTERNAL,
    SOURCE_HOME_ASSISTANT,
)

CONF_SOURCE = "source"

TRIGGER_TYPES = {
    "session_started": EVENT_SESSION_STARTED,
    "session_ended": EVENT_SESSION_ENDED,
}

TRIGGER_SCHEMA = DEVICE_TRIGGER_BASE_SCHEMA.extend(
    {
        vol.Required(CONF_TYPE): vol.In(TRIGGER_TYPES),
        vol.Optional(CONF_SOURCE): vol.In([SOURCE_HOME_ASSISTANT, SOURCE_EXTERNAL]),
    }
)


def _permit_id(hass: HomeAssistant, device_id: str) -> int | None:
    """The permit id of a permit device; None for other devices."""
    if (device := dr.async_get(hass).async_get(device_id)) is None:
        return None
    return next(
        (
            int(ident)
            for domain, ident in device.identifiers
            if domain == DOMAIN and ident.isdigit()
        ),
        None,
    )


async def async_get_triggers(hass: HomeAssistant, device_id: str) -> list[dict[str, Any]]:
    """Triggers for a permit device."""
    if _permit_id(hass, device_id) is None:
        return []
    return [
        {CONF_PLATFORM: "device", CONF_DOMAIN: DOMAIN, CONF_DEVICE_ID: device_id, CONF_TYPE: type_}
        for type_ in TRIGGER_TYPES
    ]


async def async_get_trigger_capabilities(
    hass: HomeAssistant, config: ConfigType
) -> dict[str, vol.Schema]:
    """Optionally only trigger for sessions started/stopped by Home Assistant or elsewhere."""
    return {
        "extra_fields": vol.Schema(
            {vol.Optional(CONF_SOURCE): vol.In([SOURCE_HOME_ASSISTANT, SOURCE_EXTERNAL])}
        )
    }


async def async_attach_trigger(
    hass: HomeAssistant,
    config: ConfigType,
    action: TriggerActionType,
    trigger_info: TriggerInfo,
) -> CALLBACK_TYPE:
    """Listen for the session event of this permit."""
    if (permit_id := _permit_id(hass, config[CONF_DEVICE_ID])) is None:
        raise InvalidDeviceAutomationConfig(f"Device {config[CONF_DEVICE_ID]} is not a permit")
    event_data: dict[str, Any] = {"permit_id": permit_id}
    if CONF_SOURCE in config:
        event_data["source"] = config[CONF_SOURCE]
    event_config = event_trigger.TRIGGER_SCHEMA(
        {
            event_trigger.CONF_PLATFORM: "event",
            event_trigger.CONF_EVENT_TYPE: TRIGGER_TYPES[config[CONF_TYPE]],
            event_trigger.CONF_EVENT_DATA: event_data,
        }
    )
    return await event_trigger.async_attach_trigger(
        hass, event_config, action, trigger_info, platform_type="device"
    )
