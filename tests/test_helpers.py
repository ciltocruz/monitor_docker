"""DockerAPI/DockerContainerAPI against an in-memory Docker engine."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.monitor_docker import helpers
from custom_components.monitor_docker.const import (
    DOCKER_STATS_1CPU_PERCENTAGE,
    DOCKER_STATS_MEMORY,
)
from custom_components.monitor_docker.helpers import DockerAPI, DockerContainerAPI

from .fake_docker import FakeEngine, make_config

_real_sleep = asyncio.sleep


async def _fast_sleep(seconds: float, *args: Any, **kwargs: Any) -> None:
    await _real_sleep(min(seconds, 0.01))


async def settle(rounds: int = 20) -> None:
    """Let the background loops of the API run a little."""
    for _ in range(rounds):
        await _real_sleep(0.01)


@pytest.fixture
def engine() -> FakeEngine:
    return FakeEngine("homeassistant", "esphome", "portainer", "grafana")


@pytest.fixture
async def make_api(hass: HomeAssistant, engine: FakeEngine):
    """Create DockerAPI instances on the fake engine; destroyed afterwards."""
    apis: list[DockerAPI] = []

    async def _make(**config: Any) -> DockerAPI:
        api = DockerAPI(hass, make_config(**config))
        await api.init()
        apis.append(api)
        return api

    with (
        patch.object(helpers.aiodocker, "Docker", engine),
        patch.object(helpers, "async_dispatcher_send"),
        patch.object(helpers.asyncio, "sleep", _fast_sleep),
    ):
        yield _make
        for api in apis:
            await api.destroy()
        await settle(3)


async def start_events(api: DockerAPI) -> None:
    """Run only the event loop of the API (no info loop)."""
    api._tasks["events"] = asyncio.create_task(api._run_docker_events())
    await settle(1)


# --- Fix 1: selection-aware monitoring ----------------------------------------


async def test_only_selected_containers_are_monitored(
    make_api, engine: FakeEngine
) -> None:
    """Without host totals only the selected containers are polled."""
    api = await make_api(containers=["portainer"], monitored_conditions=["status"])
    await api.run()
    await settle()

    assert list(api.list_containers()) == ["portainer"]
    assert api.list_all_containers() == [
        "esphome",
        "grafana",
        "homeassistant",
        "portainer",
    ]
    assert engine.containers.by_name("portainer").show_calls >= 1
    for name in ("homeassistant", "esphome", "grafana"):
        container = engine.containers.by_name(name)
        assert container.show_calls == 0, name
        assert container.stats_calls == 0, name
    # no host totals configured: no extra list/stats calls for the others
    assert api._aggregate is False


async def test_exclude_list_is_respected(make_api) -> None:
    """containers_exclude drops containers from an 'all containers' setup."""
    api = await make_api(
        containers_exclude=["grafana"], monitored_conditions=["status"]
    )
    await api.run()
    await settle()

    assert sorted(api.list_containers()) == ["esphome", "homeassistant", "portainer"]


@pytest.mark.parametrize(
    ("conditions", "expected"),
    [
        (["version", "status", "memory"], False),
        (["containers_running", "containers_total"], False),
        (["containers_cpu_percentage"], True),
        (["containers_1cpu_percentage"], True),
        (["containers_memory"], True),
        (["containers_memory_percentage"], True),
    ],
)
def test_needs_all_containers(conditions: list[str], expected: bool) -> None:
    """Only the summed host sensors need stats of unselected containers."""
    config = make_config(monitored_conditions=conditions)
    assert helpers.needs_all_containers(config) is expected


def test_is_container_selected() -> None:
    """The one selection rule used by the API and every platform."""
    every = make_config()
    assert helpers.is_container_selected(every, "anything")
    some = make_config(containers=["a", "b"], containers_exclude=["b"])
    assert helpers.is_container_selected(some, "a")
    assert not helpers.is_container_selected(some, "b")
    assert not helpers.is_container_selected(some, "c")


async def test_host_totals_include_unselected_containers(
    make_api, engine: FakeEngine
) -> None:
    """Host CPU/memory still sum every running container, without monitors."""
    engine.add("stopped", state="exited")
    api = await make_api(
        containers=["portainer"],
        monitored_conditions=["containers_1cpu_percentage", "containers_memory"],
    )
    assert api._aggregate is True

    # First sample: memory only, CPU needs two samples
    cpu, memory = await api._unmonitored_usage()
    assert cpu == 0.0
    assert memory == 300.0  # homeassistant, esphome, grafana (100 MB each)

    # 4 CPUs, host counter +4e9: homeassistant 1e9 -> 100 %, grafana 2e9 -> 200 %
    engine.tick(4_000_000_000, homeassistant=1_000_000_000, grafana=2_000_000_000)
    cpu, memory = await api._unmonitored_usage()
    assert cpu == 300.0
    assert memory == 300.0

    # the unselected containers were never inspected one by one
    for name in ("homeassistant", "esphome", "grafana"):
        assert engine.containers.by_name(name).show_calls == 0
    # the selected container is counted by its own monitor, not twice
    assert engine.containers.by_name("portainer").stats_calls == 0
    assert engine.containers.by_name("stopped").stats_calls == 0


async def test_host_totals_match_per_container_monitors(
    make_api, engine: FakeEngine
) -> None:
    """The light sampling gives the same numbers as full monitors did."""
    api = await make_api(
        containers=["portainer"],
        monitored_conditions=["containers_1cpu_percentage", "containers_memory"],
    )
    monitor = DockerContainerAPI(api._config, engine, "grafana")
    monitor._container = engine.containers.by_name("grafana")
    await monitor._run_container_info()

    await monitor._run_container_stats()
    await api._unmonitored_usage()
    engine.tick(7_000_000_000, grafana=1_234_567_890)
    await monitor._run_container_stats()
    cpu, _memory = await api._unmonitored_usage()

    stats = monitor.get_stats()
    assert cpu == stats["cpu_percentage"] == 70.55


async def test_info_loop_reports_host_totals(make_api, engine: FakeEngine) -> None:
    """The info loop adds the selected monitors and the light samples."""
    api = await make_api(
        containers=["portainer"],
        monitored_conditions=["containers_1cpu_percentage", "containers_memory"],
    )
    await api.run()
    await settle()
    engine.tick(
        4_000_000_000,
        homeassistant=1_000_000_000,
        portainer=1_000_000_000,
        grafana=2_000_000_000,
    )
    # the monitor of the selected container first, then the info loop (the
    # real loops run independently, the order only matters for the test)
    api.get_container("portainer").request_refresh()
    await settle()
    api._refresh_event.set()
    await settle()

    info = api.get_info()
    # (100 + 100 + 200) % over 4 CPUs
    assert info[DOCKER_STATS_1CPU_PERCENTAGE] == 100.0
    assert info[DOCKER_STATS_MEMORY] == 400.0


async def test_events_follow_the_selection(make_api, engine: FakeEngine) -> None:
    """Create/destroy/rename only start monitors for selected names."""
    api = await make_api(
        containers=["portainer", "md-new"], monitored_conditions=["status"]
    )
    await api.run()
    await settle()
    assert list(api.list_containers()) == ["portainer"]

    # an unselected container appears: no monitor, but known for the flow
    engine.add("other")
    engine.event("create", "other")
    await settle()
    assert "other" not in api.list_containers()
    assert "other" in api.list_all_containers()

    # a selected container that did not exist at start-up appears
    engine.add("md-new")
    engine.event("create", "md-new")
    await settle()
    assert "md-new" in api.list_containers()
    helpers.async_dispatcher_send.assert_called_with(
        api._hass, "monitor_docker_Docker_container_added", "md-new"
    )

    # destroy of an unselected container is ignored quietly
    engine.remove("other")
    engine.event("destroy", "other")
    await settle()
    assert "other" not in api.list_all_containers()

    # a rename of an unselected container to a selected name starts its monitor
    engine.remove("md-new")
    engine.event("destroy", "md-new")
    await settle()
    assert "md-new" not in api.list_containers()
    renamed = engine.add("md-new-tmp")
    engine.event("create", "md-new-tmp")
    await settle()
    renamed.name = "md-new"
    engine.event("rename", "md-new", oldName="/md-new-tmp")
    await settle()
    assert "md-new" in api.list_containers()
    assert "md-new-tmp" not in api.list_containers()


# --- Fix 2: stale monitors (#166, #188) ---------------------------------------


async def test_failed_start_leaves_no_stale_monitor(
    make_api, engine: FakeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    """A create event for a container that is gone again leaves nothing behind."""
    api = await make_api(
        containers=["portainer", "md-new"], monitored_conditions=["status"]
    )
    await api.run()
    await start_events(api)

    # created and removed again before the monitor could attach
    engine.event("create", "md-new")
    await settle()
    assert "md-new" not in api.list_containers()
    assert "Problem during start of monitoring" in caplog.text
    assert not [r for r in caplog.records if r.levelname == "ERROR"]

    # the next container of that name is monitored again (was: "already
    # monitored" forever)
    engine.add("md-new")
    engine.event("create", "md-new")
    await settle()
    assert "md-new" in api.list_containers()
    assert engine.containers.by_name("md-new").show_calls >= 1


async def test_recreated_container_is_reattached(make_api, engine: FakeEngine) -> None:
    """Recreated without events (#188): the monitor re-attaches by name."""
    api = await make_api(containers=["portainer"], monitored_conditions=["status"])
    await api.run()
    await settle()
    monitor = api.get_container("portainer")
    old = engine.containers.by_name("portainer")

    engine.remove("portainer")
    new = engine.add("portainer")
    monitor.request_refresh()
    await settle()

    assert api.get_container("portainer") is monitor
    assert monitor._container is new
    assert new.show_calls >= 1
    assert old.id != new.id


async def test_vanished_container_monitor_is_dropped(
    make_api, engine: FakeEngine
) -> None:
    """Gone without a destroy event: the monitor is removed after two tries."""
    api = await make_api(containers=["portainer"], monitored_conditions=["status"])
    await api.run()
    await start_events(api)
    await settle()
    monitor = api.get_container("portainer")
    removed = []
    monitor.register_callback(lambda **kw: removed.append(kw), "test")

    engine.remove("portainer")
    monitor.request_refresh()
    await settle(40)

    assert "portainer" not in api.list_containers()
    assert {"remove": True} in removed

    # and it comes back with the next create event
    engine.add("portainer")
    engine.event("create", "portainer")
    await settle()
    assert "portainer" in api.list_containers()


async def test_create_event_replaces_stale_monitor(
    make_api, engine: FakeEngine
) -> None:
    """A create event for a monitored name with a new id replaces the monitor."""
    api = await make_api(containers=["portainer"], monitored_conditions=["status"])
    await api.run()
    await start_events(api)
    await settle()
    stale = api.get_container("portainer")

    # a duplicate create event for the same container keeps the monitor
    engine.event("create", "portainer")
    await settle()
    assert api.get_container("portainer") is stale

    # recreated, the destroy event was missed
    engine.remove("portainer")
    new = engine.add("portainer")
    engine.event("create", "portainer")
    await settle()

    fresh = api.get_container("portainer")
    assert fresh is not stale
    assert fresh._container is new
    assert stale._task.cancelled() or stale._task.done()


# --- Fix 3: one-shot stats ----------------------------------------------------


async def test_stats_use_one_shot(make_api, engine: FakeEngine) -> None:
    """Monitors and host totals ask the daemon for a single sample."""
    api = await make_api(
        containers=["portainer"],
        monitored_conditions=["containers_1cpu_percentage", "1cpu_percentage"],
    )
    await api.run()
    await settle()

    assert engine.queries
    for path, params in engine.queries:
        assert path.endswith("/stats")
        assert params == {"stream": "0", "one-shot": "1"}
    assert engine.slow_stats_calls == 0
    assert engine.containers.by_name("portainer").stats_calls >= 1


async def test_stats_fall_back_without_one_shot(engine: FakeEngine) -> None:
    """A daemon that rejects one-shot gets the classic call, asked only once."""
    engine.one_shot_status = 400
    container = engine.containers.by_name("portainer")

    first = await helpers.fetch_stats(container)
    second = await helpers.fetch_stats(container)

    assert first["cpu_stats"] and second["cpu_stats"]
    assert len(engine.queries) == 1
    assert engine.slow_stats_calls == 2


async def test_stats_old_api_skips_one_shot(engine: FakeEngine) -> None:
    """Before Docker API 1.41 the parameter does not exist."""
    engine.api_version = "v1.40"
    raw = await helpers.fetch_stats(engine.containers.by_name("portainer"))

    assert raw["memory_stats"]
    assert engine.queries == []
    assert engine.slow_stats_calls == 1


async def test_stats_404_is_raised(engine: FakeEngine) -> None:
    """A vanished container is still reported to the poll loop."""
    container = engine.remove("portainer")
    with pytest.raises(helpers.aiodocker.exceptions.DockerError) as err:
        await helpers.fetch_stats(container)
    assert err.value.status == 404


# --- Fix 4: logging arguments -------------------------------------------------


def test_log_calls_have_matching_arguments() -> None:
    """Every _LOGGER call passes as many arguments as its format expects."""
    import ast
    import re
    from pathlib import Path

    package = Path(helpers.__file__).parent
    problems = []
    for path in sorted(package.glob("*.py")):
        for node in ast.walk(ast.parse(path.read_text())):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "_LOGGER"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            expected = len(re.findall(r"%[sdfr]", node.args[0].value.replace("%%", "")))
            if expected != len(node.args) - 1:
                problems.append(f"{path.name}:{node.lineno}")
    assert problems == []


async def test_info_timeout_is_logged(
    make_api, engine: FakeEngine, caplog: pytest.LogCaptureFixture
) -> None:
    """The TCP timeout message of the info loop formats without an error."""
    api = await make_api(monitored_conditions=["version"])

    async def timeout() -> None:
        raise TimeoutError

    engine.system.info = timeout
    api._tasks["info"] = asyncio.create_task(api._run_docker_info())
    await settle(3)

    messages = [r.getMessage() for r in caplog.records]
    assert "[Docker]: run_docker_info () TCP Timeout. Retry in 60 seconds" in messages


# --- Fix 5: rename event ------------------------------------------------------


async def test_rename_with_pending_create(make_api, engine: FakeEngine) -> None:
    """A rename of a name with a pending create must not end the event loop.

    The old code deleted the *new* name from the pending creates (KeyError),
    which ended the event task: no create/destroy/rename was handled anymore.
    """
    api = await make_api(
        containers=["portainer", "renamed"], monitored_conditions=["status"]
    )
    await api.run()
    await start_events(api)
    await settle()

    # a create of 'portainer' still waits in the working table
    api._event_create["portainer"] = 0
    engine.containers.by_name("portainer").name = "renamed"
    engine.event("rename", "renamed", oldName="/portainer")
    await settle()

    assert not api._tasks["events"].done()
    assert "portainer" not in api._event_create
    assert "portainer" not in api.list_containers()
    assert "renamed" in api.list_containers()

    # events are still handled afterwards
    engine.add("portainer")
    engine.event("create", "portainer")
    await settle()
    assert "portainer" in api.list_containers()


async def test_rename_of_monitored_container(make_api, engine: FakeEngine) -> None:
    """compose recreate: the old container gets a temporary name."""
    api = await make_api(containers=["portainer"], monitored_conditions=["status"])
    await api.run()
    await start_events(api)
    await settle()
    old = api.get_container("portainer")
    removed = []
    old.register_callback(lambda **kw: removed.append(kw), "test")

    engine.containers.by_name("portainer").name = "1a2b3c_portainer"
    engine.event("rename", "1a2b3c_portainer", oldName="/portainer")
    await settle()
    assert "portainer" not in api.list_containers()
    assert "1a2b3c_portainer" not in api.list_containers()  # not selected
    assert {"remove": True} in removed

    engine.add("portainer")
    engine.event("create", "portainer")
    engine.remove("1a2b3c_portainer")
    engine.event("destroy", "1a2b3c_portainer")
    await settle()
    assert api.get_container("portainer") is not old
    assert not api._tasks["events"].done()
