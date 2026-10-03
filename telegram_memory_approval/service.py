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
                    text="Полная запись показана выше. Сохранить или отклонить это изменение?",
                    parse_mode=None, reply_markup=keyboard, disable_notification=True)
                self.store.transition(token, 'sending', 'active', message=card.message_id)
            except Exception as exc:
                self.store.transition(token, 'sending', 'uncertain', detail=type(exc).__name__)
                log.exception("Delivery uncertain; no automatic retry for %s", token)

    async def callback(self, update, context):
        q = update.callback_query
        match = re.fullmatch(PATTERN, q.data or '')
        row = self.store.get(match[1]) if match else None
        msg = q.message
        if (not row or not msg or row['owner'] != self.owner or
            row['chat'] != self.chat or row['topic'] != self.topic or
            q.from_user.id != row['owner'] or
            msg.chat.id != row['chat'] or getattr(msg, 'message_thread_id', None) != row['topic'] or
            msg.message_id != row['message'] or row['state'] != 'active'):
            await q.answer("Нет доступа, другая карточка/тема или решение уже принято.", show_alert=True)
            return
        if not self.bridge.enabled():
            await q.answer("Штатное подтверждение памяти выключено; запись запрещена.", show_alert=True)
            return
        record = self.bridge.get(row['native_id'])
        if record is None or fingerprint(record) != row['hash']:
            self.store.transition(row['token'], 'active', 'invalid', detail='Native pending record changed or missing')
            await q.answer("Предложение изменилось или исчезло; запись запрещена.", show_alert=True)
            return
        # Claim BEFORE any side effect. A crash cannot replay the write automatically.
        if not self.store.transition(row['token'], 'active', 'applying'):
            await q.answer("Решение уже принято.", show_alert=True)
            return
        try:
            if match[2] == 'reject':
                state, detail = 'rejected', 'Rejected by owner; native pending retained for audit'
            else:
                payload = validate(json.loads(row['record']))
                result = self.bridge.apply(json.loads(row["record"])["payload"])
                state = 'saved' if result.get('success') is True else 'refused'
                detail = json.dumps(result, ensure_ascii=False)
            self.store.transition(row['token'], 'applying', state, detail=detail)
        except Exception as exc:
            self.store.transition(row['token'], 'applying', 'uncertain', detail=type(exc).__name__)
            state = 'uncertain'
            log.exception("Apply uncertain; manual reconciliation required")
        label = {"saved": "✅ Сохранено", "rejected": "❌ Отклонено", "refused": "⚠️ Не применено: запись изменилась или нарушены ограничения", "uncertain": "⚠️ Результат неясен — нужна ручная проверка"}[state]
        await q.answer(label, show_alert=True)
        try:
            await q.edit_message_text(text=label, parse_mode=None, reply_markup=None)
        except Exception:
            log.warning("Could not remove terminal buttons; durable claim still prevents replay")

    async def run(self, interval):
        while True:
            try:
                await self.scan()
            except Exception:
                log.exception("Pending scan failed; no write was authorized")
            await asyncio.sleep(interval)
