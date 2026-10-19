"""Shared fixtures."""

from __future__ import annotations

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.monitor_docker.const import CONTAINER_INFO_NETWORK_AVAILABLE

CONTAINERS = ["homeassistant", "esphome", "portainer"]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow loading custom_components in every test."""
    return


def _container(cname: str) -> MagicMock:
    capi = MagicMock(name=f"container_{cname}")
    capi._config = {"url": None}
    capi.get_info.return_value = {
        CONTAINER_INFO_NETWORK_AVAILABLE: True,
        "state": "running",
        "status": "Up 2 hours",
    }
    capi.get_stats.return_value = {}
    capi.start = AsyncMock()
    capi.stop = AsyncMock()
    capi.restart = AsyncMock()
    return capi


@pytest.fixture
def mock_docker_api() -> Generator[MagicMock]:
    """Replace DockerAPI with a fake engine that has three containers."""
    containers = {cname: _container(cname) for cname in CONTAINERS}

    def factory(hass, config):
        api = MagicMock(name="DockerAPI")
        api.init = AsyncMock(
            side_effect=OSError("connection refused") if factory.fail_init else None
        )
        api.run = AsyncMock()
        api.destroy = AsyncMock()
        api.list_containers.return_value = list(containers)
        api.list_all_containers.return_value = list(containers)
        api.get_container.side_effect = containers.get
        api.get_info.return_value = {}
        api.get_url.return_value = "unix:///var/run/docker.sock"
        factory.instances.append(api)
        return api

    factory.instances = []
    factory.fail_init = False
    with (
        patch("custom_components.monitor_docker.DockerAPI", side_effect=factory),
        patch(
            "custom_components.monitor_docker.config_flow.DockerAPI",
            side_effect=factory,
        ),
    ):
        yield factory
