"""The per-chat agent config: what the user picked for a session (mode, model,
thinking) survives the session being reaped — leaving the app for a moment
must not reset it to the server defaults."""
import pytest

from server import chats, config, sessions


@pytest.fixture
def tmp_chats(tmp_path, monkeypatch):
    monkeypatch.setattr(chats, "CHATS_DIR", tmp_path)
    monkeypatch.setattr(config, "DEMO", False)
    return tmp_path


def test_config_set_in_a_session_survives_a_reap(tmp_chats):
    chat = chats.create()
    sess = sessions.Session(chat)
    assert sess.mode == "agent"                      # the server default
    changed = sess.set_config({"mode": "plan", "provider": "mistral",
                               "model": "mistral-medium-latest", "thinking": "high"})
    assert changed
    stored = chats.load(chat["id"])
    assert stored["agent_config"]["mode"] == "plan"
    assert stored["agent_config"]["provider"] == "mistral"
    assert stored["agent_config"]["thinking"] == "high"

    # what a reconnect after the app was closed for a moment builds
    revived = sessions.Session(stored)
    assert revived.mode == "plan" and revived.provider == "mistral"
    assert revived.model == "mistral-medium-latest" and revived.thinking == "high"


def test_a_untouched_session_still_starts_from_the_defaults(tmp_chats):
    chat = chats.create()
    sess = sessions.Session(chat)
    assert "agent_config" not in chat          # nothing written until a choice
    assert sess.mode == "agent" and sess.thinking == "off"
    assert sess.set_config({"mode": "agent"}) is False   # no change, no write
    assert "agent_config" not in chat


def test_stored_config_of_another_server_stays_valid(tmp_chats):
    chat = chats.create()
    chat["agent_config"] = {"mode": "not-a-mode", "provider": "nobody",
                            "model": "x", "workdir": "/etc", "thinking": "????"}
    sess = sessions.Session(chat)
    assert sess.mode == "agent"                 # invalid values fall back
    assert sess.provider != "nobody" and sess.thinking == "off"
