"""Tests for CLIProxyAPI image attachment forwarding."""

from __future__ import annotations

import base64
import json
import asyncio
from dataclasses import dataclass
from enum import IntFlag
from pathlib import Path
from io import BytesIO
from PIL import Image
import sys
from types import SimpleNamespace
from types import ModuleType

import pytest


class _Feature(IntFlag):
    GENERATE_IMAGE = 1
    SUPPORT_ATTACHMENTS = 2


class _AITaskEntity:
    hass = None


class _ConfigFlow:
    hass = None

    def __init_subclass__(cls, domain=None, **kwargs):
        super().__init_subclass__(**kwargs)

    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}

    async def async_set_unique_id(self, unique_id):
        self._last_unique_id = unique_id

    def _abort_if_unique_id_configured(self):
        return None

    def _abort_if_unique_id_mismatch(self):
        return None

    def _get_reconfigure_entry(self):
        return self.config_entry

    def async_update_reload_and_abort(self, entry, *, data_updates):
        return {"type": "abort", "entry": entry, "data_updates": data_updates}


class _OptionsFlow:
    def async_show_form(self, **kwargs):
        return {"type": "form", **kwargs}

    def async_create_entry(self, **kwargs):
        return {"type": "create_entry", **kwargs}


@dataclass
class _GenImageTaskResult:
    image_data: bytes
    conversation_id: str
    mime_type: str
    model: str
    width: int | None = None
    height: int | None = None


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
        "homeassistant.data_entry_flow": ModuleType("homeassistant.data_entry_flow"),
        "homeassistant.helpers.selector": ModuleType("homeassistant.helpers.selector"),
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
    modules[
        "homeassistant.components.conversation"
    ].AssistantContent = _AssistantContent
    modules["homeassistant.components.conversation"].ChatLog = object
    modules["homeassistant.config_entries"].ConfigEntry = object
    modules["homeassistant.config_entries"].ConfigFlow = _ConfigFlow
    modules["homeassistant.config_entries"].OptionsFlow = _OptionsFlow
    modules["homeassistant.data_entry_flow"].FlowResult = dict
    modules["homeassistant.core"].callback = lambda fn: fn

    selectors = modules["homeassistant.helpers.selector"]
    selectors.TextSelector = lambda _config=None: str
    selectors.TextSelectorConfig = lambda **kwargs: SimpleNamespace(**kwargs)
    selectors.TextSelectorType = SimpleNamespace(URL="url", PASSWORD="password")
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

from custom_components.ai_task_cli_proxy import ai_task as integration  # noqa: E402


def _image_bytes(format="PNG"):
    buffer = BytesIO()
    Image.new("RGB", (2, 3), "white").save(buffer, format=format)
    return buffer.getvalue()


PNG = _image_bytes()


class _FakeResponse:
    status = 200
    _consumed = 0

    @property
    def content(self):
        return self

    async def read(self, limit=-1):
        body = json.dumps(await self.json(content_type=None)).encode("utf-8")
        chunk = body[self._consumed : self._consumed + limit]
        self._consumed += len(chunk)
        return chunk

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
                                    "data": base64.b64encode(PNG).decode(),
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

    def post(self, _endpoint, *, headers, data, timeout, allow_redirects):
        del headers, timeout
        assert allow_redirects is False
        self.payload = json.loads(data)
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
        options={},
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
        attachments=[SimpleNamespace(path=reference, mime_type="image/png")],
    )

    result = asyncio.run(entity._async_generate_image(task, _FakeChatLog()))

    assert result.image_data == PNG
    assert session.payload["contents"][0]["parts"] == [
        {"text": "Create an icon"},
        {
            "inlineData": {
                "mimeType": "image/png",
                "data": base64.b64encode(b"reference-board").decode(),
            }
        },
    ]


class _ControlledResponse(_FakeResponse):
    def __init__(self, body=None, *, error=None, json_error=None):
        self.body = body
        self.error = error
        self.json_error = json_error

    def raise_for_status(self):
        if self.error:
            raise self.error

    async def json(self, *, content_type=None):
        if self.json_error:
            raise self.json_error
        return self.body


class _ControlledSession:
    def __init__(self, response):
        self.response = response
        self.called = False
        self.payload = None
        self.endpoint = None
        self.headers = None
        self.timeout = None

    def post(self, endpoint, *, headers, data, timeout, allow_redirects):
        self.called = True
        self.payload = json.loads(data)
        assert allow_redirects is False
        self.endpoint = endpoint
        self.headers = headers
        self.timeout = timeout
        return self.response


def _entity_for_test():
    entry = SimpleNamespace(
        entry_id="entry-id",
        options={},
        data={
            "base_url": "http://cliproxy.invalid/",
            "model": "gemini/test",
            "api_key": "not-a-real-key",
        },
    )
    entity = integration.CLIProxyAPIImageTaskEntity(entry)
    entity.hass = _FakeHass()
    entity.entity_id = "ai_task.image_generation"
    return entity


def _task_for_test(*attachments):
    return SimpleNamespace(
        instructions="Draw a simple icon",
        attachments=list(attachments),
    )


def _generated_response(parts=None):
    if parts is None:
        parts = [
            {"text": "finished"},
            {
                "inlineData": {
                    "mimeType": "image/png",
                    "data": base64.b64encode(PNG).decode("ascii"),
                }
            },
        ]
    return {"candidates": [{"content": {"parts": parts}}]}


def test_endpoint_headers_and_result(monkeypatch: pytest.MonkeyPatch):
    session = _ControlledSession(_ControlledResponse(_generated_response()))
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
    log = _FakeChatLog()
    result = asyncio.run(
        _entity_for_test()._async_generate_image(_task_for_test(), log)
    )

    assert session.called is True
    assert session.endpoint == (
        "http://cliproxy.invalid/v1beta/models/gemini%2Ftest:generateContent"
    )
    assert session.headers == {
        "Authorization": "Bearer not-a-real-key",
        "Content-Type": "application/json",
    }
    assert (result.width, result.height) == (2, 3)
    assert session.payload["contents"][0]["parts"] == [
        {"text": "Draw a simple icon"},
    ]
    assert session.payload["generationConfig"]["responseModalities"] == [
        "TEXT",
        "IMAGE",
    ]
    assert result.image_data == PNG
    assert result.mime_type == "image/png"
    assert result.model == "gemini/test"
    assert result.conversation_id == "conversation-id"
    assert log.content[0].content == "finished"
    assert log.content[0].agent_id == "ai_task.image_generation"


@pytest.mark.parametrize(
    "parts",
    [
        [{"text": "text only"}],
        [],
        [{"inlineData": {"mimeType": "text/plain", "data": "dGVzdA=="}}],
    ],
)
def test_rejects_missing_image(monkeypatch: pytest.MonkeyPatch, parts):
    session = _ControlledSession(_ControlledResponse(_generated_response(parts)))
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
    with pytest.raises(RuntimeError, match="did not include an image"):
        asyncio.run(
            _entity_for_test()._async_generate_image(_task_for_test(), _FakeChatLog())
        )


def test_rejects_invalid_base64(monkeypatch: pytest.MonkeyPatch):
    response = _generated_response(
        [
            {
                "inlineData": {"mimeType": "image/png", "data": "NOT BASE64!!!"},
            }
        ]
    )
    session = _ControlledSession(_ControlledResponse(response))
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
    with pytest.raises(RuntimeError, match="invalid image data"):
        asyncio.run(
            _entity_for_test()._async_generate_image(_task_for_test(), _FakeChatLog())
        )


def test_rejects_non_image_attachment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    f = tmp_path / "reference.txt"
    f.write_bytes(b"not an image")
    session = _ControlledSession(_ControlledResponse(_generated_response()))
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
    with pytest.raises(RuntimeError, match="only supports image attachments"):
        asyncio.run(
            _entity_for_test()._async_generate_image(
                _task_for_test(SimpleNamespace(path=f, mime_type="text/plain")),
                _FakeChatLog(),
            )
        )
    assert session.called is False


def test_rejects_missing_attachment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    session = _ControlledSession(_ControlledResponse(_generated_response()))
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
    with pytest.raises(RuntimeError, match="Could not read an image attachment"):
        asyncio.run(
            _entity_for_test()._async_generate_image(
                _task_for_test(
                    SimpleNamespace(
                        path=tmp_path / "missing.png", mime_type="image/png"
                    )
                ),
                _FakeChatLog(),
            )
        )
    assert session.called is False


def test_rejects_more_than_ten_attachments(monkeypatch: pytest.MonkeyPatch):
    session = _ControlledSession(_ControlledResponse(_generated_response()))
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
    attachments = [
        SimpleNamespace(path="/unused.png", mime_type="image/png") for _ in range(11)
    ]
    with pytest.raises(RuntimeError, match="At most 10 image attachments"):
        asyncio.run(
            _entity_for_test()._async_generate_image(
                _task_for_test(*attachments), _FakeChatLog()
            )
        )
    assert session.called is False


def test_rejects_aggregate_attachment_size(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(integration, "MAX_ATTACHMENT_BYTES", 3)
    session = _ControlledSession(_ControlledResponse(_generated_response()))
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
    f1, f2 = tmp_path / "one.png", tmp_path / "two.png"
    f1.write_bytes(b"ab")
    f2.write_bytes(b"cd")
    attachments = [
        SimpleNamespace(path=path, mime_type="image/png") for path in (f1, f2)
    ]
    with pytest.raises(RuntimeError, match="combined limit"):
        asyncio.run(
            _entity_for_test()._async_generate_image(
                _task_for_test(*attachments), _FakeChatLog()
            )
        )
    assert session.called is False


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_http_error_is_reported(monkeypatch: pytest.MonkeyPatch, status: int):
    from aiohttp import ClientResponseError

    response = _ControlledResponse(error=ClientResponseError(None, (), status=status))
    session = _ControlledSession(response)
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        asyncio.run(
            _entity_for_test()._async_generate_image(_task_for_test(), _FakeChatLog())
        )


@pytest.mark.parametrize(
    "failure",
    [
        TimeoutError("timeout"),
        ValueError("malformed response"),
    ],
)
def test_network_or_json_error_is_reported(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
):
    session = _ControlledSession(_ControlledResponse(json_error=failure))
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
    with pytest.raises(RuntimeError, match="Could not get an image response"):
        asyncio.run(
            _entity_for_test()._async_generate_image(_task_for_test(), _FakeChatLog())
        )


def test_model_option_is_used_by_provider(monkeypatch: pytest.MonkeyPatch):
    session = _ControlledSession(_ControlledResponse(_generated_response()))
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
    entity = _entity_for_test()
    entity._entry.options = {"model": "new-image-model"}
    result = asyncio.run(entity._async_generate_image(_task_for_test(), _FakeChatLog()))
    assert result.model == "new-image-model"
    assert session.endpoint.endswith("/models/new-image-model:generateContent")


def test_rejects_oversized_generated_image(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(integration, "MAX_IMAGE_BYTES", 3)
    response = _generated_response(
        [
            {
                "inlineData": {
                    "mimeType": "image/png",
                    "data": base64.b64encode(b"four").decode(),
                }
            }
        ]
    )
    session = _ControlledSession(_ControlledResponse(response))
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
    with pytest.raises(RuntimeError, match="generated image exceeds"):
        asyncio.run(
            _entity_for_test()._async_generate_image(_task_for_test(), _FakeChatLog())
        )


def test_rejects_large_http_response(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(integration, "MAX_RESPONSE_BYTES", 8)
    session = _ControlledSession(_ControlledResponse(_generated_response()))
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
    with pytest.raises(RuntimeError, match="response exceeds"):
        asyncio.run(
            _entity_for_test()._async_generate_image(_task_for_test(), _FakeChatLog())
        )


def test_rejects_non_object_response(monkeypatch: pytest.MonkeyPatch):
    session = _ControlledSession(_ControlledResponse([]))
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
    with pytest.raises(RuntimeError, match="invalid response"):
        asyncio.run(
            _entity_for_test()._async_generate_image(_task_for_test(), _FakeChatLog())
        )


def test_rejects_invalid_json_body(monkeypatch: pytest.MonkeyPatch):
    response = _ControlledResponse(_generated_response())

    chunks = iter([b"{broken JSON", b""])

    async def read(_limit=-1):
        return next(chunks)

    response.read = read
    session = _ControlledSession(response)
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
    with pytest.raises(RuntimeError, match="invalid JSON"):
        asyncio.run(
            _entity_for_test()._async_generate_image(_task_for_test(), _FakeChatLog())
        )


def test_can_find_image_in_second_candidate(monkeypatch: pytest.MonkeyPatch):
    response = _generated_response()
    response["candidates"].insert(0, {"content": {"parts": [{"text": "thinking"}]}})
    session = _ControlledSession(_ControlledResponse(response))
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _hass: session)
    result = asyncio.run(
        _entity_for_test()._async_generate_image(_task_for_test(), _FakeChatLog())
    )
    assert result.image_data == PNG


def test_options_flow_keeps_existing_model(monkeypatch: pytest.MonkeyPatch):
    from custom_components.ai_task_cli_proxy.config_flow import CLIProxyAPIOptionsFlow

    flow = CLIProxyAPIOptionsFlow()
    flow.config_entry = SimpleNamespace(
        data={"model": "original-model"}, options={"model": "chosen-model"}
    )
    form = asyncio.run(flow.async_step_init())
    assert form["type"] == "form"
    assert form["step_id"] == "init"
    assert form["data_schema"]({})["model"] == "chosen-model"
    saved = asyncio.run(flow.async_step_init({"model": " new-model "}))
    assert saved == {"type": "create_entry", "data": {"model": "new-model"}}
    invalid = asyncio.run(flow.async_step_init({"model": "  "}))
    assert invalid["errors"]["model"] == "invalid_model"


def test_reconfigure_keeps_entry_id_and_old_key(monkeypatch: pytest.MonkeyPatch):
    from custom_components.ai_task_cli_proxy import config_flow

    flow = config_flow.CLIProxyAPIConfigFlow()
    entry = SimpleNamespace(
        unique_id="http://original.example:8317",
        data={"base_url": "http://original.example:8317", "api_key": "old-secret"},
    )
    flow.config_entry = entry

    async def check_connection(_hass, _base_url, _api_key):
        return None

    monkeypatch.setattr(config_flow, "_async_check_connection", check_connection)
    result = asyncio.run(
        flow.async_step_reconfigure(
            {
                "base_url": "http://new.example:8317/",
            }
        )
    )
    assert result["type"] == "abort"
    assert result["entry"] is entry
    assert result["data_updates"] == {
        "base_url": "http://new.example:8317",
        "api_key": "old-secret",
    }
    assert flow._last_unique_id == entry.unique_id


def test_reconfigure_rejects_invalid_url():
    from custom_components.ai_task_cli_proxy import config_flow

    flow = config_flow.CLIProxyAPIConfigFlow()
    flow.config_entry = SimpleNamespace(
        unique_id="entry",
        data={"base_url": "http://cliproxy.invalid", "api_key": "secret"},
    )
    invalid = asyncio.run(flow.async_step_reconfigure({"base_url": "file:///bad"}))
    assert invalid["errors"]["base"] == "invalid_url"


@pytest.mark.parametrize("fragments", [2, 7])
def test_fragmented_stream(monkeypatch, fragments):
    from aiohttp import StreamReader
    from aiohttp.base_protocol import BaseProtocol

    async def run():
        raw = json.dumps(_generated_response()).encode()
        protocol = BaseProtocol(asyncio.get_running_loop())
        protocol.transport = SimpleNamespace()
        reader = StreamReader(protocol, limit=65536)

        class Response(_ControlledResponse):
            @property
            def content(self):
                return reader

        response = Response()
        step = max(1, len(raw) // fragments)

        async def feed():
            for i in range(0, len(raw), step):
                reader.feed_data(raw[i : i + step])
                await asyncio.sleep(0)
            reader.feed_eof()

        producer = asyncio.create_task(feed())
        session = _ControlledSession(response)
        monkeypatch.setattr(integration, "async_get_clientsession", lambda _: session)
        try:
            result = await _entity_for_test()._async_generate_image(
                _task_for_test(), _FakeChatLog()
            )
            assert result.image_data == PNG
        finally:
            await producer

    asyncio.run(run())


@pytest.mark.parametrize("delta", [0, 1])
def test_cumulative_response_limit(monkeypatch, delta):
    raw = json.dumps(_generated_response()).encode()
    monkeypatch.setattr(integration, "MAX_RESPONSE_BYTES", len(raw) - delta)
    response = _ControlledResponse()
    chunks = iter([raw[:4], raw[4:10], raw[10:], b""])

    async def read(limit):
        return next(chunks)

    response.read = read
    session = _ControlledSession(response)
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _: session)
    action = _entity_for_test()._async_generate_image(_task_for_test(), _FakeChatLog())
    if delta:
        with pytest.raises(RuntimeError, match="response exceeds"):
            asyncio.run(action)
    else:
        assert asyncio.run(action).image_data == PNG


@pytest.mark.parametrize(
    ("data", "mime", "message"),
    [
        (b"", "image/png", "invalid image data"),
        (b"not an image", "image/png", "invalid image data"),
        (PNG[:24], "image/png", "invalid image data"),
        (PNG, "image/jpeg", "MIME type"),
        (b"<svg/>", "image/svg+xml", "invalid image data"),
    ],
)
def test_invalid_image_contents(monkeypatch, data, mime, message):
    response = _generated_response(
        [{"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}]
    )
    session = _ControlledSession(_ControlledResponse(response))
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _: session)
    with pytest.raises(RuntimeError, match=message):
        asyncio.run(
            _entity_for_test()._async_generate_image(_task_for_test(), _FakeChatLog())
        )


@pytest.mark.parametrize(
    ("format", "mime"),
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp")],
)
def test_supported_formats(format, mime):
    data = _image_bytes(format)
    assert integration._validate_image(data, mime) == (2, 3)


def test_pixel_limit(monkeypatch):
    monkeypatch.setattr(integration, "MAX_IMAGE_PIXELS", 5)
    with pytest.raises(RuntimeError, match="pixel limit"):
        integration._validate_image(PNG, "image/png")


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirect_rejected(monkeypatch, status):
    response = _ControlledResponse(_generated_response())
    response.status = status
    session = _ControlledSession(response)
    monkeypatch.setattr(integration, "async_get_clientsession", lambda _: session)
    with pytest.raises(RuntimeError, match=f"HTTP {status}"):
        asyncio.run(
            _entity_for_test()._async_generate_image(_task_for_test(), _FakeChatLog())
        )
    assert not response._consumed


@pytest.mark.parametrize(
    ("body", "status", "expected"),
    [
        ({"data": [{"id": "custom-alias"}]}, 200, None),
        ({"data": []}, 200, None),
        ({"data": [42]}, 200, "cannot_connect"),
        ({"data": [{"id": ""}]}, 200, "cannot_connect"),
        ({"unexpected": []}, 200, "cannot_connect"),
        ([], 200, "cannot_connect"),
        ({"data": []}, 307, "cannot_connect"),
    ],
)
def test_models_response(monkeypatch, body, status, expected):
    from custom_components.ai_task_cli_proxy import config_flow

    response = _ControlledResponse(body)
    response.status = status

    def get(endpoint, *, headers, timeout, allow_redirects):
        assert endpoint == "http://unused.invalid/v1/models"
        assert allow_redirects is False
        return response

    monkeypatch.setattr(
        config_flow, "async_get_clientsession", lambda _: SimpleNamespace(get=get)
    )
    assert (
        asyncio.run(
            config_flow._async_check_connection(None, "http://unused.invalid", "fake")
        )
        == expected
    )


def test_models_response_limit(monkeypatch):
    from custom_components.ai_task_cli_proxy import config_flow

    monkeypatch.setattr(config_flow, "MAX_MODELS_RESPONSE_BYTES", 5)
    response = _ControlledResponse({"data": [{"id": "model"}]})
    monkeypatch.setattr(
        config_flow,
        "async_get_clientsession",
        lambda _: SimpleNamespace(get=lambda *a, **kw: response),
    )
    assert (
        asyncio.run(
            config_flow._async_check_connection(None, "http://unused.invalid", "fake")
        )
        == "cannot_connect"
    )


def test_english_translations_match_strings():
    root = Path(__file__).resolve().parents[1] / "custom_components/ai_task_cli_proxy"
    assert json.loads((root / "strings.json").read_text()) == json.loads(
        (root / "translations/en.json").read_text()
    )
