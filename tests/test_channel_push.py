"""The watch's channel (a conversation, not a list of reports) and push to the
phones: what lands where, what the watch may do when asked, and that a phone
nobody looks at hears about the assistant."""
import asyncio
import json
import time

import pytest

from server import activity, agents, ai, channel, config, push, sessions, watch


@pytest.fixture
def w(tmp_path, monkeypatch, clean_settings):
    monkeypatch.setattr(watch, "STATE_FILE", tmp_path / "watch_state.json")
    monkeypatch.setattr(agents, "NOTIF_FILE", tmp_path / "notifications.json")
    monkeypatch.setattr(activity, "FILE", tmp_path / "activity.jsonl")
    monkeypatch.setattr(channel, "CHANNEL_FILE", tmp_path / "watch_channel.json")
    monkeypatch.setattr(push, "DEVICES_FILE", tmp_path / "push_devices.json")
    channel.replying.clear()
    config.settings["watch"] = {"enabled": True, "timezone": "Europe/Berlin", "lang": "en"}
    sent = []
    monkeypatch.setattr(push, "notify", lambda title, body, **kw: sent.append((title, body, kw)))
    return sent


# ------------------------------------------------------------------ the channel

def test_channel_pages_oldest_first_and_counts_unread(w):
    a = channel.add("watch", "Disk is filling.", importance="important", topic="disk")
    b = channel.add("user", "How fast?")
    c = channel.add("watch", "About 2 GB a day.")
    page = channel.page()
    assert [m["id"] for m in page["messages"]] == [a["id"], b["id"], c["id"]]
    # writing yourself marks everything before as read; the answer is new
    assert page["unread"] == 1
    channel.mark_read()
    assert channel.page()["unread"] == 0
    assert [m["id"] for m in channel.page(after=b["t"])["messages"]] == [c["id"]]
    older = channel.page(before=c["t"], limit=1)
    assert [m["id"] for m in older["messages"]] == [b["id"]] and older["more"] is True


def test_an_upgrade_takes_the_watch_messages_along(w):
    agents.add_notification("watch", "warn", "Backup missing", "No backup since Tuesday.")
    agents.add_notification("sentinel", "info", "Digest", "A long digest nobody reads.")
    msgs = channel.page()["messages"]
    assert [m["text"] for m in msgs] == ["No backup since Tuesday."]
    assert msgs[0]["importance"] == "important" and msgs[0]["role"] == "watch"


def test_unanswered_is_what_came_after_the_watch_last_spoke(w):
    channel.add("user", "first")
    channel.add("watch", "answer")
    q1 = channel.add("user", "second")
    q2 = channel.add("user", "third")
    assert [m["id"] for m in channel.unanswered()] == [q1["id"], q2["id"]]


def test_delivered_messages_land_in_the_channel_and_on_the_phone(w):
    state, s = {}, watch.settings()
    asyncio.run(watch.deliver({"text": "**Nextcloud** is down 🔥", "detail": "502 since 14:02",
                               "importance": "critical", "topic": "nextcloud-down",
                               "title": "Nextcloud down"}, "incident", [], state, s))
    msg = channel.page()["messages"][-1]
    assert msg["text"] == "Nextcloud is down" and msg["detail"] == "502 since 14:02"
    assert msg["importance"] == "critical" and msg["kind"] == "incident"
    title, body, kw = w[-1]
    assert title == "Nextcloud down" and body == "Nextcloud is down"
    assert kw["kind"] == "watch" and kw["importance"] == "critical" and kw["data"]["message"] == msg["id"]
    assert state["sent"][-1]["message"] == msg["id"]


def test_turning_the_watch_on_greets_and_retires_the_digests(w):
    config.settings["watch"] = {"enabled": False}
    config.settings["agent_loops"] = [{"id": "a", "enabled": True, "interval_min": 360}]
    watch.update_settings({"enabled": True, "lang": "de"})
    assert config.settings["agent_loops"][0]["enabled"] is False
    first = channel.page()["messages"]
    assert len(first) == 1 and first[0]["text"].startswith("Ich behalte")
    watch.update_settings({"enabled": True})
    assert len(channel.page()["messages"]) == 1          # once, not on every save


# ------------------------------------------------------------------ answering

def _fake_stream(script):
    """ai.get_stream stand-in: each call yields the next scripted step."""
    steps = list(script)

    async def stream(cfg, messages, system, tools, *rest):
        kind, payload = steps.pop(0)
        if kind == "tool":
            yield "tool_call", payload
        else:
            yield "text", payload
        yield "usage", {"input": 10, "output": 5}
    return stream


def test_the_watch_answers_and_can_mute_when_asked(w, monkeypatch):
    monkeypatch.setattr(config, "get_ai_route", lambda f: {"provider": "mistral", "model": "m"})
    monkeypatch.setattr(watch.accounts, "usable", lambda p: True)
    monkeypatch.setattr(ai, "_cfg_for", lambda p, m: {"provider": p, "model": m})
    monkeypatch.setattr(ai, "_persist_usage", lambda *a, **k: None)
    monkeypatch.setattr(ai, "estimate_cost", lambda *a, **k: 0.001)

    async def no_context(kind, incidents):
        return "Now: CPU 3%."
    monkeypatch.setattr(watch, "context_text", no_context)
    monkeypatch.setattr(ai, "get_stream", _fake_stream([
        ("tool", {"id": "t1", "name": "mute_topic", "args": {"topic": "backup", "days": 7}}),
        ("text", "Okay — no more backup messages for a week."),
    ]))
    channel.add("user", "Stop telling me about the backups.")
    msg = asyncio.run(watch.answer())
    assert msg["role"] == "watch" and "backup" in msg["text"]
    assert watch.muted(watch.settings(), "backup")
    assert not channel.replying
    assert w[-1][2]["thread"] == "watch"


def test_the_watch_cannot_change_the_server_from_the_channel(w, monkeypatch):
    monkeypatch.setattr(config, "get_ai_route", lambda f: {"provider": "mistral", "model": "m"})
    monkeypatch.setattr(watch.accounts, "usable", lambda p: True)
    monkeypatch.setattr(ai, "_cfg_for", lambda p, m: {"provider": p, "model": m})
    monkeypatch.setattr(ai, "_persist_usage", lambda *a, **k: None)
    monkeypatch.setattr(ai, "estimate_cost", lambda *a, **k: 0.0)
    ran = []

    async def fake_exec(name, args, workdir):
        ran.append(args.get("command"))
        return "ok"

    async def no_context(kind, incidents):
        return ""
    monkeypatch.setattr(watch, "context_text", no_context)
    monkeypatch.setattr(ai, "execute_tool", fake_exec)
    monkeypatch.setattr(ai, "get_stream", _fake_stream([
        ("tool", {"id": "t1", "name": "run_command", "args": {"command": "docker restart nextcloud"}}),
        ("tool", {"id": "t2", "name": "run_command", "args": {"command": "docker ps"}}),
        ("text", "I can only look — the assistant can restart it."),
    ]))
    channel.add("user", "Restart nextcloud")
    asyncio.run(watch.answer())
    assert ran == ["docker ps"]                       # the restart never ran


def test_no_ai_says_so_in_the_channel(w, monkeypatch):
    monkeypatch.setattr(config, "get_ai_route", lambda f: {"provider": "", "model": ""})
    channel.add("user", "hello?")
    msg = asyncio.run(watch.answer())
    assert msg["role"] == "system" and "No AI" in msg["text"]


def test_an_engine_answer_may_end_with_an_action_line(w, monkeypatch):
    monkeypatch.setattr(config, "get_ai_route", lambda f: {"provider": "claude", "model": ""})
    monkeypatch.setattr(watch.accounts, "usable", lambda p: True)
    monkeypatch.setattr(watch.engines, "ENGINES", {"claude": {"label": "Claude Code"}})

    async def no_context(kind, incidents):
        return ""

    async def headless(provider, prompt, model="", mode="plan", timeout=600):
        assert mode == "plan"
        return {"text": 'Pausing until tomorrow morning.\n{"pause_hours": 8}', "steps": []}
    monkeypatch.setattr(watch, "context_text", no_context)
    monkeypatch.setattr(watch.engines, "run_headless", headless)
    channel.add("user", "Be quiet tonight")
    msg = asyncio.run(watch.answer())
    assert msg["text"] == "Pausing until tomorrow morning."
    assert watch.settings()["paused_until"] > time.time() + 7 * 3600


# ------------------------------------------------------------------ push

RID = "A" * 43


def test_devices_register_once_and_validate(w):
    d1 = push.register(RID, "Max's iPhone")
    d2 = push.register(RID, "Max's iPhone", min_importance="important")
    assert d1["id"] == d2["id"] and len(push.devices()) == 1
    assert push.devices()[0]["min"] == "important"
    with pytest.raises(ValueError):
        push.register("short")
    assert "relay_id" not in push.devices()[0]           # the capability stays on the server


def test_send_respects_preferences_and_drops_dead_phones(w, monkeypatch):
    push.register(RID, "a", min_importance="important")
    push.register("B" * 43, "b", min_importance="info", preview=False)
    posted = []

    async def fake_post(client, device, payload):
        posted.append(payload)
        return 410 if payload["relay_id"].startswith("B") else 200
    monkeypatch.setattr(push, "_post", fake_post)
    monkeypatch.setattr(config, "get_server_name", lambda: "box")
    result = asyncio.run(push.send("Disk", "Root is 93% full.", importance="info"))
    # only b wants info messages — and b was deleted from the phone
    assert [p["relay_id"][0] for p in posted] == ["B"]
    assert posted[0]["body"] == "New message from your server."     # previews off
    assert result == {"sent": 0, "failed": 0, "removed": 1}
    assert [d["name"] for d in push.devices()] == ["a"]
    posted.clear()
    asyncio.run(push.send("Down", "Nextcloud is down.", importance="critical"))
    assert posted[0]["body"] == "Nextcloud is down." and posted[0]["level"] == "time-sensitive"


def test_the_assistant_pushes_only_when_nobody_looks(w, monkeypatch, tmp_path):
    monkeypatch.setattr(sessions.chats, "save", lambda chat: None)
    s = sessions.Session({"id": "c1", "title": "Fix it", "messages": []})

    class FakeWS:
        async def send_text(self, text):
            pass
    client = sessions.Client(FakeWS())
    s.attach(client)
    s._push_device("The assistant is waiting for your OK", "docker restart web")
    assert w == []                                     # the chat is open on a phone
    client.away = True
    s._push_device("The assistant is waiting for your OK", "docker restart web")
    title, body, kw = w[-1]
    assert kw["kind"] == "assistant" and kw["thread"] == "chat-c1" and kw["data"]["chat"] == "c1"
