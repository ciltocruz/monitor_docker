"""A small in-memory stand-in for aiodocker, enough for helpers.DockerAPI."""

from __future__ import annotations

import asyncio
import itertools
from datetime import UTC, datetime, timedelta
from typing import Any

from aiodocker.exceptions import DockerError

from custom_components.monitor_docker import DOCKER_SCHEMA

_ids = itertools.count(1)


def make_config(**kwargs: Any) -> dict[str, Any]:
    """A validated host config like the one stored in a config entry."""
    return DOCKER_SCHEMA({"name": "Docker", "url": "unix:///fake.sock", **kwargs})


class FakeContainer:
    """One container, as aiodocker's DockerContainer."""

    def __init__(self, engine: FakeEngine, name: str, state: str = "running"):
        self.engine = engine
        self.name = name
        self.state = state
        self.id = f"{next(_ids):064x}"
        self._id = self.id
        self.cpu_total = 1_000_000
        self.memory = 100 * 1024**2
        self.show_calls = 0
        self.stats_calls = 0

    @property
    def _container(self) -> dict[str, Any]:
        return {"Id": self.id, "Names": [f"/{self.name}"], "State": self.state}

    def _check(self) -> None:
        if self.engine.containers.by_id.get(self.id) is not self:
            raise DockerError(404, f"No such container: {self.id}")

    async def show(self) -> dict[str, Any]:
        self._check()
        self.show_calls += 1
        return {
            "Id": self.id,
            "Image": "sha256:abc",
            "Created": "2026-10-01T10:00:00Z",
            "Config": {"Image": "busybox:latest"},
            "HostConfig": {"NetworkMode": "bridge"},
            "State": {
                "Status": self.state,
                "StartedAt": "2026-10-01T10:00:00Z",
                "FinishedAt": "2026-10-01T09:00:00Z",
                "ExitCode": 0,
            },
        }

    def sample(self) -> dict[str, Any]:
        """A raw stats sample; CPU counters grow with the engine clock."""
        self.engine.clock += 1
        read = datetime(2026, 10, 19, 10, 0, tzinfo=UTC) + timedelta(
            seconds=self.engine.clock
        )
        return {
            "read": read.isoformat(),
            "cpu_stats": {
                "cpu_usage": {"total_usage": self.cpu_total},
                "system_cpu_usage": self.engine.system_cpu,
                "online_cpus": 4,
            },
            "memory_stats": {
                "usage": self.memory,
                "limit": 4096 * 1024**2,
                "stats": {"inactive_file": 0},
            },
            "networks": {"eth0": {"rx_bytes": 1000, "tx_bytes": 2000}},
        }

    @property
    def docker(self) -> FakeEngine:
        return self.engine

    async def stats(self, *, stream: bool = True, timeout: Any = None):
        """The slow path: the daemon samples twice, one second apart."""
        self._check()
        self.stats_calls += 1
        self.engine.slow_stats_calls += 1
        return [self.sample()]


class FakeContainers:
    """aiodocker's DockerContainers."""

    def __init__(self, engine: FakeEngine):
        self.engine = engine
        self.by_id: dict[str, FakeContainer] = {}
        self.list_calls = 0

    def by_name(self, name: str) -> FakeContainer | None:
        for container in self.by_id.values():
            if container.name == name:
                return container
        return None

    async def list(self, **kwargs: Any) -> list[FakeContainer]:
        self.list_calls += 1
        return [
            c
            for c in self.by_id.values()
            if kwargs.get("all") or c.state in ("running", "paused")
        ]

    async def get(self, name: str, **kwargs: Any) -> FakeContainer:
        container = self.by_name(name) or self.by_id.get(name)
        if container is None:
            raise DockerError(404, f"No such container: {name}")
        return container


class FakeSystem:
    def __init__(self, engine: FakeEngine):
        self.engine = engine

    async def info(self) -> dict[str, Any]:
        running = [c for c in self.engine.containers.by_id.values()]
        return {
            "ServerVersion": "29.9.0",
            "Containers": len(running),
            "ContainersRunning": sum(c.state == "running" for c in running),
            "ContainersPaused": 0,
            "ContainersStopped": sum(c.state == "exited" for c in running),
            "Images": 3,
            "MemTotal": 4096 * 1024**2,
            "NCPU": 4,
        }


class FakeSubscriber:
    def __init__(self, queue: asyncio.Queue):
        self.queue = queue

    async def get(self):
        return await self.queue.get()


class FakeEvents:
    def __init__(self):
        self.queue: asyncio.Queue = asyncio.Queue()

    def subscribe(self) -> FakeSubscriber:
        return FakeSubscriber(self.queue)


class FakeEngine:
    """aiodocker.Docker with containers, events and system info."""

    def __init__(self, *names: str):
        self.containers = FakeContainers(self)
        self.system = FakeSystem(self)
        self.events = FakeEvents()
        self.system_cpu = 10_000_000_000
        self.clock = 0
        self.queries: list[tuple[str, dict]] = []
        self.slow_stats_calls = 0
        # HTTP status the daemon answers a one-shot request with (None = OK)
        self.one_shot_status: int | None = None
        self.api_version = "v1.52"
        for name in names:
            self.add(name)

    # aiodocker.Docker(...) is replaced by the engine itself
    def __call__(self, *args: Any, **kwargs: Any) -> FakeEngine:
        return self

    async def _query_json(
        self, path: str, method: str = "GET", *, params: Any = None, **kw: Any
    ) -> Any:
        """Only the stats endpoint of a container, like aiodocker's helper."""
        self.queries.append((path, dict(params or {})))
        _, cid, endpoint = path.split("/")
        assert endpoint == "stats"
        if self.one_shot_status is not None and "one-shot" in (params or {}):
            raise DockerError(self.one_shot_status, "unknown parameter one-shot")
        container = self.containers.by_id.get(cid)
        if container is None:
            raise DockerError(404, f"No such container: {cid}")
        container.stats_calls += 1
        return container.sample()

    async def version(self) -> dict[str, Any]:
        return {"Version": "29.9.0", "ApiVersion": "1.52"}

    async def close(self) -> None:
        return None

    def add(self, name: str, state: str = "running") -> FakeContainer:
        container = FakeContainer(self, name, state)
        self.containers.by_id[container.id] = container
        return container

    def remove(self, name: str) -> FakeContainer:
        container = self.containers.by_name(name)
        del self.containers.by_id[container.id]
        return container

    def tick(self, system: int, **cpu: int) -> None:
        """Advance the host CPU counter and the counters of some containers."""
        self.system_cpu += system
        for name, used in cpu.items():
            self.containers.by_name(name).cpu_total += used

    def event(self, action: str, name: str, **attributes: str) -> None:
        self.events.queue.put_nowait(
            {
                "Type": "container",
                "Action": action,
                "Actor": {"Attributes": {"name": name, **attributes}},
            }
        )
