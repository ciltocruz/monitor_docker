"""Logging policy regressions using the real statistics/polling methods."""
from copy import deepcopy
from datetime import datetime, timezone
import asyncio
from unittest.mock import AsyncMock

from test_lifecycle import HelperCase, DockerError


class DiagnosticTests(HelperCase):
    def sample(self):
        return {
            "read": "2026-10-19T10:00:01+00:00",
            "cpu_stats": {"cpu_usage": {"total_usage": 1000},
                          "system_cpu_usage": 10000, "online_cpus": 4},
            "memory_stats": {"usage": 1048576, "limit": 10485760, "stats": {}},
            "networks": {"eth0": {"rx_bytes": 1000, "tx_bytes": 2000}},
        }

    async def stats(self, raw):
        self.monitor._info["network_available"] = True
        self.ns["fetch_stats"] = AsyncMock(return_value=raw)
        await self.monitor._run_container_stats()

    async def test_incomplete_sample_is_debug_only_then_recovers(self):
        raw = self.sample()
        del raw["cpu_stats"]["system_cpu_usage"]
        raw["memory_stats"] = {}
        del raw["networks"]
        await self.stats(raw)
        self.assertEqual(self.warnings(), [])
        diagnostics = [r for r in self.records if "Incomplete" in r.getMessage()]
        self.assertEqual(len(diagnostics), 3)
        self.assertEqual([r.levelname for r in diagnostics], ["DEBUG"] * 3)
        self.assertIsNone(self.monitor.get_stats()["cpu_percentage"])
        self.assertIsNone(self.monitor.get_stats()["memory"])
        self.assertIsNone(self.monitor.get_stats()["network_speed_up"])
        await self.stats(self.sample())
        self.assertEqual(self.monitor._cpu_error, 0)
        self.assertEqual(self.monitor._memory_error, 0)
        self.assertEqual(self.monitor._network_error, 0)
        self.assertEqual(self.warnings(), [])

    async def test_persistent_incomplete_sections_warn_once_per_streak(self):
        raw = self.sample()
        raw.pop("cpu_stats")
        raw["memory_stats"] = {}
        raw.pop("networks")
        for _ in range(8):
            await self.stats(raw)
        self.assertEqual(self.warnings(), [
            ("WARNING", f"[test] svc: Incomplete {section} statistics for 3 consecutive samples ({error})")
            for section, error in [("cpu", "'cpu_stats'"), ("memory", "'usage'"), ("network", "'networks'")]
        ])
        self.assertTrue(self.monitor._info["network_available"])
        await self.stats(self.sample())
        self.records.clear()
        for _ in range(3):
            await self.stats(raw)
        self.assertEqual(len(self.warnings()), 3)
        self.assertEqual([r[0] for r in self.warnings()], ["WARNING"] * 3)

    async def test_empty_and_missing_timestamp_warn_only_if_persistent(self):
        for raw in ({}, {"cpu_stats": {}}):
            self.records.clear()
            await self.stats(raw)
            self.assertEqual(self.warnings(), [])
            for _ in range(6):
                await self.stats(raw)
            self.assertEqual(len(self.warnings()), 1)
            self.assertEqual(self.warnings()[0][0], "WARNING")
            self.assertIn("Incomplete sample statistics for 3 consecutive samples", self.warnings()[0][1])
            await self.stats(self.sample())

    async def test_network_counter_reset_and_nonpositive_time_are_not_errors(self):
        baseline = self.sample()
        await self.stats(baseline)
        for read in ("2026-10-19T10:00:02+00:00", "2026-10-19T10:00:02+00:00",
                     "2026-10-19T10:00:01+00:00"):
            raw = deepcopy(baseline)
            raw["read"] = read
            raw["networks"]["eth0"] = {"rx_bytes": 0, "tx_bytes": 0}
            await self.stats(raw)
            self.assertEqual(self.monitor.get_stats()["network_speed_up"], 0)
            self.assertEqual(self.monitor.get_stats()["network_speed_down"], 0)
        self.assertEqual(self.warnings(), [])
        self.assertEqual(self.monitor._network_old["total_tx"], 0)
        raw = self.sample()
        raw["read"] = "2026-10-19T10:00:03+00:00"
        await self.stats(raw)
        self.assertEqual(self.monitor.get_stats()["network_speed_up"], 0.98)
        self.assertEqual(self.monitor.get_stats()["network_speed_down"], 0.49)

    async def test_valid_stats_keep_existing_cpu_memory_and_network_calculations(self):
        raw = self.sample()
        await self.stats(raw)
        raw = deepcopy(raw)
        raw["read"] = "2026-10-19T10:00:02+00:00"
        raw["cpu_stats"]["cpu_usage"]["total_usage"] += 1000
        raw["cpu_stats"]["system_cpu_usage"] += 10000
        raw["networks"]["eth0"]["tx_bytes"] += 1024
        raw["networks"]["eth0"]["rx_bytes"] += 2048
        await self.stats(raw)
        stats = self.monitor.get_stats()
        self.assertEqual(stats["cpu_percentage"], 40)
        self.assertEqual(stats["1cpu_percentage"], 10)
        self.assertEqual(stats["memory"], 1)
        self.assertEqual(stats["memory_percentage"], 10)
        self.assertEqual(stats["network_speed_up"], 1)
        self.assertEqual(stats["network_speed_down"], 2)
        self.assertEqual(self.warnings(), [])

    async def test_authentication_and_connection_start_failures_remain_errors(self):
        self.api._containers.clear()
        for error in (DockerError(401, "unauthorized"), ConnectionError("connection refused")):
            self.records.clear()
            self.docker.containers.get.side_effect = error
            await self.api._container_add("svc")
            self.assertEqual(self.api._containers, {})
            self.assertEqual(len(self.warnings()), 1)
            self.assertEqual(self.warnings()[0][0], "ERROR")
            self.assertIn(str(error), self.warnings()[0][1])

    async def test_persistent_poll_failures_error_once_reset_on_success(self):
        self.monitor._task.cancel()
        await asyncio.gather(self.monitor._task, return_exceptions=True)
        error = DockerError(401, "unauthorized")
        self.monitor._run_container_info = AsyncMock(side_effect=error)
        self.monitor._run_container_stats = AsyncMock()
        retries = 0
        async def retry(seconds):
            nonlocal retries
            retries += 1
            if retries == 8:
                self.monitor._retired = True
        self.ns["asyncio"].sleep = retry
        await self.monitor._run()
        self.assertEqual(self.monitor._run_container_info.await_count, 8)
        self.assertEqual(self.warnings(), [("ERROR", "[test] svc: Container polling failed (unauthorized). Retry in 3600 seconds")])
        self.records.clear()
        self.monitor._retired = False
        self.monitor._info["state"] = "running"
        self.monitor._run_container_info = AsyncMock(side_effect=[None, ConnectionError("offline")])
        async def refreshed(seconds):
            pass
        self.monitor._sleep_or_refresh = refreshed
        async def stop(seconds):
            self.monitor._retired = True
        self.ns["asyncio"].sleep = stop
        await self.monitor._run()
        self.assertEqual(self.warnings(), [("ERROR", "[test] svc: Container polling failed (offline). Retry in 3600 seconds")])

    async def test_network_failures_do_not_disable_future_info_and_stats(self):
        raw = self.sample()
        raw.pop("networks")
        for _ in range(8):
            await self.stats(raw)
        self.monitor._container.show = AsyncMock(return_value={
            "State": {"Status": "exited", "StartedAt": "2026-10-19T10:00:00+00:00",
                      "FinishedAt": "2026-10-19T10:00:00+00:00", "ExitCode": 0},
            "Config": {"Image": "busybox"}, "Image": "sha256:test",
            "HostConfig": {"NetworkMode": "bridge"}})
        self.monitor._calcdockerformat = lambda dt: "1 second"
        await self.monitor._run_container_info()
        self.assertTrue(self.monitor.get_info()["network_available"])
        self.ns["fetch_stats"] = AsyncMock(return_value=self.sample())
        await self.monitor._run_container_stats()
        self.assertEqual(self.monitor._network_error, 0)
        self.assertIsNotNone(self.monitor.get_stats()["network_total_up"])
