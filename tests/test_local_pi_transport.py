import asyncio
from contextlib import suppress

import pytest

from bcikit.session import DeviceSession
from bcikit.transport import LocalPiTransport, TransportError, VirtualTransport


@pytest.mark.asyncio
async def test_local_pi_transport_uses_native_handshake_and_samples():
    async def handle(reader, writer):
        virtual = VirtualTransport()
        await virtual.open()

        async def commands():
            while data := await reader.read(4096):
                await virtual.write(data)

        async def samples():
            while True:
                writer.write(await virtual.read())
                await writer.drain()

        tasks = [asyncio.create_task(commands()), asyncio.create_task(samples())]
        try:
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            await asyncio.gather(*done, *pending, return_exceptions=True)
        finally:
            await virtual.close()
            writer.close()
            with suppress(Exception):
                await writer.wait_closed()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        for _ in range(2):
            session = DeviceSession(LocalPiTransport(port=port))
            try:
                await session.connect()
                await session.start()
                batch = await asyncio.wait_for(session.sample_queue.get(), timeout=2)
                assert batch.sample_count > 0
                assert batch.channel_count == 8
                await session.stop()
            finally:
                await session.close()
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_local_pi_transport_reports_absent_service():
    server = await asyncio.start_server(lambda _r, w: w.close(), "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    with pytest.raises(TransportError, match="本机采集服务"):
        await LocalPiTransport(port=port, timeout=0.1).open()
