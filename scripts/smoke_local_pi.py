"""Short native-protocol hardware check on a Raspberry Pi with electrodes removed."""

from __future__ import annotations

import argparse
import asyncio

from bcikit.models import SessionConfig
from bcikit.session import DeviceSession
from bcikit.transport import LocalPiTransport


async def run(board_count: int, sample_rate: int, target_samples: int) -> None:
    session = DeviceSession(LocalPiTransport(), SessionConfig(board_count, sample_rate))
    try:
        await session.connect()
        boards = session.device_status.board_count if session.device_status else "?"
        print(f"connected: boards={boards}, verified={session.device_status.register_verified}")
        await session.start()
        total = 0
        while total < target_samples:
            batch = await asyncio.wait_for(session.sample_queue.get(), timeout=3)
            total += batch.sample_count
        print(f"received: {total} samples, {session.stats.valid_frames} valid frames")
        await session.stop()
        print(f"status: {await session.read_status()}")
    finally:
        await session.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--electrodes-disconnected", action="store_true", required=True)
    parser.add_argument("--boards", type=int, default=1, choices=range(1, 5))
    parser.add_argument("--rate", type=int, default=250, choices=(250, 500))
    parser.add_argument("--samples", type=int, default=50)
    args = parser.parse_args()
    if not args.electrodes_disconnected:
        parser.error("先断开人体和所有电极，再传入 --electrodes-disconnected")
    if not 1 <= args.samples <= 10_000:
        parser.error("--samples 必须在 1 到 10000 之间")
    asyncio.run(run(args.boards, args.rate, args.samples))


if __name__ == "__main__":
    main()
