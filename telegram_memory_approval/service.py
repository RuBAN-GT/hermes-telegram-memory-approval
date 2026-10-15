"""Delivery and callback orchestration with fail-closed uncertain outcomes."""
import asyncio
import json
import logging
import re
from .bridge import fingerprint, validate
from .render import parts

log = logging.getLogger(__name__)
PATTERN = r"^hma:([a-f0-9]{32}):(save|reject)$"


class Service:
    def __init__(self, bridge, store, bot, owner, chat, topic=None, ready=None):
        self.bridge, self.store, self.bot = bridge, store, bot
        self.owner, self.chat, self.topic = owner, chat, topic
        self.ready = ready or (lambda: True)

    async def scan(self):
        if not self.ready() or not self.bridge.enabled():
            return
        delivered = 0
        for record in self.bridge.pending():
            if delivered >= 100:
                break
            if isinstance(record, dict) and isinstance(record.get('id'), str):
                previous = self.store.get_native(record.get('id'))
                if previous and previous['state'] in {'saved', 'rejected'}:
                    self.discard_decided(previous)
                    continue
            try:
                validate(record)
            except ValueError as exc:
                log.warning("Unsupported pending proposal %s: %s", record.get('id'), exc)
                continue
            token = self.store.insert(record, fingerprint(record), self.owner, self.chat, self.topic)
            if token is None:
                continue
            delivered += 1
            try:
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                # No buttons on text chunks. A separate final message owns exactly two.
                for text in parts(validate(record)):
                    await self.bot.send_message(chat_id=self.chat, message_thread_id=self.topic,
                        text=text, parse_mode=None, disable_notification=True)
                keyboard = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Сохранить", callback_data=f"hma:{token}:save"),
                    InlineKeyboardButton("❌ Отклонить", callback_data=f"hma:{token}:reject")]])
                card = await self.bot.send_message(chat_id=self.chat, message_thread_id=self.topic,
                    text="Предложение целиком показано выше. Сохранить или отклонить все его изменения?",
                    parse_mode=None, reply_markup=keyboard, disable_notification=True)
                self.store.transition(token, 'sending', 'active', message=card.message_id)
            except asyncio.CancelledError:
                self.store.transition(token, 'sending', 'uncertain', detail='Delivery cancelled')
                raise
            except Exception as exc:
                self.store.transition(token, 'sending', 'uncertain', detail=type(exc).__name__)
                log.exception("Delivery uncertain; no automatic retry for %s", token)

    async def callback(self, update, context):
        q = update.callback_query
        if q is None:
            return
        match = re.fullmatch(PATTERN, q.data) if isinstance(q.data, str) else None
        row = self.store.get(match[1]) if match else None
        msg = q.message
        if (not row or not msg or row['owner'] != self.owner or
            row['chat'] != self.chat or row['topic'] != self.topic or
            q.from_user.id != row['owner'] or
            msg.chat.id != row['chat'] or
            (getattr(msg, 'message_thread_id', None) or 1) != (row['topic'] or 1) or
            msg.message_id != row['message'] or row['state'] != 'active'):
            await q.answer("Нет доступа, другая карточка/тема или решение уже принято.", show_alert=False)
            return
        if not self.bridge.enabled():
            await q.answer("Штатное подтверждение памяти выключено; запись запрещена.", show_alert=False)
            return
        record = self.bridge.get(row['native_id'])
        if record is None or fingerprint(record) != row['hash']:
            self.store.transition(row['token'], 'active', 'invalid', detail='Native pending record changed or missing')
            await q.answer("Предложение изменилось или исчезло; запись запрещена.", show_alert=False)
            return
        # Claim BEFORE any side effect. A crash cannot replay the write automatically.
        if not self.store.transition(row['token'], 'active', 'applying'):
            await q.answer("Решение уже принято.", show_alert=False)
            return
        try:
            if match[2] == 'reject':
                state, detail = 'rejected', 'Rejected by owner'
            else:
                validate(json.loads(row['record']))
                result = self.bridge.apply(json.loads(row["record"])["payload"])
                state = 'saved' if result.get('success') is True else 'refused'
                detail = json.dumps(result, ensure_ascii=False)
            self.store.transition(row['token'], 'applying', state, detail=detail)
        except Exception as exc:
            self.store.transition(row['token'], 'applying', 'uncertain', detail=type(exc).__name__)
            state = 'uncertain'
            log.exception("Apply uncertain; manual reconciliation required")
        # Commit the disposition BEFORE removing native pending. Cleanup failure
        # must not turn a known decision into an uncertain write or replay it.
        self.discard_decided(self.store.get(row['token']))
        label = {"saved": "✅ Сохранено", "rejected": "❌ Отклонено", "refused": "⚠️ Не применено: запись изменилась или нарушены ограничения", "uncertain": "⚠️ Результат неясен — нужна ручная проверка"}[state]
        try:
            # Clear Telegram's button spinner silently; the card owns the result.
            await q.answer()
        except Exception:
            # An expired callback must not prevent updating a completed decision.
            log.warning("Could not acknowledge callback; updating terminal card anyway")
        try:
            await q.edit_message_text(text=label, parse_mode=None, reply_markup=None)
        except Exception:
            log.warning("Could not remove terminal buttons; durable claim still prevents replay")

    def discard_decided(self, row):
        if not row or row['state'] not in {'saved', 'rejected'}:
            return
        try:
            record = self.bridge.get(row['native_id'])
            if record is None:
                return
            if fingerprint(record) != row['hash']:
                log.warning("Decided native proposal changed; not removing %s", row['native_id'])
                return
            if not self.bridge.discard(row['native_id']):
                log.warning("Native pending cleanup failed; will retry %s", row['native_id'])
        except Exception:
            log.exception("Native pending cleanup failed; durable decision retained")

    async def run(self, interval):
        while True:
            try:
                await self.scan()
            except Exception:
                log.exception("Pending scan failed; no write was authorized")
            await asyncio.sleep(interval)
