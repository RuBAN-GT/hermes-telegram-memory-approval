"""Supported native Telegram factory and supervised plugin lifecycle."""
import fcntl
from .bridge import NativeBridge
from .service import PATTERN, Service
from .store import Store


def register(ctx):
    from hermes_constants import get_hermes_home
    bridge = NativeBridge()
    if not bridge.enabled():
        raise RuntimeError("Enable native memory.write_approval before loading this plugin")
    owner = ctx.get_config('owner_user_id')
    chat = ctx.get_config('fallback_chat_id')
    topic = ctx.get_config('fallback_topic_id', None)
    interval = ctx.get_config('poll_seconds', 5)
    if type(owner) is not int or owner <= 0 or type(chat) is not int or chat == 0:
        raise ValueError("Explicit integer owner_user_id and fallback_chat_id required")
    if topic is not None and (type(topic) is not int or topic <= 0):
        raise ValueError("fallback_topic_id must be a positive integer or null (General)")
    if type(interval) not in (int, float) or not 1 <= interval <= 300:
        raise ValueError("poll_seconds must be between 1 and 300")
    path = get_hermes_home() / 'plugin-state' / 'telegram-memory-approval'
    path.mkdir(parents=True, exist_ok=True, mode=0o700)

    def wire(application, adapter):
        from telegram.ext import CallbackQueryHandler
        # A pending record has no authenticated chat/topic. Never infer origin from
        # its human-readable summary or global process environment: use fallback.
        lock = (path / 'runtime.lock').open('a')
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            lock.close()
            raise RuntimeError("Another approval transport owns this profile")
        store = Store(path / 'proposals.sqlite3')
        store.recover()
        service = Service(bridge, store, application.bot, owner, chat, topic,
                          ready=lambda: adapter.is_connected and application.running)
        handler = CallbackQueryHandler(service.callback, pattern=PATTERN)
        application.add_handler(handler)
        task = ctx.spawn_task(service.run(interval), name='telegram-memory-approval:poll')

        def close():
            task.cancel()
            application.remove_handler(handler)
            store.close()
            lock.close()
        ctx.on_unload(close)

    ctx.register_telegram_handler(wire)
