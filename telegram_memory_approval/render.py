"""Plain exact entry text; conservative UTF-16 limits, no Markdown parser."""
def parts(payload):
    if payload["action"] == "batch":
        ops = payload["operations"]
        text = f"Атомарный пакет: {len(ops)} изменений. Решение применяется ко всему пакету.\n"
        for index, op in enumerate(ops, 1):
            text += f"\nОперация {index}/{len(ops)}\n" + _entry_text(op) + "\n"
    else:
        text = _entry_text(payload)
    chunks, chunk, units = [], '', 0
    for char in text:
        size = 2 if ord(char) > 0xffff else 1
        if units + size > 3500:
            chunks.append(chunk)
            chunk, units = '', 0
        chunk += char
        units += size
    if chunk:
        chunks.append(chunk)
    return chunks


def _entry_text(payload):
    action = payload["action"]
    label = {"add": "Добавить", "replace": "Заменить", "patch": "Изменить фрагмент", "remove": "Удалить"}[action]
    target = "USER.md" if payload["target"] == "user" else "MEMORY.md"
    text = f"Изменение памяти: {label} → {target}\n"
    if action != "add":
        text += "БЫЛО (полная запись):\n" + payload["matched_entry"] + "\n"
    if action != "remove":
        after = (payload["matched_entry"].replace(payload["old_text"], payload["content"], 1)
                 if action == "patch" else payload["content"])
        text += "СТАНЕТ (полная запись):\n" + after
    return text
