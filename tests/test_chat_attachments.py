"""0.26: attaching things to an assistant message — a picture or a file from
the phone, or context from the server (files, services, the overview).

Pictures go to models that can see them, and when a model refuses them the
turn goes on with the file's path instead of failing."""
import asyncio
import base64
import json

import pytest

from server import ai, chats, engines, sessions, uploads

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII=")


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads, "roots", lambda: (str(tmp_path / "up"), "/var/lib/pocketadm/uploads"))
    return tmp_path / "up"


def test_uploads_are_stored_named_and_typed(upload_dir):
    pic = uploads.save("../../etc/shot.png", PNG)
    assert pic["kind"] == "image" and pic["media_type"] == "image/png"
    assert pic["path"].startswith("/var/lib/pocketadm/uploads/") and pic["name"] == "shot.png"
    again = uploads.save("shot.png", PNG)
    assert again["name"] != "shot.png"                          # never overwrites
    text = uploads.save("nginx.conf", b"server { listen 80; }\n")
    assert text["kind"] == "text" and "listen 80" in text["text"]
    blob = uploads.save("dump.bin", b"\x00\x01\x02")
    assert blob["kind"] == "file" and blob["text"] == ""
    assert uploads.load_image(pic["path"])[0] == "image/png"
    assert uploads.load_image("/etc/passwd") is None            # only the uploads folder
    assert uploads.load_image(text["path"]) is None
    with pytest.raises(ValueError):
        uploads.save("big.txt", b"x" * (uploads.MAX_BYTES + 1))


def _session_with(messages):
    s = sessions.Session({"id": "att", "title": "t", "messages": messages})
    return s


def test_pictures_reach_vision_models_in_both_formats(upload_dir):
    pic = uploads.save("err.png", PNG)
    msgs = [{"role": "user", "content": "What is this error?", "images": [pic["path"]]}]
    captured = {}

    class FakeResp:
        status_code = 200

        async def aiter_lines(self):
            yield 'data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}'

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
            captured["body"] = json
            return FakeResp()

    import httpx
    orig = httpx.AsyncClient
    httpx.AsyncClient = FakeClient
    try:
        cfg = {"provider": "openrouter", "api_key": "k", "model": "m", "base_url": ""}
        out = asyncio.run(_collect(ai.stream_openai(cfg, msgs, "sys", [])))
        content = captured["body"]["messages"][1]["content"]
        assert content[0] == {"type": "text", "text": "What is this error?"}
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        # without vision: the path, said in words
        asyncio.run(_collect(ai.stream_openai(cfg, msgs, "sys", [], vision=False)))
        text = captured["body"]["messages"][1]["content"]
        assert isinstance(text, str) and pic["path"] in text and "cannot see" in text
    finally:
        httpx.AsyncClient = orig
    assert ("text", "ok") in out


async def _collect(gen):
    return [item async for item in gen]


def test_a_model_that_refuses_pictures_gets_the_path(upload_dir, monkeypatch):
    pic = uploads.save("err.png", PNG)
    calls = []

    def fake_stream(cfg, messages, sysprompt, tool_names, thinking="off", vision=True):
        calls.append(vision)

        async def gen():
            if vision:
                raise RuntimeError("API error 400: this model does not support image input")
            yield ("text", "It says the disk is full.")
            yield ("usage", {"input": 10, "output": 5})
            yield ("stop", "end")
        return gen()
    monkeypatch.setattr(ai, "get_stream", fake_stream)
    monkeypatch.setattr(ai, "_cfg_for", lambda p, m: {"provider": p, "model": m, "api_key": "k",
                                                      "base_url": ""})
    monkeypatch.setattr(sessions.chats, "save", lambda chat: None)

    async def no_map(force=False):
        return ""
    monkeypatch.setattr(sessions.servermap, "get", no_map)

    async def no_ids():
        return set()
    monkeypatch.setattr(sessions.discovery, "snapshot_ids", no_ids)
    s = _session_with([])
    s.provider, s.model, s.mode = "openrouter", "x", "chat"
    events = []

    async def capture(live=True, **event):
        events.append(event)
    s.broadcast = capture
    s._safe_broadcast = capture

    async def go():
        await s.submit_user("why?", images=[pic["path"], "/etc/shadow"],
                            attachments=[{"name": "err.png", "kind": "image"}])
        await s.run_task
    asyncio.run(go())
    assert calls == [True, False]
    assert s.messages[0]["images"] == [pic["path"]]               # /etc/shadow was dropped
    assert any(e.get("type") == "notice" for e in events)
    assert s.messages[-1]["content"] == "It says the disk is full."
    echo = next(e for e in events if e.get("type") == "user_echo")
    assert echo["attachments"] == [{"name": "err.png", "kind": "image"}]


def test_coding_agents_get_the_picture_paths(upload_dir, monkeypatch):
    monkeypatch.setattr(uploads, "agent_path", lambda p: "/host" + p)
    s = _session_with([{"role": "user", "content": "look", "images": ["/var/lib/pocketadm/uploads/a.png"]}])
    s.chat["engine_sessions"] = {"claude-code": "x"}
    prompt = engines._prompt_for(s, "claude-code")
    assert prompt.startswith("look") and "/host/var/lib/pocketadm/uploads/a.png" in prompt


def test_attached_context_shows_as_chips_not_text():
    content = ("[Attached context — provided by the user for this request]\n\nFile: /etc/hosts\n"
               "\n\n[/Attached context]\n\nWhy is this wrong?")
    events = chats.display_events([{"role": "user", "content": content,
                                    "attachments": [{"name": "hosts", "kind": "file"}]}])
    assert events == [{"t": "user", "text": "Why is this wrong?",
                       "attachments": [{"name": "hosts", "kind": "file"}]}]
    # the web app's context (no attachments list) stays as it was
    old = chats.display_events([{"role": "user", "content": "[Attached context — x]\n\nhi"}])
    assert old[0]["text"].startswith("[Attached context")


def test_upload_endpoint(upload_dir):
    from starlette.testclient import TestClient
    from server import auth, main
    c = TestClient(main.app)
    c.headers["Authorization"] = "Bearer " + auth.issue_token()
    r = c.post("/api/chat/upload?name=photo.png", content=PNG)
    assert r.status_code == 200 and r.json()["kind"] == "image"
    assert c.post("/api/chat/upload?name=empty.txt", content=b"").status_code == 400
    assert "chat_attachments" in c.get("/api/me").json()["features"]
