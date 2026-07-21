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
