"""Exercise native configuration, setup failures, and reauthentication."""

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry
from custom_components.ai_task_cli_proxy import config_flow, providers

DOMAIN = "ai_task_cli_proxy"
URL = "http://proxy.invalid"
CATALOG = [{"id": "gpt-image-2.6-flare", "owned_by": "openai"}]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    async def fetch(*args):
        return CATALOG

    monkeypatch.setattr(config_flow, "async_fetch_catalog", fetch)
    monkeypatch.setattr(providers, "async_fetch_catalog", fetch)


def entry(hass, url=URL):
    item = MockConfigEntry(
        domain=DOMAIN,
        title="CLIProxyAPI Images",
        unique_id=url,
        data={"base_url": url, "api_key": "synthetic", "model": "legacy"},
    )
    item.add_to_hass(hass)
    return item


async def test_user_create_and_duplicate(hass, enable_custom_integrations):
    assert await async_setup_component(hass, "homeassistant", {})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"base_url": URL + "/", "api_key": "synthetic"}
    )
    assert result["type"] == "create_entry"
    item = result["result"]
    assert item.data == {"base_url": URL, "api_key": "synthetic"}
    assert item.options == {"provider": "chatgpt", "model": "gpt-image-2.6-flare"}
    await hass.async_block_till_done()
    assert item.state is ConfigEntryState.LOADED
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"base_url": URL + "/", "api_key": "synthetic"}
    )
    assert result["reason"] == "already_configured"
    assert await hass.config_entries.async_unload(item.entry_id)


@pytest.mark.parametrize(
    ("url", "failure", "expected"),
    [
        ("file:///bad", None, "invalid_url"),
        (URL, ConfigEntryAuthFailed("synthetic"), "invalid_auth"),
        (URL, HomeAssistantError("synthetic"), "cannot_connect"),
        (URL, [], "model_unavailable"),
    ],
)
async def test_user_errors(hass, enable_custom_integrations, url, failure, expected):
    assert await async_setup_component(hass, "homeassistant", {})
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "user"}
    )
    fetch = AsyncMock(
        side_effect=failure if isinstance(failure, Exception) else None,
        return_value=failure,
    )
    with patch.object(config_flow, "async_fetch_catalog", fetch):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"base_url": url, "api_key": "synthetic"}
        )
    assert result["errors"]["base"] == expected
    assert not hass.config_entries.async_entries(DOMAIN)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"base_url": URL, "api_key": "synthetic"}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert result["result"].state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(result["result"].entry_id)


async def test_reconfigure_duplicate_and_new_url(hass, enable_custom_integrations):
    assert await async_setup_component(hass, "homeassistant", {})
    a = entry(hass)
    b = entry(hass, "http://other.invalid")
    assert await hass.config_entries.async_setup(a.entry_id)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": a.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"base_url": b.data["base_url"], "api_key": "synthetic"}
    )
    assert result["reason"] == "already_configured"
    assert a.data["base_url"] == URL
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reconfigure", "entry_id": a.entry_id}
    )
    fid = result["flow_id"]
    result = await hass.config_entries.flow.async_configure(
        fid, {"base_url": "http://new.invalid"}
    )
    assert result["errors"]["base"] == "key_required_for_new_url"
    with patch.object(
        config_flow,
        "async_fetch_catalog",
        AsyncMock(side_effect=HomeAssistantError("synthetic")),
    ):
        result = await hass.config_entries.flow.async_configure(
            fid, {"base_url": "http://new.invalid", "api_key": "new-synthetic"}
        )
    assert result["errors"]["base"] == "cannot_connect"
    assert a.data["base_url"] == URL
    result = await hass.config_entries.flow.async_configure(
        fid, {"base_url": "http://new.invalid", "api_key": "new-synthetic"}
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    assert a.unique_id == a.data["base_url"] == "http://new.invalid"
    assert a.data["api_key"] == "new-synthetic"
    assert a.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(a.entry_id)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (ConfigEntryAuthFailed("synthetic"), ConfigEntryState.SETUP_ERROR),
        (HomeAssistantError("synthetic"), ConfigEntryState.SETUP_RETRY),
        ([], ConfigEntryState.SETUP_RETRY),
    ],
)
async def test_setup_failure(hass, enable_custom_integrations, failure, expected):
    assert await async_setup_component(hass, "homeassistant", {})
    a = entry(hass)
    with patch.object(
        providers,
        "async_fetch_catalog",
        AsyncMock(
            side_effect=failure if isinstance(failure, Exception) else None,
            return_value=failure,
        ),
    ):
        assert not await hass.config_entries.async_setup(a.entry_id)
        await hass.async_block_till_done()
    assert a.state is expected
    if expected is ConfigEntryState.SETUP_ERROR:
        assert any(
            flow["context"]["source"] == "reauth"
            for flow in hass.config_entries.flow.async_progress()
        )
    else:
        assert await hass.config_entries.async_reload(a.entry_id)
        await hass.async_block_till_done()
        assert a.state is ConfigEntryState.LOADED
        assert await hass.config_entries.async_unload(a.entry_id)


async def test_reauth_keeps_identity(hass, enable_custom_integrations):
    assert await async_setup_component(hass, "homeassistant", {})
    a = entry(hass)
    assert await hass.config_entries.async_setup(a.entry_id)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": "reauth", "entry_id": a.entry_id}, data=a.data
    )
    assert result["step_id"] == "reauth_confirm"
    fid = result["flow_id"]
    with patch.object(
        config_flow,
        "async_fetch_catalog",
        AsyncMock(side_effect=ConfigEntryAuthFailed("synthetic")),
    ):
        result = await hass.config_entries.flow.async_configure(
            fid, {"api_key": "bad-synthetic"}
        )
    assert result["errors"]["base"] == "invalid_auth"
    result = await hass.config_entries.flow.async_configure(
        fid, {"api_key": "new-synthetic"}
    )
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    assert a.data["api_key"] == "new-synthetic"
    assert a.unique_id == URL
    assert a.state is ConfigEntryState.LOADED
    assert await hass.config_entries.async_unload(a.entry_id)
