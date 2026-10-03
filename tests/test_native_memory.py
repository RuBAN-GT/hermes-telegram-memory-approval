"""Passing native guarantees plus executable unmet requirements.

No memory behavior is mocked. Two independent real MemoryStores act as
competing Hermes writers; disk readback uses a third fresh store.
"""
import hashlib

import pytest

from tools.memory_tool import MemoryStore, apply_memory_pending
from tools.write_approval import discard_pending, get_pending, list_pending, stage_write


TARGETS = ["memory", "user"]


def disk_entries(target):
    fresh = MemoryStore(memory_char_limit=10000, user_char_limit=10000)
    fresh.load_from_disk()
    return fresh.user_entries if target == "user" else fresh.memory_entries


def memory_path(profile, target):
    return profile / "memories" / ("USER.md" if target == "user" else "MEMORY.md")


@pytest.mark.parametrize("target", TARGETS)
def test_native_pending_staging_apply_discard_roundtrip(stores, target):
    first, _ = stores
    payload = {"action": "add", "target": target, "content": "A synthetic durable fact."}
    record = stage_write("memory", payload, summary="Synthetic test", origin="foreground")
    assert get_pending("memory", record["id"])["payload"] == payload
    assert list_pending("memory") == [record]
    assert disk_entries(target) == []
    result = apply_memory_pending(payload, first)
    assert result["success"] is True
    assert disk_entries(target) == [payload["content"]]
    # Native replay is deliberately independent of consuming a pending record.
    assert get_pending("memory", record["id"]) is not None
    assert discard_pending("memory", record["id"]) is True
    assert get_pending("memory", record["id"]) is None


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("action", ["replace", "remove"])
def test_native_pin_refuses_changed_target_entry(stores, target, action):
    first, competing = stores
    original = "Synthetic entry version one."
    assert first.add(target, original)["success"]
    pin = first.resolve_entry(target, original, action)
    assert pin["matched_entry"] == original
    payload = {"action": action, "target": target, "old_text": original,
               "content": "Proposed replacement.", "matched_entry": original}
    assert competing.replace(target, original, "Synthetic entry version two.")["success"]
    result = apply_memory_pending(payload, first)
    assert result["success"] is False
    assert disk_entries(target) == ["Synthetic entry version two."]


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("action", ["replace", "remove"])
def test_legacy_unpinned_destructive_replay_is_refused(stores, target, action):
    first, _ = stores
    original = "Synthetic existing entry."
    assert first.add(target, original)["success"]
    result = apply_memory_pending({"action": action, "target": target,
                                   "old_text": original, "content": "Synthetic replacement."}, first)
    assert result["success"] is False
    assert disk_entries(target) == [original]


@pytest.mark.parametrize("target", TARGETS)
def test_native_prompt_snapshot_remains_frozen(stores, target):
    first, _ = stores
    assert first.add(target, "Synthetic initial fact.")["success"]
    first.load_from_disk()
    frozen = first.format_for_system_prompt(target)
    assert apply_memory_pending({"action": "add", "target": target,
                                 "content": "Synthetic second fact."}, first)["success"]
    assert first.format_for_system_prompt(target) == frozen
    assert disk_entries(target) == ["Synthetic initial fact.", "Synthetic second fact."]


@pytest.mark.parametrize("target", TARGETS)
@pytest.mark.parametrize("action", ["add", "replace", "remove"])
def test_unrelated_entry_changes_do_not_invalidate_approval(stores, profile, target, action):
    first, competing = stores
    original = "Synthetic original fact."
    assert first.add(target, original)["success"]
    payload = {"action": action, "target": target, "content": "Synthetic proposed fact."}
    if action != "add":
        payload.update(old_text=original, matched_entry=original)
    path = memory_path(profile, target)
    baseline = hashlib.sha256(path.read_bytes()).hexdigest()
    # The plugin has just validated its baseline. A different native writer now
    # wins the gap between that check and native replay. SQLite plugin claims do
    # not serialize a separate Hermes memory store's file lock.
    assert hashlib.sha256(path.read_bytes()).hexdigest() == baseline
    assert competing.add(target, "Synthetic concurrently saved fact.")["success"]
    assert hashlib.sha256(path.read_bytes()).hexdigest() != baseline
    result = apply_memory_pending(payload, first)
    assert result["success"] is True
    expected = [original, "Synthetic concurrently saved fact."]
    if action == "add":
        expected.append(payload["content"])
    elif action == "replace":
        expected[0] = payload["content"]
    else:
        expected.pop(0)
    assert disk_entries(target) == expected
