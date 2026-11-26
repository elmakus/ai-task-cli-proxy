"""AI Task platform for CLIProxyAPI Image Generation."""

from __future__ import annotations

import base64
import binascii
import json
from io import BytesIO
from pathlib import Path
from typing import Any, override
from urllib.parse import quote

from aiohttp import ClientError, ClientResponseError, ClientTimeout
from PIL import Image

from homeassistant.components import ai_task, conversation
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import REQUEST_TIMEOUT_SECONDS

from .http import ResponseTooLarge, async_read_bounded
from .providers import (
    active_background,
    active_model,
    active_provider,
    async_list_models,
    model_protocol,
    provider_connection,
)

MAX_ATTACHMENT_COUNT = 10
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024
MAX_RESPONSE_BYTES = 40 * 1024 * 1024
MAX_IMAGE_BYTES = 25 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
IMAGE_FORMATS = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def _read_bounded(path: Path, remaining_bytes: int) -> bytes:
    """Read no more than the remaining attachment budget plus one byte."""
    with path.open("rb") as stream:
        return stream.read(remaining_bytes + 1)


def _serialize_payload(payload: dict[str, Any]) -> bytes:
    """Serialize and encode a request outside the event loop."""
    return json.dumps(payload).encode("utf-8")


def _encode_attachment(data: bytes) -> str:
    """Encode an attachment outside the event loop."""
    return base64.b64encode(data).decode("ascii")


def _validate_image(data: bytes, mime_type: str) -> tuple[int, int]:
    """Accept only bounded, decodable raster images matching their declared type."""
    try:
        with Image.open(BytesIO(data), formats=list(IMAGE_FORMATS)) as image:
            if IMAGE_FORMATS.get(image.format or "") != mime_type:
                raise HomeAssistantError(
                    "CLIProxyAPI image MIME type does not match its format"
                )
            width, height = image.size
            if width * height > MAX_IMAGE_PIXELS:
                raise HomeAssistantError(
                    "CLIProxyAPI generated image exceeds the pixel limit"
                )
            if getattr(image, "is_animated", False):
                raise HomeAssistantError("CLIProxyAPI returned an animated image")
            image.verify()
        with Image.open(BytesIO(data), formats=list(IMAGE_FORMATS)) as image:
            image.load()
    except (OSError, ValueError, SyntaxError, Image.DecompressionBombError) as err:
        raise HomeAssistantError("CLIProxyAPI returned invalid image data") from err
    return width, height


def _decode_image(encoded: str) -> bytes:
    """Decode a bounded inline image without accepting remote image URLs."""
    if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4 + 4:
        raise HomeAssistantError(
            "CLIProxyAPI generated image exceeds the 25 MB size limit"
        )
    try:
        decoded = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as err:
        raise HomeAssistantError("CLIProxyAPI returned invalid image data") from err
    if len(decoded) > MAX_IMAGE_BYTES:
        raise HomeAssistantError(
            "CLIProxyAPI generated image exceeds the 25 MB size limit"
        )
    return decoded


def _serialize_openai_payload(
    model: str, parts: list[dict[str, Any]], background: str
) -> bytes:
    """Encode a JSON Images API request; the installed proxy supports data URLs."""
    payload = {
        "model": model,
        "prompt": parts[0]["text"],
        "background": background,
        "output_format": "png",
        "response_format": "b64_json",
        "n": 1,
    }
    if len(parts) > 1:
        payload["images"] = [
            {
                "image_url": "data:"
                + part["inlineData"]["mimeType"]
                + ";base64,"
                + part["inlineData"]["data"]
            }
            for part in parts[1:]
        ]
    return _serialize_payload(payload)


def _parse_response(
    raw_response: bytearray, openai: bool = False
) -> tuple[bytes, str, str, int, int]:
    """Parse bounded provider JSON and validate its first returned image."""
    try:
        result: Any = json.loads(raw_response)
    except (ValueError, UnicodeDecodeError, RecursionError) as err:
        raise HomeAssistantError("CLIProxyAPI returned invalid JSON") from err
    if not isinstance(result, dict):
        raise HomeAssistantError("CLIProxyAPI returned an invalid response")

    if openai:
        images = result.get("data")
        if (
            not isinstance(images, list)
            or not images
            or not isinstance(images[0], dict)
        ):
            raise HomeAssistantError("CLIProxyAPI response did not include an image")
        encoded = images[0].get("b64_json")
        if not isinstance(encoded, str):
            raise HomeAssistantError(
                "CLIProxyAPI response did not include an inline image"
            )
        data = _decode_image(encoded)
        format_name = result.get("output_format") or "png"
        mime = (
            {"png": "image/png", "jpeg": "image/jpeg", "webp": "image/webp"}.get(
                format_name
            )
            if isinstance(format_name, str)
            else None
        )
        if mime is None:
            raise HomeAssistantError("CLIProxyAPI returned an unsupported image format")
        width, height = _validate_image(data, mime)
        revised = images[0].get("revised_prompt")
        return data, mime, revised if isinstance(revised, str) else "", width, height

    response_text: list[str] = []
    image_bytes: bytes | None = None
    mime_type: str | None = None

    candidates = result.get("candidates")
    if not isinstance(candidates, list):
        candidates = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        content = candidate.get("content")
        if not isinstance(content, dict):
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue
        for part in parts:
            if not isinstance(part, dict):
                continue
            inline_data = part.get("inlineData") or part.get("inline_data")
            if isinstance(inline_data, dict) and image_bytes is None:
                encoded = inline_data.get("data")
                candidate_mime = inline_data.get("mimeType") or inline_data.get(
                    "mime_type"
                )
                if (
                    isinstance(encoded, str)
                    and isinstance(candidate_mime, str)
                    and candidate_mime.startswith("image/")
                ):
                    image_bytes = _decode_image(encoded)
                    mime_type = candidate_mime
            elif isinstance(part.get("text"), str):
                response_text.append(part["text"])

    if image_bytes is None or mime_type is None:
        raise HomeAssistantError("CLIProxyAPI response did not include an image")
    width, height = _validate_image(image_bytes, mime_type)
    return image_bytes, mime_type, "".join(response_text), width, height


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
        """Generate an image through the selected CLIProxyAPI route."""
        provider = active_provider(self._entry)
        model = active_model(self._entry)
        base_url, headers = provider_connection(self._entry, provider)
        protocol = model_protocol(provider, model)
        if protocol is None:
            raise HomeAssistantError(
                "No verified image route for the selected provider and model"
            )
        openai = protocol == "openai"
        background = active_background(self._entry)
        if openai:
            action = "edits" if task.attachments else "generations"
            endpoint = f"{base_url}/v1/images/{action}"
        else:
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
                        _read_bounded,
                        Path(attachment.path),
                        MAX_ATTACHMENT_BYTES - total_attachment_bytes,
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
                            "data": await self.hass.async_add_executor_job(
                                _encode_attachment, attachment_data
                            ),
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

        # Serialize the potentially large JSON payload off the event loop.
        if openai:
            body = await self.hass.async_add_executor_job(
                _serialize_openai_payload, model, parts, background
            )
        else:
            body = await self.hass.async_add_executor_job(_serialize_payload, payload)
        try:
            async with async_get_clientsession(self.hass).post(
                endpoint,
                headers={**headers, "Content-Type": "application/json"},
                data=body,
                allow_redirects=False,
                timeout=ClientTimeout(total=REQUEST_TIMEOUT_SECONDS),
            ) as response:
                response.raise_for_status()
                if not 200 <= response.status < 300:
                    raise HomeAssistantError(
                        f"CLIProxyAPI returned HTTP {response.status} while generating an image"
                    )
                try:
                    raw_response = await async_read_bounded(
                        response.content, MAX_RESPONSE_BYTES
                    )
                except ResponseTooLarge as err:
                    raise HomeAssistantError(
                        "CLIProxyAPI response exceeds the 40 MB size limit"
                    ) from err
        except ClientResponseError as err:
            # A generation error can be upstream auth/quota, not the proxy key.
            # Check only the free catalog before starting reauthentication.
            if err.status in (401, 403):
                try:
                    await async_list_models(self.hass, self._entry, provider)
                except ConfigEntryAuthFailed:
                    self._entry.async_start_reauth(self.hass)
                except HomeAssistantError:
                    pass
            raise HomeAssistantError(
                f"CLIProxyAPI returned HTTP {err.status} while generating an image"
            ) from err
        except (ClientError, TimeoutError, ValueError) as err:
            raise HomeAssistantError(
                "Could not get an image response from CLIProxyAPI"
            ) from err

        (
            image_bytes,
            mime_type,
            response_text,
            width,
            height,
        ) = await self.hass.async_add_executor_job(
            _parse_response, raw_response, openai
        )

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
            width=width,
            height=height,
        )
