"""Hermes directory-plugin entrypoint (also importable without Telegram)."""
def register(ctx):
    from .telegram_memory_approval.plugin import register as entrypoint
    return entrypoint(ctx)
