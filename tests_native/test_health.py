"""Native health transitions, recovery, and repairs with no paid requests."""

import logging
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import entity_registry as er, issue_registry as ir
from homeassistant.setup import async_setup_component
from pytest_homeassistant.common import MockConfigEntry

from custom_components import ai_task_cli_proxy as integration
from custom_components.ai_task_cli_proxy import providers

DOMAIN = "ai_task_cli_proxy"
CATALOG = [{"id": "gpt-image-2.6-flare", "owned_by": "openai"}]


async def test_health_recovery_and_model_repair(hass, enable_custom_integrations, caplog):
    """Lose only verified catalog readiness, log once, recover and clear the repair."""
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"base_url": "http://proxy.invalid", "api_key": "synthetic"},
    )
    entry.add_to_hass(hass)
    fetch = AsyncMock(return_value=CATALOG)
    with patch.object(providers, "async_fetch_catalog", fetch):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = entry.runtime_data
        entity_id = er.async_get(hass).async_get_entity_id("ai_task", DOMAIN, entry.entry_id)
        issue_id = f"missing_model_{entry.entry_id}"
        registry = ir.async_get(hass)
        assert hass.states.get(entity_id).state != "unavailable"
        caplog.set_level(logging.INFO, logger=f"custom_components.{DOMAIN}.coordinator")
        fetch.side_effect = HomeAssistantError("synthetic connection error")
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        assert hass.states.get(entity_id).state == "unavailable"
        assert sum("Error fetching" in rec.message for rec in caplog.records) == 1
        assert registry.async_get_issue(DOMAIN, issue_id) is None
        fetch.side_effect = None
        await coordinator.async_refresh()
        assert hass.states.get(entity_id).state != "unavailable"
        assert sum("data recovered" in rec.message for rec in caplog.records) == 1
        fetch.return_value = []
        await coordinator.async_refresh()
        await coordinator.async_refresh()
        assert hass.states.get(entity_id).state == "unavailable"
        assert registry.async_get_issue(DOMAIN, issue_id) is not None
        fetch.return_value = CATALOG
        await coordinator.async_refresh()
        assert hass.states.get(entity_id).state != "unavailable"
        assert registry.async_get_issue(DOMAIN, issue_id) is None
        fetch.side_effect = ConfigEntryAuthFailed("synthetic auth error")
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == "unavailable"
        assert any(flow["context"]["source"] == "reauth" for flow in hass.config_entries.flow.async_progress())
        with patch.object(hass.config_entries, "async_unload_platforms", AsyncMock(return_value=False)):
            assert not await integration.async_unload_entry(hass, entry)
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_remove_entry_with_failed_setup(hass, enable_custom_integrations):
    """A missing route creates an actionable repair, removed with its entry."""
    assert await async_setup_component(hass, "homeassistant", {})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"base_url": "http://proxy.invalid", "api_key": "synthetic"},
    )
    entry.add_to_hass(hass)
    with patch.object(providers, "async_fetch_catalog", AsyncMock(return_value=[])):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.SETUP_RETRY
        registry = ir.async_get(hass)
        issue_id = f"missing_model_{entry.entry_id}"
        assert registry.async_get_issue(DOMAIN, issue_id) is not None
        await hass.config_entries.async_remove(entry.entry_id)
        assert registry.async_get_issue(DOMAIN, issue_id) is None
