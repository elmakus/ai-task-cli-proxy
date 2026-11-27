"""Configuration and options flows for CLIProxyAPI Image Generation."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit
from yarl import URL

import probatio as vol

from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
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

from .providers import (
    CONF_PROVIDER,
    CONF_SAVED_MODELS,
    CONF_BACKGROUND,
    BACKGROUND_CHOICES,
    active_background,
    CHATGPT,
    PROVIDER_LABELS,
    active_provider,
    active_model,
    async_list_models,
    async_fetch_catalog,
    filter_models,
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
        return str(URL(url)).rstrip("/")
    except (ValueError, UnicodeError):
        return None


async def _async_check_connection(
    hass: HomeAssistant,
    base_url: str,
    api_key: str,
    provider: str = CHATGPT,
    model: str = DEFAULT_MODEL,
) -> str | None:
    """Check authentication and the selected image route without generation."""
    try:
        rows = await async_fetch_catalog(hass, base_url, api_key)
    except ConfigEntryAuthFailed:
        return "invalid_auth"
    except HomeAssistantError:
        return "cannot_connect"
    if model not in filter_models(rows, provider):
        return "model_unavailable"
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
    ) -> ConfigFlowResult:
        """Configure a new provider."""
        errors: dict[str, str] = {}
        if user_input is not None:
            base_url = _normalize_base_url(user_input[CONF_BASE_URL])
            api_key = user_input[CONF_API_KEY]

            if not base_url:
                errors["base"] = "invalid_url"
            else:
                self._async_abort_entries_match({CONF_BASE_URL: base_url})
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
                        },
                        options={CONF_PROVIDER: CHATGPT, CONF_MODEL: DEFAULT_MODEL},
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
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change the server URL or key without creating a second entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = _normalize_base_url(user_input[CONF_BASE_URL])
            new_key = user_input.get(CONF_API_KEY)
            if not base_url:
                errors["base"] = "invalid_url"
            elif (
                base_url != _normalize_base_url(entry.data[CONF_BASE_URL])
                and not new_key
            ):
                errors["base"] = "key_required_for_new_url"
            else:
                self._async_abort_entries_match({CONF_BASE_URL: base_url})
                api_key = new_key or entry.data[CONF_API_KEY]
                error = await _async_check_connection(
                    self.hass,
                    base_url,
                    api_key,
                    active_provider(entry),
                    active_model(entry),
                )
                if error:
                    errors["base"] = error
                else:
                    await self.async_set_unique_id(base_url)
                    return self.async_update_reload_and_abort(
                        entry,
                        unique_id=base_url,
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

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start the native reauthentication flow."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Replace only the proxy API key."""
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY]
            error = await _async_check_connection(
                self.hass,
                entry.data[CONF_BASE_URL],
                api_key,
                active_provider(entry),
                active_model(entry),
            )
            if error:
                errors["base"] = error
            else:
                await self.async_set_unique_id(entry.unique_id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_API_KEY: api_key}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_API_KEY): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            errors=errors,
        )


class CLIProxyAPIOptionsFlow(config_entries.OptionsFlow):
    """Select and remember image routes without reloading the AI Task entity."""

    _provider: str
    _models: list[str]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return self.async_show_menu(
            step_id="init",
            menu_options=[
                "select_provider",
                "refresh_models",
                "test_connection",
                "background",
            ],
        )

    async def async_step_select_provider(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            self._provider = user_input[CONF_PROVIDER]
            return await self.async_step_model()
        return self.async_show_form(
            step_id="select_provider",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_PROVIDER, default=active_provider(self.config_entry)
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=key, label=label)
                                for key, label in PROVIDER_LABELS.items()
                            ]
                        )
                    )
                }
            ),
        )

    async def async_step_refresh_models(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        self._provider = active_provider(self.config_entry)
        return await self.async_step_model()

    async def async_step_model(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        provider = self._provider
        entry = self.config_entry
        if user_input is not None:
            model = user_input[CONF_MODEL]
            if model not in self._models:
                return self.async_abort(reason="invalid_model")
            options = dict(entry.options)
            options.pop("google_api_key", None)
            saved = dict(options.get(CONF_SAVED_MODELS, {}))
            saved[active_provider(entry)] = active_model(entry)
            saved[provider] = model
            options.update(
                {CONF_PROVIDER: provider, CONF_MODEL: model, CONF_SAVED_MODELS: saved}
            )
            return self.async_create_entry(title="", data=options)

        try:
            self._models = await async_list_models(self.hass, entry, provider)
        except ConfigEntryAuthFailed:
            self.config_entry.async_start_reauth(self.hass)
            return self.async_abort(reason="invalid_auth")
        except HomeAssistantError:
            return self.async_abort(reason="cannot_connect")
        if not self._models:
            return self.async_abort(reason="no_image_models")
        saved = entry.options.get(CONF_SAVED_MODELS, {})
        current = (
            active_model(entry)
            if provider == active_provider(entry)
            else saved.get(provider, DEFAULT_MODEL if provider == CHATGPT else None)
        )
        field = (
            vol.Required(CONF_MODEL, default=current)
            if current in self._models
            else vol.Required(CONF_MODEL)
        )
        return self.async_show_form(
            step_id="model",
            description_placeholders={
                "provider": PROVIDER_LABELS[provider],
                "count": str(len(self._models)),
            },
            data_schema=vol.Schema(
                {field: SelectSelector(SelectSelectorConfig(options=self._models))}
            ),
        )

    async def async_step_test_connection(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        try:
            models = await async_list_models(
                self.hass, self.config_entry, active_provider(self.config_entry)
            )
        except ConfigEntryAuthFailed:
            self.config_entry.async_start_reauth(self.hass)
            return self.async_abort(reason="invalid_auth")
        except HomeAssistantError:
            return self.async_abort(reason="cannot_connect")
        return self.async_abort(
            reason="connection_test_complete",
            description_placeholders={
                "provider": PROVIDER_LABELS[active_provider(self.config_entry)],
                "count": str(len(models)),
                "selected": "Available"
                if active_model(self.config_entry) in models
                else "Not listed",
            },
        )

    async def async_step_background(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            value = user_input[CONF_BACKGROUND]
            if value not in BACKGROUND_CHOICES:
                return self.async_abort(reason="invalid_background")
            options = dict(self.config_entry.options)
            options.pop("google_api_key", None)
            options[CONF_BACKGROUND] = value
            return self.async_create_entry(title="", data=options)
        return self.async_show_form(
            step_id="background",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BACKGROUND, default=active_background(self.config_entry)
                    ): SelectSelector(
                        SelectSelectorConfig(options=list(BACKGROUND_CHOICES))
                    )
                }
            ),
        )
