"""Provider profiles and conservative discovery of image-generation models."""

from __future__ import annotations

import json
from typing import Any

from aiohttp import ClientError, ClientResponseError, ClientTimeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_BASE_URL, CONF_MODEL, DEFAULT_MODEL
from .http import async_read_bounded

CHATGPT = "chatgpt"
ANTIGRAVITY = "antigravity"
MUSE = "muse"
OPENCODE = "opencode"
GOOGLE_PAID = "google_paid_api"
CONF_PROVIDER = "provider"
CONF_SAVED_MODELS = "saved_models"
CONF_BACKGROUND = "background"
DEFAULT_BACKGROUND = "transparent"
BACKGROUND_CHOICES = ("transparent", "opaque", "auto")
PROVIDER_LABELS = {
    CHATGPT: "ChatGPT",
    ANTIGRAVITY: "Antigravity",
    MUSE: "Muse",
    OPENCODE: "OpenCode",
    GOOGLE_PAID: "Google Paid API",
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
    """Legacy entries without an explicit provider use the new default."""
    return entry.options.get(CONF_PROVIDER, CHATGPT)


def active_model(entry: ConfigEntry) -> str:
    if CONF_PROVIDER not in entry.options:
        return DEFAULT_MODEL
    return entry.options.get(CONF_MODEL, DEFAULT_MODEL)


def active_background(entry: ConfigEntry) -> str:
    value = entry.options.get(CONF_BACKGROUND, DEFAULT_BACKGROUND)
    if value not in BACKGROUND_CHOICES:
        raise HomeAssistantError("Unsupported background setting")
    return value


def provider_connection(
    entry: ConfigEntry, provider: str
) -> tuple[str, dict[str, str]]:
    """All profiles use the same CLIProxyAPI server and credential."""
    if provider not in PROVIDER_LABELS:
        raise HomeAssistantError("Unsupported image provider")
    return entry.data[CONF_BASE_URL].rstrip("/"), {
        "Authorization": f"Bearer {entry.data[CONF_API_KEY]}"
    }


def model_protocol(provider: str, model: str) -> str | None:
    """Do not route ambiguous Gemini IDs or unverified image transports."""
    if provider == CHATGPT and model in OPENAI_IMAGE_MODELS:
        return "openai"
    if provider == ANTIGRAVITY and model.startswith("antigravity/"):
        if model.removeprefix("antigravity/") in GEMINI_IMAGE_MODELS:
            return "gemini"
    if provider == GOOGLE_PAID and model == "google-paid/gemini-3.1-flash-image":
        return "gemini"
    # Muse and the installed OpenCode Go plugin have no confirmed image
    # generation transport. Keep profiles selectable but expose no models.
    return None


def filter_models(rows: list[dict[str, Any]], provider: str) -> list[str]:
    """Only advertise supported, explicitly scoped image routes."""
    owners = {
        CHATGPT: {"openai", "codex"},
        ANTIGRAVITY: {"antigravity"},
        GOOGLE_PAID: {"google", "gemini", "aistudio"},
    }
    result: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        model = row.get("id")
        owner = row.get("owned_by")
        if not isinstance(model, str) or not isinstance(owner, str):
            continue
        if owner not in owners.get(provider, set()):
            continue
        if model_protocol(provider, model) is None:
            continue
        modalities = row.get("output_modalities")
        if isinstance(modalities, list) and "image" not in modalities:
            continue
        result.add(model)
    return sorted(result)


async def async_fetch_catalog(
    hass: HomeAssistant, base_url: str, api_key: str
) -> list[dict[str, Any]]:
    """Read the authenticated catalog without generating an image."""
    try:
        async with async_get_clientsession(hass).get(
            f"{base_url.rstrip('/')}/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            allow_redirects=False,
            timeout=ClientTimeout(total=20),
        ) as response:
            response.raise_for_status()
            if not 200 <= response.status < 300:
                raise HomeAssistantError("Invalid model catalog status")
            payload = json.loads(
                await async_read_bounded(response.content, MAX_MODELS_RESPONSE_BYTES)
            )
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise HomeAssistantError("Invalid model catalog")
        rows = payload["data"]
        if not all(
            isinstance(row, dict)
            and isinstance(row.get("id"), str)
            and row["id"].strip()
            for row in rows
        ):
            raise HomeAssistantError("Invalid model catalog")
        return rows
    except ClientResponseError as err:
        if err.status in (401, 403):
            raise ConfigEntryAuthFailed("Invalid CLIProxyAPI key") from None
        raise HomeAssistantError(f"Model catalog returned HTTP {err.status}") from None
    except (ClientError, TimeoutError, ValueError, OSError, RecursionError):
        raise HomeAssistantError("Could not retrieve model catalog") from None


async def async_list_models(
    hass: HomeAssistant, entry: ConfigEntry, provider: str
) -> list[str]:
    """Return only verified image models from the current catalog."""
    rows = await async_fetch_catalog(
        hass, entry.data[CONF_BASE_URL], entry.data[CONF_API_KEY]
    )
    return filter_models(rows, provider)
