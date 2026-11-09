import asyncio
import json

import aiohttp
import pytest

from bcikit.protocol import TYPE_ACK, encode_control_frame
from bcikit.session import DeviceSession
from bcikit.transport import (
    BleDevice,
    BleTransport,
    TransportError,
    VirtualTransport,
    WiFiTransport,
)


class FakeWiFiFirmware:
    def __init__(self, fail_tcp: bool = False, drop_post_reply: bool = False) -> None:
        self.fail_tcp = fail_tcp
        self.drop_post_reply = drop_post_reply
        self.server = None
        self.port = 0
        self.virtual = None
        self.relay_tasks = []
        self.data_writer = None
        self.tcp_output = None
        self.tcp_target = None
        self.delete_count = 0

    async def start(self) -> None:
        self.server = await asyncio.start_server(self._http, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def close(self) -> None:
        await self._close_stream()
        if self.server is not None:
            self.server.close()
            await self.server.wait_closed()

    async def _close_stream(self) -> None:
        for task in self.relay_tasks:
            task.cancel()
        await asyncio.gather(*self.relay_tasks, return_exceptions=True)
        self.relay_tasks = []
        if self.data_writer is not None:
            self.data_writer.close()
            await self.data_writer.wait_closed()
            self.data_writer = None
        if self.virtual is not None:
            await self.virtual.close()
            self.virtual = None

    async def _http(self, reader, writer) -> None:
        header = await reader.readuntil(b"\r\n\r\n")
        lines = header.decode("ascii").split("\r\n")
        method, path, _ = lines[0].split()
        content_length = 0
        for line in lines[1:]:
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":", 1)[1])
        body = await reader.readexactly(content_length) if content_length else b""
        status = 200
        response = {}
        if method == "GET" and path == "/board":
            response = {"board_connected": True, "num_channels": 8}
        elif method == "GET" and path == "/all":
            response = {"ip": "127.0.0.1", "name": "Fake BCIKit"}
        elif method == "GET" and path == "/tcp":
            response = self.tcp_target or {"ip": "0.0.0.0", "port": 0}
        elif method == "POST" and path == "/tcp":
            if self.fail_tcp:
                status = 504
                response = {"error": "connect failed"}
            else:
                await self._close_stream()
                target = json.loads(body)
                self.tcp_target = {
                    "ip": target["ip"], "port": target["port"], "connected": True
                }
                self.tcp_output = target.get("output")
                data_reader, self.data_writer = await asyncio.open_connection(
                    target["ip"], target["port"]
                )
                self.virtual = VirtualTransport()
                await self.virtual.open()
                self.relay_tasks = [
                    asyncio.create_task(self._relay_to_virtual(data_reader)),
                    asyncio.create_task(self._relay_from_virtual(self.data_writer)),
                ]
                response = {"connected": True, "output": "bcikit"}
                if self.drop_post_reply:
                    writer.close()
                    await writer.wait_closed()
                    return
        elif method == "DELETE" and path == "/tcp":
            self.delete_count += 1
            self.tcp_target = None
            await self._close_stream()
            response = {"connected": False}
        elif method == "GET" and path == "/stream/stop":
            response = {"streaming": False}
        else:
            status = 404
            response = {"error": "not found"}
        payload = json.dumps(response).encode()
        reason = "OK" if status == 200 else "Gateway Timeout" if status == 504 else "Not Found"
        writer.write(
            f"HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
            + payload
        )
        await writer.drain()
        writer.close()
        await writer.wait_closed()

    async def _relay_to_virtual(self, reader) -> None:
        while chunk := await reader.read(4096):
            await self.virtual.write(chunk)

    async def _relay_from_virtual(self, writer) -> None:
        while True:
            writer.write(await self.virtual.read())
            await writer.drain()


@pytest.mark.asyncio
async def test_wifi_reverse_tcp_full_handshake_and_samples():
    firmware = FakeWiFiFirmware()
    await firmware.start()
    session = DeviceSession(WiFiTransport("127.0.0.1", firmware.port, require_softap_subnet=False))
    try:
        await session.connect()
        await session.start()
        batch = await asyncio.wait_for(session.sample_queue.get(), 1.0)
        assert batch.channel_count == 8
        assert session.device_info is not None
        assert session.device_status is not None
        assert firmware.tcp_output == "auto"
    finally:
        await session.close()
        await firmware.close()


@pytest.mark.asyncio
async def test_wifi_504_has_firewall_advice():
    firmware = FakeWiFiFirmware(fail_tcp=True)
    await firmware.start()
    transport = WiFiTransport("127.0.0.1", firmware.port, require_softap_subnet=False)
    try:
        with pytest.raises(TransportError, match="入站连接"):
            await transport.open()
    finally:
        await transport.close()
        await firmware.close()


@pytest.mark.asyncio
async def test_wifi_close_releases_device_session_before_reconnect():
    firmware = FakeWiFiFirmware()
    await firmware.start()
    first = WiFiTransport("127.0.0.1", firmware.port, require_softap_subnet=False)
    second = WiFiTransport("127.0.0.1", firmware.port, require_softap_subnet=False)
    try:
        await first.open()
        assert firmware.tcp_target is not None
        await first.close()
        assert firmware.tcp_target is None
        assert firmware.delete_count == 1
        await second.open()
        assert firmware.tcp_target is not None
    finally:
        await first.close()
        await second.close()
        await firmware.close()


@pytest.mark.asyncio
async def test_wifi_lost_post_reply_still_releases_accepted_target():
    firmware = FakeWiFiFirmware(drop_post_reply=True)
    await firmware.start()
    transport = WiFiTransport("127.0.0.1", firmware.port, require_softap_subnet=False)
    try:
        with pytest.raises(TransportError, match="POST /tcp"):
            await transport.open()
        assert firmware.tcp_target is None
        assert firmware.delete_count == 1
    finally:
        await transport.close()
        await firmware.close()


@pytest.mark.asyncio
async def test_wifi_connect_clears_only_inactive_same_host_target():
    firmware = FakeWiFiFirmware()
    await firmware.start()
    transport = WiFiTransport("127.0.0.1", firmware.port, require_softap_subnet=False)
    try:
        transport._http = aiohttp.ClientSession()
        firmware.tcp_target = {"ip": "127.0.0.2", "port": 1234, "connected": False}
        await transport._release_stale_same_host_target("127.0.0.1")
        assert firmware.delete_count == 0
        firmware.tcp_target = {"ip": "127.0.0.1", "port": 1234, "connected": False}
        await transport._release_stale_same_host_target("127.0.0.1")
        assert firmware.delete_count == 1
        assert firmware.tcp_target is None
    finally:
        await transport.close()
        await firmware.close()


@pytest.mark.asyncio
async def test_concurrent_session_close_waits_for_transport_release(monkeypatch):
    transport = VirtualTransport("normal")
    release_started = asyncio.Event()
    allow_release = asyncio.Event()

    async def slow_transport_close():
        release_started.set()
        await allow_release.wait()

    monkeypatch.setattr(transport, "close", slow_transport_close)
    session = DeviceSession(transport)
    first = asyncio.create_task(session.close())
    await asyncio.wait_for(release_started.wait(), timeout=1.0)
    second = asyncio.create_task(session.close())
    await asyncio.sleep(0)
    assert not second.done()

    allow_release.set()
    await asyncio.wait_for(asyncio.gather(first, second), timeout=1.0)
    assert session.state.value == "disconnected"


@pytest.mark.asyncio
async def test_wifi_session_reconnects_after_data_socket_drop():
    firmware = FakeWiFiFirmware()
    await firmware.start()
    session = DeviceSession(WiFiTransport("127.0.0.1", firmware.port, require_softap_subnet=False))
    try:
        await session.connect()
        await session.start()
        await asyncio.wait_for(session.sample_queue.get(), 1.0)
        assert firmware.data_writer is not None
        firmware.data_writer.close()
        await firmware.data_writer.wait_closed()
        while session.state.value != "error":
            await asyncio.wait_for(session.state_queue.get(), 1.0)
        assert await session.reconnect((0.01,))
        batch = await asyncio.wait_for(session.sample_queue.get(), 1.0)
        assert batch.channel_count == 8
        assert session.stats.reconnects == 1
    finally:
        await session.close()
        await firmware.close()


@pytest.mark.asyncio
async def test_ble_transport_reassembles_control_notifications_without_mixing_streams():
    transport = BleTransport(BleDevice("test-device", "BCIKit-TEST"))
    control = encode_control_frame(TYPE_ACK, 7, 0x0B, 0, b"pong")

    transport._accept_notification(transport._control_decoder, bytearray(control[:5]))
    assert transport._frames.empty()
    transport._accept_notification(transport._control_decoder, bytearray(control[5:]))

    assert await transport.read() == control
    await transport.close()
