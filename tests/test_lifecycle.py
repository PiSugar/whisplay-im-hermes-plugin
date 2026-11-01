"""Behavioral lifecycle tests: real Hermes base + real HTTPX, fake bridge I/O.

Events/queues control requests and backoff; no wall-clock retry sleeps, real
network, gateway process, credentials, or physical device are required.
"""
import asyncio
import os
from types import SimpleNamespace
import unittest
from unittest import mock

from support import load_adapter

import httpx
from agent import secret_scope
from gateway.config import Platform, PlatformConfig
from gateway.platform_registry import PlatformEntry, PlatformRegistry


class Bridge:
    def __init__(self):
        self.requests = []
        self.polls = asyncio.Queue()
        self.responses = asyncio.Queue()
        self.cancelled_polls = 0

    async def __call__(self, request):
        self.requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"id": "test-reply"})
        self.polls.put_nowait(request)
        try:
            response = await self.responses.get()
        except asyncio.CancelledError:
            self.cancelled_polls += 1
            raise
        if isinstance(response, Exception):
            raise response
        return response

    async def next_poll(self):
        return await asyncio.wait_for(self.polls.get(), timeout=2)

    def reply(self, response):
        self.responses.put_nowait(response)


class LifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.enterContext(mock.patch.dict(os.environ, {}))
        for key in list(os.environ):
            if key.startswith("WHISPLAY_IM_"):
                del os.environ[key]
        token = secret_scope.set_secret_scope({})
        self.addCleanup(secret_scope.reset_secret_scope, token)
        # A fresh registry avoids replacing registrations owned by another test.
        registry = PlatformRegistry()
        self.enterContext(mock.patch("gateway.platform_registry.platform_registry", registry))
        self.enterContext(mock.patch.dict(Platform._value2member_map_))
        self.enterContext(mock.patch.dict(Platform._member_map_))
        self.mod = load_adapter()
        self.mod.register(SimpleNamespace(register_platform=lambda **kw: registry.register(PlatformEntry(**kw))))
        self.bridge = Bridge()
        self.clients = []
        real_client = httpx.AsyncClient

        def make_client(**kwargs):
            client = real_client(transport=httpx.MockTransport(self.bridge), **kwargs)
            self.clients.append(client)
            return client

        self.enterContext(mock.patch.object(self.mod.httpx, "AsyncClient", side_effect=make_client))
        self.sleep_started = asyncio.Queue()
        self.resume_sleep = asyncio.Queue()

        async def controlled_sleep(delay):
            self.sleep_started.put_nowait(delay)
            await self.resume_sleep.get()

        # Replace only this module's asyncio reference, not asyncio.sleep globally.
        setattr(self.mod, "asyncio", SimpleNamespace(**vars(asyncio)))
        self.mod.asyncio.sleep = controlled_sleep
        self.adapter = self.mod.WhisplayIMAdapter(PlatformConfig(extra={
            "base_url": "http://bridge.example.test:18888", "reconnect_sec": 0.25,
        }))

    def live_pollers(self):
        return [task for task in asyncio.all_tasks()
                if task.get_coro().__qualname__.endswith("WhisplayIMAdapter._poll_loop")
                and not task.done()]

    async def asyncTearDown(self):
        # Also clean leaked duplicate tasks when deliberately testing a broken revision.
        tasks = self.live_pollers()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.adapter.disconnect()
        for client in self.clients:
            await client.aclose()

    async def wait_for_sleep(self):
        return await asyncio.wait_for(self.sleep_started.get(), timeout=2)

    async def establish_connection(self):
        self.assertTrue(await self.adapter.connect(is_reconnect=True))
        await self.bridge.next_poll()
        self.bridge.reply(httpx.Response(204))
        request = await self.bridge.next_poll()
        self.assertTrue(self.adapter.is_connected)
        self.assertEqual(request.url.params["waitSec"], "30")

    async def assert_stopped(self, task, request_count):
        self.assertTrue(task.done())
        self.assertIsNone(self.adapter._poll_task)
        self.assertIsNone(self.adapter._client)
        self.assertFalse(self.adapter.is_connected)
        self.assertFalse(self.adapter._bridge_connected)
        self.assertFalse(self.adapter.send_path_degraded)
        self.assertTrue(all(client.is_closed for client in self.clients))
        self.assertEqual(self.live_pollers(), [])
        # Release everything that might wake an orphaned poller, then yield the loop.
        self.bridge.reply(httpx.Response(204))
        self.resume_sleep.put_nowait(None)
        for _ in range(3):
            await asyncio.sleep(0)
        self.assertEqual(len(self.bridge.requests), request_count)
        self.assertEqual(self.live_pollers(), [])

    async def test_pending_initial_poll_is_not_connected(self):
        self.assertTrue(await self.adapter.connect())
        await self.bridge.next_poll()
        self.assertFalse(self.adapter.is_connected)
        self.assertTrue(self.adapter.send_path_degraded)
        self.assertFalse((await self.adapter.send("test", "hello")).success)

    async def test_initial_outage_reports_degraded_until_recovery(self):
        # The runner uses this public hook after connect() returns True, for
        # both startup and reconnect; is_connected alone is not sufficient.
        self.assertFalse(self.adapter.send_path_degraded)
        self.assertTrue(await self.adapter.connect(is_reconnect=True))
        await self.bridge.next_poll()
        self.bridge.reply(httpx.ConnectError("simulated initial outage"))
        self.assertEqual(await self.wait_for_sleep(), 0.25)
        self.assertFalse(self.adapter.is_connected)
        self.assertTrue(self.adapter.send_path_degraded)
        task = self.adapter._poll_task
        self.assertFalse(task.done())
        self.assertFalse((await self.adapter.send("test", "offline")).success)
        self.resume_sleep.put_nowait(None)
        probe = await self.bridge.next_poll()
        self.assertEqual(probe.url.params["waitSec"], "0")
        self.bridge.reply(httpx.Response(204))
        await self.bridge.next_poll()
        self.assertTrue(self.adapter.is_connected)
        self.assertFalse(self.adapter.send_path_degraded)
        await self.adapter.disconnect()
        self.assertTrue(task.done())
        self.assertFalse(self.adapter.send_path_degraded)

    async def test_success_failure_recovery(self):
        await self.establish_connection()
        self.assertFalse(self.adapter.send_path_degraded)
        self.bridge.reply(httpx.ConnectError("simulated bridge outage"))
        self.assertEqual(await self.wait_for_sleep(), 0.25)
        self.assertFalse(self.adapter.is_connected)
        self.assertFalse(self.adapter._bridge_connected)
        self.assertTrue(self.adapter.send_path_degraded)
        request_count = len(self.bridge.requests)
        result = await self.adapter.send("test", "must not send offline")
        self.assertFalse(result.success)
        self.assertTrue(result.retryable)
        await self.adapter.send_typing("test")
        self.assertEqual(len(self.bridge.requests), request_count)
        # Reconnect calls during backoff must not spawn a second poller/client.
        task = self.adapter._poll_task
        self.assertTrue(await self.adapter.connect(is_reconnect=True))
        self.assertIs(self.adapter._poll_task, task)
        self.assertEqual(len(self.clients), 1)
        self.resume_sleep.put_nowait(None)
        probe = await self.bridge.next_poll()
        self.assertEqual(probe.url.params["waitSec"], "0")
        self.assertFalse(self.adapter.is_connected)
        self.bridge.reply(httpx.Response(204))
        await self.bridge.next_poll()
        self.assertTrue(self.adapter.is_connected)
        self.assertFalse(self.adapter.send_path_degraded)
        self.assertTrue((await self.adapter.send("test", "recovered")).success)
        await self.adapter.disconnect()
        await self.assert_stopped(task, len(self.bridge.requests))

    async def test_disconnect_during_in_flight_poll(self):
        await self.establish_connection()
        task = self.adapter._poll_task
        request_count = len(self.bridge.requests)
        await asyncio.wait_for(self.adapter.disconnect(), timeout=2)
        self.assertEqual(self.bridge.cancelled_polls, 1)
        await self.assert_stopped(task, request_count)

    async def test_disconnect_during_reconnect_sleep(self):
        await self.establish_connection()
        self.bridge.reply(httpx.ConnectError("simulated bridge outage"))
        await self.wait_for_sleep()
        task = self.adapter._poll_task
        request_count = len(self.bridge.requests)
        await asyncio.wait_for(self.adapter.disconnect(), timeout=2)
        await self.assert_stopped(task, request_count)

    async def test_disconnect_during_http_backoff(self):
        self.assertTrue(await self.adapter.connect())
        await self.bridge.next_poll()
        self.bridge.reply(httpx.Response(503, text="simulated unavailable"))
        self.assertEqual(await self.wait_for_sleep(), 1.0)
        task = self.adapter._poll_task
        request_count = len(self.bridge.requests)
        await asyncio.wait_for(self.adapter.disconnect(), timeout=2)
        await self.assert_stopped(task, request_count)

    async def test_repeated_and_concurrent_connect_have_one_owner(self):
        self.assertEqual(await asyncio.gather(
            self.adapter.connect(), self.adapter.connect(is_reconnect=True)), [True, True])
        await self.bridge.next_poll()
        task = self.adapter._poll_task
        self.assertTrue(await self.adapter.connect())
        self.assertIs(self.adapter._poll_task, task)
        self.assertEqual(len(self.clients), 1)
        self.assertEqual(len(self.live_pollers()), 1)
        await self.adapter.disconnect()
        await self.assert_stopped(task, len(self.bridge.requests))

    async def test_connect_waits_for_disconnect_to_close_client(self):
        await self.establish_connection()
        old_task = self.adapter._poll_task
        old_client = self.clients[0]
        close_started = asyncio.Event()
        finish_close = asyncio.Event()
        real_close = old_client.aclose

        async def close_slowly():
            close_started.set()
            await finish_close.wait()
            await real_close()

        with mock.patch.object(old_client, "aclose", side_effect=close_slowly):
            stopping = asyncio.create_task(self.adapter.disconnect())
            starting = None
            try:
                await asyncio.wait_for(close_started.wait(), timeout=2)
                starting = asyncio.create_task(self.adapter.connect(is_reconnect=True))
                await asyncio.sleep(0)
                self.assertFalse(starting.done())
                self.assertEqual(len(self.clients), 1)
                self.assertFalse(self.adapter.is_connected)
            finally:
                finish_close.set()
                await stopping
                if starting is not None:
                    await starting
        self.assertTrue(old_task.done())
        self.assertTrue(old_client.is_closed)
        self.assertEqual(len(self.clients), 2)
        await self.bridge.next_poll()
        self.bridge.reply(httpx.Response(204))
        await self.bridge.next_poll()
        self.assertTrue(self.adapter.is_connected)
        task = self.adapter._poll_task
        await self.adapter.disconnect()
        await self.assert_stopped(task, len(self.bridge.requests))

    async def test_concurrent_disconnect_is_idempotent(self):
        await self.establish_connection()
        task = self.adapter._poll_task
        request_count = len(self.bridge.requests)
        await asyncio.wait_for(asyncio.gather(
            self.adapter.disconnect(), self.adapter.disconnect()), timeout=2)
        await self.assert_stopped(task, request_count)


if __name__ == "__main__":
    unittest.main()
