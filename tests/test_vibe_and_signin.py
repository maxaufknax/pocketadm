"""Mistral Vibe as a chat engine (ACP), signing the CLIs in from the phone,
and choosing which AI does what.

The CLIs are fakes that speak the real protocols: Vibe's Agent Client
Protocol (JSON-RPC over stdio, delegated browser sign-in included), Claude
Code's `auth login` over pipes, Codex's device login."""
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

FAKE_CLAUDE = r'''
import json, os, sys
home = os.environ["HOME"]
creds = os.path.join(home, ".claude", ".credentials.json")
args = sys.argv[1:]
if args == ["auth", "status", "--json"]:
    if os.path.exists(creds):
        print(json.dumps({"loggedIn": True, "authMethod": "claude.ai", "subscriptionType": "max"}))
        sys.exit(0)
    print(json.dumps({"loggedIn": False, "authMethod": "none"})); sys.exit(1)
if args == ["auth", "logout"]:
    if os.path.exists(creds):
        os.remove(creds)
    sys.exit(0)
if args[:1] == ["-p"]:
    good = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").endswith("GOOD")
    print(json.dumps({"type": "result", "is_error": not good,
                      "result": "OK" if good else "Invalid bearer token"}))
    sys.exit(0 if good else 1)
assert args == ["auth", "login", "--claudeai"], args
counter = os.path.join(home, "logins")
n = int(open(counter).read()) + 1 if os.path.exists(counter) else 1
open(counter, "w").write(str(n))
state = "s%d" % n
print("Opening browser to sign in\u2026")
print("If the browser didn't open, visit: https://claude.com/cai/oauth/authorize?code=true"
      "&client_id=abc&response_type=code&redirect_uri=https%3A%2F%2Fplatform.claude.com"
      "%2Foauth%2Fcode%2Fcallback&state=" + state, flush=True)
sys.stdout.write("Paste code here if prompted > "); sys.stdout.flush()
code = sys.stdin.readline().strip()
open(os.path.join(home, "pasted"), "a").write(code + "\n")
if code == "good#" + state:
    os.makedirs(os.path.dirname(creds), exist_ok=True)
    open(creds, "w").write("{}")
    print("Login successful."); sys.exit(0)
print("Login failed: Request failed with status code 400"); sys.exit(1)
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
    _make(bin_dir / "claude", FAKE_CLAUDE)
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
    """The real failure on a phone: a code the CLI rejects. The person must
    see why, and get a fresh page — the old code was bound to the old one."""
    home = fakes["home"]

    async def go():
        flow = await signin.start("claude-code")
        flow = await _wait(flow.id, {"waiting_code", "failed"})
        assert flow.state == "waiting_code", flow.error
        assert flow.url.startswith("https://claude.com/cai/oauth/authorize?code=true")
        assert flow.url.endswith("state=s1")

        # half a code never reaches the CLI
        await signin.submit_code(flow.id, "good")
        await asyncio.sleep(0.3)
        flow = await _wait(flow.id, {"waiting_code"})
        assert "# in the middle" in flow.message
        assert not (home / "pasted").exists()

        # a code from another page is caught before it is spent
        await signin.submit_code(flow.id, "good#s7")
        await asyncio.sleep(0.3)
        flow = await _wait(flow.id, {"waiting_code"})
        assert "older sign-in page" in flow.message

        # Claude rejects a code: its reason, and a new page
        await signin.submit_code(flow.id, "bad#s1")
        await asyncio.sleep(0.3)
        flow = await _wait(flow.id, {"waiting_code"})
        assert "status code 400" in flow.message and "new now" in flow.message
        assert flow.url.endswith("state=s2") and flow.attempt == 2

        # the whole callback address works as well as the code
        await signin.submit_code(
            flow.id, " https://platform.claude.com/oauth/code/callback?code=good&state=s2 ")
        flow = await _wait(flow.id, {"done", "failed"})
        assert flow.state == "done", flow.error
        assert "(Max)" in flow.message
    config.set_engine_token("claude-code", "sk-ant-oat01-" + "old" * 10)
    asyncio.run(go())
    assert (home / "pasted").read_text().split() == ["bad#s1", "good#s2"]
    # the CLI keeps the login; a stale pasted token must not shadow it
    assert config.get_engine_token("claude-code") == ""
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in engines._env()
    assert engines.signed_in("claude-code")
    status = asyncio.run(accounts.engine_status("claude-code"))
    assert status["signed_in"] and status["plan"] == "max"


def test_claude_sign_in_with_a_setup_token(fakes):
    """`claude setup-token` run on a laptop: paste the token instead of a code."""
    async def go():
        flow = await signin.start("claude-code")
        flow = await _wait(flow.id, {"waiting_code", "failed"})
        await signin.submit_code(flow.id, "sk-ant-oat01-" + "x" * 30 + "BAD")
        await asyncio.sleep(0.3)
        flow = await _wait(flow.id, {"waiting_code"})
        assert "did not accept this token" in flow.message
        good = "sk-ant-oat01-" + "Ab3_-" * 8 + "GOOD"
        await signin.submit_code(flow.id, good)
        flow = await _wait(flow.id, {"done", "failed"})
        assert flow.state == "done", flow.error
        return good
    token = asyncio.run(go())
    assert config.get_engine_token("claude-code") == token
    assert engines._env()["CLAUDE_CODE_OAUTH_TOKEN"] == token
    # the token never leaves the server
    assert all(token not in json.dumps(f.as_dict()) for f in signin.FLOWS.values())


def test_claude_code_checks():
    url = "https://claude.com/cai/oauth/authorize?code=true&state=abc"
    assert signin.claude_code_problem("x#abc", url) == ""
    assert "older" in signin.claude_code_problem("x#zzz", url)
    assert "part of the code" in signin.claude_code_problem("xabc", url)
    assert "API key" in signin.claude_code_problem("sk-ant-api03-" + "k" * 30, url)
    assert signin.normalize_claude_code(' "a b#c" ') == "ab#c"
    assert signin.normalize_claude_code(
        "https://platform.claude.com/oauth/code/callback?code=A1&state=S2") == "A1#S2"
    # Ink draws spaces as cursor moves: messages stay readable
    assert signin.clean("Login\x1b[1Cfailed:\x1b[2Cno") == "Login failed:  no"


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
    creds = fakes["home"] / ".claude" / ".credentials.json"
    creds.parent.mkdir(parents=True, exist_ok=True)
    creds.write_text("{}")
    asyncio.run(signin.sign_out("claude-code"))
    assert signin.vibe_key() == "" and config.get_engine_token("claude-code") == ""
    assert not creds.exists()


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


def test_removing_a_key_really_removes_it(fakes, monkeypatch):
    """"Remove key" used to store its "-" as the key: the provider stayed
    connected and could not be removed again."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config.set_keys({"openai": "sk-test-123"})
    assert "openai" in config.configured_providers()
    config.set_keys({"openai": "-"})
    assert config.get_key("openai") == ""
    assert "openai" not in config.settings["ai_keys"]
    assert "openai" not in config.configured_providers()
    # a placeholder an older version left behind does not count as a key
    config.settings["ai_keys"]["openai"] = "-"
    assert config.get_key("openai") == "" and "openai" not in config.configured_providers()
    # "" keeps what is stored
    config.set_keys({"mistral": "mk-1"})
    config.set_keys({"mistral": ""})
    assert config.get_key("mistral") == "mk-1"
    # a key from the environment is reported as such (the app cannot remove it)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    assert config.key_from_env("openai") and config.get_key("openai") == "sk-env"


def test_codex_not_logged_in_is_not_signed_in(fakes, monkeypatch):
    _make(fakes["bin"] / "codex", "import sys\nprint('Not logged in')\n")
    accounts._status_cache.clear()
    status = asyncio.run(accounts.engine_status("codex"))
    assert status["installed"] and not status["signed_in"]


# ------------------------------------------------------------------ Vibe models

_MISTRAL_LIST = [
    {"id": "mistral-medium-latest", "name": "mistral-medium-latest",
     "aliases": ["mistral-medium-3.5", "mistral-vibe-cli-latest"],
     "capabilities": {"completion_chat": True, "function_calling": True}},
    {"id": "mistral-vibe-cli-latest", "name": "mistral-medium-latest", "aliases": [],
     "capabilities": {"completion_chat": True, "function_calling": True}},
    {"id": "mistral-small-2603", "name": "mistral-small-2603", "aliases": ["mistral-small-latest"],
     "capabilities": {"completion_chat": True, "function_calling": True}},
    {"id": "zai-glm-5-3", "name": "zai-glm-5-3", "aliases": ["zai-glm-latest"],
     "capabilities": {"completion_chat": True, "function_calling": True, "reasoning": True}},
    {"id": "mistral-embed", "name": "mistral-embed", "aliases": [],
     "capabilities": {"completion_chat": False, "function_calling": False}},
    {"id": "voxtral-small-2507", "name": "voxtral-small-2507", "aliases": [],
     "capabilities": {"completion_chat": True, "function_calling": True}},
    {"id": "labs-leanstral-1-5", "name": "labs-leanstral-1-5", "aliases": [],
     "capabilities": {"completion_chat": True, "function_calling": True}},
]


def test_mistral_model_rows():
    from server import mistral_models as mm
    rows = mm.chat_models(_MISTRAL_LIST)
    assert [r["id"] for r in rows] == ["mistral-medium-latest", "zai-glm-5-3", "mistral-small-2603"]
    assert rows[1]["name"] == "GLM 5.3" and rows[2]["name"] == "Mistral Small 4"
    assert mm.display_name("mistral-large-4") == "Mistral Large 4"
    assert mm.display_name("ministral-14b-2512") == "Ministral 14B (25.12)"
    assert mm.display_name("devstral-small-2512") == "Devstral Small 2"
    assert mm.covers(rows, "zai-glm-latest") and not mm.covers(rows, "mistral-large-4")


def test_vibe_offers_login_models_and_key_models(fakes, monkeypatch):
    from server import mistral_models
    _sign_in_vibe(fakes["home"])
    config.set_keys({"mistral": "api-key"})
    login_rows = mistral_models.chat_models(_MISTRAL_LIST[:3])        # no GLM on the login
    key_rows = mistral_models.chat_models(_MISTRAL_LIST)

    async def fake_fetch(key, force=False):
        return login_rows if key == "mk-test" else key_rows
    monkeypatch.setattr(mistral_models, "fetch", fake_fetch)
    rows = asyncio.run(engines.vibe_models())
    ids = [r["id"] for r in rows]
    assert ids == ["default", "mistral-small-2603", "key:zai-glm-5-3"]
    assert rows[2]["billing"] == "api" and "billed" in rows[2]["hint"]
    entry = next(e for e in asyncio.run(engines.providers_live()) if e["provider"] == "mistral-vibe")
    assert [m["id"] for m in entry["models"]] == ids


def test_vibe_env_picks_the_model_and_the_key():
    assert engines.vibe_env("default") == {} and engines.vibe_env("default", "high") == {}
    env = engines.vibe_env("mistral-small-2603", "high")
    models = json.loads(env["VIBE_MODELS"])
    assert env["VIBE_ACTIVE_MODEL"] == "mistral-small-2603" and models[0]["thinking"] == "high"
    assert "MISTRAL_API_KEY" not in env
    # a model that cannot reason is never sent a reasoning effort
    assert "thinking" not in json.loads(engines.vibe_env("codestral-2508", "high")["VIBE_MODELS"])[0]
    config.settings.setdefault("ai_keys", {})["mistral"] = "api-key-9"
    env = engines.vibe_env("key:zai-glm-5-3")
    assert env["VIBE_ACTIVE_MODEL"] == "zai-glm-5-3" and env["MISTRAL_API_KEY"] == "api-key-9"
    config.settings["ai_keys"].pop("mistral")
    with pytest.raises(RuntimeError):
        engines.vibe_env("key:zai-glm-5-3")


def test_vibe_runs_the_chosen_model_and_restarts_on_a_new_one(fakes, monkeypatch):
    _sign_in_vibe(fakes["home"])
    seen = []
    real_spawn = engines._spawn

    async def spy(argv, cwd, extra_env=None):
        seen.append(dict(extra_env or {}))
        return await real_spawn(argv, cwd, extra_env)
    monkeypatch.setattr(engines, "_spawn", spy)
    s = _session()
    s.model = "mistral-small-2603"
    _turn(s, "one RUN: echo hi|")
    assert seen[-1]["VIBE_ACTIVE_MODEL"] == "mistral-small-2603"
    first = s.chat["engine_sessions"]["mistral-vibe"]
    assert s.messages[-1]["by"] == "Mistral Vibe · Mistral Small 4"
    # same model: the session is resumed
    _turn(s, "two RUN: echo hi|")
    assert s.chat["engine_models"]["mistral-vibe"] == "mistral-small-2603"
    # another model: a fresh session that gets the conversation as a transcript
    s.model = "default"
    _turn(s, "three RUN: echo hi|")
    assert seen[-1] == {} and s.chat["engine_models"]["mistral-vibe"] == "default"
    assert s.chat["engine_sessions"]["mistral-vibe"] == first     # the fake reuses its id
    assert ai.answered_by("mistral-vibe", "key:zai-glm-5-3") == "Mistral Vibe · GLM 5.3 (API key)"
    assert ai.answered_by("openrouter", "openrouter/free") == "OpenRouter · free router"
