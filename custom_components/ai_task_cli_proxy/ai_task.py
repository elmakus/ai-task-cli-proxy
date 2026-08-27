"""AI Task platform for CLIProxyAPI Image Generation."""

from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Any, override
from urllib.parse import quote

from aiohttp import ClientError, ClientResponseError, ClientTimeout

from homeassistant.components import ai_task, conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_BASE_URL, CONF_MODEL, REQUEST_TIMEOUT_SECONDS

MAX_ATTACHMENT_COUNT = 10
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the CLIProxyAPI AI Task entity."""
    async_add_entities([CLIProxyAPIImageTaskEntity(entry)])


class CLIProxyAPIImageTaskEntity(ai_task.AITaskEntity):
    """Generate images through a local CLIProxyAPI endpoint."""

    _attr_has_entity_name = True
    _attr_name = "Image generation"
    _attr_supported_features = (
        ai_task.AITaskEntityFeature.GENERATE_IMAGE
        | ai_task.AITaskEntityFeature.SUPPORT_ATTACHMENTS
    )

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialize the entity."""
        self._entry = entry
        self._attr_unique_id = entry.entry_id

    @override
    async def _async_generate_image(
        self,
        task: ai_task.GenImageTask,
        chat_log: conversation.ChatLog,
    ) -> ai_task.GenImageTaskResult:
        """Generate an image with CLIProxyAPI's Gemini-compatible endpoint."""
        base_url = self._entry.data[CONF_BASE_URL].rstrip("/")
        model = self._entry.data[CONF_MODEL]
        api_key = self._entry.data[CONF_API_KEY]
        endpoint = (
            f"{base_url}/v1beta/models/{quote(model, safe='')}:generateContent"
        )
        parts: list[dict[str, Any]] = [{"text": task.instructions}]
        if task.attachments:
            if len(task.attachments) > MAX_ATTACHMENT_COUNT:
                raise HomeAssistantError(
                    f"At most {MAX_ATTACHMENT_COUNT} image attachments are supported"
                )

            total_attachment_bytes = 0
            for attachment in task.attachments:
                if not attachment.mime_type.startswith("image/"):
                    raise HomeAssistantError(
                        "CLIProxyAPI image generation only supports image attachments"
                    )
                try:
                    attachment_data = await self.hass.async_add_executor_job(
                        Path(attachment.path).read_bytes
                    )
                except OSError as err:
                    raise HomeAssistantError(
                        "Could not read an image attachment"
                    ) from err

                total_attachment_bytes += len(attachment_data)
                if total_attachment_bytes > MAX_ATTACHMENT_BYTES:
                    raise HomeAssistantError(
                        "Image attachments exceed the 20 MB combined limit"
                    )
                parts.append(
                    {
                        "inlineData": {
                            "mimeType": attachment.mime_type,
                            "data": base64.b64encode(attachment_data).decode("ascii"),
                        }
                    }
                )

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": parts,
                }
            ],
            "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
        }

        try:
            async with async_get_clientsession(self.hass).post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
                timeout=ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            ) as response:
                response.raise_for_status()
                result: dict[str, Any] = await response.json(content_type=None)
        except ClientResponseError as err:
            raise HomeAssistantError(
                f"CLIProxyAPI returned HTTP {err.status} while generating an image"
            ) from err
        except (ClientError, TimeoutError, ValueError) as err:
            raise HomeAssistantError(
                "Could not get an image response from CLIProxyAPI"
            ) from err

        response_text = ""
        image_bytes: bytes | None = None
        mime_type: str | None = None
        candidates = result.get("candidates") or []
        if candidates:
            parts = (candidates[0].get("content") or {}).get("parts") or []
            for part in parts:
                inline_data = part.get("inlineData") or part.get("inline_data")
                if inline_data and image_bytes is None:
                    encoded = inline_data.get("data")
                    candidate_mime = inline_data.get("mimeType") or inline_data.get(
                        "mime_type"
                    )
                    if encoded and candidate_mime and candidate_mime.startswith("image/"):
                        try:
                            image_bytes = base64.b64decode(encoded, validate=True)
                        except (binascii.Error, ValueError) as err:
                            raise HomeAssistantError(
                                "CLIProxyAPI returned invalid image data"
                            ) from err
                        mime_type = candidate_mime
                elif isinstance(part.get("text"), str):
                    response_text += part["text"]

        if image_bytes is None or mime_type is None:
            raise HomeAssistantError("CLIProxyAPI response did not include an image")

        chat_log.async_add_assistant_content_without_tools(
            conversation.AssistantContent(
                agent_id=self.entity_id,
                content=response_text,
            )
        )
        return ai_task.GenImageTaskResult(
            image_data=image_bytes,
            conversation_id=chat_log.conversation_id,
            mime_type=mime_type,
            model=model,
        )
