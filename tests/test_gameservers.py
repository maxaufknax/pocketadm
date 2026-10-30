"""Game servers watched by the launcher's status ping: the ping itself against a
real socket, join/leave detection between two looks, nothing reported for what
was already going on at the first look, and settings that reject nonsense."""
import asyncio
import json
import struct

import pytest

from server import gameservers


def varint(n: int) -> bytes:
    return gameservers._varint(n)


def status_payload(names, online=None, version="Paper 26.1.2", motd="§aJA MAN"):
    return {
        "version": {"name": version, "protocol": 775},
        "players": {"max": 50, "online": len(names) if online is None else online,
                    "sample": [{"name": n, "id": "00000000-0000-0000-0000-000000000000"} for n in names]},
        "description": {"text": "", "extra": [{"text": motd}]},
    }


@pytest.fixture
def fake_server():
    """A tiny Minecraft status server on localhost that answers with `payload`."""
    state = {"payload": status_payload(["Alex"]), "seen_handshake": None}

    async def handle(reader, writer):
        length = await gameservers._read_varint(reader)
        state["seen_handshake"] = await reader.readexactly(length)
        await gameservers._read_varint(reader)  # status request length
        await reader.readexactly(1)
        body = json.dumps(state["payload"]).encode()
        packet = varint(0) + varint(len(body)) + body
        writer.write(varint(len(packet)) + packet)
        await writer.drain()
        writer.close()

    async def run(coro_factory):
        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            return await coro_factory(port)
        finally:
            server.close()
            await server.wait_closed()

    state["run"] = lambda f: asyncio.run(run(f))
    return state


def test_ping_reads_players_version_and_plain_motd(fake_server):
    fake_server["payload"] = status_payload(["Steve", "alex"])
    result = fake_server["run"](lambda port: gameservers.ping("127.0.0.1", port))
    assert result["online"] == 2 and result["max"] == 50
    assert result["names"] == ["alex", "Steve"]          # sorted, case-insensitive
    assert result["version"] == "Paper 26.1.2"
    assert result["motd"] == "JA MAN"                    # colour codes gone
    assert isinstance(result["latency_ms"], int)
    # handshake asked for "status" (next state 1) with the host and port given
    hs = fake_server["seen_handshake"]
    assert hs.endswith(b"\x01") and b"127.0.0.1" in hs


def test_ping_of_a_closed_port_raises():
    async def go():
        server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        server.close()
        await server.wait_closed()
        return await gameservers.ping("127.0.0.1", port, timeout=2)
    with pytest.raises(Exception):
        asyncio.run(go())


@pytest.fixture
def recorded(monkeypatch):
    """Capture what would go to the activity feed and to the phone."""
    gameservers._state.clear()
    out = {"events": [], "joins": []}
    monkeypatch.setattr(gameservers, "_event",
                        lambda kind, title, detail="", severity="info", target="": out["events"].append((kind, title)))
    def joined(server, names):
        out["joins"].append(names)
    monkeypatch.setattr(gameservers, "_joined", joined)
    yield out
    gameservers._state.clear()


SERVER = {"name": "Survival", "host": "127.0.0.1", "port": 25565, "notify": True}


def result(names):
    return gameservers.parse_status(status_payload(names))


def test_first_look_is_not_news(recorded):
    gameservers.observe(SERVER, result(["Alex", "Steve"]), now=1)
    assert recorded["joins"] == [] and recorded["events"] == []


def test_join_and_leave_between_two_looks(recorded):
    gameservers.observe(SERVER, result(["Alex"]), now=1)
    gameservers.observe(SERVER, result(["Alex", "Steve"]), now=2)
    gameservers.observe(SERVER, result(["Steve"]), now=3)
    assert recorded["joins"] == [["Steve"]]
    assert ("game.leave", "Alex hat Survival verlassen") in recorded["events"]
    over = gameservers._state["127.0.0.1:25565"]
    assert over["names"] == ["Steve"] and over["seen"]["Alex"] == 2


def test_one_missed_ping_is_not_an_outage(recorded):
    gameservers.observe(SERVER, result(["Alex"]), now=1)
    gameservers.observe(SERVER, None, now=2)
    gameservers.observe(SERVER, None, now=3)
    assert not any(k == "game.down" for k, _ in recorded["events"])
    # back before the third miss: Alex is still on, nobody "joined"
    gameservers.observe(SERVER, result(["Alex"]), now=4)
    assert recorded["joins"] == []


def test_down_after_three_misses_then_up_again(recorded):
    gameservers.observe(SERVER, result(["Alex"]), now=1)
    for t in (2, 3, 4):
        gameservers.observe(SERVER, None, now=t)
    assert ("game.down", "Survival ist offline") in recorded["events"]
    gameservers.observe(SERVER, result(["Alex"]), now=5)
    assert any(k == "game.up" for k, _ in recorded["events"])
    # everyone on a server that just came back has joined it
    assert recorded["joins"] == [["Alex"]]


def test_a_join_becomes_a_notification_and_a_push(monkeypatch):
    from server import agents, push
    gameservers._state.clear()
    notes, pushes, annotated = [], [], []
    monkeypatch.setattr(gameservers, "_event", lambda *a, **k: None)
    monkeypatch.setattr(agents, "add_notification",
                        lambda source, status, title, body, steps=None: notes.append((source, status, title, body)) or {"id": "n1"})
    monkeypatch.setattr(agents, "annotate_notification", lambda nid, **f: annotated.append((nid, f)))
    monkeypatch.setattr(push, "notify", lambda title, body, **k: pushes.append((title, k)))
    gameservers.observe(SERVER, result(["Alex"]), now=1)
    gameservers.observe(SERVER, result(["Alex", "Steve"]), now=2)
    assert notes == [("gameservers", "info", "Steve ist auf Survival", "2 von 50 online: Alex, Steve")]
    assert annotated[0][1]["kind"] == "game"
    assert pushes[0][0] == "Steve ist auf Survival" and pushes[0][1]["kind"] == "game"
    # muted server: feed only
    gameservers.observe({**SERVER, "notify": False}, result(["Alex", "Steve", "Zoe"]), now=3)
    assert len(notes) == 1 and len(pushes) == 1
    gameservers._state.clear()


def test_settings_are_validated(clean_settings, monkeypatch):
    monkeypatch.setattr(gameservers.config, "save_settings", lambda s: None)
    clean = gameservers.set_servers([{"name": "  Survival  ", "host": "172.18.0.1", "port": "25565"},
                                     {"host": "mc.example.org"}])
    assert clean == [{"name": "Survival", "host": "172.18.0.1", "port": 25565, "notify": True},
                     {"name": "mc.example.org", "host": "mc.example.org", "port": 25565, "notify": True}]
    for bad in ([{"host": "bad host; rm -rf /"}], [{"host": "a.b", "port": 70000}],
                [{"host": "a.b"}, {"host": "a.b"}], [{"host": f"h{i}.de"} for i in range(7)], "x"):
        with pytest.raises(ValueError):
            gameservers.set_servers(bad)
    assert gameservers.servers() == clean


def test_nothing_is_watched_by_default(clean_settings):
    assert gameservers.servers() == []
    assert gameservers.overview()["servers"] == []
