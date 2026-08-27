"""CLIProxyAPI Image Generation integration."""

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

PLATFORMS = (Platform.AI_TASK,)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up CLIProxyAPI Image Generation from a config entry."""
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a CLIProxyAPI Image Generation config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
