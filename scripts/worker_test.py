#!/usr/bin/env python3
"""
Simulate backpressure on the symbol-sharded worker pool.
"""

import asyncio
import random
import time


async def simulate_backpressure():
    WORKER_COUNT = 10
    signal_queues = [asyncio.Queue(maxsize=10) for _ in range(WORKER_COUNT)]
    shutdown_event = asyncio.Event()

    async def _mock_worker(worker_id: int, q: asyncio.Queue):
        while not shutdown_event.is_set():
            try:
                try:
                    queued_ts, sym, sig = await asyncio.wait_for(q.get(), timeout=1.0)
                except TimeoutError:
                    continue
                # Simulate heavy processing
                await asyncio.sleep(0.5)
                q.task_done()
            except asyncio.CancelledError:
                break

    worker_tasks = [asyncio.create_task(_mock_worker(i, signal_queues[i])) for i in range(WORKER_COUNT)]

    print(f"Starting backpressure simulation with {WORKER_COUNT} workers...")

    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"]
    dropped = 0
    processed = 0

    for i in range(200):
        sym = random.choice(symbols)
        worker_idx = hash(sym) % WORKER_COUNT
        q = signal_queues[worker_idx]
        if q.full():
            dropped += 1
        else:
            await q.put((time.time(), sym, "mock_signal"))
            processed += 1
        await asyncio.sleep(0.01)

    print(f"Simulation complete. Processed: {processed}, Dropped: {dropped}")

    shutdown_event.set()
    for wt in worker_tasks:
        wt.cancel()

if __name__ == "__main__":
    asyncio.run(simulate_backpressure())
