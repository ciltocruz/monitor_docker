"""Deterministic regressions on real helper classes, without HA/Docker installs.

Run: python3 -m unittest discover -s tests/standalone -v
Only imports/framework globals are substituted; class/function bodies are compiled
unchanged from helpers.py. This complements, not replaces, the HA pytest suite.
"""
from __future__ import annotations

import ast
import asyncio
import concurrent.futures
from datetime import datetime, timezone
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
import weakref

ROOT = Path(__file__).resolve().parents[2]
LOGGER = logging.getLogger("monitor_docker.standalone")


class DockerError(Exception):
    def __init__(self, status, message="Docker failure"):
        self.status = status
        super().__init__(message)


def load_helpers():
    ns = {"asyncio": SimpleNamespace(**{n: getattr(asyncio, n) for n in dir(asyncio)}),
          "concurrent": concurrent, "datetime": datetime, "timezone": timezone,
          "weakref": weakref, "_LOGGER": LOGGER,
          "aiodocker": SimpleNamespace(exceptions=SimpleNamespace(DockerError=DockerError)),
          "parser": SimpleNamespace(parse=datetime.fromisoformat),
          "async_dispatcher_send": Mock()}
    constants = ast.parse((ROOT / "custom_components/monitor_docker/const.py").read_text())
    for node in constants.body:
        if isinstance(node, ast.Assign):
            try:
                exec(compile(ast.Module([node], []), "const.py", "exec"), ns)
            except (NameError, AttributeError):
                pass  # HA sensor descriptions are not used by these classes.
    for name in ("NAME", "SCAN_INTERVAL", "MONITORED_CONDITIONS", "URL"):
        ns[f"CONF_{name}"] = name.lower()
    tree = ast.parse((ROOT / "custom_components/monitor_docker/helpers.py").read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             or isinstance(n, ast.ClassDef) and n.name in ("DockerAPI", "DockerContainerAPI")]
    future = ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)
    exec(compile(ast.fix_missing_locations(ast.Module([future, *nodes], [])), "helpers.py", "exec"), ns)
    ns.update(VERSION="test", ONE_SHOT_MIN_API=(1, 41), _ONE_SHOT_UNSUPPORTED=weakref.WeakSet())
    return ns


class Container:
    def __init__(self, cid):
        self._id = cid
        self.show_calls = 0

    async def show(self):
        self.show_calls += 1
        raise DockerError(404)


class HelperCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.ns = load_helpers()
        self.tasks = []
        self.config = dict(name="test", scan_interval=3600, retry=3600, memorychange=100,
                           precision_cpu=2, precision_memory_mb=2, precision_memory_percentage=2,
                           precision_network_kb=2, precision_network_mb=2)
        def spawn(coro, name):
            task = asyncio.create_task(coro, name=name)
            self.tasks.append(task)
            return task
        self.hass = SimpleNamespace(async_create_background_task=spawn)
        self.api = self.ns["DockerAPI"](self.hass, self.config)
        self.docker = SimpleNamespace(containers=SimpleNamespace(get=AsyncMock(side_effect=DockerError(404))))
        self.api._api = self.docker
        self.monitor = self.new_monitor()
        self.api._containers["svc"] = self.monitor
        self.removed = Mock()
        self.monitor.register_callback(self.removed, "test")
        self.monitor._task = asyncio.create_task(asyncio.Event().wait())
        self.tasks.append(self.monitor._task)
        self.records = []
        self.handler = logging.Handler()
        self.handler.emit = self.records.append
        LOGGER.addHandler(self.handler)
        LOGGER.setLevel(logging.DEBUG)

    def new_monitor(self):
        monitor = self.ns["DockerContainerAPI"](self.config, self.docker, "svc", on_gone=self.api._container_gone)
        monitor._container = Container("old")
        return monitor

    async def asyncTearDown(self):
        LOGGER.removeHandler(self.handler)
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def warnings(self):
        return [(r.levelname, r.getMessage()) for r in self.records if r.levelno >= logging.WARNING]


class LifecycleTests(HelperCase):
    async def test_concurrent_destroy_and_disappearance_drain_queues(self):
        """Hold the first cleanup at an explicit barrier, then run destroy.

        Before the fix both removals pass the membership check; the second
        deletion raises KeyError inside the queue processor and strands destroy.
        No scheduler timing or wall-clock sleeps select the interleaving.
        """
        entered, release = asyncio.Event(), asyncio.Event()
        async def cleanup_sleep(seconds):
            if seconds == 0.1:
                entered.set()
                await release.wait()
        self.ns["asyncio"].sleep = cleanup_sleep
        first = asyncio.create_task(self.api._container_remove("svc"))
        self.tasks.append(first)
        await entered.wait()
        self.api._event_destroy["svc"] = self.monitor
        second = asyncio.create_task(self.api._container_create_destroy())
        self.tasks.append(second)
        # A ready-queue barrier lets the second remover enter before releasing.
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(first, second)
        self.assertEqual(self.api._containers, {})
        self.assertEqual(self.api._event_destroy, {})
        self.assertEqual(self.api._event_create, {})
        self.assertTrue(self.monitor._task.done())
        self.assertEqual(self.monitor._subscribers, [])
        self.removed.assert_called_once_with(remove=True)
        self.assertEqual(self.warnings(), [])
        replacement = Container("new")
        self.docker.containers.get.side_effect = None
        self.docker.containers.get.return_value = replacement
        self.ns["DockerContainerAPI"]._run = AsyncMock()
        self.api._event_create["svc"] = 3
        await self.api._container_create_destroy()
        fresh = self.api._containers["svc"]
        self.tasks.append(fresh._task)
        self.assertIs(fresh._container, replacement)
        self.assertEqual(self.api._event_create, {})
        self.assertEqual(self.api._event_destroy, {})

    async def test_cleanup_cannot_delete_replacement(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def cleanup_sleep(seconds):
            entered.set()
            await release.wait()
        self.ns["asyncio"].sleep = cleanup_sleep
        removal = asyncio.create_task(self.api._container_remove("svc"))
        self.tasks.append(removal)
        await entered.wait()
        fresh = self.new_monitor()
        self.api._containers["svc"] = fresh
        release.set()
        await removal
        self.assertIs(self.api._containers.get("svc"), fresh)
        self.assertEqual(self.warnings(), [])

    async def test_scheduled_old_retirement_cannot_remove_new_monitor(self):
        self.api._container_gone(self.monitor)
        fresh = self.new_monitor()
        self.api._containers["svc"] = fresh
        await asyncio.gather(*self.tasks[1:])
        self.assertIs(self.api._containers.get("svc"), fresh)
        self.assertTrue(self.monitor._task.done())
        self.assertEqual(self.monitor._subscribers, [])
        self.removed.assert_called_once_with(remove=True)
        self.assertEqual(self.warnings(), [])

    async def test_two_missing_name_lookups_retire_and_stop_queries(self):
        await self.monitor._handle_container_gone("404")
        self.assertIs(self.api._containers["svc"], self.monitor)
        await self.monitor._handle_container_gone("404")
        await asyncio.gather(*self.tasks[1:])
        self.assertEqual(self.api._containers, {})
        self.assertTrue(self.monitor._task.done())
        self.assertEqual(self.monitor._subscribers, [])
        calls = self.docker.containers.get.await_count
        self.monitor.request_refresh()
        await asyncio.sleep(0)
        self.assertEqual(self.docker.containers.get.await_count, calls)
        self.assertEqual(self.warnings(), [])

    async def test_disappeared_before_attach_has_no_duplicate_warnings(self):
        self.api._containers.clear()
        await self.api._container_add("svc")
        self.assertEqual(self.api._containers, {})
        self.assertEqual(self.api._event_create, {})
        self.assertEqual(self.api._event_destroy, {})
        self.assertEqual(self.warnings(), [])
        diagnostics = [r for r in self.records if "not available anymore (2a)" in r.getMessage()]
        self.assertEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0].levelname, "DEBUG")
        self.assertFalse(any("Problem during start" in r.getMessage() for r in self.records))

    async def drive_events(self, events):
        finished = asyncio.Event()
        pending = iter(events)
        async def get():
            try:
                return next(pending)
            except StopIteration:
                finished.set()
                await asyncio.Event().wait()
        self.docker.events = SimpleNamespace(subscribe=lambda: SimpleNamespace(get=get))
        task = asyncio.create_task(self.api._run_docker_events())
        self.tasks.append(task)
        await finished.wait()
        self.assertFalse(task.done(), "event consumer stopped unexpectedly")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    def event(self, action, cid="old"):
        return {"Type": "container", "Action": action,
                "Actor": {"ID": cid, "Attributes": {"name": "svc"}}}

    async def test_duplicate_and_old_id_events_keep_current_monitor(self):
        async def no_delay(seconds):
            await asyncio.sleep(0)
        self.ns["asyncio"].sleep = no_delay
        self.docker.containers.get.side_effect = None
        self.docker.containers.get.return_value = self.monitor._container
        await self.drive_events([self.event("create"), self.event("create"),
                                 self.event("destroy", "older")])
        self.assertIs(self.api._containers["svc"], self.monitor)
        self.assertFalse(self.monitor._task.done())
        self.removed.assert_not_called()
        self.assertEqual(self.api._event_create, {})
        self.assertEqual(self.api._event_destroy, {})
        self.assertEqual(self.warnings(), [])

    async def test_duplicate_destroy_then_create_connects_new_id(self):
        async def no_delay(seconds):
            await asyncio.sleep(0)
        self.ns["asyncio"].sleep = no_delay
        new = Container("new")
        self.docker.containers.get.side_effect = None
        self.docker.containers.get.return_value = new
        async def run(monitor):
            await asyncio.Event().wait()
        self.ns["DockerContainerAPI"]._run = run
        await self.drive_events([self.event("destroy"), self.event("destroy"),
                                 self.event("create", "new"), self.event("create", "new"),
                                 self.event("destroy", "old")])
        fresh = self.api._containers["svc"]
        self.tasks.append(fresh._task)
        self.assertIs(fresh._container, new)
        self.assertFalse(fresh._task.done())
        self.assertTrue(self.monitor._task.done())
        self.assertEqual(self.monitor._subscribers, [])
        self.removed.assert_called_once_with(remove=True)
        self.assertEqual(self.api._event_create, {})
        self.assertEqual(self.api._event_destroy, {})
        self.assertEqual(self.warnings(), [])
        self.ns["async_dispatcher_send"].assert_called_once_with(
            self.hass, "monitor_docker_test_container_added", "svc")

    async def test_destroy_during_attach_does_not_start_or_publish(self):
        self.api._containers.clear()
        entered, release = asyncio.Event(), asyncio.Event()
        async def get(name):
            entered.set()
            await release.wait()
            return Container("new")
        self.docker.containers.get.side_effect = get
        adding = asyncio.create_task(self.api._container_add("svc"))
        self.tasks.append(adding)
        await entered.wait()
        monitor = self.api._containers["svc"]
        await self.api._container_remove("svc", monitor)
        release.set()
        await adding
        self.assertIsNone(monitor._task)
        self.assertEqual(self.api._containers, {})
        self.ns["async_dispatcher_send"].assert_not_called()
        self.assertEqual(self.warnings(), [])

    async def test_destroy_during_publication_delay_does_not_publish(self):
        self.api._containers.clear()
        entered, release = asyncio.Event(), asyncio.Event()
        async def delay(seconds):
            if seconds == 1:
                entered.set()
                await release.wait()
        self.ns["asyncio"].sleep = delay
        self.docker.containers.get.side_effect = None
        self.docker.containers.get.return_value = Container("new")
        self.ns["DockerContainerAPI"]._run = AsyncMock()
        adding = asyncio.create_task(self.api._container_add("svc"))
        self.tasks.append(adding)
        await entered.wait()
        monitor = self.api._containers["svc"]
        await self.api._container_remove("svc", monitor)
        release.set()
        await adding
        self.assertTrue(monitor._task.done())
        self.assertEqual(self.api._containers, {})
        self.ns["async_dispatcher_send"].assert_not_called()

    async def test_real_poll_loop_stops_after_two_missing_lookups(self):
        self.monitor._task.cancel()
        await asyncio.gather(self.monitor._task, return_exceptions=True)
        self.monitor._run_container_info = AsyncMock(side_effect=DockerError(404))
        retired = asyncio.Event()
        original = self.api._container_remove
        async def remove(*args):
            await original(*args)
            retired.set()
        self.api._container_remove = remove
        async def no_delay(seconds):
            await asyncio.sleep(0)
        self.ns["asyncio"].sleep = no_delay
        self.monitor._task = asyncio.create_task(self.monitor._run())
        self.tasks.append(self.monitor._task)
        await retired.wait()
        self.assertEqual(self.monitor._run_container_info.await_count, 2)
        self.assertEqual(self.docker.containers.get.await_count, 2)
        self.assertTrue(self.monitor._task.done())
        self.assertEqual(self.monitor._subscribers, [])
        self.assertEqual(self.api._containers, {})
        self.monitor.request_refresh()
        await asyncio.sleep(0)
        self.assertEqual(self.monitor._run_container_info.await_count, 2)
        self.assertEqual(self.warnings(), [])

    async def test_recovery_by_name_resets_baselines(self):
        self.monitor._cpu_old = {"total": 3}
        self.monitor._network_old = {"total_rx": 4}
        self.monitor._memory_prev = 500
        self.docker.containers.get.side_effect = None
        new = Container("new")
        self.docker.containers.get.return_value = new
        self.assertTrue(await self.monitor._handle_container_gone("404"))
        self.assertIs(self.monitor._container, new)
        self.assertEqual(self.monitor._cpu_old, {})
        self.assertEqual(self.monitor._network_old, {})
        self.assertIsNone(self.monitor._memory_prev)
        self.assertEqual(self.warnings(), [])

    async def test_bad_subscriber_cannot_strand_queue_or_skip_others(self):
        bad = Mock(side_effect=ValueError("bad subscriber"))
        other = Mock()
        self.monitor.register_callback(bad, "bad")
        self.monitor.register_callback(other, "other")
        self.api._event_destroy["svc"] = self.monitor
        await self.api._container_create_destroy()
        self.assertEqual(self.api._event_destroy, {})
        self.assertEqual(self.api._containers, {})
        self.assertTrue(self.monitor._task.done())
        self.assertEqual(self.monitor._subscribers, [])
        other.assert_called_once_with(remove=True)
        self.assertEqual(self.warnings(), [("ERROR", "[test] svc: Entity removal callback failed")])

    async def test_initial_run_survives_retirement_during_lookup(self):
        self.api._containers = {"svc": None}
        self.api._tasks = {"info": self.monitor._task, "events": self.monitor._task}
        self.ns["EVENT_HOMEASSISTANT_STOP"] = "stop"
        self.hass.bus = SimpleNamespace(async_listen_once=Mock())
        entered, release = asyncio.Event(), asyncio.Event()
        async def get(name):
            entered.set()
            await release.wait()
            return Container("new")
        self.docker.containers.get.side_effect = get
        running = asyncio.create_task(self.api.run())
        self.tasks.append(running)
        await entered.wait()
        initial = self.api._containers["svc"]
        await self.api._container_remove("svc", initial)
        release.set()
        await running
        self.assertEqual(self.api._containers, {})
        self.assertIsNone(initial._task)
        self.assertEqual(self.warnings(), [])

    async def test_same_id_lookup_returning_after_retirement_reattaches(self):
        entered, release, retired = asyncio.Event(), asyncio.Event(), asyncio.Event()
        old_container = self.monitor._container
        lookups = 0
        async def get(name):
            nonlocal lookups
            lookups += 1
            if lookups == 1:
                entered.set()
                await release.wait()
                return old_container
            if lookups <= 3:
                raise DockerError(404)
            return old_container
        self.docker.containers.get.side_effect = get
        async def no_delay(seconds):
            await asyncio.sleep(0)
        self.ns["asyncio"].sleep = no_delay
        original_remove = self.api._container_remove
        async def remove(*args):
            await original_remove(*args)
            retired.set()
        self.api._container_remove = remove
        adding = asyncio.create_task(self.api._container_add("svc"))
        self.tasks.append(adding)
        await entered.wait()
        self.monitor._task.cancel()
        await asyncio.gather(self.monitor._task, return_exceptions=True)
        self.monitor._run_container_info = AsyncMock(side_effect=DockerError(404))
        self.monitor._task = asyncio.create_task(self.monitor._run())
        self.tasks.append(self.monitor._task)
        await retired.wait()
        self.assertEqual(self.api._containers, {})
        self.assertEqual(self.monitor._run_container_info.await_count, 2)
        # Let the held lookup return a positive result for the *old* identity.
        # A later lookup by the replacement attach can resolve the same live ID.
        async def idle(monitor):
            await asyncio.Event().wait()
        self.ns["DockerContainerAPI"]._run = idle
        release.set()
        await adding
        fresh = self.api._containers.get("svc")
        self.assertIsNotNone(fresh)
        self.assertIsNot(fresh, self.monitor)
        self.assertIs(fresh._container, old_container)
        self.tasks.append(fresh._task)
        self.assertFalse(fresh._task.done())
        self.assertTrue(self.monitor._task.done())
        self.assertEqual(self.monitor._subscribers, [])
        # Normal update callbacks can precede retirement; removal occurs once.
        removals = [call.kwargs for call in self.removed.call_args_list
                    if call.kwargs.get("remove")]
        self.assertEqual(removals, [{"remove": True}])
        self.assertEqual(self.api._event_create, {})
        self.assertEqual(self.api._event_destroy, {})
        self.assertEqual(self.warnings(), [])
        self.ns["async_dispatcher_send"].assert_called_once_with(
            self.hass, "monitor_docker_test_container_added", "svc")


if __name__ == "__main__":
    unittest.main()
