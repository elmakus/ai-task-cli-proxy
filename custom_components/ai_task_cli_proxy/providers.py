"""Provider profiles and conservative discovery of image-generation models."""

from __future__ import annotations

import json
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientTimeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_BASE_URL, CONF_MODEL
from .http import async_read_bounded

PROXY_GEMINI = "proxy_gemini"
PROXY_OPENAI = "proxy_openai"
GOOGLE_API = "google_api"
CONF_PROVIDER = "provider"
CONF_SAVED_MODELS = "saved_models"
CONF_GOOGLE_API_KEY = "google_api_key"
GOOGLE_BASE_URL = "https://generativelanguage.googleapis.com"
PROVIDER_LABELS = {
    PROXY_GEMINI: "CLIProxyAPI — Gemini / Antigravity",
    PROXY_OPENAI: "CLIProxyAPI — ChatGPT / OpenAI",
    GOOGLE_API: "Google API — Paid",
}
MAX_MODELS_RESPONSE_BYTES = 1024 * 1024

# Model-list endpoints often omit output modality. Fail closed for unknown
# models instead of confusing image understanding with image generation.
GEMINI_IMAGE_MODELS = {
    "gemini-2.0-flash-preview-image-generation",
    "gemini-2.5-flash-image",
    "gemini-2.5-flash-image-preview",
    "gemini-3-pro-image-preview",
    "gemini-3.1-flash-image",
    "gemini-3.1-flash-image-preview",
    "gemini-3.1-flash-lite-image",
    "gemini-3.1-flash-lite-image-preview",
}
OPENAI_IMAGE_MODELS = {
    "gpt-image-1",
    "gpt-image-1-mini",
    "gpt-image-1.5",
    "gpt-image-2",
    "gpt-image-2.5",
    "gpt-image-2.5-flare",
    "gpt-image-2.5-sunburst",
    "gpt-image-2.6",
    "gpt-image-2.6-flare",
    "gpt-image-2.6-sunburst",
}


def active_provider(entry: ConfigEntry) -> str:
    """Existing entries keep their original Gemini-compatible route."""
    return entry.options.get(CONF_PROVIDER, PROXY_GEMINI)


def active_model(entry: ConfigEntry) -> str:
    return entry.options.get(CONF_MODEL, entry.data[CONF_MODEL])


def provider_connection(
    entry: ConfigEntry, provider: str, *, google_key: str | None = None
) -> tuple[str, dict[str, str]]:
    """Keep the direct Google credential separate from proxy credentials."""
    if provider == GOOGLE_API:
        key = (
            google_key
            if google_key is not None
            else entry.options.get(CONF_GOOGLE_API_KEY)
        )
        if not key:
            raise HomeAssistantError("Google API key is not configured")
        return GOOGLE_BASE_URL, {"x-goog-api-key": key}
    if provider not in (PROXY_GEMINI, PROXY_OPENAI):
        raise HomeAssistantError("Unsupported image provider")
    return entry.data[CONF_BASE_URL].rstrip("/"), {
        "Authorization": f"Bearer {entry.data[CONF_API_KEY]}"
    }


def filter_models(rows: list[dict[str, Any]], provider: str) -> list[str]:
    """Return exact advertised IDs for supported image families only."""
    models: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if provider == GOOGLE_API:
            name = row.get("name")
            if not isinstance(name, str) or not name.startswith("models/"):
                continue
            name = name.removeprefix("models/")
            methods = row.get("supportedGenerationMethods")
            if (
                isinstance(methods, list)
                and "generateContent" in methods
                and name in GEMINI_IMAGE_MODELS
            ):
                models.add(name)
            continue
        name = row.get("id")
        if not isinstance(name, str) or not name:
            continue
        owner = row.get("owned_by", "")
        family = name.rsplit("/", 1)[-1]
        if (
            provider == PROXY_GEMINI
            and owner in ("google", "gemini", "antigravity", "aistudio", "vertex")
            and family in GEMINI_IMAGE_MODELS
        ):
            models.add(name)
        elif (
            provider == PROXY_OPENAI
            and owner in ("openai", "codex")
            and family in OPENAI_IMAGE_MODELS
        ):
            models.add(name)
    return sorted(models)


async def async_list_models(
    hass: HomeAssistant,
    entry: ConfigEntry,
    provider: str,
    *,
    google_key: str | None = None,
) -> list[str]:
    """Refresh the catalog without issuing any generation requests."""
    base_url, headers = provider_connection(entry, provider, google_key=google_key)
    google = provider == GOOGLE_API
    url = f"{base_url}/v1beta/models" if google else f"{base_url}/v1/models"
    params = {"pageSize": "100"} if google else {}
    rows: list[dict[str, Any]] = []
    tokens: set[str] = set()
    try:
        for _ in range(10 if google else 1):
            async with async_get_clientsession(hass).get(
                url,
                headers=headers,
                params=params,
                allow_redirects=False,
                timeout=ClientTimeout(total=20),
            ) as response:
                response.raise_for_status()
                if not 200 <= response.status < 300:
                    raise HomeAssistantError("Model catalog redirect or invalid status")
                payload = json.loads(
                    await async_read_bounded(
                        response.content, MAX_MODELS_RESPONSE_BYTES
                    )
                )
            if not isinstance(payload, dict):
                raise HomeAssistantError("Invalid model catalog")
            page = payload.get("models" if google else "data")
            if not isinstance(page, list) or not all(
                isinstance(row, dict) for row in page
            ):
                raise HomeAssistantError("Invalid model catalog")
            rows.extend(page)
            token = payload.get("nextPageToken") if google else None
            if not token:
                return filter_models(rows, provider)
            if not isinstance(token, str) or token in tokens:
                raise HomeAssistantError("Invalid model catalog pagination")
            tokens.add(token)
            params = {"pageSize": "100", "pageToken": token}
        raise HomeAssistantError("Model catalog pagination limit reached")
    except ClientResponseError as err:
        raise HomeAssistantError(f"Model catalog returned HTTP {err.status}") from err
    except (ClientError, TimeoutError, ValueError, OSError, RecursionError) as err:
        raise HomeAssistantError("Could not retrieve model catalog") from err
