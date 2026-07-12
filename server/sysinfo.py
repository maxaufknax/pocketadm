"""Host metrics read from /proc — works inside a container too, since
/proc/stat, /proc/meminfo and /proc/loadavg are not namespaced by Docker.
Disk usage prefers /host (host root mounted ro in the container) over /.
"""
import os
import shutil
import time

_last_cpu: tuple[float, float] | None = None  # (busy, total)


def _read_cpu() -> tuple[float, float]:
    with open("/proc/stat") as f:
        parts = f.readline().split()[1:]
    nums = [float(x) for x in parts]
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)  # idle + iowait
    total = sum(nums)
    return total - idle, total


def cpu_percent() -> float:
    global _last_cpu
    busy, total = _read_cpu()
    if _last_cpu is None:
        _last_cpu = (busy, total)
        time.sleep(0.15)
        busy, total = _read_cpu()
    pb, pt = _last_cpu
    _last_cpu = (busy, total)
    dt = total - pt
    return round(100.0 * (busy - pb) / dt, 1) if dt > 0 else 0.0


def memory() -> dict:
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            key, val = line.split(":", 1)
            info[key] = int(val.strip().split()[0]) * 1024  # kB -> bytes
    total = info.get("MemTotal", 0)
    avail = info.get("MemAvailable", 0)
    return {"total": total, "used": total - avail, "available": avail,
            "percent": round(100.0 * (total - avail) / total, 1) if total else 0.0}


def disk() -> dict:
    root = "/host" if os.path.isdir("/host") else "/"
    du = shutil.disk_usage(root)
    return {"total": du.total, "used": du.used, "free": du.free,
            "percent": round(100.0 * du.used / du.total, 1) if du.total else 0.0}


def loadavg() -> list[float]:
    with open("/proc/loadavg") as f:
        return [float(x) for x in f.read().split()[:3]]


def uptime_seconds() -> float:
    with open("/proc/uptime") as f:
        return float(f.read().split()[0])


def net_counters() -> tuple[int, int]:
    """Total rx/tx bytes across all non-loopback, non-virtual interfaces.
    /proc/net/dev is host-wide inside the container (net ns not shared, but
    the container's eth0 mirrors all Helmsman traffic; when running natively
    this covers real NICs)."""
    rx = tx = 0
    try:
        with open("/proc/net/dev") as f:
            for line in f.readlines()[2:]:
                name, rest = line.split(":", 1)
                name = name.strip()
                if name == "lo" or name.startswith(("veth", "br-", "docker")):
                    continue
                nums = rest.split()
                rx += int(nums[0])
                tx += int(nums[8])
    except (OSError, ValueError, IndexError):
        pass
    return rx, tx


def snapshot_light() -> dict:
    """Cheap snapshot for the metrics collector (no hostname/disk stat spam)."""
    return {
        "cpu_percent": cpu_percent(),
        "memory_percent": memory()["percent"],
        "disk_percent": disk()["percent"],
        "load1": loadavg()[0],
    }


def hostname() -> str:
    for path in ("/host/etc/hostname",):
        if os.path.exists(path):
            with open(path) as f:
                return f.read().strip()
    return os.environ.get("HELMSMAN_HOSTNAME") or os.uname().nodename


def snapshot() -> dict:
    return {
        "hostname": hostname(),
        "cpu_percent": cpu_percent(),
        "cpu_count": os.cpu_count(),
        "memory": memory(),
        "disk": disk(),
        "load": loadavg(),
        "uptime": uptime_seconds(),
        "time": time.time(),
    }
