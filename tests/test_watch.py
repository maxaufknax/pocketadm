"""The watch: decides on its own whether a message is worth sending, within
guardrails that live in code — quiet hours, daily limits, mutes, pauses, no
repeats, a budget — and runs on whatever AI the user picked."""
import asyncio
import json
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from server import activity, agents, ai, config, probes, updates, watch


@pytest.fixture
def w(tmp_path, monkeypatch, clean_settings):
    monkeypatch.setattr(watch, "STATE_FILE", tmp_path / "watch_state.json")
    monkeypatch.setattr(agents, "NOTIF_FILE", tmp_path / "notifications.json")
    monkeypatch.setattr(activity, "FILE", tmp_path / "activity.jsonl")
    monkeypatch.setattr(updates, "SEEN_FILE", tmp_path / "updates_seen.json")

    async def no_sites():
        return []
    monkeypatch.setattr(probes, "sites", no_sites)
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
                 "resolves": False, "title": "Backup failed",
                 "text": "The nightly backup failed at 02:31.",
                 "detail": "", "remember": "backup target is the T5"}
    with_detail = text.replace('"remember"', '"detail": "exit code 1 at 02:31", "remember"')
    assert watch.parse_decision(with_detail)["detail"] == "exit code 1 at 02:31"
    assert watch.parse_decision("no json here")["decision"] == "silent"
    assert watch.parse_decision('{"decision": "maybe"}')["decision"] == "silent"


def test_clean_text_strips_markdown_and_emoji():
    assert watch.clean_text("## Heads up\n**Disk** is full 🔥\n\n\n\nfix it") == "Heads up\nDisk is full \n\nfix it".replace(" \n", "\n")


def test_events_are_bundled_and_throttled(w):
    # only events here: a round or the Sunday look back would otherwise be due
    # whenever the suite runs outside quiet hours
    config.settings["watch"].update(interval_min=0, weekly=False)
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
    # the command sees the user's time zone; the step shows what the model asked for
    assert executed == ["export TZ=Europe/Berlin; df -h /"]
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


# ------------------------------------------------------------------ crying wolf

@pytest.mark.parametrize("a,b,same", [
    ("updates-pending", "docker-image-updates-pending", True),
    ("nextcloud-update-pending", "docker-image-updates-pending", True),
    ("reverse-proxy-certificates", "reverse-proxy-certificates-dns-challenge-pending", True),
    ("backup-stud-api-missing", "backup-stud-api-fixed-on-ssd", True),
    ("dyn-dns-inactive", "dns-updater-inactive", True),
    ("fwupd-refresh-failure", "fwupd-refresh-service-failure", True),
    ("helmsman-ram", "helmsman-ram-baseline", True),
    ("nextcloud-down", "nextcloud-update-pending", False),
    ("disk-root", "disk-backup-ssd", False),
    ("studgo-down", "pocketadm-down", False),
    ("", "updates", False),
])
def test_the_same_thing_under_a_new_name(a, b, same):
    # the keys the watch made up in October 2026 for one certificate story,
    # one backup story, one update story …
    assert watch.same_topic(a, b) is same


def test_a_state_that_stays_is_mentioned_twice_a_week(w):
    s = watch.settings()
    noon = _at(12)
    state = {"sent": [{"t": noon - 3 * 86400, "importance": "important",
                       "topic": "reverse-proxy-certificates"},
                      {"t": noon - 2 * 86400, "importance": "important",
                       "topic": "reverse-proxy-certificates-fail"}]}
    assert watch.may_send(s, state, "important", "reverse-proxy-certificates-dns-challenge",
                          noon) == (False, "reminded twice this week")
    # a new incident is news again, and so is getting worse
    assert watch.may_send(s, state, "important", "certificate-renewal", noon, kind="incident")[0]
    assert watch.may_send(s, state, "critical", "certificates-expired", noon)[0]


def test_an_all_clear_is_never_a_repeat(w):
    s = watch.settings()
    noon = _at(12)
    state = {"sent": [{"t": noon - 3600, "importance": "important", "topic": "studgo-down"}]}
    assert watch.may_send(s, state, "info", "studgo-down", noon) == (False, "said that already")
    assert watch.may_send(s, state, "info", "studgo-down", noon, resolves=True) == (True, "")
    # an "all-clear" about something never reported is just another message
    state["sent"][0]["importance"] = "info"
    assert watch.may_send(s, state, "info", "studgo-down", noon, resolves=True) == \
        (False, "said that already")


def test_a_made_up_cve_never_reaches_the_user(w, monkeypatch):
    config.set_keys({"mistral": "mk"})
    config.set_ai_route("watch", "mistral", "zai-glm-5-3")
    shown = {"output": "synapse 1.140.0, update to 1.141.0 available"}

    async def fake_stream(cfg, messages, sysprompt, tools, thinking="off"):
        if messages[-1]["role"] == "user":
            yield ("tool_call", {"id": "1", "name": "run_command", "args": {"command": "docker ps"}})
        else:
            yield ("tool_call", {"id": "2", "name": "notify", "args": {
                "text": "Synapse hat ein Sicherheitsupdate (CVE-2026-12345).",
                "importance": "important", "topic": "synapse-update"}})
        yield ("usage", {"input": 10, "output": 5})

    async def fake_exec(name, args, workdir):
        return shown["output"]

    async def no_context(kind, incidents):
        return ""
    monkeypatch.setattr(ai, "get_stream", fake_stream)
    monkeypatch.setattr(ai, "execute_tool", fake_exec)
    monkeypatch.setattr(ai, "_persist_usage", lambda *a, **k: None)
    monkeypatch.setattr(watch, "context_text", no_context)
    monkeypatch.setattr(watch, "in_quiet_hours", lambda s, now=None: False)
    record = asyncio.run(watch.run("observe"))
    assert record["decision"] == "held" and record["reason"] == "unverified CVE-2026-12345"
    assert agents.notifications()["items"] == []
    shown["output"] = "Release 1.141.0 fixes CVE-2026-12345"       # now it read it somewhere
    assert asyncio.run(watch.run("observe"))["decision"] == "notify"


def test_the_all_clear_says_which_sites_and_how_long(w):
    s = {**watch.settings(), "lang": "de"}
    since = _at(12, 22)
    one = watch.all_clear([{"host": "studgo.de", "since": since, "lasted": 18 * 60}], s)
    assert one["text"] == ("studgo.de ist wieder erreichbar – der Ausfall dauerte 18 Minuten "
                           "(seit 12:22 Uhr).")
    assert one["resolves"] and one["importance"] == "info"
    many = [{"host": h, "since": since, "lasted": 65 * 60}
            for h in ("studgo.de", "pocketadm.com", "homelab.example", "cloud.homelab.example")]
    assert watch.all_clear(many, s)["text"] == (
        "studgo.de, pocketadm.com und 2 weitere sind wieder erreichbar – der Ausfall dauerte "
        "1 Stunde 5 Minuten (seit 12:22 Uhr).")


def test_an_outage_the_user_heard_about_ends_with_an_all_clear(w, monkeypatch):
    config.settings["watch"].update(interval_min=0, weekly=False, lang="de")
    up = {"ok": False}

    async def sites():
        return ["studgo.de"]

    async def check(client, host):
        return {"ok": up["ok"], "status": 200 if up["ok"] else 0, "ms": 4, "via": "IPv4",
                "error": "" if up["ok"] else "no answer within 10 seconds"}

    async def cert(host):
        return {}
    monkeypatch.setattr(probes, "sites", sites)
    monkeypatch.setattr(probes, "check_site", check)
    monkeypatch.setattr(probes, "served_cert", cert)
    investigated = []

    async def fake_run(kind, incidents=None, force=False):
        investigated.append([e["kind"] for e in incidents or []])
        st = watch.load_state()                       # the watch wrote to the user about it
        st["sites"]["studgo.de"]["announced"] = True
        watch.save_state(st)
        return {}
    monkeypatch.setattr(watch, "run", fake_run)
    t0 = time.time()
    asyncio.run(watch.tick(t0))                       # one failed visit: the internet
    assert investigated == [] and not watch.load_state().get("incidents")
    asyncio.run(watch.tick(t0 + 300))                 # two in a row: an outage
    assert investigated == [["site.down"]]
    up["ok"] = True
    asyncio.run(watch.tick(t0 + 600))
    item = agents.notifications()["items"][0]
    assert item["title"] == "Wieder erreichbar"
    assert item["body"].startswith("studgo.de ist wieder erreichbar – der Ausfall dauerte 10 Minuten")
    assert watch.load_state()["sent"][-1]["resolves"]


def test_glm_on_mistrals_api_streams_typed_chunks(monkeypatch):
    """GLM 5.3 and Magistral answer in chunks ({"type": "thinking"} and
    {"type": "text"}) — taken for a string, the watch's first run crashed."""
    lines = [
        'data: {"choices":[{"delta":{"content":[{"type":"thinking","thinking":'
        '[{"type":"text","text":"Disk first."}]}]}}]}',
        'data: {"choices":[{"delta":{"content":[{"type":"text","text":"Alles ruhig."}]}}]}',
        'data: {"choices":[{"delta":{"content":" Wirklich."},"finish_reason":"stop"}]}',
    ]

    class FakeResp:
        status_code = 200

        async def aiter_lines(self):
            for line in lines:
                yield line

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, json=None, headers=None):
            return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    async def collect():
        cfg = {"provider": "mistral", "api_key": "k", "model": "zai-glm-5-3", "base_url": ""}
        return [x async for x in ai.stream_openai(cfg, [{"role": "user", "content": "hi"}], "s", [])]
    out = asyncio.run(collect())
    assert ("thinking", "Disk first.") in out
    assert [p for k, p in out if k == "text"] == ["Alles ruhig.", " Wirklich."]


def test_cached_prompt_tokens_are_not_priced_twice(monkeypatch):
    class FakeResp:
        status_code = 200

        async def aiter_lines(self):
            yield ('data: {"choices":[],"usage":{"prompt_tokens":1000,"completion_tokens":50,'
                   '"prompt_tokens_details":{"cached_tokens":800}}}')

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, json=None, headers=None):
            return FakeResp()

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)

    async def collect():
        cfg = {"provider": "mistral", "api_key": "k", "model": "zai-glm-5-3", "base_url": ""}
        return [x async for x in ai.stream_openai(cfg, [{"role": "user", "content": "hi"}], "s", [])]
    usage = dict(asyncio.run(collect()))["usage"]
    assert usage == {"input": 200, "output": 50, "cache_read": 800}
    # GLM at 1.4 $/M in, cache reads at a tenth: 200·1.4 + 800·0.14 + 50·4.4
    assert ai.estimate_cost("mistral", "zai-glm-5-3", 200, 50, 800) == \
        pytest.approx((200 * 1.4 + 800 * 0.14 + 50 * 4.4) / 1e6)


def test_a_round_that_keeps_looking_still_ends_in_a_decision(w, monkeypatch):
    config.set_keys({"mistral": "mk"})
    config.set_ai_route("watch", "mistral", "zai-glm-5-3")
    offered, executed = [], []

    async def curious(cfg, messages, sysprompt, tools, thinking="off"):
        names = [t if isinstance(t, str) else t["name"] for t in tools]
        offered.append(names)
        if "run_command" in names:                    # it never stops looking on its own …
            yield ("tool_call", {"id": f"c{len(offered)}a", "name": "run_command",
                                 "args": {"command": "uptime"}})
            yield ("tool_call", {"id": f"c{len(offered)}b", "name": "run_command",
                                 "args": {"command": "df -h"}})
        else:                                         # … until looking is no longer offered
            yield ("tool_call", {"id": "end", "name": "stay_silent", "args": {"reason": "all fine"}})
        yield ("usage", {"input": 100, "output": 10})

    async def fake_exec(name, args, workdir):
        executed.append(args["command"])
        return "ok"

    async def no_context(kind, incidents):
        return ""
    monkeypatch.setattr(ai, "get_stream", curious)
    monkeypatch.setattr(ai, "execute_tool", fake_exec)
    monkeypatch.setattr(ai, "_persist_usage", lambda *a, **k: None)
    monkeypatch.setattr(watch, "context_text", no_context)
    record = asyncio.run(watch.run("observe"))
    assert record["decision"] == "silent" and record["reason"] == "all fine"
    assert len(offered) == watch.MAX_STEPS and "run_command" not in offered[-1]
    assert len(executed) == watch.MAX_LOOKS


def test_channel_seeding_counts_history_as_read(w, monkeypatch):
    """First open of the watch channel: the notifications that existed before
    it were already delivered — they must not arrive as one unread flood."""
    from server import channel
    monkeypatch.setattr(channel, "CHANNEL_FILE", w / "watch_channel.json")
    agents.NOTIF_FILE.write_text(json.dumps([
        {"id": "n1", "time": 100.0, "source": "watch", "status": "warn",
         "title": "old one", "body": "yesterday's story"},
        {"id": "n2", "time": 200.0, "source": "watch", "status": "warn",
         "title": "another old one", "body": "the day before"},
    ]))
    data = channel._load()
    assert len(data["messages"]) == 2
    assert data["read"] >= 200.0                  # everything seeded counts as seen
    assert channel.unread(data) == 0
    channel.add("watch", "a fresh one")
    assert channel.unread() == 1                 # only what arrives after stays unread


def test_info_round_reports_do_not_count_as_unread(w, monkeypatch):
    """A badge counts what needs attention, not every status snapshot the
    watch filed — otherwise it grows forever and gets ignored."""
    agents.NOTIF_FILE.write_text(json.dumps([
        {"id": "a", "time": 100.0, "source": "watch", "status": "info",
         "title": "Netz-Runde sauber", "body": ""},
        {"id": "b", "time": 200.0, "source": "watch", "status": "warn",
         "title": "DynDNS-Fix noetig", "body": ""},
    ]))
    config.settings["notifications_seen"] = 50.0
    page = agents.notifications()
    assert page["unseen"] == 1                    # the warning, not the report
    agents.mark_seen()
    assert agents.notifications()["unseen"] == 0
