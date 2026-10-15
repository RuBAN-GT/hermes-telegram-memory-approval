"""Fake Telegram I/O, real PTB handlers and real native memory/pending files."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from tools.write_approval import stage_write
from tools.memory_tool import MemoryStore
from telegram_memory_approval.bridge import NativeBridge, fingerprint
from telegram_memory_approval.service import Service
from telegram_memory_approval.store import Store
from test_native_memory import disk_entries


class Bot:
    def __init__(self, fail_at=None):
        self.messages = []
        self.fail_at = fail_at

    async def send_message(self, **kwargs):
        self.messages.append(kwargs)
        if len(self.messages) == self.fail_at:
            raise TimeoutError("Send result unknown")
        return NS(message_id=len(self.messages))


class Query:
    def __init__(self, token, action='save', owner=42, chat=-100, topic=7, message=2):
        self.data = f'hma:{token}:{action}'
        self.from_user = NS(id=owner)
        self.message = NS(chat=NS(id=chat), message_thread_id=topic, message_id=message)
        self.answers = []
        self.answer_options = []
        self.edits = []

    async def answer(self, text=None, **kwargs):
        self.answers.append(text)
        self.answer_options.append(kwargs)

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)


@pytest.fixture
def setup(profile):
    # Actual config in a disposable HERMES_HOME. No core gate mock.
    (profile / 'config.yaml').write_text('memory:\n  write_approval: true\n  memory_char_limit: 20000\n  user_char_limit: 20000\nplugins:\n  entries:\n    telegram-memory-approval:\n      settings:\n        owner_user_id: 42\n        fallback_chat_id: -100\n        fallback_topic_id: 7\n')
    store = Store(profile / 'approval' / 'test.sqlite3')
    bot = Bot()
    service = Service(NativeBridge(), store, bot, 42, -100, 7)
    assert service.bridge.enabled()
    yield service, bot, store
    store.close()


def stage(action='add', target='memory', content='Exact proposed fact.', matched=None):
    p = dict(action=action, target=target)
    if action != 'remove':
        p['content'] = content
    if matched is not None:
        p.update(old_text=matched, matched_entry=matched)
    return stage_write('memory', p, summary='not used as authorization', origin='foreground')


def token(store):
    return store.db.execute('SELECT token FROM proposals').fetchone()[0]


def click(service, q):
    asyncio.run(service.callback(NS(callback_query=q), None))
    assert all(not options.get('show_alert', False) for options in q.answer_options)


@pytest.mark.parametrize('target', ['memory', 'user'])
@pytest.mark.parametrize('action', ['add', 'replace', 'remove'])
def test_transport_native_roundtrip_and_no_replay(setup, target, action):
    service, bot, store = setup
    native = MemoryStore(20000, 20000)
    native.load_from_disk()
    if action != 'add':
        assert native.add(target, 'Original full entry.')['success']
    record = stage(action, target, matched='Original full entry.' if action != 'add' else None)
    asyncio.run(service.scan())
    assert len(bot.messages) == 2
    assert bot.messages[0]['parse_mode'] is None
    buttons = bot.messages[-1]['reply_markup'].inline_keyboard[0]
    assert [b.text for b in buttons] == ['✅ Сохранить', '❌ Отклонить']
    q = Query(token(store))
    click(service, q)
    assert store.get(token(store))['state'] == 'saved'
    assert service.bridge.get(record['id']) is None
    assert q.answers == [None]
    assert q.edits == [dict(text='✅ Сохранено', parse_mode=None, reply_markup=None)]
    expected = [] if action == 'remove' else ['Exact proposed fact.']
    assert disk_entries(target) == expected
    click(service, q)
    asyncio.run(service.scan())
    assert disk_entries(target) == expected
    assert len(bot.messages) == 2


@pytest.mark.parametrize('override', [dict(owner=43), dict(chat=-101), dict(topic=8), dict(message=1)])
def test_nonowner_cross_topic_wrong_card(setup, override):
    service, bot, store = setup
    stage()
    asyncio.run(service.scan())
    click(service, Query(token(store), **override))
    assert disk_entries('memory') == []
    assert store.get(token(store))['state'] == 'active'


def test_reject_is_terminal(setup):
    service, bot, store = setup
    record = stage()
    asyncio.run(service.scan())
    q = Query(token(store), action='reject')
    click(service, q)
    assert q.answers == [None]
    assert q.edits == [dict(text='❌ Отклонено', parse_mode=None, reply_markup=None)]
    click(service, Query(token(store)))
    assert store.get(token(store))['state'] == 'rejected'
    assert service.bridge.get(record['id']) is None
    assert disk_entries('memory') == []


def test_expired_callback_still_updates_terminal_card(setup):
    service, bot, store = setup
    stage()
    asyncio.run(service.scan())
    q = Query(token(store))
    async def expired_answer(*args, **kwargs):
        raise TimeoutError('Callback expired')
    q.answer = expired_answer
    click(service, q)
    assert store.get(token(store))['state'] == 'saved'
    assert q.edits == [dict(text='✅ Сохранено', parse_mode=None, reply_markup=None)]
    assert len(bot.messages) == 2
    assert disk_entries('memory') == ['Exact proposed fact.']


def test_payload_rewrite_invalidates_card(setup, profile):
    service, bot, store = setup
    record = stage()
    asyncio.run(service.scan())
    record['payload']['content'] = 'Unapproved rewritten content.'
    (profile / 'pending' / 'memory' / (record['id']+'.json')).write_text(json.dumps(record))
    click(service, Query(token(store)))
    assert store.get(token(store))['state'] == 'invalid'
    assert disk_entries('memory') == []


@pytest.mark.parametrize('action', ['replace', 'remove'])
def test_changed_target_refused_other_entry_allowed(setup, action):
    service, bot, store = setup
    native = MemoryStore(20000, 20000)
    native.load_from_disk()
    native.add('memory', 'Original.')
    stage(action, matched='Original.')
    asyncio.run(service.scan())
    native.replace('memory', 'Original.', 'Changed.')
    click(service, Query(token(store)))
    assert store.get(token(store))['state'] == 'refused'
    assert disk_entries('memory') == ['Changed.']


def test_unrelated_writer_does_not_invalidate_card(setup):
    service, bot, store = setup
    native = MemoryStore(20000, 20000)
    native.load_from_disk()
    native.add('memory', 'Original.')
    stage('replace', matched='Original.')
    asyncio.run(service.scan())
    native.add('memory', 'Independent.')
    click(service, Query(token(store)))
    assert disk_entries('memory') == ['Exact proposed fact.', 'Independent.']


def test_full_unicode_chunks_before_activation(setup):
    service, bot, store = setup
    text = '🦊<b>literal</b>\n' * 650
    text = text.strip()
    stage(content=text)
    asyncio.run(service.scan())
    displayed = ''.join(m['text'] for m in bot.messages[:-1])
    assert displayed.endswith(text)
    assert all('reply_markup' not in m for m in bot.messages[:-1])
    assert all(len(m['text'].encode('utf-16-le')) // 2 <= 3500 for m in bot.messages[:-1])
    assert store.get(token(store))['state'] == 'active'


@pytest.mark.parametrize('fail_at', [1, 2])
def test_uncertain_send_never_resends_or_activates(setup, fail_at):
    service, bot, store = setup
    bot.fail_at = fail_at
    stage()
    asyncio.run(service.scan())
    sent = len(bot.messages)
    assert store.get(token(store))['state'] == 'uncertain'
    store.recover()
    asyncio.run(service.scan())
    click(service, Query(token(store)))
    assert len(bot.messages) == sent
    assert disk_entries('memory') == []


@pytest.mark.parametrize('state', ['sending', 'applying', 'active'])
def test_restart_recovery_no_duplicates(setup, state):
    service, bot, store = setup
    record = stage()
    t = store.insert(record, fingerprint(record), 42, -100, 7)
    if state != 'sending':
        store.transition(t, 'sending', state, message=2)
    store.recover()
    asyncio.run(service.scan())
    assert not bot.messages
    assert store.get(t)['state'] == ('active' if state == 'active' else 'uncertain')


def test_malformed_batch_and_legacy_fail_closed(setup):
    service, bot, store = setup
    stage('replace')  # missing pin
    stage_write('memory', {'action':'batch', 'target':'memory', 'operations':[{'action':'add','content':'x'}, {'action':'remove','old_text':'y'}]}, summary='', origin='foreground')
    asyncio.run(service.scan())
    assert bot.messages == []
    assert disk_entries('memory') == []


def test_cross_connection_atomic_claim(setup, profile):
    service, bot, store = setup
    record = stage()
    t = store.insert(record, fingerprint(record), 42, -100, 7)
    store.transition(t, 'sending', 'active', message=2)
    other = Store(profile / 'approval' / 'test.sqlite3')
    assert store.transition(t, 'active', 'applying')
    assert not other.transition(t, 'active', 'applying')
    other.close()




def test_unknown_apply_outcome_never_replays(setup, monkeypatch):
    service, bot, store = setup
    stage()
    asyncio.run(service.scan())
    real_apply = service.bridge.apply
    calls = []
    def uncertain(payload):
        calls.append(payload)
        assert real_apply(payload)['success']
        raise OSError('Crash-like error after durable native write')
    monkeypatch.setattr(service.bridge, 'apply', uncertain)
    click(service, Query(token(store)))
    assert store.get(token(store))['state'] == 'uncertain'
    click(service, Query(token(store)))
    store.recover()
    asyncio.run(service.scan())
    assert len(calls) == 1
    assert disk_entries('memory') == ['Exact proposed fact.']


def test_reopen_sqlite_keeps_active_card_and_no_duplicate(setup, profile):
    service, bot, store = setup
    stage()
    asyncio.run(service.scan())
    t = token(store)
    reopened = Store(profile / 'approval' / 'test.sqlite3')
    reopened.recover()
    fresh = Service(NativeBridge(), reopened, bot, 42, -100, 7)
    asyncio.run(fresh.scan())
    assert len(bot.messages) == 2
    click(fresh, Query(t))
    assert disk_entries('memory') == ['Exact proposed fact.']
    reopened.close()


def test_real_directory_loader_context_factory_and_lifecycle(setup):
    service, bot, store = setup
    from hermes_cli.plugins import PluginContext, PluginManager
    from hermes_cli.plugins_manifest import PluginManifest
    root = Path(__file__).resolve().parents[1]
    manifest = PluginManifest(name='telegram-memory-approval', path=str(root), source='user')
    manager = PluginManager()
    module = manager._load_directory_module(manifest)
    ctx = PluginContext(manifest, manager)
    module.register(ctx)
    factory, name = manager.get_platform_handler_factories('telegram')[0]
    assert name == 'telegram-memory-approval'
    class App:
        def __init__(self):
            self.bot, self.handlers = bot, []
        def add_handler(self, handler):
            self.handlers.append(handler)
        def remove_handler(self, handler):
            self.handlers.remove(handler)
    app = App()
    async def run():
        factory(app, NS())
        assert len(app.handlers) == 1
        assert app.handlers[0].pattern.pattern.startswith('^hma:')
        # Exercise real PluginContext unload callbacks and task cancellation.
        registrations = list(manager._registration_order)
        for handle in reversed(registrations):
            handle.dispose()
        await asyncio.sleep(0)
        assert app.handlers == []
    asyncio.run(run())


@pytest.mark.parametrize('action', ['add', 'replace', 'remove'])
def test_one_entry_batch_native_replay(setup, action):
    service, bot, store = setup
    from tools.memory_tool import memory_tool, load_on_disk_store
    if action != 'add':
        result = load_on_disk_store().add('memory', 'Original pinned entry')
        assert result['success']
    if action == 'remove': assert load_on_disk_store().add('memory', 'Unrelated remaining entry')['success']
    op = {'action': action}
    if action != 'remove': op['new_text'] = 'Batch single entry'
    if action != 'add': op['old_text'] = 'Original pinned'
    if action == 'replace':
        # Legacy replace proposals still replay, but current Hermes no longer
        # lets the model create them. Exercise the public staging/replay API.
        op['matched_entry'] = 'Original pinned entry'
        result = {'pending_id': stage_write('memory', dict(action='batch', target='memory', operations=[op]), summary='', origin='foreground')['id']}
    else:
        result = json.loads(memory_tool(target='memory', operations=[op], store=load_on_disk_store()))
    assert 'pending_id' in result, result
    asyncio.run(service.scan())
    assert len(bot.messages) == 2
    assert 'Batch single entry' in bot.messages[0]['text'] if action != 'remove' else 'Original pinned entry' in bot.messages[0]['text']
    click(service, Query(token(store)))
    assert store.get(token(store))['state'] == 'saved'
    assert disk_entries('memory') == (['Unrelated remaining entry'] if action == 'remove' else ['Batch single entry'])


def test_unready_transport_does_not_claim_pending(setup):
    service, bot, store = setup
    stage()
    service.ready = lambda: False
    asyncio.run(service.scan())
    assert not bot.messages
    assert store.db.execute('select count(*) from proposals').fetchone()[0] == 0
    service.ready = lambda: True
    asyncio.run(service.scan())
    assert len(bot.messages) == 2
    assert store.get(token(store))['state'] == 'active'


@pytest.mark.parametrize('incoming_topic', [None, 1])
def test_general_topic_accepts_telegram_thread_one(setup, incoming_topic):
    service, bot, store = setup
    service.topic = None
    stage()
    asyncio.run(service.scan())
    click(service, Query(token(store), topic=incoming_topic))
    assert store.get(token(store))['state'] == 'saved'


def test_cancelled_delivery_is_quarantined(setup):
    service, bot, store = setup
    stage()
    async def run():
        sending = asyncio.Event()
        async def blocked_send(**kwargs):
            sending.set()
            await asyncio.Future()
        bot.send_message = blocked_send
        task = asyncio.create_task(service.scan())
        await sending.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert store.get(token(store))['state'] == 'uncertain'
    asyncio.run(service.scan())
    assert not bot.messages


def test_unload_keeps_lock_until_cancelled_send_finishes(setup):
    import fcntl
    from telegram_memory_approval import plugin
    service, bot, store = setup
    callbacks = []
    factories = []
    ctx = NS(get_config=lambda key, default=None: {
        'owner_user_id': 42, 'fallback_chat_id': -100, 'fallback_topic_id': 7,
    }.get(key, default), register_telegram_handler=factories.append,
        spawn_task=lambda coro, **kwargs: asyncio.create_task(coro),
        on_unload=callbacks.append)
    plugin.register(ctx)
    stage()
    class App:
        running = True
        def __init__(self):
            self.bot, self.handlers = bot, []
        def add_handler(self, handler):
            self.handlers.append(handler)
        def remove_handler(self, handler):
            self.handlers.remove(handler)
    async def run():
        sending = asyncio.Event()
        cancelled = asyncio.Event()
        finish = asyncio.Event()
        async def blocked_send(**kwargs):
            sending.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                cancelled.set()
                await finish.wait()
                raise
        bot.send_message = blocked_send
        app = App()
        factories[0](app, NS(is_connected=True))
        await sending.wait()
        callbacks[0]()
        await cancelled.wait()
        assert not app.handlers
        from hermes_constants import get_hermes_home
        path = get_hermes_home() / 'plugin-state' / 'telegram-memory-approval'
        with (path / 'runtime.lock').open('a') as lock:
            with pytest.raises(BlockingIOError):
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finish.set()
            # Drain cancellation, then the task's resource-release callback.
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        reopened = Store(path / 'proposals.sqlite3')
        try:
            assert reopened.get(token(reopened))['state'] == 'uncertain'
        finally:
            reopened.close()
    asyncio.run(run())


def test_factory_failure_releases_profile_lock(setup):
    from telegram_memory_approval import plugin
    factories = []
    callbacks = []
    fail = [True]
    def spawn(coro, **kwargs):
        if fail[0]:
            raise RuntimeError('Cannot spawn')
        return asyncio.create_task(coro)
    ctx = NS(get_config=lambda key, default=None: {
        'owner_user_id': 42, 'fallback_chat_id': -100,
    }.get(key, default), register_telegram_handler=factories.append,
        spawn_task=spawn, on_unload=callbacks.append)
    plugin.register(ctx)
    class App:
        bot = setup[1]
        running = False
        def __init__(self):
            self.handlers = []
        def add_handler(self, handler):
            self.handlers.append(handler)
        def remove_handler(self, handler):
            self.handlers.remove(handler)
    async def run():
        app = App()
        with pytest.raises(RuntimeError, match='Cannot spawn'):
            factories[0](app, NS(is_connected=False))
        assert not app.handlers
        fail[0] = False
        factories[0](app, NS(is_connected=False))
        callbacks[0]()
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    asyncio.run(run())


@pytest.mark.skipif(not hasattr(MemoryStore, 'patch'), reason='Hermes predates span patches')
@pytest.mark.parametrize('target', ['memory', 'user'])
@pytest.mark.parametrize('batch', [False, True])
@pytest.mark.parametrize('decision', ['save', 'reject'])
def test_real_background_patch_waits_for_owner(setup, target, batch, decision):
    from tools.memory_tool import memory_tool, load_on_disk_store
    from tools.skill_provenance import set_current_write_origin, reset_current_write_origin
    service, bot, store = setup
    before = 'Context: old value. Keep the rest 🦊.'
    after = 'Context: new value. Keep the rest 🦊.'
    assert load_on_disk_store().add(target, before)['success']
    origin = set_current_write_origin('background_review')
    try:
        op = dict(action='patch', old_text=' old ', content=' new ')
        kwargs = dict(operations=[op]) if batch else op
        result = json.loads(memory_tool(target=target, store=load_on_disk_store(), **kwargs))
    finally:
        reset_current_write_origin(origin)
    assert result.get('staged'), result
    assert disk_entries(target) == [before]
    asyncio.run(service.scan())
    displayed = ''.join(m['text'] for m in bot.messages[:-1])
    assert f'БЫЛО (полная запись):\n{before}' in displayed
    assert f'СТАНЕТ (полная запись):\n{after}' in displayed
    click(service, Query(token(store), owner=43))
    assert disk_entries(target) == [before]
    click(service, Query(token(store), action=decision))
    assert disk_entries(target) == ([after] if decision == 'save' else [before])
    assert store.get(token(store))['state'] == ('saved' if decision == 'save' else 'rejected')


@pytest.mark.skipif(not hasattr(MemoryStore, 'patch'), reason='Hermes predates span patches')
@pytest.mark.parametrize('target', ['memory', 'user'])
@pytest.mark.parametrize('outcome', ['save', 'reject', 'stale'])
def test_background_atomic_batch_is_one_decision(setup, target, outcome, monkeypatch):
    from tools.memory_tool import memory_tool, load_on_disk_store
    from tools.skill_provenance import set_current_write_origin, reset_current_write_origin
    service, bot, store = setup
    native = load_on_disk_store()
    assert native.add(target, 'Old fact. Keep context.')['success']
    assert native.add(target, 'Obsolete fact.')['success']
    ops = [dict(action='patch', old_text='Old', new_text='New'),
           dict(action='remove', old_text='Obsolete'),
           dict(action='add', content='Additional fact.')]
    origin = set_current_write_origin('background_review')
    try:
        result = json.loads(memory_tool(target=target, operations=ops, store=load_on_disk_store()))
    finally:
        reset_current_write_origin(origin)
    assert result.get('staged'), result
    assert disk_entries(target) == ['Old fact. Keep context.', 'Obsolete fact.']
    asyncio.run(service.scan())
    displayed = ''.join(m['text'] for m in bot.messages[:-1])
    for text in ['Атомарный пакет: 3', 'New fact. Keep context.', 'Obsolete fact.', 'Additional fact.']:
        assert text in displayed
    assert sum('reply_markup' in m for m in bot.messages) == 1
    calls = []
    apply = service.bridge.apply
    def track(payload):
        calls.append(payload)
        return apply(payload)
    monkeypatch.setattr(service.bridge, 'apply', track)
    if outcome == 'stale':
        assert native.replace(target, 'Obsolete', 'Changed independently.')['success']
    else:
        assert native.add(target, 'Independent fact.')['success']
    q = Query(token(store), action='reject' if outcome == 'reject' else 'save', message=len(bot.messages))
    click(service, q)
    click(service, q)
    assert len(calls) == (0 if outcome == 'reject' else 1)
    if calls:
        assert calls[0]['action'] == 'batch'
        assert len(calls[0]['operations']) == 3
    expected = {
        'save': ['New fact. Keep context.', 'Independent fact.', 'Additional fact.'],
        'reject': ['Old fact. Keep context.', 'Obsolete fact.', 'Independent fact.'],
        'stale': ['Old fact. Keep context.', 'Changed independently.'],
    }
    assert disk_entries(target) == expected[outcome]
    assert store.get(token(store))['state'] == {'save': 'saved', 'reject': 'rejected', 'stale': 'refused'}[outcome]


@pytest.mark.parametrize('op', [
    {'action': 'patch', 'old_text': 'Old', 'content': 'New'},  # missing pin
    {'action': 'patch', 'old_text': 'missing', 'content': 'New', 'matched_entry': 'Old fact'},
    {'action': 'patch', 'old_text': 'aa', 'content': 'b', 'matched_entry': 'aaa'},  # overlapping
    {'action': 'patch', 'old_text': 'Old', 'content': 'Old', 'matched_entry': 'Old fact'},
    {'action': 'patch', 'old_text': 'Old', 'content': '§', 'matched_entry': 'Old fact'},
    {'action': 'patch', 'old_text': 'Old', 'content': ' ', 'matched_entry': 'Old fact'},
    {'action': 'add', 'content': 'valid', 'target': 'user'},  # per-op target not native
    {'action': 'future-action', 'content': 'valid'},
    None,
])
def test_invalid_operation_prevents_entire_batch_card(setup, op):
    service, bot, store = setup
    stage_write('memory', dict(action='batch', target='memory', operations=[
        dict(action='add', content='Must not be applied alone'), op,
    ]), summary='', origin='background_review')
    asyncio.run(service.scan())
    assert not bot.messages
    assert store.db.execute('select count(*) from proposals').fetchone()[0] == 0
    assert disk_entries('memory') == []


def test_multi_entry_unicode_batch_delivers_all_text_before_buttons(setup):
    service, bot, store = setup
    texts = [('🦊 literal <tag> ' * 350).strip(), ('🦉 second entry ' * 350).strip()]
    stage_write('memory', dict(action='batch', target='memory', operations=[
        dict(action='add', content=text) for text in texts
    ]), summary='', origin='background_review')
    asyncio.run(service.scan())
    displayed = ''.join(m['text'] for m in bot.messages[:-1])
    assert all(text in displayed for text in texts)
    assert all('reply_markup' not in m for m in bot.messages[:-1])
    assert all(len(m['text'].encode('utf-16-le')) // 2 <= 3500 for m in bot.messages[:-1])
    assert store.get(token(store))['state'] == 'active'


@pytest.mark.parametrize('state', ['saved', 'rejected', 'refused', 'uncertain', 'invalid', 'active'])
def test_scan_cleans_only_known_decisions_from_old_versions(setup, state):
    service, bot, store = setup
    record = stage()
    t = store.insert(record, fingerprint(record), 42, -100, 7)
    store.transition(t, 'sending', state, message=2)
    store.recover()
    asyncio.run(service.scan())
    assert (service.bridge.get(record['id']) is None) == (state in {'saved', 'rejected'})
    assert store.get(t)['state'] == state
    assert json.loads(store.get(t)['record']) == record  # audit is retained
    assert not bot.messages
    assert disk_entries('memory') == []


@pytest.mark.parametrize('decision', ['save', 'reject'])
@pytest.mark.parametrize('failure', ['false', 'exception'])
def test_cleanup_failure_retries_without_replaying_decision(setup, monkeypatch, decision, failure):
    service, bot, store = setup
    record = stage()
    asyncio.run(service.scan())
    discard = service.bridge.discard
    def failed_discard(native_id):
        # The terminal SQLite disposition must already be durable.
        assert store.get(token(store))['state'] == ('saved' if decision == 'save' else 'rejected')
        if failure == 'exception':
            raise OSError('Simulated cleanup failure')
        return False
    monkeypatch.setattr(service.bridge, 'discard', failed_discard)
    q = Query(token(store), action=decision)
    click(service, q)
    assert q.edits[0]['text'] == ('✅ Сохранено' if decision == 'save' else '❌ Отклонено')
    assert service.bridge.get(record['id']) is not None
    monkeypatch.setattr(service.bridge, 'discard', discard)
    def no_apply(payload):
        pytest.fail('Cleanup must never replay a native write')
    monkeypatch.setattr(service.bridge, 'apply', no_apply)
    click(service, q)
    asyncio.run(service.scan())
    assert service.bridge.get(record['id']) is None
    assert disk_entries('memory') == (['Exact proposed fact.'] if decision == 'save' else [])
    assert len(bot.messages) == 2


@pytest.mark.parametrize('state', ['saved', 'rejected'])
def test_cleanup_preserves_rewritten_native_record(setup, profile, state):
    service, bot, store = setup
    record = stage()
    t = store.insert(record, fingerprint(record), 42, -100, 7)
    store.transition(t, 'sending', state)
    record['payload']['content'] = 'Different unreviewed proposal'
    (profile / 'pending' / 'memory' / (record['id'] + '.json')).write_text(json.dumps(record))
    asyncio.run(service.scan())
    assert service.bridge.get(record['id']) == record
    assert not bot.messages
    assert disk_entries('memory') == []
