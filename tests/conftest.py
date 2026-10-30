"""Shared test fixtures.

Point HELMSMAN_DATA at a throwaway directory *before* anything imports
``server.config`` (which reads settings + creates its data dir at import time),
so the suite never touches a real instance's ``/data`` or the repo's ``./data``.
"""
import os
import tempfile

# Must happen before the first `import server.config`, i.e. before test modules
# are collected. conftest.py is imported first, so this is the right place.
_TMP = tempfile.mkdtemp(prefix="pocketadm-tests-")
os.environ["HELMSMAN_DATA"] = _TMP
os.environ.pop("ADMIN_PASSWORD", None)  # let tests drive the password explicitly
# Provider keys from the developer's own environment (e.g. MISTRAL_API_KEY
# in a local shell) would leak into the engine defaults and key handling:
# the suite must run the same on a laptop as in CI.
for _k in ("AI_API_KEY", "AI_PROVIDER", "AI_MODEL", "AI_BASE_URL",
           "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "OPENROUTER_API_KEY",
           "MISTRAL_API_KEY", "GEMINI_API_KEY"):
    os.environ.pop(_k, None)

import pytest

from server import config


@pytest.fixture
def clean_settings():
    """Give a test an isolated, empty settings dict and restore it afterwards.

    ``server.config.settings`` is a process-global that several modules mutate
    (integrations, auth generation, TOTP …). Swapping in a fresh dict keeps
    tests from leaking state into one another.
    """
    saved = config.settings
    config.settings = {}
    try:
        yield config.settings
    finally:
        config.settings = saved
