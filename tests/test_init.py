"""Setup from configuration.yaml: import, entity ids and unload."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.monitor_docker.const import API, DOMAIN

# A real 1.20 YAML setup (anonymised), the entity ids below are the ones that
# install has today.
YAML_HOST = {
    "name": "Docker",
    "containers": ["homeassistant", "esphome", "portainer"],
    "rename": {"homeassistant": "Home Assistant", "esphome": "ESPHome"},
    "monitored_conditions": [
        "version",
        "status",
        "containers_1cpu_percentage",
        "1cpu_percentage",
        "memory",
        "containers_memory",
    ],
    "buttonenabled": True,
}

EXPECTED_ENTITY_IDS = [
    "sensor.docker_version",
    "sensor.docker_1cpu",
    "sensor.docker_memory",
    "sensor.docker_homeassistant_status",
    "sensor.docker_homeassistant_1cpu",
    "sensor.docker_esphome_1cpu",
    "sensor.docker_esphome_memory",
    "sensor.docker_portainer_1cpu",
    "switch.docker_homeassistant",
    "switch.docker_esphome",
    "switch.docker_portainer",
    "button.docker_portainer_restart",
]


async def _setup_yaml(hass: HomeAssistant, hosts: list[dict]) -> None:
    assert await async_setup_component(hass, DOMAIN, {DOMAIN: hosts})
    await hass.async_block_till_done()


async def test_yaml_import_keeps_entity_ids(
    hass: HomeAssistant, mock_docker_api
) -> None:
    """An imported YAML setup gets exactly the entity ids of 1.20."""
    await _setup_yaml(hass, [dict(YAML_HOST)])

    for entity_id in EXPECTED_ENTITY_IDS:
        assert hass.states.get(entity_id) is not None, entity_id

    assert hass.states.get("switch.docker_homeassistant").name == "Home Assistant"
    assert hass.states.get("sensor.docker_esphome_1cpu").name == "ESPHome 1CPU"

    registry = er.async_get(hass)
    assert (
        registry.async_get("switch.docker_portainer").unique_id
        == "docker_portainer_switch"
    )
    assert (
        registry.async_get("button.docker_portainer_restart").unique_id
        == "docker_portainer_restart"
    )


async def test_yaml_import_all_hosts_and_keys(
    hass: HomeAssistant, mock_docker_api
) -> None:
    """Every YAML host is imported with all of its options."""
    second = {
        "name": "NAS",
        "url": "tcp://192.0.2.10:2376",
        "prefix": "storage",
        "rename": {"^home.*": "ha"},
        "rename_entity": True,
        "containers_exclude": ["portainer"],
    }
    await _setup_yaml(hass, [dict(YAML_HOST), second])

    entries = {e.title: e for e in hass.config_entries.async_entries(DOMAIN)}
    assert set(entries) == {"Docker", "NAS"}
    assert entries["Docker"].data["rename"] == YAML_HOST["rename"]
    assert entries["NAS"].data["prefix"] == "storage"
    assert entries["NAS"].data["containers_exclude"] == ["portainer"]
    # prefix and rename_entity shape the entity ids exactly like 1.20
    assert hass.states.get("switch.storage_esphome") is not None
    assert hass.states.get("switch.storage_ha") is not None
    assert hass.states.get("button.storage_homeassistant_restart") is None  # buttons off
    # the excluded container gets no entities
    assert hass.states.get("switch.storage_portainer") is None


async def test_unload_cleans_up(hass: HomeAssistant, mock_docker_api) -> None:
    """Unloading removes the instance data and stops the Docker API."""
    await _setup_yaml(hass, [dict(YAML_HOST)])
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    api = hass.data[DOMAIN]["Docker"][API]

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.NOT_LOADED
    assert "Docker" not in hass.data[DOMAIN]
    api.destroy.assert_awaited_once()


async def test_reload_service_refreshes(hass: HomeAssistant, mock_docker_api) -> None:
    """monitor_docker.reload asks every instance for an immediate refresh."""
    await _setup_yaml(hass, [dict(YAML_HOST)])
    api = hass.data[DOMAIN]["Docker"][API]

    await hass.services.async_call(DOMAIN, "reload", blocking=True)

    api.request_refresh.assert_called_once()


async def test_repair_issue_removed_with_yaml(
    hass: HomeAssistant, mock_docker_api
) -> None:
    """The import issue exists after the import and goes away with the YAML."""
    from homeassistant.helpers import issue_registry as ir

    from custom_components.monitor_docker import async_setup

    await _setup_yaml(hass, [dict(YAML_HOST)])
    issues = ir.async_get(hass)
    assert issues.async_get_issue(DOMAIN, "remove_configuration_yaml_Docker")

    # next start without the YAML section
    await async_setup(hass, {})
    assert issues.async_get_issue(DOMAIN, "remove_configuration_yaml_Docker") is None
