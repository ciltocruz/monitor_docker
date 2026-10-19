"""UI setup, reconfigure and error handling."""

from __future__ import annotations

from homeassistant.config_entries import SOURCE_RECONFIGURE, SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component

from custom_components.monitor_docker.const import API, DOMAIN

USER_INPUT = {
    "name": "Docker",
    "url": "",
    "scan_interval": 10,
    "certpath": "",
    "retry": 60,
}


async def _start(hass: HomeAssistant):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )


async def test_user_flow_creates_entry(hass: HomeAssistant, mock_docker_api) -> None:
    """Connection, containers and conditions steps create an entry."""
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["step_id"] == "containers"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"containers": ["esphome"]}
    )
    assert result["step_id"] == "conditions"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "monitored_docker_conditions": ["version"],
            "monitored_container_conditions": ["status", "1cpu_percentage"],
        },
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Docker"
    data = result["data"]
    assert data["containers"] == ["esphome"]
    assert data["monitored_conditions"] == ["version", "status", "1cpu_percentage"]
    assert data["version"] == "auto"
    # the flow's own test connection is closed again
    mock_docker_api.instances[0].destroy.assert_awaited()
    # the new entry works and keeps the 1.20 entity id scheme
    assert hass.states.get("sensor.docker_esphome_1cpu") is not None


async def test_user_flow_cannot_connect(hass: HomeAssistant, mock_docker_api) -> None:
    """A failing connection shows a translated error instead of the raw text."""
    mock_docker_api.fail_init = True
    result = await _start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], USER_INPUT
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_connection"}


async def test_two_flows_do_not_share_data(
    hass: HomeAssistant, mock_docker_api
) -> None:
    """Input of one flow must not leak into the defaults of another."""
    first = await _start(hass)
    await hass.config_entries.flow.async_configure(
        first["flow_id"], {**USER_INPUT, "name": "First", "retry": 5}
    )
    second = await _start(hass)
    defaults = {
        str(key): key.default() for key in second["data_schema"].schema
    }
    assert defaults["name"] == "Docker"
    assert defaults["retry"] == 60


async def test_reconfigure_preselects_and_updates(
    hass: HomeAssistant, mock_docker_api
) -> None:
    """Reconfigure starts from the entry's conditions and keeps the live API."""
    assert await async_setup_component(
        hass,
        DOMAIN,
        {DOMAIN: [{"name": "Docker", "monitored_conditions": ["version", "memory"]}]},
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    live_api = hass.data[DOMAIN]["Docker"][API]

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "conditions"}
    )
    assert result["step_id"] == "conditions"
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults["monitored_docker_conditions"] == ["version"]
    assert defaults["monitored_container_conditions"] == ["memory"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            "monitored_docker_conditions": ["version"],
            "monitored_container_conditions": ["memory", "status"],
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data["monitored_conditions"] == ["version", "memory", "status"]
    live_api.destroy.assert_awaited()  # reload unloads the old instance


async def test_reconfigure_lists_every_container(
    hass: HomeAssistant, mock_docker_api
) -> None:
    """Only selected containers are monitored, but all can be selected."""
    assert await async_setup_component(
        hass,
        DOMAIN,
        {DOMAIN: [{"name": "Docker", "containers": ["esphome", "not-created-yet"]}]},
    )
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    live_api = hass.data[DOMAIN]["Docker"][API]
    live_api.list_containers.return_value = ["esphome"]

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_RECONFIGURE, "entry_id": entry.entry_id}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "containers"}
    )
    assert result["step_id"] == "containers"
    selector = result["data_schema"].schema["containers"]
    assert selector.config["options"] == [
        "esphome",
        "homeassistant",
        "not-created-yet",
        "portainer",
    ]
