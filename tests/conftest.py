"""Load real Hermes code; isolate every test in a temporary profile."""
import os
from pathlib import Path
import sys

import pytest

source = Path(os.environ["HERMES_SOURCE"]).resolve()
if not (source / "tools/memory_tool.py").is_file():
    raise RuntimeError("HERMES_SOURCE must point to a real Hermes checkout")
sys.path.insert(0, str(source))


@pytest.fixture
def profile(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    from hermes_constants import (
        get_hermes_home, set_hermes_home_override, reset_hermes_home_override,
    )
    token = set_hermes_home_override(tmp_path)
    try:
        assert get_hermes_home() == tmp_path
        yield tmp_path
    finally:
        reset_hermes_home_override(token)


@pytest.fixture
def stores(profile):
    from tools.memory_tool import MemoryStore
    first = MemoryStore(memory_char_limit=10000, user_char_limit=10000)
    second = MemoryStore(memory_char_limit=10000, user_char_limit=10000)
    first.load_from_disk()
    second.load_from_disk()
    return first, second
