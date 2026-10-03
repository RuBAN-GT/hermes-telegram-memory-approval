"""Only public Hermes memory and pending APIs; no core mutations."""
import hashlib
import json
import re


def fingerprint(record):
    return hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def validate(record):
    if not isinstance(record, dict) or not re.fullmatch(r"[a-f0-9]{8}", str(record.get("id", ""))):
        raise ValueError("Invalid native pending ID")
    if record.get("subsystem") != "memory":
        raise ValueError("Not a native memory proposal")
    p = record.get("payload")
    if not isinstance(p, dict):
        raise ValueError("Invalid payload")
    if p.get("action") == "batch":
        ops = p.get("operations")
        if set(p) - {"action", "target", "operations"} or not isinstance(ops, list) or len(ops) != 1 or not isinstance(ops[0], dict):
            raise ValueError("Only one-entry batches supported; multi-entry atomic batches require native review")
        p = dict(ops[0], target=p.get("target"))
        if "new_text" in p:
            p["content"] = p.get("content") or p.pop("new_text")
            p.pop("new_text", None)
    if p.get("action") not in {"add", "replace", "remove"} or "operations" in p:
        raise ValueError("Only single operations supported")
    if p.get("target") not in {"memory", "user"}:
        raise ValueError("Unknown target")
    if set(p) - {"action", "target", "content", "old_text", "matched_entry"}:
        raise ValueError("Unknown payload fields")
    if p["action"] in {"add", "replace"}:
        text = p.get("content")
        if not isinstance(text, str) or not text or text != text.strip() or "§" in text:
            raise ValueError("Content must be nonempty canonical native entry text without separator §")
    if p["action"] in {"replace", "remove"}:
        if not isinstance(p.get("matched_entry"), str) or not p["matched_entry"]:
            raise ValueError("Destructive proposals require a native matched_entry pin")
    return p


class NativeBridge:
    def enabled(self):
        from tools.write_approval import write_approval_enabled
        return write_approval_enabled("memory")

    def pending(self):
        from tools.write_approval import list_pending
        return list_pending("memory")

    def get(self, pending_id):
        from tools.write_approval import get_pending
        return get_pending("memory", pending_id)

    def apply(self, payload):
        from tools.memory_tool import apply_memory_pending, load_on_disk_store
        return apply_memory_pending(payload, load_on_disk_store())
