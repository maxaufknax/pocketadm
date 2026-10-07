"""The dashboard's network and disk numbers come from the host, not the app's
own container — and count every byte once."""
from server import metrics, sysinfo

NET_DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo: 80373554  169504    0    0    0     0          0         0 80373554  169504    0    0    0     0       0          0
  eno1: 2769450402 3170765    0 40302    0     0          0      1953 4021464693 3853297    0    0    0     0       0          0
wlp0s20f3:   12707      28    0    0    0     0          0         0     6357      47    0    0    0     0       0          0
   wg0: 208771536  358162    0    0    0     0          0         0 205673292  385039    0   10    0     0       0          0
br-b9f9e6e79d19: 19837457   73998    0    0    0     0          0         0 16096647   56162    0    0    0     0       0          0
docker0: 500 5 0 0 0 0 0 0 600 6 0 0 0 0 0 0
veth022caee: 700 7 0 0 0 0 0 0 800 8 0 0 0 0 0 0
"""


def test_physical_interfaces_only_when_sys_knows():
    physical = {"eno1": True, "wlp0s20f3": True, "wg0": False,
                "br-b9f9e6e79d19": False, "docker0": False, "veth022caee": False}
    rx, tx = sysinfo.parse_net_dev(NET_DEV, physical.get)
    assert rx == 2769450402 + 12707
    assert tx == 4021464693 + 6357


def test_name_filter_when_sys_is_unknown():
    # without /sys, tunnels, bridges and veths are recognised by name
    rx, tx = sysinfo.parse_net_dev(NET_DEV, lambda name: None)
    assert rx == 2769450402 + 12707
    assert tx == 4021464693 + 6357


def test_loopback_never_counts_and_garbage_is_skipped():
    text = NET_DEV + "broken line without colon\n  eth9: x y\n"
    rx, _ = sysinfo.parse_net_dev(text, lambda name: True)
    # lo is skipped even when /sys claims it is physical; eth9 is unparseable
    assert rx == sum([2769450402, 12707, 208771536, 19837457, 500, 700])


DISKSTATS = """ 259       0 nvme0n1 1000 0 2000 0 3000 0 4000 0 0 0 0
 259       1 nvme0n1p1 10 0 20 0 30 0 40 0 0 0 0
   8       0 sda 5 0 100 0 6 0 200 0 0 0 0
   8       1 sda1 5 0 100 0 6 0 200 0 0 0 0
 253       0 dm-0 900 0 1800 0 2700 0 3600 0 0 0 0
   7       0 loop0 14 0 34 0 0 0 0 0 0 0 0
"""


def test_diskstats_whole_disks_only():
    read, written = sysinfo.parse_diskstats(DISKSTATS)
    assert read == (2000 + 100) * 512
    assert written == (4000 + 200) * 512


def test_rates_never_negative(monkeypatch):
    ticks = iter([100.0, 110.0])
    monkeypatch.setattr(metrics.time, "monotonic", lambda: next(ticks))
    last, a, b = metrics._rates(None, (1000, 2000))
    assert (a, b) == (0.0, 0.0)
    # a counter that went backwards (interface gone) reads as zero
    _, a, b = metrics._rates(last, (500, 3000))
    assert a == 0.0 and b == 100.0
