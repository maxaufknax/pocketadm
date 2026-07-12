"""In-memory metrics history: a background collector samples the system every
10 seconds and keeps ~2 hours of points, so the UI can show live graphs when
a stat tile is tapped. Also measures internet reachability/latency (TCP
connect timing — no ICMP privileges needed inside a container).
"""
import asyncio
import time
from collections import deque

from . import sysinfo

INTERVAL = 10
HISTORY = deque(maxlen=720)  # 720 × 10s = 2h

_task: asyncio.Task | None = None
_last_net: tuple[float, int, int] | None = None  # (time, rx_bytes, tx_bytes)

# latency probes: (host, port) — 443 is almost never blocked outbound
PROBES = (("1.1.1.1", 443), ("8.8.8.8", 443), ("9.9.9.9", 443))


async def measure_latency() -> float | None:
    """Best latency (ms) of the probe set; None = offline."""
    best: float | None = None
    for host, port in PROBES:
        t0 = time.monotonic()
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=3)
            ms = (time.monotonic() - t0) * 1000
            writer.close()
            best = ms if best is None or ms < best else best
        except Exception:
            continue
    return round(best, 1) if best is not None else None


def _net_rates() -> tuple[float, float]:
    """Current rx/tx rate in bytes/s based on /proc/net/dev deltas."""
    global _last_net
    now = time.monotonic()
    rx, tx = sysinfo.net_counters()
    if _last_net is None:
        _last_net = (now, rx, tx)
        return 0.0, 0.0
    dt = now - _last_net[0]
    rates = ((rx - _last_net[1]) / dt, (tx - _last_net[2]) / dt) if dt > 0 else (0.0, 0.0)
    _last_net = (now, rx, tx)
    return max(0.0, rates[0]), max(0.0, rates[1])


async def _collect_once() -> dict:
    snap = await asyncio.to_thread(sysinfo.snapshot_light)
    rx, tx = _net_rates()
    ping = await measure_latency()
    return {
        "t": time.time(),
        "cpu": snap["cpu_percent"],
        "mem": snap["memory_percent"],
        "disk": snap["disk_percent"],
        "load": snap["load1"],
        "rx": round(rx),
        "tx": round(tx),
        "ping": ping,
    }


async def _loop() -> None:
    while True:
        try:
            HISTORY.append(await _collect_once())
        except Exception:
            pass
        await asyncio.sleep(INTERVAL)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _task = asyncio.ensure_future(_loop())


def history(minutes: int = 60) -> list[dict]:
    cutoff = time.time() - minutes * 60
    return [p for p in HISTORY if p["t"] >= cutoff]


def latest() -> dict | None:
    return HISTORY[-1] if HISTORY else None
