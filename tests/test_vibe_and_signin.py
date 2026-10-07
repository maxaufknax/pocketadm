"""Mistral Vibe as a chat engine (ACP), signing the CLIs in from the phone,
and choosing which AI does what.

The CLIs are fakes that speak the real protocols: Vibe's Agent Client
Protocol (JSON-RPC over stdio, delegated browser sign-in included), Claude
Code's `setup-token` dialogue in a terminal, Codex's device login."""
import asyncio
import json
import stat
import sys
import time

import pytest

from server import accounts, ai, clis, config, engines, sessions, signin

FAKE_VIBE = r'''
import json, os, sys, time
def out(o):
    o.setdefault("jsonrpc", "2.0"); sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
def read():
    line = sys.stdin.readline()
    return json.loads(line) if line else None
home = os.environ["HOME"]
envfile = os.path.join(home, ".vibe", ".env")
def signed_in():
    try:
        return "MISTRAL_API_KEY=" in open(envfile).read()
    except OSError:
        return False
sid = None
while True:
    m = read()
    if m is None:
        break
    method, params = m.get("method"), m.get("params") or {}
    if method == "initialize":
        assert m.get("jsonrpc") == "2.0"
        delegated = ((params.get("clientCapabilities") or {}).get("_meta") or {}).get("browser-auth-delegated")
        methods = [{"id": "browser-auth", "name": "Sign in"}]
        if delegated:
            methods.append({"id": "browser-auth-delegated", "name": "Sign in elsewhere"})
        out({"id": m["id"], "result": {"protocolVersion": 1, "authMethods": methods,
             "agentCapabilities": {"loadSession": True}}})
    elif method == "authenticate":
        meta = params.get("_meta") or {}
        if meta.get("action") == "start":
            out({"id": m["id"], "result": {"_meta": {"browser-auth-delegated": {
                "attemptId": "att-1", "expiresAt": "2099-01-01T00:00:00Z",
                "signInUrl": "https://console.mistral.ai/vibe/sign-in/att-1"}}}})
        else:
            time.sleep(0.5)
            os.makedirs(os.path.dirname(envfile), exist_ok=True)
            open(envfile, "a").write("MISTRAL_API_KEY=mk-test-123\n")
            out({"id": m["id"], "result": {"_meta": {"browser-auth-delegated": {
                "attemptId": meta.get("attemptId"), "status": "completed"}}}})
    elif method in ("session/new", "session/load"):
        if not signed_in():
            out({"id": m["id"], "error": {"code": -32000, "message": "Missing API key for mistral provider."}})
            continue
        sid = params.get("sessionId") or "vibe-sess-1"
        if method == "session/load":
            out({"method": "session/update", "params": {"sessionId": sid, "update": {
                "sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "OLD HISTORY"}}}})
        modes = {"currentModeId": "ask", "availableModes": [{"id": i, "name": i} for i in
                 ("ask", "plan", "accept-edits", "auto-approve")]}
        out({"id": m["id"], "result": ({"sessionId": sid} if method == "session/new" else {}) | {"modes": modes}})
    elif method == "session/set_mode":
        open(os.path.join(home, "mode.txt"), "w").write(params["modeId"])
        out({"id": m["id"], "result": {}})
    elif method == "session/prompt":
        text = params["prompt"][0]["text"]
        def upd(u):
            out({"method": "session/update", "params": {"sessionId": sid, "update": u}})
        upd({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Looking."}})
        upd({"sessionUpdate": "plan", "entries": [{"content": "Check disk", "status": "completed", "priority": "high"},
                                                  {"content": "Report", "status": "in_progress", "priority": "low"}]})
        cmd = text.split("RUN:", 1)[1].split("|")[0].strip() if "RUN:" in text else "df -h"
        upd({"sessionUpdate": "tool_call", "toolCallId": "call-1", "title": "bash", "kind": "execute",
             "status": "pending", "rawInput": {"command": cmd}})
        out({"id": 77, "method": "session/request_permission", "params": {"sessionId": sid,
             "toolCall": {"toolCallId": "call-1"}, "options": [
                {"optionId": "allow", "name": "Allow", "kind": "allow_once"},
                {"optionId": "always", "name": "Always", "kind": "allow_always"},
                {"optionId": "reject", "name": "Reject", "kind": "reject_once"}]}})
        answer = read()
        assert answer["id"] == 77
        chosen = answer["result"]["outcome"].get("optionId")
        if chosen == "allow":
            upd({"sessionUpdate": "tool_call_update", "toolCallId": "call-1", "status": "in_progress"})
            upd({"sessionUpdate": "tool_call_update", "toolCallId": "call-1", "status": "in_progress",
                 "content": [{"type": "content", "content": {"type": "text", "text": "Filesystem 50%\n"}}]})
            upd({"sessionUpdate": "tool_call_update", "toolCallId": "call-1", "status": "completed"})
        else:
            upd({"sessionUpdate": "tool_call_update", "toolCallId": "call-1", "status": "failed",
                 "content": [{"type": "content", "content": {"type": "text", "text": "rejected"}}]})
        upd({"sessionUpdate": "tool_call", "toolCallId": "todo-1", "title": "todo", "kind": "other",
             "status": "completed", "rawInput": {"todos": [{"content": "x"}]}})
        upd({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": " Done."}})
        out({"id": m["id"], "result": {"stopReason": "end_turn",
             "usage": {"inputTokens": 321, "outputTokens": 45}}})
    elif method == "session/cancel":
        pass
'''

FAKE_CLAUDE_SETUP = r'''
import sys, time
if sys.argv[1:] == ["auth", "status", "--json"]:
    print('{"loggedIn": false, "authMethod": "none"}'); sys.exit(0)
if sys.argv[1:2] == ["auth"]:
    sys.exit(0)
assert sys.argv[1:] == ["setup-token"], sys.argv
print("\x1b[1mWelcome to Claude Code\x1b[0m")
print("Browser didn't open? Use the url below to sign in:\n")
url = "https://claude.ai/oauth/authorize?code=true&client_id=abc&response_type=code&state=s1"
print("\x1b]8;;" + url + "\x07" + url + "\x1b]8;;\x07\n")
sys.stdout.write("Paste code here if prompted > "); sys.stdout.flush()
while True:
    code = sys.stdin.readline().strip()
    if code == "good#s1":
        break
    print("Invalid code. Please try again.")
    sys.stdout.write("Paste code here if prompted > "); sys.stdout.flush()
print("\n✓ Long-lived authentication token created successfully!\n")
print("Your OAuth token (valid for 1 year):\n")
print("sk-ant-oat01-" + "Ab3_-" * 20)
print("\nStore this token securely.")
time.sleep(0.2)
'''

FAKE_CODEX_LOGIN = r'''
import sys, time
if sys.argv[1:] == ["login", "status"]:
    print("Logged in using ChatGPT"); sys.exit(0)
assert sys.argv[1:] == ["login", "--device-auth"], sys.argv
print("Follow these steps to sign in with ChatGPT:\n")
print("1. Open this link in your browser\n   https://auth.openai.com/codex/device\n")
print("2. Enter this one-time code (expires in 15 minutes)\n   ABCD-12345\n")
sys.stdout.flush()
time.sleep(1.5)
print("Successfully logged in")
'''


def _make(path, body):
    path.write_text(f"#!{sys.executable}\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def fakes(tmp_path, monkeypatch, clean_settings):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    _make(bin_dir / "vibe-acp", FAKE_VIBE)
    _make(bin_dir / "vibe", "print('vibe 2.25.4')\n")
    _make(bin_dir / "claude", FAKE_CLAUDE_SETUP)
    _make(bin_dir / "codex", FAKE_CODEX_LOGIN)
    monkeypatch.setattr(clis, "BIN_DIR", bin_dir)
    monkeypatch.setattr(clis.terminal, "PERSIST_HOME", home)
    monkeypatch.setattr(sessions.chats, "save", lambda chat: None)
    monkeypatch.setattr(sessions.audit, "record", lambda *a, **k: None)
    monkeypatch.setattr(engines.audit, "record", lambda *a, **k: None)
    monkeypatch.setattr(signin.audit, "record", lambda *a, **k: None)
    accounts._status_cache.clear()

    async def no_ids():
        return set()
    monkeypatch.setattr(sessions.discovery, "snapshot_ids", no_ids)
    return {"bin": bin_dir, "home": home}


def _sign_in_vibe(home):
    (home / ".vibe").mkdir(exist_ok=True)
    (home / ".vibe" / ".env").write_text("MISTRAL_API_KEY=mk-test\n")


def _session(mode="agent"):
    s = sessions.Session({"id": "vibe", "title": "t", "messages": []})
    s.provider, s.model, s.mode, s.workdir = "mistral-vibe", "default", mode, "/tmp"
    return s


def _turn(session, text, approve=None):
    events = []

    async def capture(live=True, **event):
        events.append(event)
        if event.get("type") == "tool_request":
            assert approve is not None, f"unexpected approval card: {event}"
            session.resolve_approval({"id": event["id"], "approved": approve})

    session.broadcast = capture
    session.messages.append({"role": "user", "content": text})

    async def go():
        session._safe_broadcast = capture
        await session._engine_cycle()
    asyncio.run(asyncio.wait_for(go(), 30))
    return events


# ------------------------------------------------------------------ Vibe engine

def test_vibe_is_an_engine_when_installed(fakes):
    assert engines.installed("mistral-vibe")
    offered = {p["provider"]: p for p in engines.providers()}
    assert offered["mistral-vibe"]["label"] == "Mistral Vibe"
    assert offered["mistral-vibe"]["signed_in"] is False
    _sign_in_vibe(fakes["home"])
    assert {p["provider"]: p for p in engines.providers()}["mistral-vibe"]["signed_in"] is True


def test_vibe_turn_streams_asks_and_records(fakes):
    _sign_in_vibe(fakes["home"])
    s = _session("agent")
    events = _turn(s, "please RUN: rm -rf /tmp/cache | thanks", approve=True)
    text = "".join(e["delta"] for e in events if e["type"] == "text")
    assert text == "Looking. Done."
    request = next(e for e in events if e["type"] == "tool_request")
    assert request["name"] == "run_command" and request["args"]["command"] == "rm -rf /tmp/cache"
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["output"] == "Filesystem 50%\n"
    # the to-do tool shows as the plan, never as a card
    assert not any(e.get("name") == "todo" for e in events)
    plan = next(e for e in events if e["type"] == "plan")
    assert [p["status"] for p in plan["items"]] == ["done", "in_progress"]
    assert s.chat["engine_sessions"]["mistral-vibe"] == "vibe-sess-1"
    # "ask" is already the session's mode: nothing to switch
    assert not (fakes["home"] / "mode.txt").exists()
    usage = next(e for e in events if e["type"] == "usage")
    assert usage["turn"]["input"] == 321 and usage["turn"]["output"] == 45


def test_vibe_read_only_command_needs_no_tap(fakes):
    _sign_in_vibe(fakes["home"])
    events = _turn(_session("agent"), "RUN: df -h", approve=None)
    start = next(e for e in events if e["type"] == "tool_start")
    assert start["auto"] == "read-only"


def test_vibe_plan_mode_refuses_changes(fakes):
    _sign_in_vibe(fakes["home"])
    s = _session("plan")
    events = _turn(s, "RUN: rm -rf /srv/data", approve=None)
    assert not any(e["type"] == "tool_request" for e in events)
    refused = next(e for e in events if e["type"] == "tool_result")
    assert "not in plan mode" in refused["output"]
    assert (fakes["home"] / "mode.txt").read_text() == "plan"


def test_vibe_resume_does_not_replay_history_into_the_chat(fakes):
    _sign_in_vibe(fakes["home"])
    s = _session("agent")
    _turn(s, "RUN: df -h")
    events = _turn(s, "again RUN: df -h")
    text = "".join(e["delta"] for e in events if e["type"] == "text")
    assert "OLD HISTORY" not in text and text == "Looking. Done."


def test_vibe_not_signed_in_says_where_to_connect(fakes):
    with pytest.raises(RuntimeError, match="AI accounts"):
        _turn(_session("agent"), "hello")


def test_headless_run_returns_text_and_steps(fakes):
    _sign_in_vibe(fakes["home"])
    result = asyncio.run(engines.run_headless("mistral-vibe", "look RUN: df -h", mode="plan"))
    assert result["text"] == "Looking. Done."
    assert result["steps"] and result["steps"][0]["detail"] == "df -h"


# ------------------------------------------------------------------ sign-in flows

def _wait(flow_id, states, timeout=20):
    async def go():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            flow = signin.get(flow_id)
            if flow and flow.state in states:
                return flow
            await asyncio.sleep(0.1)
        raise AssertionError(f"flow stuck in {signin.get(flow_id).state}: {signin.get(flow_id).error}")
    return go()


def test_mistral_sign_in_through_acp(fakes):
    async def go():
        flow = await signin.start("mistral-vibe")
        flow = await _wait(flow.id, {"waiting_browser", "failed"})
        assert flow.state == "waiting_browser", flow.error
        assert flow.url == "https://console.mistral.ai/vibe/sign-in/att-1"
        flow = await _wait(flow.id, {"done", "failed"})
        assert flow.state == "done", flow.error
    asyncio.run(go())
    assert signin.vibe_key() == "mk-test-123"
    assert engines.signed_in("mistral-vibe")


def test_claude_sign_in_with_a_pasted_code(fakes):
    async def go():
        flow = await signin.start("claude-code")
        flow = await _wait(flow.id, {"waiting_code", "failed"})
        assert flow.state == "waiting_code", flow.error
        assert flow.url.startswith("https://claude.ai/oauth/authorize?code=true")
        await signin.submit_code(flow.id, "wrong")
        flow = await _wait(flow.id, {"waiting_code"})
        await asyncio.sleep(0.5)
        assert "did not work" in signin.get(flow.id).message
        await signin.submit_code(flow.id, "good#s1")
        flow = await _wait(flow.id, {"done", "failed"})
        assert flow.state == "done", flow.error
    asyncio.run(go())
    token = config.get_engine_token("claude-code")
    assert token.startswith("sk-ant-oat01-") and len(token) == len("sk-ant-oat01-") + 100
    assert engines._env()["CLAUDE_CODE_OAUTH_TOKEN"] == token
    # the token never leaves the server
    assert token not in json.dumps(signin.get(next(iter(signin.FLOWS))).as_dict())


def test_codex_device_sign_in(fakes):
    async def go():
        flow = await signin.start("codex")
        flow = await _wait(flow.id, {"waiting_browser", "failed"})
        assert flow.url == "https://auth.openai.com/codex/device"
        assert flow.user_code == "ABCD-12345"
        flow = await _wait(flow.id, {"done", "failed"})
        assert flow.state == "done", flow.error
    asyncio.run(go())


def test_sign_out_removes_the_logins(fakes):
    _sign_in_vibe(fakes["home"])
    config.set_engine_token("claude-code", "sk-ant-oat01-" + "x" * 30)
    asyncio.run(signin.sign_out("mistral-vibe"))
    asyncio.run(signin.sign_out("claude-code"))
    assert signin.vibe_key() == "" and config.get_engine_token("claude-code") == ""


# ------------------------------------------------------------------ accounts & routes

def test_accounts_overview(fakes, monkeypatch):
    async def no_local():
        return False
    monkeypatch.setattr(accounts.localai, "available", no_local)

    async def no_plan(key):
        return "pro"
    monkeypatch.setattr(accounts, "_mistral_plan", no_plan)
    config.set_keys({"openrouter": "sk-or-x"})
    _sign_in_vibe(fakes["home"])
    data = asyncio.run(accounts.overview())
    rows = {a["id"]: a for a in data["accounts"]}
    assert rows["mistral"]["signed_in"] and rows["mistral"]["plan"] == "pro"
    assert rows["anthropic"]["cli_installed"] and not rows["anthropic"]["signed_in"]
    assert rows["openai"]["signed_in"]        # the fake codex says "Logged in using ChatGPT"
    assert rows["openrouter"]["key_set"] and rows["openrouter"]["connected"]
    assert "assistant" in data["routes"] and data["routes"]["watch"]["custom"] is False


def test_routes_fall_back_to_the_assistant(fakes):
    config.set_keys({"mistral": "mk", "openrouter": "or"})
    config.set_ai_default("openrouter", "openrouter/free")
    assert config.get_ai_route("watch") == {"provider": "openrouter", "model": "openrouter/free"}
    config.set_ai_route("watch", "mistral", "mistral-small-latest")
    assert config.get_ai_route("watch")["provider"] == "mistral"
    assert config.get_ai_route("insights")["provider"] == "openrouter"
    config.set_ai_route("watch", "", "")
    assert config.get_ai_route("watch")["provider"] == "openrouter"
    # an engine can be the assistant's default
    config.set_ai_default("mistral-vibe", "default")
    assert config.get_ai_route("assistant") == {"provider": "mistral-vibe", "model": "default"}


def test_one_shot_runs_through_an_engine_route(fakes):
    _sign_in_vibe(fakes["home"])
    config.set_ai_route("insights", "mistral-vibe", "default")
    text = asyncio.run(ai.one_shot("Explain RUN: df -h", "be brief", feature="insights"))
    assert text == "Looking. Done."
