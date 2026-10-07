"""The live activity feed: what the host's logs and Docker say, in one shape."""
import asyncio
import json

from server import activity, audit


def test_ssh_login_and_sudo():
    row = activity.parse_auth("2026-10-07T23:20:01.050008+00:00 stream sshd[1234]: Accepted publickey "
                              "for maxaufknax from 192.168.178.20 port 50122 ssh2: ED25519 SHA256:x")
    assert row["title"] == "maxaufknax logged in over SSH"
    assert row["detail"] == "from 192.168.178.20 (publickey)"
    assert abs(row["t"] - 1791415201.05) < 1
    row = activity.parse_auth("2026-10-07T23:29:59.141411+00:00 stream sudo: maxaufknax : "
                              "PWD=/home/maxaufknax ; USER=root ; COMMAND=/usr/bin/apt update")
    assert row["kind"] == "sudo" and row["detail"] == "apt update"


def test_failed_logins_are_aggregated_per_address():
    activity._failed = activity.FailedLogins()
    for user in ("root", "admin", "root"):
        assert activity.parse_auth(f"2026-10-07T10:00:00+00:00 h sshd[9]: Failed password for "
                                   f"invalid user {user} from 203.0.113.9 port 1 ssh2") is None
    rows = activity._failed.flush(now=2_000_000_000)
    assert len(rows) == 1
    assert rows[0]["title"] == "3 failed SSH logins from 203.0.113.9"
    assert rows[0]["detail"] == "tried: admin, root"
    # quiet period: the next burst from the same address waits
    activity.parse_auth("2026-10-07T10:01:00+00:00 h sshd[9]: Invalid user x from 203.0.113.9 port 2")
    assert activity._failed.flush(now=2_000_000_100) == []


def test_syslog_kernel_dpkg_fail2ban():
    row = activity.parse_syslog("2026-10-04T00:02:19.493042+00:00 stream systemd[1]: "
                                "ionos-dyndns.service: Failed with result 'exit-code'.")
    assert row["title"] == "ionos-dyndns failed" and row["severity"] == "warn"
    assert activity.parse_syslog("2026-10-04T00:00:00+00:00 h systemd[1]: Started cron.service.") is None
    done = activity.parse_syslog("2026-10-04T02:31:40+00:00 h systemd[1]: Finished backup.service - Nightly backup.")
    assert done and done["kind"] == "systemd.finished"
    row = activity.parse_kern("2026-10-07T13:00:00+00:00 h kernel: Out of memory: Killed process 4242 (java) "
                              "total-vm:1kB")
    assert row["severity"] == "crit" and "java" in row["title"]
    row = activity.parse_kern("2026-10-07T13:13:05+00:00 h kernel: sd 1:0:0:0: [sda] Attached SCSI disk")
    assert row["title"] == "Drive connected (sda)"
    row = activity.parse_dpkg("2026-10-07 11:20:01 upgrade openssl:amd64 3.0.13-0ubuntu3.5 3.0.13-0ubuntu3.6")
    assert row["title"] == "Package openssl upgraded" and "→" in row["detail"]
    row = activity.parse_fail2ban("2026-10-07 12:31:39,258 fail2ban.actions [1331]: NOTICE  [sshd] Ban 198.51.100.4")
    assert row["title"] == "fail2ban banned 198.51.100.4" and row["detail"] == "jail sshd"


def test_docker_events():
    die = activity.docker_event({"Type": "container", "Action": "die", "time": 1,
                                 "Actor": {"Attributes": {"name": "nextcloud", "exitCode": "1"}}})
    assert die["title"] == "nextcloud exited with code 1" and die["severity"] == "warn"
    stopped = activity.docker_event({"Type": "container", "Action": "die", "time": 1,
                                     "Actor": {"Attributes": {"name": "x", "exitCode": "143"}}})
    assert stopped["severity"] == "info"
    sick = activity.docker_event({"Type": "container", "Action": "health_status: unhealthy",
                                  "time": 1, "Actor": {"Attributes": {"name": "synapse"}}})
    assert sick["title"] == "synapse turned unhealthy"
    assert activity.docker_event({"Type": "container", "Action": "exec_start: sh -c curl",
                                  "time": 1, "Actor": {"Attributes": {"name": "x"}}}) is None
    assert activity.docker_event({"Type": "network", "Action": "connect", "time": 1}) is None
    pull = activity.docker_event({"Type": "image", "Action": "pull", "time": 1,
                                  "Actor": {"ID": "nginx:alpine"}})
    assert pull["category"] == "updates"


def test_internet_down_and_back():
    c = activity.Connectivity()
    assert c.sample(None, 100) is None and c.sample(None, 110) is None
    down = c.sample(None, 120)
    assert down["kind"] == "net.down"
    assert c.sample(None, 130) is None
    up = c.sample(12.5, 300)
    assert up["kind"] == "net.up" and "3 min" in up["title"]


def test_tail_follows_rotation(tmp_path):
    log = tmp_path / "auth.log"
    log.write_text("old line\n")
    tail = activity.Tail(str(log))
    assert tail.read_new() == []                 # starts at the end
    with log.open("a") as f:
        f.write("new one\npartial")
    assert tail.read_new() == ["new one"]
    log.unlink()
    log.write_text("after rotation\n")
    assert tail.read_new() == ["after rotation"]


def test_audit_entries_land_in_the_feed():
    audit.record("container_action", target="abc123", detail="restart")
    assert activity.EVENTS[-1]["title"] == "Container restart"
    assert activity.EVENTS[-1]["category"] == "containers"
    feed = activity.recent(limit=5, categories=["containers"])
    assert feed["events"][0]["kind"] == "pocketadm.container_action"
    assert "containers" in feed["stats"]["counts"]


def test_live_stream_delivers_new_events():
    async def run():
        gen = activity.stream(["security"])
        assert (await gen.__anext__()).startswith("retry:")
        nxt = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0)
        activity.push("containers", "x", "not for this listener")
        activity.push("security", "ssh.login", "alice logged in over SSH")
        line = await asyncio.wait_for(nxt, 2)
        await gen.aclose()
        return json.loads(line[len("data: "):])
    event = asyncio.run(run())
    assert event["title"] == "alice logged in over SSH"
