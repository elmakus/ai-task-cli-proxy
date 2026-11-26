"""CLIProxyAPI Image Generation integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)

from .providers import active_model, active_provider, async_list_models

PLATFORMS = (Platform.AI_TASK,)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CLIProxyAPI Image Generation from a config entry."""
    try:
        models = await async_list_models(hass, entry, active_provider(entry))
    except ConfigEntryAuthFailed:
        raise
    except HomeAssistantError as err:
        raise ConfigEntryNotReady(str(err)) from None
    if active_model(entry) not in models:
        raise ConfigEntryNotReady("The selected image model is not available")
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a CLIProxyAPI Image Generation config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
