"""Containers that appear after start-up get entities of the config entry."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.monitor_docker import helpers
from custom_components.monitor_docker.const import API, DOMAIN

from .fake_docker import FakeEngine, make_config

_real_sleep = asyncio.sleep


async def _fast_sleep(seconds: float, *args: Any, **kwargs: Any) -> None:
    await _real_sleep(min(seconds, 0.01))


async def settle(hass: HomeAssistant, rounds: int = 30) -> None:
    for _ in range(rounds):
        await _real_sleep(0.01)
    await hass.async_block_till_done()


@pytest.fixture
async def setup_entry(hass: HomeAssistant):
    engine = FakeEngine("homeassistant", "portainer")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Docker",
        unique_id="Docker",
        data=make_config(
            containers=["portainer", "md-test-dummy", "md-test-dummy2"],
            monitored_conditions=["version", "status", "1cpu_percentage", "memory"],
            buttonenabled=True,
        ),
    )
    entry.add_to_hass(hass)
    with (
        patch.object(helpers.aiodocker, "Docker", engine),
        patch.object(helpers.asyncio, "sleep", _fast_sleep),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await settle(hass)
        yield engine, entry
        assert await hass.config_entries.async_unload(entry.entry_id)
        await settle(hass, 3)


DUMMY_ENTITIES = {
    "sensor.docker_md_test_dummy_status": "docker_md_test_dummy_status",
    "sensor.docker_md_test_dummy_1cpu": "docker_md_test_dummy_1cpu",
    "sensor.docker_md_test_dummy_memory": "docker_md_test_dummy_memory",
    "switch.docker_md_test_dummy": "docker_md_test_dummy_switch",
    "button.docker_md_test_dummy_restart": "docker_md_test_dummy_restart",
}


def _assert_dummy_entities(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    registry = er.async_get(hass)
    devices = dr.async_get(hass)
    for entity_id, unique_id in DUMMY_ENTITIES.items():
        state = hass.states.get(entity_id)
        assert state is not None, entity_id
        assert state.state != "unavailable", entity_id
        reg = registry.async_get(entity_id)
        assert reg is not None, entity_id
        assert reg.unique_id.endswith(unique_id), (entity_id, reg.unique_id)
        assert reg.config_entry_id == entry.entry_id, entity_id
        device = devices.async_get(reg.device_id)
        assert device is not None, entity_id
        assert (DOMAIN, "Docker_container_md-test-dummy") in device.identifiers


def _assert_removed(hass: HomeAssistant, entity_id: str) -> None:
    """Removed entities keep a restored 'unavailable' state (HA behaviour)."""
    state = hass.states.get(entity_id)
    assert state is None or state.attributes.get("restored"), state


async def test_created_container_gets_entry_entities(
    hass: HomeAssistant, setup_entry
) -> None:
    """A selected container created after start-up gets entities of the entry."""
    engine, entry = setup_entry
    assert hass.states.get("switch.docker_portainer") is not None
    assert hass.states.get("switch.docker_md_test_dummy") is None

    engine.add("md-test-dummy")
    engine.event("create", "md-test-dummy")
    await settle(hass)

    _assert_dummy_entities(hass, entry)


async def test_recreated_container_keeps_entities(
    hass: HomeAssistant, setup_entry
) -> None:
    """Remove + recreate and rename keep entity_ids, unique_ids and the entry."""
    engine, entry = setup_entry
    engine.add("md-test-dummy")
    engine.event("create", "md-test-dummy")
    await settle(hass)
    registry = er.async_get(hass)
    before = {
        entity_id: registry.async_get(entity_id).id for entity_id in DUMMY_ENTITIES
    }

    # docker rm -f + docker run
    engine.remove("md-test-dummy")
    engine.event("destroy", "md-test-dummy")
    await settle(hass)
    _assert_removed(hass, "switch.docker_md_test_dummy")
    engine.add("md-test-dummy")
    engine.event("create", "md-test-dummy")
    await settle(hass)
    _assert_dummy_entities(hass, entry)

    # docker rename md-test-dummy md-test-dummy2 and back
    engine.containers.by_name("md-test-dummy").name = "md-test-dummy2"
    engine.event("rename", "md-test-dummy2", oldName="/md-test-dummy")
    await settle(hass)
    _assert_removed(hass, "switch.docker_md_test_dummy")
    assert hass.states.get("switch.docker_md_test_dummy2").state == "on"
    assert (
        registry.async_get("switch.docker_md_test_dummy2").config_entry_id
        == entry.entry_id
    )
    engine.containers.by_name("md-test-dummy2").name = "md-test-dummy"
    engine.event("rename", "md-test-dummy", oldName="/md-test-dummy2")
    await settle(hass)
    _assert_dummy_entities(hass, entry)

    # same registry entries as before: nothing was re-created under new ids
    after = {
        entity_id: registry.async_get(entity_id).id for entity_id in DUMMY_ENTITIES
    }
    assert after == before


async def test_unload_removes_late_entities(hass: HomeAssistant, setup_entry) -> None:
    """Entities of late containers belong to the entry and unload with it."""
    engine, entry = setup_entry
    engine.add("md-test-dummy")
    engine.event("create", "md-test-dummy")
    await settle(hass)

    assert await hass.config_entries.async_reload(entry.entry_id)
    await settle(hass)
    # after a reload the container is known at start-up, still the same ids
    _assert_dummy_entities(hass, entry)
    assert hass.data[DOMAIN]["Docker"][API].get_container("md-test-dummy")
