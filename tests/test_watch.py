"""The watch: decides on its own whether a message is worth sending, within
guardrails that live in code — quiet hours, daily limits, mutes, pauses, no
repeats, a budget — and runs on whatever AI the user picked."""
import asyncio
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from server import activity, agents, ai, config, watch


@pytest.fixture
def w(tmp_path, monkeypatch, clean_settings):
    monkeypatch.setattr(watch, "STATE_FILE", tmp_path / "watch_state.json")
    monkeypatch.setattr(agents, "NOTIF_FILE", tmp_path / "notifications.json")
    monkeypatch.setattr(activity, "FILE", tmp_path / "activity.jsonl")
    config.settings["watch"] = {"enabled": True, "timezone": "Europe/Berlin"}
    return tmp_path


def _at(hour, minute=0, day=8):
    return datetime(2026, 10, day, hour, minute, tzinfo=ZoneInfo("Europe/Berlin")).timestamp()


def test_quiet_hours_cross_midnight(w):
    s = watch.settings()
    assert watch.in_quiet_hours(s, _at(23, 30))
    assert watch.in_quiet_hours(s, _at(3, 0))
    assert not watch.in_quiet_hours(s, _at(7, 45))
    assert not watch.in_quiet_hours(s, _at(14, 0))


def test_guardrails(w):
    s = watch.settings()
    state = {}
    noon = _at(12)
    assert watch.may_send(s, state, "info", "disk", noon) == (True, "")
    # at night only critical gets through
    assert watch.may_send(s, state, "important", "disk", _at(2)) == (False, "quiet hours")
    assert watch.may_send(s, state, "critical", "disk", _at(2))[0]
    # three info messages a day
    state["sent"] = [{"t": noon - 60 * i, "importance": "info", "topic": f"t{i}"} for i in range(3)]
    assert watch.may_send(s, state, "info", "new", noon) == (False, "daily limit")
    assert watch.may_send(s, state, "important", "new", noon)[0]
    # the same topic again within twelve hours only if it got worse
    state["sent"] = [{"t": noon - 3600, "importance": "important", "topic": "nextcloud-down"}]
    assert watch.may_send(s, state, "important", "nextcloud-down", noon) == (False, "said that already")
    assert watch.may_send(s, state, "critical", "nextcloud-down", noon)[0]
    assert watch.may_send(s, state, "important", "nextcloud-down", _at(12, day=9))[0]


def test_pause_and_mute(w):
    watch.pause(60)
    s = watch.settings()
    assert watch.may_send(s, {}, "important", "x") == (False, "paused")
    assert watch.may_send(s, {}, "critical", "x")[0]
    watch.pause(0)
    watch.mute("minecraft", 24, "stopped on purpose")
    s = watch.settings()
    assert watch.may_send(s, {}, "critical", "minecraft-down") == (False, "muted topic")
    assert watch.status()["settings"]["mutes"][0]["note"] == "stopped on purpose"


def test_settings_keep_secrets_and_validate(w):
    watch.update_settings({"matrix_token": "syt_secret", "quiet_start": "nonsense",
                           "info_per_day": "5", "budget_usd": -3, "unknown": 1})
    pub = watch.public_settings()
    assert "matrix_token" not in pub and pub["matrix_token_set"]
    assert pub["quiet_start"] == "23:00" and pub["info_per_day"] == 5 and pub["budget_usd"] == 0
    watch.update_settings({"matrix_token": ""})          # empty keeps it
    assert watch.settings()["matrix_token"] == "syt_secret"
    watch.update_settings({"matrix_token": "-"})         # "-" clears it
    assert watch.settings()["matrix_token"] == ""


def test_parse_decision_from_an_engine():
    text = ('I looked at the logs.\n'
            '{"decision": "notify", "importance": "important", "topic": "backup", '
            '"title": "Backup failed", "message": "The nightly backup failed at 02:31.", '
            '"remember": "backup target is the T5"}')
    d = watch.parse_decision(text)
    assert d == {"decision": "notify", "importance": "important", "topic": "backup",
                 "title": "Backup failed", "text": "The nightly backup failed at 02:31.",
                 "remember": "backup target is the T5"}
    assert watch.parse_decision("no json here")["decision"] == "silent"
    assert watch.parse_decision('{"decision": "maybe"}')["decision"] == "silent"


def test_clean_text_strips_markdown_and_emoji():
    assert watch.clean_text("## Heads up\n**Disk** is full 🔥\n\n\n\nfix it") == "Heads up\nDisk is full \n\nfix it".replace(" \n", "\n")


def test_events_are_bundled_and_throttled(w):
    for i in range(3):
        activity.push("containers", "docker.die", f"web exited with code 1 ({i})",
                      severity="warn", target="web", source="docker")
    activity.push("app", "pocketadm.login_failed", "Failed PocketADM sign-in",
                  severity="warn", source="pocketadm")         # not an incident
    state = watch.load_state()
    assert len(state["incidents"]) == 3

    ran = []

    async def fake_run(kind, incidents=None, force=False):
        ran.append((kind, len(incidents or [])))
        return {}
    import server.watch as wm
    orig = wm.run
    wm.run = fake_run
    try:
        first = state["first_incident_at"]
        assert asyncio.run(watch.tick(first + 30)) is None            # still collecting
        assert asyncio.run(watch.tick(first + watch.DEBOUNCE + 1)) == "incident"
        assert ran == [("incident", 3)]
        # the same container crashing again right away is not a new investigation
        activity.push("containers", "docker.die", "web exited again", severity="warn",
                      target="web", source="docker")
        assert watch.load_state()["incidents"] == []
    finally:
        wm.run = orig


def test_a_full_run_on_an_api_model(w, monkeypatch):
    config.set_keys({"mistral": "mk"})
    config.set_ai_route("watch", "mistral", "mistral-medium-latest")
    calls = []

    async def fake_stream(cfg, messages, sysprompt, tools, thinking="off"):
        calls.append(len(messages))
        assert "notify" in [t["name"] for t in tools if isinstance(t, dict)]
        assert "Write in German" in sysprompt
        if messages[-1]["role"] == "user":          # a run's first step
            yield ("tool_call", {"id": "1", "name": "run_command", "args": {"command": "df -h /"}})
            yield ("tool_call", {"id": "2", "name": "run_command", "args": {"command": "rm -rf /"}})
            yield ("usage", {"input": 1000, "output": 50})
        else:
            assert "[blocked" in messages[-1]["content"]        # rm never ran
            yield ("tool_call", {"id": "3", "name": "notify", "args": {
                "text": "## Platte\nDie Platte ist zu **91 %** voll. 🔥",
                "importance": "important", "topic": "disk-root", "title": "Platte fast voll"}})
            yield ("usage", {"input": 1200, "output": 80})

    executed = []

    async def fake_exec(name, args, workdir):
        executed.append(args["command"])
        return "/dev/sda1 100G 91G 9G 91% /"
    monkeypatch.setattr(ai, "get_stream", fake_stream)
    monkeypatch.setattr(ai, "execute_tool", fake_exec)
    monkeypatch.setattr(ai, "_persist_usage", lambda *a, **k: None)
    watch.update_settings({"lang": "de"})
    monkeypatch.setattr(watch, "in_quiet_hours", lambda s, now=None: False)
    record = asyncio.run(watch.run("observe"))
    assert record["decision"] == "notify" and record["topic"] == "disk-root"
    assert executed == ["df -h /"]
    items = agents.notifications()["items"]
    assert items[0]["title"] == "Platte fast voll"
    assert items[0]["body"] == "Platte\nDie Platte ist zu 91 % voll."
    assert items[0]["kind"] == "watch" and items[0]["importance"] == "important"
    assert any(a["kind"] == "open" and a["target"] == "storage" for a in items[0]["actions"])
    assert items[0]["steps"][0]["detail"] == "df -h /"
    # said once: the same finding an hour later is held back
    record = asyncio.run(watch.run("observe"))
    assert record["decision"] == "held" and record["reason"] == "said that already"
    st = watch.status()
    assert st["sent_today"]["important"] == 1 and st["spent_30d"] > 0


def test_no_ai_connected_is_reported_not_raised(w):
    record = asyncio.run(watch.run("test"))
    assert record["decision"] == "error" and "No AI" in record["error"]


def test_budget_stops_rounds_but_not_a_test(w, monkeypatch):
    config.set_keys({"mistral": "mk"})
    config.set_ai_route("watch", "mistral", "m")
    watch.update_settings({"budget_usd": 1.0})
    state = {"ledger": [{"t": time.time(), "cost": 1.2, "kind": "observe"}]}
    watch.save_state(state)
    assert asyncio.run(watch.run("observe"))["decision"] == "skipped"


def test_feedback_is_kept_for_the_next_run(w):
    notif = agents.add_notification("watch", "warn", "x", "y")
    st = watch.load_state()
    st["sent"] = [{"t": time.time(), "id": notif["id"], "topic": "updates-nag", "importance": "info"}]
    watch.save_state(st)
    watch.feedback(notif["id"], helpful=False)
    assert agents.notifications()["items"][0]["feedback"] == "not_helpful"
    text = asyncio.run(watch.context_text("observe", []))
    assert "updates-nag" in text


def test_threshold_events():
    events = watch.threshold_events({"disk": {"percent": 93}, "memory": {"percent": 50}})
    assert events[0]["kind"] == "disk.full" and events[0]["severity"] == "crit"
    assert watch.threshold_events({"disk": {"percent": 50}, "memory": {"percent": 50}}) == []
