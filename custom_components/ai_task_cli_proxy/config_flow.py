"""Configuration and options flows for CLIProxyAPI Image Generation."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from aiohttp import ClientError, ClientResponseError, ClientTimeout
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_BASE_URL,
    CONF_MODEL,
    DEFAULT_BASE_URL,
    DEFAULT_MODEL,
    DOMAIN,
)


def _normalize_base_url(value: str) -> str | None:
    """Accept a server URL, not a URL containing credentials or query data."""
    url = value.strip().rstrip("/")
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            return None
        _ = parsed.port
    except ValueError:
        return None
    return url


async def _async_check_connection(
    hass: HomeAssistant, base_url: str, api_key: str
) -> str | None:
    """Validate connection and authentication without generating an image."""
    try:
        async with async_get_clientsession(hass).get(
            f"{base_url}/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=ClientTimeout(total=20),
        ) as response:
            response.raise_for_status()
    except ClientResponseError as err:
        return "invalid_auth" if err.status in (401, 403) else "cannot_connect"
    except (ClientError, TimeoutError, ValueError, OSError):
        return "cannot_connect"
    return None


class CLIProxyAPIConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a CLIProxyAPI image provider."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> CLIProxyAPIOptionsFlow:
        """Expose model selection without rebuilding the config entry."""
        return CLIProxyAPIOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Configure a new provider."""
        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = _normalize_base_url(user_input[CONF_BASE_URL])
            api_key = user_input[CONF_API_KEY]
            model = user_input[CONF_MODEL].strip()

            if not base_url:
                errors["base"] = "invalid_url"
            elif not model:
                errors[CONF_MODEL] = "invalid_model"
            else:
                error = await _async_check_connection(self.hass, base_url, api_key)
                if error:
                    errors["base"] = error
                else:
                    # Preserve the ID strategy of existing installations.
                    await self.async_set_unique_id(base_url)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title="CLIProxyAPI Images",
                        data={
                            CONF_BASE_URL: base_url,
                            CONF_API_KEY: api_key,
                            CONF_MODEL: model,
                        },
                    )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BASE_URL,
                    default=(user_input or {}).get(CONF_BASE_URL, DEFAULT_BASE_URL),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
                vol.Required(CONF_API_KEY): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Required(
                    CONF_MODEL,
                    default=(user_input or {}).get(CONF_MODEL, DEFAULT_MODEL),
                ): TextSelector(),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Change the server URL or key without creating a second entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = _normalize_base_url(user_input[CONF_BASE_URL])
            api_key = user_input.get(CONF_API_KEY) or entry.data[CONF_API_KEY]

            if not base_url:
                errors["base"] = "invalid_url"
            else:
                error = await _async_check_connection(self.hass, base_url, api_key)
                if error:
                    errors["base"] = error
                else:
                    # Preserve the installed entry's unique ID and all entity IDs.
                    await self.async_set_unique_id(entry.unique_id)
                    self._abort_if_unique_id_mismatch()
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
                            CONF_BASE_URL: base_url,
                            CONF_API_KEY: api_key,
                        },
                    )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_BASE_URL,
                    default=(user_input or {}).get(
                        CONF_BASE_URL, entry.data[CONF_BASE_URL]
                    ),
                ): TextSelector(TextSelectorConfig(type=TextSelectorType.URL)),
                vol.Optional(CONF_API_KEY): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
            }
        )
        return self.async_show_form(
            step_id="reconfigure", data_schema=schema, errors=errors
        )


class CLIProxyAPIOptionsFlow(config_entries.OptionsFlow):
    """Allow changing the image model with no reload or loss of entity IDs."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Update the image model used by subsequent requests."""
        errors: dict[str, str] = {}

        if user_input is not None:
            model = user_input[CONF_MODEL].strip()
            if model:
                return self.async_create_entry(data={CONF_MODEL: model})
            errors[CONF_MODEL] = "invalid_model"

        current_model = self.config_entry.options.get(
            CONF_MODEL, self.config_entry.data[CONF_MODEL]
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_MODEL,
                    default=(user_input or {}).get(CONF_MODEL, current_model),
                ): TextSelector(),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
