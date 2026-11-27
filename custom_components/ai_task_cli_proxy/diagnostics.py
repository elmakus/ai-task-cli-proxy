"""Privacy-safe CLIProxyAPI diagnostics without network calls."""

from .coordinator import CLIProxyAPIConfigEntry
from homeassistant.core import HomeAssistant

from .providers import (
    BACKGROUND_CHOICES,
    CONF_BACKGROUND,
    DEFAULT_BACKGROUND,
    active_model,
    active_provider,
    model_protocol,
)


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: CLIProxyAPIConfigEntry
) -> dict[str, object]:
    """Export only known routing and capability information."""
    provider = active_provider(entry)
    model = active_model(entry)
    protocol = model_protocol(provider, model)
    background = entry.options.get(CONF_BACKGROUND, DEFAULT_BACKGROUND)
    coordinator = getattr(entry, "runtime_data", None)
    return {
        "entry_state": entry.state.value,
        "catalog_available": coordinator.last_update_success
        if coordinator is not None
        else None,
        "provider": provider if protocol is not None else "unverified",
        "model": model if protocol is not None else "unverified",
        "protocol": protocol,
        "background": background if background in BACKGROUND_CHOICES else "invalid",
    }
