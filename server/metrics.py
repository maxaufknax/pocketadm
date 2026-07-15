"""In-memory metrics history: a background collector samples the system every
10 seconds and keeps ~2 hours of points, so the UI can show live graphs when
a stat tile is tapped. Also measures internet reachability/latency (TCP
connect timing — no ICMP privileges needed inside a container).

On top of the fine-grained ring there is a *long* history: 5-minute averages
covering the last 7 days, persisted to the data volume so graphs survive
restarts. That's what powers the 6h/24h/7d ranges in the metric sheets.
"""
import asyncio
import json
import time
from collections import deque

from . import config, sysinfo

INTERVAL = 10
HISTORY = deque(maxlen=720)  # 720 × 10s = 2h

LONG_BUCKET = 300                          # seconds per long-history point
HISTORY_LONG = deque(maxlen=2016)          # 2016 × 5min = 7 days
LONG_FILE = config.DATA_DIR / "metrics-long.json"
_bucket: list[dict] = []                   # fine points of the bucket in progress
_bucket_start = 0.0

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


def _fold_bucket() -> None:
    """Average the finished 5-min bucket into the long history and persist."""
    global _bucket
    if not _bucket:
        return
    point: dict = {"t": _bucket[-1]["t"]}
    for key in ("cpu", "mem", "disk", "load", "rx", "tx", "ping"):
        vals = [p[key] for p in _bucket if p.get(key) is not None]
        point[key] = round(sum(vals) / len(vals), 1) if vals else None
    HISTORY_LONG.append(point)
    _bucket = []
    try:
        LONG_FILE.write_text(json.dumps(list(HISTORY_LONG)))
    except OSError:
        pass


def _load_long() -> None:
    try:
        pts = json.loads(LONG_FILE.read_text())
        cutoff = time.time() - LONG_BUCKET * HISTORY_LONG.maxlen
        HISTORY_LONG.extend(p for p in pts if isinstance(p, dict) and p.get("t", 0) >= cutoff)
    except (OSError, ValueError):
        pass


async def _loop() -> None:
    global _bucket_start
    while True:
        try:
            point = await _collect_once()
            HISTORY.append(point)
            if point["t"] - _bucket_start >= LONG_BUCKET:
                _fold_bucket()
                _bucket_start = point["t"]
            _bucket.append(point)
        except Exception:
            pass
        await asyncio.sleep(INTERVAL)


def start() -> None:
    global _task
    if _task is None or _task.done():
        _load_long()
        _task = asyncio.ensure_future(_loop())


def history(minutes: int = 60) -> list[dict]:
    cutoff = time.time() - minutes * 60
    fine = [p for p in HISTORY if p["t"] >= cutoff]
    if minutes <= 130:
        return fine
    # long ranges: 5-min averages for the old part, fine points for the recent
    fine_start = fine[0]["t"] if fine else time.time()
    coarse = [p for p in HISTORY_LONG if cutoff <= p["t"] < fine_start]
    return coarse + fine


def latest() -> dict | None:
    return HISTORY[-1] if HISTORY else None
