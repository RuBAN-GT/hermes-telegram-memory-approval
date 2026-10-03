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
        self.edits = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)

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


@pytest.mark.parametrize('target', ['memory', 'user'])
@pytest.mark.parametrize('action', ['add', 'replace', 'remove'])
def test_transport_native_roundtrip_and_no_replay(setup, target, action):
    service, bot, store = setup
    native = MemoryStore(20000, 20000)
    native.load_from_disk()
    if action != 'add':
        assert native.add(target, 'Original full entry.')['success']
    stage(action, target, matched='Original full entry.' if action != 'add' else None)
    asyncio.run(service.scan())
    assert len(bot.messages) == 2
    assert bot.messages[0]['parse_mode'] is None
    buttons = bot.messages[-1]['reply_markup'].inline_keyboard[0]
    assert [b.text for b in buttons] == ['✅ Сохранить', '❌ Отклонить']
    q = Query(token(store))
    click(service, q)
    assert store.get(token(store))['state'] == 'saved'
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
    stage()
    asyncio.run(service.scan())
    click(service, Query(token(store), action='reject'))
    click(service, Query(token(store)))
    assert store.get(token(store))['state'] == 'rejected'
    assert disk_entries('memory') == []


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


def test_batch_and_legacy_fail_closed(setup):
    service, bot, store = setup
    stage('replace')  # missing pin
    stage_write('memory', {'action':'batch', 'target':'memory', 'operations':[{'action':'add','content':'x'}, {'action':'add','content':'y'}]}, summary='', origin='foreground')
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
