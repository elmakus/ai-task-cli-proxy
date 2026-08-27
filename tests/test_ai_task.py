"""Tests for CLIProxyAPI image attachment forwarding."""

from __future__ import annotations

import base64
import asyncio
from dataclasses import dataclass
from enum import IntFlag
from pathlib import Path
import sys
from types import SimpleNamespace
from types import ModuleType

import pytest


class _Feature(IntFlag):
    GENERATE_IMAGE = 1
    SUPPORT_ATTACHMENTS = 2


class _AITaskEntity:
    hass = None


@dataclass
class _GenImageTaskResult:
    image_data: bytes
    conversation_id: str
    mime_type: str
    model: str


@dataclass
class _AssistantContent:
    agent_id: str | None
    content: str


def _install_home_assistant_stubs() -> None:
    """Provide only the HA interfaces needed by this isolated adapter test."""
    modules = {
        "homeassistant": ModuleType("homeassistant"),
        "homeassistant.components": ModuleType("homeassistant.components"),
        "homeassistant.components.ai_task": ModuleType(
            "homeassistant.components.ai_task"
        ),
        "homeassistant.components.conversation": ModuleType(
            "homeassistant.components.conversation"
        ),
        "homeassistant.config_entries": ModuleType("homeassistant.config_entries"),
        "homeassistant.const": ModuleType("homeassistant.const"),
        "homeassistant.core": ModuleType("homeassistant.core"),
        "homeassistant.exceptions": ModuleType("homeassistant.exceptions"),
        "homeassistant.helpers": ModuleType("homeassistant.helpers"),
        "homeassistant.helpers.aiohttp_client": ModuleType(
            "homeassistant.helpers.aiohttp_client"
        ),
        "homeassistant.helpers.entity_platform": ModuleType(
            "homeassistant.helpers.entity_platform"
        ),
    }
    modules["homeassistant.components"].ai_task = modules[
        "homeassistant.components.ai_task"
    ]
    modules["homeassistant.components"].conversation = modules[
        "homeassistant.components.conversation"
    ]
    modules["homeassistant.components.ai_task"].AITaskEntity = _AITaskEntity
    modules["homeassistant.components.ai_task"].AITaskEntityFeature = _Feature
    modules["homeassistant.components.ai_task"].GenImageTaskResult = _GenImageTaskResult
    modules["homeassistant.components.conversation"].AssistantContent = _AssistantContent
    modules["homeassistant.components.conversation"].ChatLog = object
    modules["homeassistant.config_entries"].ConfigEntry = object
    modules["homeassistant.const"].CONF_API_KEY = "api_key"
    modules["homeassistant.const"].Platform = SimpleNamespace(AI_TASK="ai_task")
    modules["homeassistant.core"].HomeAssistant = object
    modules["homeassistant.exceptions"].HomeAssistantError = RuntimeError
    modules["homeassistant.helpers.aiohttp_client"].async_get_clientsession = (
        lambda _hass: None
    )
    modules[
        "homeassistant.helpers.entity_platform"
    ].AddConfigEntryEntitiesCallback = object
    sys.modules.update(modules)


_install_home_assistant_stubs()

from custom_components.ai_task_cli_proxy import ai_task as integration


class _FakeResponse:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def raise_for_status(self) -> None:
        return None

    async def json(self, *, content_type=None):
        del content_type
        return {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "done"},
                            {
                                "inlineData": {
                                    "mimeType": "image/png",
                                    "data": base64.b64encode(b"generated").decode(),
                                }
                            },
                        ]
                    }
                }
            ]
        }


class _FakeSession:
    def __init__(self) -> None:
        self.payload = None

    def post(self, _endpoint, *, headers, json, timeout):
        del headers, timeout
        self.payload = json
        return _FakeResponse()


class _FakeHass:
    async def async_add_executor_job(self, target, *args):
        return target(*args)


class _FakeChatLog:
    conversation_id = "conversation-id"

    def __init__(self) -> None:
        self.content = []

    def async_add_assistant_content_without_tools(self, content) -> None:
        self.content.append(content)


def test_image_attachment_is_sent_as_gemini_inline_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"reference-board")
    session = _FakeSession()
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)

    entry = SimpleNamespace(
        entry_id="entry-id",
        data={
            "base_url": "http://cliproxy.invalid",
            "model": "gemini-test",
            "api_key": "not-a-real-key",
        },
    )
    entity = integration.CLIProxyAPIImageTaskEntity(entry)
    entity.hass = _FakeHass()
    entity.entity_id = "ai_task.cli_proxy"
    task = SimpleNamespace(
        instructions="Create an icon",
        attachments=[
            SimpleNamespace(path=reference, mime_type="image/png")
        ],
    )

    result = asyncio.run(entity._async_generate_image(task, _FakeChatLog()))

    assert result.image_data == b"generated"
    assert session.payload["contents"][0]["parts"] == [
        {"text": "Create an icon"},
        {
            "inlineData": {
                "mimeType": "image/png",
                "data": base64.b64encode(b"reference-board").decode(),
            }
        },
    ]
