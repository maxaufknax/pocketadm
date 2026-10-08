"""Claude Code and Codex as chat engines (server/engines.py).

The CLIs are replaced by small fakes that speak the same machine protocols —
Claude Code's stream-json with control requests, Codex's app-server JSON-RPC —
so the whole round trip runs here: streaming text, permission questions that
become approval cards on the phone, PocketADM's own read-only/egress rules on
top of the CLI's, plans, session resume, usage, and the errors a user meets
when a CLI is not signed in yet.
"""
import asyncio
import json
import stat
import sys

import pytest

from server import clis, config, engines, sessions

FAKE_CLAUDE = r'''
import json, sys
argv = sys.argv[1:]
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
def read():
    line = sys.stdin.readline()
    return json.loads(line) if line else None
first = read()
prompt = first["message"]["content"]
sid = "sess-resumed" if "--resume" in argv else "sess-new"
out({"type": "system", "subtype": "init", "session_id": sid})
if "LOGGED_OUT" in prompt:
    out({"type": "result", "subtype": "success", "is_error": True,
         "result": "Not logged in - Please run /login", "session_id": sid})
    sys.stdin.read(); sys.exit(0)
for piece in ("Checking ", "the box."):
    out({"type": "stream_event", "parent_tool_use_id": None,
         "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": piece}}})
tools = []
if "RUN:" in prompt:
    tools.append(("Bash", {"command": prompt.split("RUN:", 1)[1].split("|")[0].strip()}))
if "EDIT" in prompt:
    tools.append(("Edit", {"file_path": "/host/etc/x.conf", "old_string": "a=1", "new_string": "a=2"}))
if "TODO" in prompt:
    tools.append(("TodoWrite", {"todos": [
        {"content": "Look at the disk", "status": "completed", "activeForm": "Looking"},
        {"content": "Clean up", "status": "in_progress", "activeForm": "Cleaning"}]}))
if "EXITPLAN" in prompt:
    tools.append(("ExitPlanMode", {"plan": "1. Rotate the logs"}))
out({"type": "assistant", "parent_tool_use_id": None, "message": {"content":
     [{"type": "text", "text": "Checking the box."}] +
     [{"type": "tool_use", "id": f"toolu_{i}", "name": n, "input": a} for i, (n, a) in enumerate(tools)]}})
# a sub-agent's inner chatter must not reach the chat
out({"type": "assistant", "parent_tool_use_id": "toolu_x", "message": {"content": [{"type": "text", "text": "SUBAGENT"}]}})
for i, (name, args) in enumerate(tools):
    tid = f"toolu_{i}"
    out({"type": "control_request", "request_id": f"r{i}", "request": {
        "subtype": "can_use_tool", "tool_name": name, "input": args, "tool_use_id": tid}})
    answer = read()["response"]["response"]
    if answer["behavior"] == "allow":
        out({"type": "user", "parent_tool_use_id": None, "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": [{"type": "text", "text": "ran " + name}]}]}})
    else:
        out({"type": "user", "parent_tool_use_id": None, "message": {"content": [
            {"type": "tool_result", "tool_use_id": tid, "content": answer["message"], "is_error": True}]}})
        if answer.get("interrupt"):
            break
out({"type": "result", "subtype": "success", "is_error": False, "result": "done", "session_id": sid,
     "usage": {"input_tokens": 100, "output_tokens": 20, "cache_read_input_tokens": 5,
               "cache_creation_input_tokens": 1}, "total_cost_usd": 0.01})
sys.stdin.read()
'''

FAKE_CODEX = r'''
import json, sys
def out(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
def read():
    line = sys.stdin.readline()
    return json.loads(line) if line else None
thread = "th-new"
while True:
    m = read()
    if m is None:
        break
    method = m.get("method")
    if method == "initialize":
        assert m["params"]["clientInfo"]["name"] == "pocketadm"
        out({"id": m["id"], "result": {"userAgent": "fake-codex"}})
    elif method in ("thread/start", "thread/resume"):
        thread = m["params"].get("threadId", "th-new")
        assert m["params"]["sandbox"] == "danger-full-access"
        out({"id": m["id"], "result": {"thread": {"id": thread}, "approvalPolicy": m["params"]["approvalPolicy"]}})
    elif method == "turn/start":
        prompt = m["params"]["input"][0]["text"]
        out({"id": m["id"], "result": {"turn": {"id": "tu-1", "status": "inProgress", "items": []}}})
        out({"method": "turn/started", "params": {"threadId": thread, "turn": {"id": "tu-1"}}})
        if "UNAUTH" in prompt:
            out({"method": "error", "params": {"error": {"message": "401 Unauthorized"}, "willRetry": False,
                                              "threadId": thread, "turnId": "tu-1"}})
            out({"method": "turn/completed", "params": {"threadId": thread, "turn": {
                "id": "tu-1", "status": "failed", "error": {"message": "401 Unauthorized"}}}})
            continue
        out({"method": "item/agentMessage/delta", "params": {"delta": "On it.", "itemId": "m1",
                                                            "threadId": thread, "turnId": "tu-1"}})
        cmd = prompt.split("RUN:", 1)[1].strip() if "RUN:" in prompt else "ls"
        out({"id": 900, "method": "item/commandExecution/requestApproval", "params": {
            "itemId": "c1", "threadId": thread, "turnId": "tu-1", "command": cmd, "startedAtMs": 1}})
        decision = read()["result"]["decision"]
        if decision == "accept":
            out({"method": "item/started", "params": {"item": {"type": "commandExecution", "id": "c1",
                 "command": cmd, "status": "inProgress"}, "threadId": thread, "turnId": "tu-1", "startedAtMs": 1}})
            out({"method": "item/completed", "params": {"item": {"type": "commandExecution", "id": "c1",
                 "command": cmd, "status": "completed", "aggregatedOutput": "hello\n", "exitCode": 0},
                 "threadId": thread, "turnId": "tu-1", "completedAtMs": 2}})
        else:
            out({"method": "item/completed", "params": {"item": {"type": "commandExecution", "id": "c1",
                 "command": cmd, "status": "declined"}, "threadId": thread, "turnId": "tu-1", "completedAtMs": 2}})
        out({"method": "turn/plan/updated", "params": {"threadId": thread, "turnId": "tu-1", "plan": [
            {"step": "Check", "status": "completed"}, {"step": "Fix", "status": "inProgress"}]}})
        out({"method": "item/completed", "params": {"item": {"type": "agentMessage", "id": "m1", "text": "On it."},
             "threadId": thread, "turnId": "tu-1", "completedAtMs": 3}})
        out({"method": "thread/tokenUsage/updated", "params": {"threadId": thread, "turnId": "tu-1", "tokenUsage": {
            "total": {"inputTokens": 1500, "outputTokens": 70, "cachedInputTokens": 10},
            "last": {"inputTokens": 500, "outputTokens": 70, "cachedInputTokens": 10}}}})
        out({"method": "turn/completed", "params": {"threadId": thread, "turn": {"id": "tu-1", "status": "completed"}}})
    elif method == "turn/interrupt":
        out({"id": m["id"], "result": {}})
'''


@pytest.fixture
def fake_clis(tmp_path, monkeypatch, clean_settings):
    for name, body in (("claude", FAKE_CLAUDE), ("codex", FAKE_CODEX)):
        path = tmp_path / name
        path.write_text(f"#!{sys.executable}\n" + body)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(clis, "BIN_DIR", tmp_path)
    monkeypatch.setattr(sessions.chats, "save", lambda chat: None)
    monkeypatch.setattr(sessions.audit, "record", lambda *a, **k: None)
    monkeypatch.setattr(engines.audit, "record", lambda *a, **k: None)

    async def no_ids():
        return set()
    monkeypatch.setattr(sessions.discovery, "snapshot_ids", no_ids)
    return tmp_path


def _session(engine: str, mode: str = "agent", workdir: str = "/tmp") -> sessions.Session:
    s = sessions.Session({"id": "eng", "title": "t", "messages": []})
    s.provider, s.model, s.mode, s.workdir = engine, "default", mode, workdir
    return s


def _turn(session, text: str, approve=None):
    """Run one engine turn; returns the broadcast events. `approve` answers
    every approval card (None = there must be none)."""
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


def _types(events):
    return [e["type"] for e in events]


# ------------------------------------------------------------------ catalog

def test_only_installed_engines_are_offered(fake_clis):
    offered = {p["provider"]: p for p in engines.providers()}
    assert set(offered) == {"claude-code", "codex"}
    assert offered["claude-code"]["agent"] is True and offered["claude-code"]["label"] == "Claude Code"
    (fake_clis / "codex").unlink()
    assert [p["provider"] for p in engines.providers()] == ["claude-code"]


def test_api_lists_engines_and_counts_as_configured(fake_clis, monkeypatch):
    from starlette.testclient import TestClient
    from server import ai, auth, main

    async def no_models():
        return []
    monkeypatch.setattr(ai, "list_models", no_models)
    monkeypatch.setattr(config, "DEMO", False)
    client = TestClient(main.app)
    headers = {"Authorization": "Bearer " + auth.issue_token()}
    providers = client.get("/api/ai/models", headers=headers).json()["providers"]
    assert {p["provider"] for p in providers} == {"claude-code", "codex"}
    assert client.get("/api/me", headers=headers).json()["ai_configured"] is True


def test_a_new_chat_defaults_to_an_engine_without_api_keys(fake_clis):
    s = sessions.Session({"id": "x", "title": "t", "messages": []})
    assert s.provider == "claude-code" and s.model == "default"


# ------------------------------------------------------------------ Claude Code

def test_claude_streams_text_and_remembers_its_session(fake_clis):
    s = _session("claude-code")
    events = _turn(s, "hello")
    assert "".join(e["delta"] for e in events if e["type"] == "text") == "Checking the box."
    assert s.chat["engine_sessions"]["claude-code"] == "sess-new"
    assert s.messages[-1] == {"role": "assistant", "content": "Checking the box.", "tool_calls": [],
                              "by": "Claude Code"}
    usage = next(e for e in events if e["type"] == "usage")
    assert usage["turn"]["input"] == 106 and usage["turn"]["output"] == 20
    assert usage["turn"]["cost"] is None, "a subscription has no per-token price"
    assert not any("SUBAGENT" in json.dumps(e) for e in events)


def test_claude_resumes_its_session_next_turn(fake_clis):
    s = _session("claude-code")
    _turn(s, "first")
    _turn(s, "second")
    assert s.chat["engine_sessions"]["claude-code"] == "sess-resumed"


def test_claude_asks_before_a_change_and_runs_it_when_approved(fake_clis):
    s = _session("claude-code")
    events = _turn(s, "RUN: rm -rf /tmp/old", approve=True)
    req = next(e for e in events if e["type"] == "tool_request")
    assert req["name"] == "run_command" and req["args"]["command"] == "rm -rf /tmp/old"
    assert _types(events).index("tool_start") > _types(events).index("tool_request")
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["output"] == "ran Bash"
    assert any(m.get("role") == "tool" for m in s.messages)


def test_claude_denied_change_does_not_run(fake_clis):
    s = _session("claude-code")
    events = _turn(s, "RUN: rm -rf /tmp/old", approve=False)
    assert "tool_start" not in _types(events)
    assert any(e["type"] == "tool_result" and e["output"] == "[denied]" for e in events)


def test_claude_read_only_command_runs_without_a_tap(fake_clis):
    s = _session("claude-code")
    events = _turn(s, "RUN: df -h")
    start = next(e for e in events if e["type"] == "tool_start")
    assert start["auto"] == "read-only"


def test_claude_internet_access_asks_first(fake_clis):
    s = _session("claude-code")
    events = _turn(s, "RUN: curl https://example.com", approve=False)
    assert any(e["type"] == "tool_request" for e in events)


def test_claude_edit_shows_a_diff(fake_clis):
    s = _session("claude-code")
    events = _turn(s, "EDIT", approve=True)
    diff = next(e for e in events if e["type"] == "tool_result")["diff"]
    assert diff["path"] == "/host/etc/x.conf" and diff["added"] == 1 and diff["removed"] == 1


def test_claude_plan_mode_refuses_changes_without_asking(fake_clis):
    s = _session("claude-code", mode="plan")
    events = _turn(s, "EDIT")
    assert "tool_request" not in _types(events) and "tool_start" not in _types(events)


def test_claude_plan_mode_shows_the_plan_and_stops(fake_clis):
    s = _session("claude-code", mode="plan")
    events = _turn(s, "EXITPLAN")
    text = "".join(e["delta"] for e in events if e["type"] == "text")
    assert "1. Rotate the logs" in text
    assert "tool_request" not in _types(events)


def test_claude_todo_list_becomes_the_plan_panel(fake_clis):
    s = _session("claude-code")
    events = _turn(s, "TODO")
    plan = next(e for e in events if e["type"] == "plan")["items"]
    assert plan == [{"title": "Look at the disk", "status": "done"},
                    {"title": "Clean up", "status": "in_progress"}]
    assert "tool_start" not in _types(events), "the to-do list is a panel, not a tool card"


def test_claude_auto_mode_never_asks(fake_clis):
    s = _session("claude-code", mode="auto")
    events = _turn(s, "RUN: rm -rf /tmp/old")
    assert "tool_request" not in _types(events) and "tool_start" in _types(events)


def test_claude_not_signed_in_says_what_to_do(fake_clis):
    s = _session("claude-code")
    with pytest.raises(RuntimeError) as exc:
        _turn(s, "LOGGED_OUT")
    assert "run `claude` once" in str(exc.value)


def test_switching_engines_carries_the_conversation_over(fake_clis):
    s = _session("claude-code")
    s.messages.extend([{"role": "user", "content": "Why is the disk full?"},
                       {"role": "assistant", "content": "Docker images use 40 GB.", "tool_calls": []}])
    s.messages.append({"role": "user", "content": "Clean them up"})
    prompt = engines._prompt_for(s, "claude-code")
    assert "Earlier in this conversation" in prompt and "Docker images use 40 GB." in prompt
    assert prompt.endswith("Clean them up")
    s.chat["engine_sessions"] = {"claude-code": "sess-1"}
    assert engines._prompt_for(s, "claude-code") == "Clean them up"


# ------------------------------------------------------------------ Codex

def test_codex_turn_with_an_approved_command(fake_clis):
    s = _session("codex")
    events = _turn(s, "RUN: rm -rf /tmp/old", approve=True)
    assert "".join(e["delta"] for e in events if e["type"] == "text") == "On it."
    assert s.chat["engine_sessions"]["codex"] == "th-new"
    result = next(e for e in events if e["type"] == "tool_result")
    assert result["output"].startswith("hello")
    plan = next(e for e in events if e["type"] == "plan")["items"]
    assert plan == [{"title": "Check", "status": "done"}, {"title": "Fix", "status": "in_progress"}]
    usage = next(e for e in events if e["type"] == "usage")["turn"]
    assert usage["input"] == 510 and usage["output"] == 70, "the turn's share, not the thread total"


def test_codex_declined_command(fake_clis):
    s = _session("codex")
    events = _turn(s, "RUN: rm -rf /tmp/old", approve=False)
    assert "tool_start" not in _types(events)
    assert any(e["type"] == "tool_result" and e["output"] == "[denied]" for e in events)


def test_codex_read_only_command_is_approved_automatically(fake_clis):
    s = _session("codex")
    events = _turn(s, "RUN: ls -la")
    assert "tool_request" not in _types(events)
    assert next(e for e in events if e["type"] == "tool_start")["auto"] == "read-only"


def test_codex_resumes_its_thread(fake_clis):
    s = _session("codex")
    s.chat["engine_sessions"] = {"codex": "th-7"}
    _turn(s, "RUN: ls")
    assert s.chat["engine_sessions"]["codex"] == "th-7"


def test_codex_not_signed_in_says_what_to_do(fake_clis):
    s = _session("codex")
    with pytest.raises(RuntimeError) as exc:
        _turn(s, "UNAUTH")
    assert "codex login" in str(exc.value)


def test_engine_missing_is_a_clear_error(fake_clis):
    (fake_clis / "codex").unlink()
    s = _session("codex")
    with pytest.raises(RuntimeError) as exc:
        _turn(s, "hi")
    assert "not installed" in str(exc.value)


@pytest.mark.parametrize("message", [
    "Not logged in · Please run /login",
    "Failed to authenticate: OAuth session expired and could not be refreshed",
    "401 Unauthorized",
    "Invalid API key · Please run /login",
])
def test_every_flavour_of_signed_out_gets_the_sign_in_hint(message):
    """Real wording seen from Claude Code 2.1 in a container whose login had
    expired; the user should read what to do, not the CLI's internals."""
    assert engines._looks_logged_out(message)


def test_ordinary_failures_are_not_mistaken_for_a_login_problem():
    assert not engines._looks_logged_out("Error: max turns reached")
