"""Managing chats from the phone: pin, search, delete several, export."""
import pytest

from server import chats, config


@pytest.fixture
def tmp_chats(tmp_path, monkeypatch):
    monkeypatch.setattr(chats, "CHATS_DIR", tmp_path)
    monkeypatch.setattr(config, "DEMO", False)
    return tmp_path


def _chat(title, *texts):
    c = chats.create()
    c["title"] = title
    for i, t in enumerate(texts):
        c["messages"].append({"role": "user" if i % 2 == 0 else "assistant", "content": t})
    chats.save(c)
    return c


def test_pinned_first_and_preview(tmp_chats):
    a = _chat("Disk full", "Why is the disk full?", "The Docker images take 10 GB.")
    b = _chat("Nextcloud slow", "Nextcloud is slow", "Redis was missing.")
    assert chats.list_chats()[0]["id"] == b["id"]          # newest first
    assert chats.set_pinned(a["id"], True)
    rows = chats.list_chats()
    assert rows[0]["id"] == a["id"] and rows[0]["pinned"]
    assert rows[0]["preview"] == "The Docker images take 10 GB."
    assert rows[0]["message_count"] == 2


def test_search_finds_text_inside_messages(tmp_chats):
    _chat("Disk full", "Why is the disk full?", "The Docker images take 10 GB.")
    _chat("Nextcloud slow", "Nextcloud is slow", "Redis was missing in the config.")
    rows = chats.list_chats("redis")
    assert [r["title"] for r in rows] == ["Nextcloud slow"]
    assert "Redis was missing" in rows[0]["snippet"]
    assert [r["title"] for r in chats.list_chats("disk")] == ["Disk full"]


def test_pin_and_rename_do_not_reorder_by_time(tmp_chats):
    a = _chat("first", "x")
    b = _chat("second", "y")
    before = chats.load(a["id"])["updated"]
    chats.rename(a["id"], "renamed")
    assert chats.load(a["id"])["updated"] == before
    assert chats.list_chats()[0]["id"] == b["id"]


def test_export_markdown(tmp_chats):
    c = _chat("Logs", "Show me the logs")
    c["messages"].append({"role": "assistant", "content": "Checking.",
                          "tool_calls": [{"id": "t1", "name": "run_command",
                                          "args": {"command": "docker logs web"}}]})
    c["messages"].append({"role": "tool", "tool_call_id": "t1", "content": "ERROR boom"})
    chats.save(c)
    md = chats.export_markdown(c["id"])
    assert md.startswith("# Logs\n")
    assert "**You:**" in md and "`run_command` docker logs web" in md and "ERROR boom" in md
    assert chats.export_markdown("doesnotexist") is None
