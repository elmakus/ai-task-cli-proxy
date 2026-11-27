"""Non-billable health checks for the configured image route."""

from __future__ import annotations

from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN
from .providers import active_model, active_provider, async_list_models

_LOGGER = logging.getLogger(__name__)

type CLIProxyAPIConfigEntry = ConfigEntry[CLIProxyAPICoordinator]


class CLIProxyAPICoordinator(DataUpdateCoordinator[list[str]]):
    """Track catalog availability, never perform image generation."""

    def __init__(self, hass: HomeAssistant, entry: CLIProxyAPIConfigEntry) -> None:
        """Initialize the health monitor for this entry."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=5),
            always_update=False,
        )
        self.entry = entry

    async def _async_update_data(self) -> list[str]:
        """Check the proxy and selected model using only the catalog."""
        try:
            models = await async_list_models(
                self.hass, self.entry, active_provider(self.entry)
            )
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            raise UpdateFailed(
                str(err),
                translation_domain=DOMAIN,
                translation_key="setup_connection",
            ) from None
        if active_model(self.entry) not in models:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                f"missing_model_{self.entry.entry_id}",
                config_entry_id=self.entry.entry_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="missing_model",
            )
            raise UpdateFailed(
                "The selected image model is not available",
                translation_domain=DOMAIN,
                translation_key="model_unavailable",
            )
        ir.async_delete_issue(self.hass, DOMAIN, f"missing_model_{self.entry.entry_id}")
        return models
