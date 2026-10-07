"""Host metrics read from /proc — works inside a container too, since
/proc/stat, /proc/meminfo and /proc/loadavg are not namespaced by Docker.
Disk usage prefers /host (host root mounted ro in the container) over /.
"""
import os
import re
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


# Interfaces that only carry traffic which also crosses a physical NIC (or
# never leaves the box). Counting them would double the numbers.
_VIRTUAL_NICS = ("lo", "veth", "br-", "docker", "virbr", "vnet", "wg", "tun", "tap",
                 "tailscale", "zt", "cni", "flannel", "kube", "cali", "vxlan")


def _net_dev_path() -> tuple[str, str]:
    """(/proc/net/dev of the host, root of the host's /sys).

    The app runs in a container with a network namespace of its own: its
    /proc/net/dev only counts PocketADM's own traffic, which made the
    dashboard read "42 B/s" on a busy server. The host's procfs is reachable
    under the /host mount, and pid 1 there lives in the host's namespace."""
    if os.access("/host/proc/1/net/dev", os.R_OK):
        return "/host/proc/1/net/dev", "/host"
    return "/proc/net/dev", ""


def parse_net_dev(text: str, is_physical=None) -> tuple[int, int]:
    """rx/tx byte totals of the interfaces that carry real traffic.

    `is_physical(name)` answers True/False from /sys (a device behind the
    interface) or None when that is unknown; unknown interfaces fall back to
    the name filter."""
    rx = tx = 0
    for line in text.splitlines()[2:]:
        if ":" not in line:
            continue
        name, rest = line.split(":", 1)
        name = name.strip()
        if name == "lo":
            continue
        physical = is_physical(name) if is_physical else None
        if physical is False or (physical is None and name.startswith(_VIRTUAL_NICS)):
            continue
        nums = rest.split()
        try:
            rx += int(nums[0])
            tx += int(nums[8])
        except (ValueError, IndexError):
            continue
    return rx, tx


def net_counters() -> tuple[int, int]:
    """Total rx/tx bytes of the server's real network interfaces."""
    path, root = _net_dev_path()

    def is_physical(name: str) -> bool | None:
        base = f"{root}/sys/class/net/{name}"
        if not os.path.exists(base):
            return None
        return os.path.exists(base + "/device")

    try:
        with open(path) as f:
            return parse_net_dev(f.read(), is_physical)
    except OSError:
        return 0, 0


# Whole disks only: partitions, device-mapper and RAID devices are views of
# the same sectors and would be counted twice.
_DISK_RE = re.compile(r"^(sd[a-z]+|hd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)$")


def parse_diskstats(text: str) -> tuple[int, int]:
    """Bytes read and written since boot across the physical disks."""
    read = written = 0
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 10 or not _DISK_RE.match(parts[2]):
            continue
        try:
            read += int(parts[5]) * 512
            written += int(parts[9]) * 512
        except ValueError:
            continue
    return read, written


def disk_io_counters() -> tuple[int, int]:
    """/proc/diskstats is not namespaced, so the container sees the host's."""
    try:
        with open("/proc/diskstats") as f:
            return parse_diskstats(f.read())
    except OSError:
        return 0, 0


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
