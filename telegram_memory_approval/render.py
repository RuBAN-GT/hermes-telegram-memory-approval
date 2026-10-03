"""Plain exact entry text; conservative UTF-16 limits, no Markdown parser."""
def parts(payload):
    action = payload["action"]
    label = {"add": "Добавить", "replace": "Заменить", "remove": "Удалить"}[action]
    target = "USER.md" if payload["target"] == "user" else "MEMORY.md"
    text = f"Изменение памяти: {label} → {target}\n"
    if action != "add":
        text += "БЫЛО (полная запись):\n" + payload["matched_entry"] + "\n"
    if action != "remove":
        text += "СТАНЕТ (полная запись):\n" + payload["content"]
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
