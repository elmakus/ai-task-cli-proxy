"""Native HA contract tests with a synthetic provider response."""

import asyncio
import base64
import json
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
from aiohttp import StreamReader
from aiohttp.base_protocol import BaseProtocol
from PIL import Image
from homeassistant.components import ai_task, conversation
from pytest_homeassistant.common import MockConfigEntry
import pytest
from custom_components.ai_task_cli_proxy import ai_task as adapter


@pytest.fixture(autouse=True)
def synthetic_catalog(monkeypatch):
    """Prevent real catalog traffic during native integration setup."""
    from custom_components.ai_task_cli_proxy import providers, config_flow

    async def fetch(*_args):
        return [
            {"id": "gpt-image-2.6-flare", "owned_by": "openai"},
            {"id": "gpt-image-2", "owned_by": "openai"},
            {"id": "antigravity/gemini-3.1-flash-image", "owned_by": "antigravity"},
        ]

    monkeypatch.setattr(providers, "async_fetch_catalog", fetch)
    monkeypatch.setattr(config_flow, "async_fetch_catalog", fetch)


class Response:
    status = 200

    def __init__(self, payload, split):
        protocol = BaseProtocol(asyncio.get_running_loop())
        protocol.transport = SimpleNamespace()
        self.content = StreamReader(protocol, 65536)
        if split:
            self.content.feed_data(payload[:12])
            asyncio.get_running_loop().call_soon(self.finish, payload[12:])
        else:
            self.finish(payload)

    def finish(self, data):
        self.content.feed_data(data)
        self.content.feed_eof()

    def raise_for_status(self):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None


@pytest.mark.parametrize("split", [False, True])
async def test_actual_ha_result(hass, split):
    f = BytesIO()
    Image.new("RGB", (2, 2), "white").save(f, format="PNG")
    image = f.getvalue()
    raw = json.dumps(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": base64.b64encode(image).decode(),
                                }
                            }
                        ]
                    }
                }
            ]
        }
    ).encode()
    entry = MockConfigEntry(
        domain="ai_task_cli_proxy",
        data={
            "base_url": "http://unused.invalid",
            "model": "synthetic",
            "api_key": "fake",
        },
        options={
            "provider": "antigravity",
            "model": "antigravity/gemini-3.1-flash-image",
        },
    )
    entity = adapter.CLIProxyAPIImageTaskEntity(entry)
    entity.hass = hass
    entity.entity_id = "ai_task.audit_fixture"
    assistant_contents = []
    chat = SimpleNamespace(
        conversation_id="audit",
        async_add_assistant_content_without_tools=assistant_contents.append,
    )
    session = SimpleNamespace(post=lambda *args, **kwargs: Response(raw, split))
    with patch.object(adapter, "async_get_clientsession", return_value=session):
        result = await entity._async_generate_image(
            ai_task.GenImageTask(name="audit", instructions="synthetic"), chat
        )
    assert isinstance(result, ai_task.GenImageTaskResult)
    assert result.image_data == image
    assert isinstance(assistant_contents[0], conversation.AssistantContent)


async def test_native_service(hass, enable_custom_integrations, tmp_path):
    """Exercise actual integration setup, entity registration and AI Task service."""
    from homeassistant.setup import async_setup_component
    from homeassistant.helpers import entity_registry as er

    hass.config.media_dirs = {"local": str(tmp_path)}
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain="ai_task_cli_proxy",
        title="CLIProxyAPI Images",
        data={
            "base_url": "http://unused.invalid",
            "model": "synthetic",
            "api_key": "fake",
        },
        options={
            "provider": "antigravity",
            "model": "antigravity/gemini-3.1-flash-image",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "ai_task", "ai_task_cli_proxy", entry.entry_id
    )
    assert entity_id is not None
    assert hass.services.has_service("ai_task", "generate_image")
    image = BytesIO()
    Image.new("RGB", (2, 3), "white").save(image, format="PNG")
    raw = json.dumps(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": base64.b64encode(image.getvalue()).decode(),
                                }
                            }
                        ]
                    }
                }
            ]
        }
    ).encode()
    session = SimpleNamespace(post=lambda *args, **kwargs: Response(raw, True))
    with patch.object(adapter, "async_get_clientsession", return_value=session):
        result = await hass.services.async_call(
            "ai_task",
            "generate_image",
            {"entity_id": entity_id, "task_name": "audit", "instructions": "synthetic"},
            blocking=True,
            return_response=True,
        )
    assert result is not None
    assert result["mime_type"] == "image/png"
    assert result["width"] == 2
    assert result["height"] == 3
    assert result["model"] == "antigravity/gemini-3.1-flash-image"
    assert result["media_source_id"].startswith("media-source://ai_task/")
    assert result["url"]
    assert any(
        path.read_bytes() == image.getvalue() for path in tmp_path.rglob("*.png")
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_native_options_flow(hass, enable_custom_integrations, tmp_path):
    """Refresh and select a provider in the real HA options flow."""
    from homeassistant.setup import async_setup_component
    from homeassistant.helpers import entity_registry as er
    from custom_components.ai_task_cli_proxy import config_flow, providers

    hass.config.media_dirs = {"local": str(tmp_path)}
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain="ai_task_cli_proxy",
        title="CLIProxyAPI Images",
        data={
            "base_url": "http://unused.invalid",
            "model": "gemini-3.1-flash-image",
            "api_key": "fake",
        },
        options={
            "provider": "antigravity",
            "model": "antigravity/gemini-3.1-flash-image",
            "background": "transparent",
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "ai_task", "ai_task_cli_proxy", entry.entry_id
    )

    async def catalog(_hass, _entry, provider, **kwargs):
        return (
            ["gpt-image-2"]
            if provider == providers.CHATGPT
            else ["antigravity/gemini-3.1-flash-image"]
        )

    with patch.object(config_flow, "async_list_models", side_effect=catalog):
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["type"] == "menu"
        flow_id = result["flow_id"]
        result = await hass.config_entries.options.async_configure(
            flow_id, {"next_step_id": "select_provider"}
        )
        assert result["step_id"] == "select_provider"
        result = await hass.config_entries.options.async_configure(
            flow_id, {"provider": providers.CHATGPT}
        )
        assert result["step_id"] == "model"
        result = await hass.config_entries.options.async_configure(
            flow_id, {"model": "gpt-image-2"}
        )
        assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert entry.options["provider"] == providers.CHATGPT
    assert entry.options["model"] == "gpt-image-2"
    assert entry.options["background"] == "transparent"
    assert (
        entry.options["saved_models"][providers.ANTIGRAVITY]
        == "antigravity/gemini-3.1-flash-image"
    )
    assert (
        registry.async_get_entity_id("ai_task", "ai_task_cli_proxy", entry.entry_id)
        == entity_id
    )
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_native_default_transparent_png(
    hass, enable_custom_integrations, tmp_path
):
    """An upgraded legacy entry uses GPT Image and returns unmodified RGBA bytes."""
    from homeassistant.setup import async_setup_component
    from homeassistant.helpers import entity_registry as er

    hass.config.media_dirs = {"local": str(tmp_path)}
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain="ai_task_cli_proxy",
        title="CLIProxyAPI Images",
        data={
            "base_url": "http://unused.invalid",
            "model": "gemini-3.1-flash-image",
            "api_key": "fake",
        },
        options={"model": "gemini-3.1-flash-image"},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "ai_task", "ai_task_cli_proxy", entry.entry_id
    )
    assert entity_id is not None
    image = BytesIO()
    rgba = Image.new("RGBA", (3, 3), (0, 0, 0, 0))
    rgba.putpixel((1, 1), (255, 100, 20, 255))
    rgba.save(image, format="PNG")
    raw = json.dumps(
        {
            "data": [{"b64_json": base64.b64encode(image.getvalue()).decode()}],
            "output_format": "png",
        }
    ).encode()
    calls = []

    def post(url, **kwargs):
        calls.append((url, json.loads(kwargs["data"])))
        return Response(raw, True)

    with patch.object(
        adapter, "async_get_clientsession", return_value=SimpleNamespace(post=post)
    ):
        result = await hass.services.async_call(
            "ai_task",
            "generate_image",
            {"entity_id": entity_id, "task_name": "audit", "instructions": "synthetic"},
            blocking=True,
            return_response=True,
        )
    assert result["model"] == "gpt-image-2.6-flare"
    assert len(calls) == 1
    assert calls[0][0] == "http://unused.invalid/v1/images/generations"
    assert calls[0][1]["background"] == "transparent"
    assert calls[0][1]["output_format"] == "png"
    assert calls[0][1]["prompt"] == "synthetic"
    assert any(
        path.read_bytes() == image.getvalue() for path in tmp_path.rglob("*.png")
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
