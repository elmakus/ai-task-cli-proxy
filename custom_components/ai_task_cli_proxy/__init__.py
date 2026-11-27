"""CLIProxyAPI Image Generation integration."""

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN
from .coordinator import CLIProxyAPIConfigEntry, CLIProxyAPICoordinator

PLATFORMS = (Platform.AI_TASK,)


async def async_setup_entry(hass: HomeAssistant, entry: CLIProxyAPIConfigEntry) -> bool:
    """Check the route before loading the AI Task entity."""
    coordinator = CLIProxyAPICoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(
    hass: HomeAssistant, entry: CLIProxyAPIConfigEntry
) -> None:
    """Refresh health when settings change, without retrying image generation."""
    await entry.runtime_data.async_request_refresh()


async def async_unload_entry(
    hass: HomeAssistant, entry: CLIProxyAPIConfigEntry
) -> bool:
    """Unload the platform and remove the entry-owned repair."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    ir.async_delete_issue(hass, DOMAIN, f"missing_model_{entry.entry_id}")
    return True


async def async_remove_entry(
    hass: HomeAssistant, entry: CLIProxyAPIConfigEntry
) -> None:
    """Remove a repair even if setup did not complete."""
    ir.async_delete_issue(hass, DOMAIN, f"missing_model_{entry.entry_id}")
