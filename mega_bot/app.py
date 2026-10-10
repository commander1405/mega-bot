from __future__ import annotations
import asyncio, json, logging, random, re, secrets, string, time
from datetime import datetime, timezone
from typing import Any
from .bale_api import BaleAPI, BaleAPIError
from .database import Database
from .media_tools import convert_media, MAX_INPUT_BYTES
from .keyboards import USER_KEYBOARD, inline, menu_page, main_admin_menu, user_actions
from config import OWNER_ID, BOT_VERSION, SUPPORT_TEXT, DONATION_TEXT

log = logging.getLogger(__name__)

DEFAULT_PERMS = {
    'stats': True, 'search': True, 'case_lookup': True,
    'unban_user': True, 'unban_group': True,
    'ban_user': False, 'ban_group': False,
    'edit_about': False, 'edit_team': False,
    'manage_admins': False, 'settings': False,
}
PERMISSION_LABELS = {
    'stats':'مشاهده آمار', 'search':'جست‌وجوی کاربران و گروه‌ها', 'case_lookup':'بررسی کد پیگیری',
    'unban_user':'رفع محدودیت کاربر', 'unban_group':'رفع محدودیت گروه',
    'ban_user':'محدودسازی کاربر', 'ban_group':'محدودسازی گروه', 'edit_about':'ویرایش درباره ما',
    'edit_team':'مدیریت تیم توسعه‌دهنده', 'manage_admins':'مدیریت ادمین‌ها', 'settings':'تنظیمات ربات'
}

class MegaBot:
    def __init__(self, api: BaleAPI, db: Database):
        self.api, self.db = api, db
        self.offset: int | None = None
        self.states: dict[int, dict[str, Any]] = {}
        self.game_cache: dict[str, dict[str, Any]] = {}

    async def safe_send(self, chat_id: int, text: str, keyboard: dict | None = None, reply_to: int | None = None, parse_mode: str | None = 'Markdown') -> dict | None:
        """Send with progressive fallbacks. Prefer no-reply first for reliability in groups.

        Logs every attempt outcome. Uses fast=True (no long retries) so group handlers
        cannot burn the 15s update budget on a single stuck TCP connect.
        """
        plain = (text or '').replace('*', '').replace('`', '').replace('_', '')
        # IMPORTANT: try WITHOUT reply_to first — reply_to is a common Bale failure mode
        # and previously caused the whole handler to sit on a long timeout before fallback.
        attempts: list[tuple[str, dict | None, int | None, str | None]] = [
            (text, keyboard, None, parse_mode),
            (text, keyboard, None, None),
            (text, None, None, None),
            (plain, None, None, None),
        ]
        if reply_to is not None:
            # Optional reply attempt only after a plain send already succeeded path isn't needed;
            # still try once with reply if plain-with-keyboard works better for UX — as last of rich attempts.
            attempts.insert(0, (text, keyboard, reply_to, parse_mode))
            # Actually put no-reply FIRST:
            attempts = [
                (text, keyboard, None, parse_mode),
                (text, keyboard, reply_to, parse_mode),
                (text, keyboard, None, None),
                (text, None, None, None),
                (plain, None, None, None),
            ]
        last_exc: Exception | None = None
        for idx, (body, kb, rt, pm) in enumerate(attempts):
            try:
                result = await self.api.send_message(
                    chat_id, body, kb, rt, pm, fast=True,
                )
                log.info('sendMessage ok chat=%s attempt=%s', chat_id, idx)
                return result
            except Exception as exc:
                last_exc = exc
                log.warning(
                    'sendMessage fail chat=%s attempt=%s err=%s detail=%s',
                    chat_id, idx, type(exc).__name__, str(exc)[:200],
                )
        log.error('sendMessage gave up chat=%s err=%s', chat_id, type(last_exc).__name__ if last_exc else None)
        return None

    def _group_keyboard(self, keyboard: dict | None) -> dict | None:
        """Reply keyboards often break in Bale groups; keep only inline keyboards there."""
        if not keyboard:
            return None
        if 'inline_keyboard' in keyboard:
            return keyboard
        # Strip reply-keyboard (USER_KEYBOARD) when used in groups
        return None

    @staticmethod
    def _is_group_chat(chat: dict) -> bool:
        t = (chat.get('type') or '').lower().strip()
        if t in ('group', 'supergroup', 'megagroup', 'chat'):
            return True
        if t in ('private', 'channel'):
            return False
        # Bale sometimes omits type or uses unexpected values; title implies group/channel.
        if chat.get('title') and t != 'private':
            return True
        return False

    @staticmethod
    def _normalize_command_text(text: str) -> str:
        """Strip @botusername from /cmd@bot and trailing mentions for matching."""
        t = (text or '').strip()
        # /start@MyBot -> /start
        t = re.sub(r'^(/[\w\u0600-\u06FF]+)@\w+', r'\1', t)
        return t.strip()

    async def safe_edit(self, chat_id: int, message_id: int, text: str, keyboard: dict | None = None) -> None:
        try:
            await self.api.edit_message(chat_id, message_id, text, keyboard)
        except Exception as exc:
            log.warning('editMessageText failed: %s', exc)
            await self.safe_send(chat_id, text, keyboard)

    async def run(self, poll_timeout: int = 25) -> None:
        await self._startup_checks()
        log.info('MEGA BOT polling started (target host tapi.bale.ai)')
        backoff = 1.0
        while True:
            try:
                updates = await self.api.get_updates(self.offset, poll_timeout)
                backoff = 1.0  # reset after a successful poll
                for update in updates:
                    self.offset = int(update.get('update_id', 0)) + 1
                    # MUST keep a strong reference to tasks; otherwise CPython may
                    # GC them mid-flight under load (typical right after the bot is
                    # promoted to group admin and message volume spikes).
                    task = asyncio.create_task(
                        self._handle_update_safe(update),
                        name=f"upd-{update.get('update_id')}",
                    )
                    self._bg_tasks.add(task)
                    task.add_done_callback(self._bg_tasks.discard)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Network blips to tapi.bale.ai are common from some regions; back off without stack spam every cycle.
                msg = f'{type(exc).__name__}: {exc}'
                if 'network error' in msg or 'Timeout' in msg or 'ClientConnector' in msg:
                    log.error('Polling failed (network); retry in %.0fs — %s', backoff, msg)
                else:
                    log.exception('Polling failed; retry in %.0fs', backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)


    async def _startup_checks(self) -> None:
        """Best-effort startup probes — never block polling for long.

        Railway (and many non-Iran hosts) often cannot TCP-connect to
        tapi.bale.ai. Fail fast and let the poll loop keep retrying.
        """
        self.bot_id = None
        self.bot_username = ''
        # Single attempt each, short overall budget.
        try:
            await asyncio.wait_for(
                self.api.call('deleteWebhook', {'drop_pending_updates': False}, retries=0),
                timeout=8.0,
            )
            log.info('deleteWebhook ok')
        except Exception as exc:
            log.warning('deleteWebhook skipped (network?): %s', type(exc).__name__)
        try:
            me = await asyncio.wait_for(self.api.call('getMe', retries=0), timeout=8.0)
            self.bot_id = int((me or {}).get('id') or 0) or None
            self.bot_username = (me or {}).get('username') or ''
            log.info(
                'getMe: @%s id=%s can_read_all_group_messages=%s',
                self.bot_username,
                self.bot_id,
                (me or {}).get('can_read_all_group_messages'),
            )
        except Exception as exc:
            log.warning(
                'getMe failed at startup (%s). Will still poll — if this keeps failing, '
                'Railway cannot reach tapi.bale.ai (use Iran VPS or a proxy).',
                type(exc).__name__,
            )


    async def _handle_update_safe(self, update: dict) -> None:
        uid = update.get('update_id')
        try:
            sem = getattr(self, '_update_sem', None)
            if sem is None:
                self._update_sem = asyncio.Semaphore(8)
                sem = self._update_sem
            async with sem:
                await asyncio.wait_for(self._dispatch_update(update), timeout=15.0)
        except asyncio.TimeoutError:
            log.error('Update %s timed out (15s) — skipped', uid)
        except Exception:
            log.exception('Failed to process update %s', uid)

    async def _dispatch_update(self, update: dict) -> None:
        uid = update.get('update_id')
        kinds = [k for k in ('message', 'callback_query', 'my_chat_member', 'edited_message', 'chat_member') if k in update]
        log.info('UPDATE id=%s kinds=%s', uid, kinds or list(update.keys())[:6])
        if 'message' in update:
            await self.on_message(update['message'])
        elif 'callback_query' in update:
            await self.on_callback(update['callback_query'])
        elif 'my_chat_member' in update:
            await self.on_my_chat_member(update['my_chat_member'])
        elif kinds:
            log.info('UPDATE id=%s ignored kinds=%s', uid, kinds)

    async def on_message(self, msg: dict) -> None:
        chat = msg.get('chat') or {}
        chat_id = int(chat.get('id', 0) or 0)
        if not chat_id:
            return

        # Pure service events — do not run the full pipeline (critical after bot is
        # promoted to group admin: Bale then delivers many status messages).
        service_keys = (
            'left_chat_member', 'new_chat_title', 'new_chat_photo', 'delete_chat_photo',
            'group_chat_created', 'supergroup_chat_created', 'channel_chat_created',
            'migrate_to_chat_id', 'migrate_from_chat_id', 'pinned_message',
            'message_auto_delete_timer_changed', 'connected_website',
        )
        if any(k in msg for k in service_keys):
            return
        if msg.get('new_chat_members'):
            try:
                await self.welcome_new_members(msg, chat)
            except Exception:
                log.exception('welcome_new_members failed')
            return

        # Bale may omit "from" on some service messages; also support sender_chat.
        user = msg.get('from') or msg.get('sender') or {}
        if not user and msg.get('sender_chat'):
            user = {
                'id': msg['sender_chat'].get('id'),
                'first_name': msg['sender_chat'].get('title') or 'ناشناس',
                'is_bot': False,
            }
        uid = int(user.get('id', 0) or 0)
        # Never process the bot's own messages (prevents loops after group replies).
        if user.get('is_bot') or not uid:
            return
        text = (msg.get('text') or msg.get('caption') or '').strip()
        text = self._normalize_command_text(text)
        chat_type = (chat.get('type') or '')
        is_group = self._is_group_chat(chat)

        # Log every group message (critical for diagnosing admin-only failures).
        if is_group:
            bot_st = getattr(self, '_bot_member_status', {}).get(chat_id, '?')
            log.info(
                'GROUP inbound chat=%s type=%r uid=%s text=%r reply=%s bot_status=%s',
                chat_id, chat_type, uid, (text or '')[:80],
                bool(msg.get('reply_to_message')), bot_st,
            )

        # /ping works in EVERY chat, BEFORE any DB or admin logic.
        # This is the primary liveness probe for group-admin regressions.
        low_fast = (text or '').lower().replace('؟', '').replace('?', '').strip()
        if text in ('/ping', 'ping', '/id', '/chatid') or low_fast in ('پینگ',):
            bot_st = getattr(self, '_bot_member_status', {}).get(chat_id, '?')
            log.info('PING reply chat=%s uid=%s bot_status=%s', chat_id, uid, bot_st)
            result = await self.safe_send(
                chat_id,
                f'pong ✅\nchat_id={chat_id}\ntype={chat_type or "?"}\nuid={uid}\ngroup={is_group}\nbot_status={bot_st}',
                reply_to=msg.get('message_id'),
                parse_mode=None,
            )
            if not result:
                log.error('PING safe_send returned None chat=%s', chat_id)
            return

        if is_group and text and (text.startswith('/start') or low_fast in ('استارت',)):
            await self.safe_send(
                chat_id,
                'سلام! من آنلاینم 🌿\nدستورها: دوز | سلام | ربات | /ping\nاگر جواب نگرفتی روی پیام ربات ریپلای کن.',
                parse_mode=None,
            )
            return

        try:
            await asyncio.wait_for(self.db.upsert_user(user), timeout=3.0)
        except Exception:
            log.warning('upsert_user skipped/failed for %s', uid)

        # Media conversion: reply to a photo/video/animation and write "گیف" or "گیف متن".
        replied = msg.get('reply_to_message') or {}
        if text == 'گیف' or text.startswith('گیف '):
            if not replied:
                await self.safe_send(chat_id, 'برای تبدیل، روی عکس یا ویدئو ریپلای کن و بنویس «گیف». برای نوشتن متن روی آن بنویس «گیف متن دلخواه».')
                return
            media_file_id = None
            media_name = 'image.jpg'
            if replied.get('photo'):
                photos = replied['photo']
                best = photos[-1] if isinstance(photos, list) else photos
                media_file_id = best.get('file_id')
                media_name = 'image.jpg'
            elif replied.get('video'):
                media_file_id = replied['video'].get('file_id'); media_name = replied['video'].get('file_name') or 'video.mp4'
            elif replied.get('animation'):
                media_file_id = replied['animation'].get('file_id'); media_name = replied['animation'].get('file_name') or 'animation.mp4'
            elif replied.get('document'):
                doc = replied['document']; media_file_id = doc.get('file_id'); media_name = doc.get('file_name') or 'media.bin'
            if not media_file_id:
                await self.safe_send(chat_id, 'این پیام عکس یا ویدئوی قابل پردازش ندارد.')
                return
            overlay = text[4:].strip() if text.startswith('گیف ') else ''
            await self.safe_send(chat_id, 'چند لحظه صبر کن تا برات گیف رو آماده کنم..✨️😊')
            try:
                raw, api_name = await self.api.get_file_bytes(media_file_id, MAX_INPUT_BYTES)
                result = await convert_media(raw, media_name or api_name, overlay)
                await self.api.send_animation_bytes(chat_id, result, 'mega-bot.gif', 'ساخته‌شده با MEGA BOT')
            except Exception as exc:
                log.exception('GIF conversion failed')
                await self.safe_send(chat_id, f'ساخت GIF ناموفق بود: {self._short(exc)}')
            return

        # Group moderation commands (reply required). Group-level via Bale ChatMember API.
        # Supported: سکوت / حذف سکوت / بن / حذف بن / انبن / آنبن / کیک
        _norm_cmd = text.replace('‌', '').replace('ـ', '').strip()
        _norm_cmd = _norm_cmd.replace('أ', 'ا').replace('إ', 'ا').replace('آ', 'ا').replace('ي', 'ی').replace('ك', 'ک')
        _mod_cmds = {
            'سکوت': 'mute',
            'حذف سکوت': 'unmute',
            'بن': 'ban',
            'حذف بن': 'unban',
            'انبن': 'unban',
            'آنبن': 'unban',
            'کیک': 'kick',
        }
        if self._is_group_chat(chat) and _norm_cmd in _mod_cmds:
            action = _mod_cmds[_norm_cmd]
            if not replied or not (replied.get('from') or replied.get('sender')):
                await self.safe_send(
                    chat_id,
                    f'برای استفاده از دستور «{_norm_cmd}» روی پیام کاربر هدف *ریپلای* کن و بعد دستور را بفرست.',
                )
                return
            if not await self._is_chat_admin(chat_id, uid):
                await self.safe_send(chat_id, '⛔ فقط ادمین گروه می‌تواند از این دستور استفاده کند.')
                return
            target_user = replied.get('from') or replied.get('sender') or {}
            target_uid = int(target_user.get('id', 0) or 0)
            if not target_uid:
                await self.safe_send(chat_id, 'شناسه معتبر برای کاربر هدف پیدا نشد؛ پیام ریپلای‌شده باید از یک کاربر واقعی باشد.')
                return
            if target_uid == OWNER_ID:
                await self.safe_send(chat_id, '⛔ مالک ربات قابل مدیریت با این دستور نیست.')
                return
            if target_uid == uid:
                await self.safe_send(chat_id, 'نمی‌توانی این دستور را روی خودت اجرا کنی.')
                return
            target_name = (target_user.get('first_name') or str(target_uid)).strip()
            try:
                if action == 'mute':
                    # Mute: cannot send messages (Bale/Telegram ChatPermissions).
                    await self.api.restrict_chat_member(
                        chat_id,
                        target_uid,
                        {
                            'can_send_messages': False,
                            'can_send_media_messages': False,
                            'can_send_other_messages': False,
                            'can_add_web_page_previews': False,
                        },
                    )
                    await self.safe_send(chat_id, f'🔇 کاربر {target_name} سکوت شد.')
                    await self.db.log(uid, 'group_mute', 'user', target_uid, {'chat_id': chat_id})
                elif action == 'unmute':
                    await self.api.restrict_chat_member(
                        chat_id,
                        target_uid,
                        {
                            'can_send_messages': True,
                            'can_send_media_messages': True,
                            'can_send_other_messages': True,
                            'can_add_web_page_previews': True,
                        },
                    )
                    await self.safe_send(chat_id, f'🔊 سکوت کاربر {target_name} برداشته شد.')
                    await self.db.log(uid, 'group_unmute', 'user', target_uid, {'chat_id': chat_id})
                elif action == 'ban':
                    await self.api.ban_chat_member(chat_id, target_uid)
                    await self.safe_send(chat_id, f'⛔ کاربر {target_name} از گروه بن شد.')
                    await self.db.log(uid, 'group_ban', 'user', target_uid, {'chat_id': chat_id})
                elif action == 'unban':
                    await self.api.unban_chat_member(chat_id, target_uid)
                    await self.safe_send(chat_id, f'✅ بن کاربر {target_name} برداشته شد؛ می‌تواند دوباره به گروه بپیوندد.')
                    await self.db.log(uid, 'group_unban', 'user', target_uid, {'chat_id': chat_id})
                elif action == 'kick':
                    await self.api.ban_chat_member(chat_id, target_uid, int(time.time()) + 45)
                    await self.api.unban_chat_member(chat_id, target_uid)
                    await self.safe_send(chat_id, f'👢 کاربر {target_name} از گروه اخراج شد.')
                    await self.db.log(uid, 'group_kick', 'user', target_uid, {'chat_id': chat_id})
            except Exception as exc:
                await self.safe_send(
                    chat_id,
                    f'عملیات انجام نشد؛ دسترسی ربات در گروه (Restrict/Ban Members) را بررسی کن. {self._short(exc)}',
                )
            return
        if self._is_group_chat(chat):
            try:
                await asyncio.wait_for(self.db.upsert_group(chat), timeout=2.0)
            except Exception:
                log.warning('upsert_group skipped for %s', chat_id)
        try:
            await asyncio.wait_for(self.db.record_usage(uid, chat, 'message'), timeout=2.0)
        except Exception:
            log.warning('record_usage skipped')

        # User restrictions apply in private and group chats, while keeping case tracking accessible.
        try:
            restricted_user = await asyncio.wait_for(self.db.is_restricted('user', uid), timeout=2.0)
        except Exception:
            log.warning('is_restricted(user) skipped')
            restricted_user = None
        if restricted_user and uid != OWNER_ID and not await self.db.is_admin(uid) and text not in ('تیم من',) and not text.startswith('/admin') and not text.startswith('/owner'):
            await self.safe_send(chat_id, f"*⛔ دسترسی تو به قابلیت‌های MEGA BOT محدود شده است.*\nکد پیگیری: `#{restricted_user['case_code']}`\nبرای بررسی دلیل و مدت، بخش *تیم من* را باز کن.", USER_KEYBOARD)
            return

        # Keep group-level restriction enforced for bot features.
        if self._is_group_chat(chat):
            try:
                restricted_group = await asyncio.wait_for(self.db.is_restricted('group', chat_id), timeout=2.0)
            except Exception:
                log.warning('is_restricted(group) skipped')
                restricted_group = None
            feature_request = text.startswith('/') or text in ('دوز','بازی دوز','شیر یا خط','قوانین','پین','لیست ویژه ها','خوبی','چخبر','خدافظ','شب خوش','سلام ربات') or text.startswith(('اکو ','تنظیم قوانین ','هرکی تک')) or ('ربات' in text.lower())
            if restricted_group and feature_request and not self._is_admin_identity(uid):
                await self.safe_send(chat_id, f"*⛔ این گروه توسط مدیریت محدود شده است.*\nکد پیگیری: `#{restricted_group['case_code']}`", reply_to=msg.get('message_id'))
                return
        if text.startswith('/start'):
            await self.safe_send(chat_id, '*سلام! به MEGA BOT خوش اومدی* 🌿\nاز منوی پایین برای دیدن بخش‌ها استفاده کن.', USER_KEYBOARD)
            return
        if text.startswith('/admin'):
            if uid != OWNER_ID and not await self.db.is_admin(uid):
                await self.safe_send(chat_id, '⛔ دسترسی به پنل مدیریت نداری.')
                return
            await self.show_admin(chat_id, uid, owner=(uid == OWNER_ID))
            return
        if text.startswith('/owner'):
            if uid != OWNER_ID:
                await self.safe_send(chat_id, '⛔ این پنل فقط برای مالک ربات است.')
                return
            await self.show_admin(chat_id, uid, owner=True)
            return
        # Owner diagnostics for a specific group id (e.g. /checkgroup 4739068741)
        if text.startswith('/checkgroup') or text.startswith('/گروه'):
            if uid != OWNER_ID:
                await self.safe_send(chat_id, '⛔ فقط مالک.')
                return
            parts = text.split()
            if len(parts) < 2 or not parts[1].lstrip('-').isdigit():
                await self.safe_send(chat_id, 'مثال: `/checkgroup 4739068741`', parse_mode=None)
                return
            await self.diagnose_group(chat_id, int(parts[1]))
            return
        if text.startswith('/unrestrictgroup') or text.startswith('/رفعگروه'):
            if uid != OWNER_ID:
                await self.safe_send(chat_id, '⛔ فقط مالک.')
                return
            parts = text.split()
            if len(parts) < 2 or not parts[1].lstrip('-').isdigit():
                await self.safe_send(chat_id, 'مثال: `/unrestrictgroup 4739068741`', parse_mode=None)
                return
            gid = int(parts[1])
            lifted = await self.db.lift_restriction('group', gid, uid, 'owner unrestrictgroup command')
            if lifted:
                await self.safe_send(chat_id, f'✅ محدودیت گروه `{gid}` برداشته شد.', parse_mode=None)
            else:
                await self.safe_send(chat_id, f'برای گروه `{gid}` محدودیت فعالی پیدا نشد.', parse_mode=None)
            # Try a live ping into the group
            ping = await self.safe_send(gid, '✅ محدودیت گروه برداشته شد؛ ربات دوباره فعال است.', parse_mode=None)
            if ping:
                await self.safe_send(chat_id, f'پیام تست به گروه `{gid}` ارسال شد.', parse_mode=None)
            else:
                await self.safe_send(chat_id, f'پیام تست به گروه `{gid}` ارسال نشد؛ دسترسی ارسال پیام ربات در گروه را چک کن.', parse_mode=None)
            return
        # If an admin/user is in a multi-step flow, consume their next text before menu shortcuts.
        state = self.states.get(uid)
        if state and int(state.get('chat_id', chat_id)) == chat_id:
            handled = await self.handle_state(chat_id, uid, user, text, state, msg)
            if handled:
                return

        if text == '🆘 پشتیبانی' or text == 'پشتیبانی':
            await self.safe_send(chat_id, '*پشتیبانی*\n' + SUPPORT_TEXT, USER_KEYBOARD)
            return
        if text == '💚 حمایت مالی' or text == 'حمایت مالی':
            await self.show_donation_intro(chat_id)
            return
        if text == '👨‍💻 تیم توسعه' or text == 'تیم توسعه':
            team = await self.db.get_team()
            body = '*تیم توسعه‌دهنده*\n\n' + ('\n'.join(f"*{m['name']}* — {m['title']}\n{m['description']}" for m in team) if team else 'هنوز عضوی ثبت نشده است.')
            await self.safe_send(chat_id, body, USER_KEYBOARD)
            return
        if text == '📊 آمار و اطلاعات' or text == 'آمار و اطلاعات':
            await self.safe_send(chat_id, self.stats_text(await self.db.stats()), USER_KEYBOARD)
            return
        if text == 'منوی بیشتر':
            await self.safe_send(chat_id, '*منوی بیشتر*\nبخش موردنظرت رو انتخاب کن.', inline([
                [('ℹ️ درباره ما','more:about'),('📚 راهنمای ربات','more:guide')],
                [('🎫 تیم من / پیگیری','more:my_cases')],
                [('↩️ برگشت','home'),('✖️ بستن','close')]
            ]), USER_KEYBOARD)
            return
        if text == 'درباره ما':
            about = await self.db.get_setting('about', 'MEGA BOT یک ربات اجتماعی برای بله است.')
            await self.safe_send(chat_id, f'*درباره MEGA BOT*\n\n{about}', USER_KEYBOARD)
            return
        if text == 'راهنما ربات':
            await self.show_guide(chat_id, 0)
            return
        if text == 'تیم من':
            await self.my_cases(chat_id, uid)
            return
        if text == 'پین' and msg.get('reply_to_message'):
            if not (await self._is_chat_admin(chat_id, uid) or uid == OWNER_ID):
                await self.safe_send(chat_id, '⛔ فقط ادمین گروه می‌تواند پیام را پین کند.')
                return
            try:
                await self.api.pin_message(chat_id, int(msg['reply_to_message']['message_id']))
                await self.safe_send(chat_id, '📌 پیام پین شد.')
            except Exception as exc:
                await self.safe_send(chat_id, f'نتونستم پیام رو پین کنم؛ دسترسی ربات رو بررسی کن.\n`{self._short(exc)}`')
            return
        if text.startswith('تنظیم قوانین '):
            if not self._is_group_chat(chat) or not await self._is_chat_admin(chat_id, uid):
                await self.safe_send(chat_id, 'فقط ادمین گروه می‌تواند قوانین را تنظیم کند.')
                return
            await self.db.set_setting(f'rules:{chat_id}', text[len('تنظیم قوانین '):].strip(), uid)
            await self.safe_send(chat_id, '✅ قوانین گروه ذخیره شد.')
            return
        if text == 'قوانین' and self._is_group_chat(chat):
            await self.safe_send(chat_id, 'میخوای قوانین این گروه رو ببینی؟', inline([[('بله','rules:yes'),('خیر','rules:no')], [('✖️ بستن','close')]]))
            return
        if text.startswith('اکو '):
            echo = text[4:].strip()
            try:
                await self.api.delete_message(chat_id, int(msg['message_id']))
            except Exception:
                pass
            if echo:
                await self.safe_send(chat_id, echo)
            return
        if text.lower().lstrip('/') in ('دوز','بازی دوز','دوز بازی') and self._is_group_chat(chat):
            log.info('CMD دوز chat=%s uid=%s', chat_id, uid)
            await self.start_ttt(chat_id, uid, user.get('first_name') or 'بازیکن')
            return
        if re.fullmatch(r'شیر\s*یا\s*خط', text):
            game_id = secrets.token_hex(3)
            self.game_cache[game_id] = {'kind':'coin','creator':uid,'chat_id':chat_id,'players':{}}
            await self.safe_send(chat_id, '*بازی شیر یا خط*\nهر دو بازیکن انتخابشون رو ثبت کنن.', inline([
                [('🦁 شیر','coin:choose:'+game_id+':heads'),('🪙 خط','coin:choose:'+game_id+':tails')],
                [('✖️ لغو بازی','game:cancel:'+game_id)]
            ]))
            return
        if re.fullmatch(r'هرکی تک بیاره+', text) or re.fullmatch(r'هرکی تک+ بیاره+', text):
            await self.start_one_game(chat_id, uid, user.get('first_name') or 'بازیکن')
            return
        if text == 'لیست ویژه ها' and self._is_group_chat(chat) and await self._is_chat_admin(chat_id, uid):
            await self.safe_send(chat_id, 'فهرست ویژه‌ها در این نسخه از پنل مالک مدیریت می‌شود؛ بخش دسترسی‌ها را باز کن.', inline([[('🛡️ پنل مالک','owner:open')], [('↩️ برگشت','home'),('✖️ بستن','close')]]))
            return

        # Social / emoji reactions (groups + private). Bot messages already skipped above.
        if not text:
            return
        # Emoji-only: single emoji reply, no text. Rate-limited per user.
        if self._is_emoji_only(text):
            last = self._emoji_last.get(uid, 0)
            now = time.time()
            if now - last >= 3.0:
                reaction = self._pick_emoji_reaction(text)
                if reaction:
                    self._emoji_last[uid] = now
                    await self.safe_send(chat_id, reaction, reply_to=msg.get('message_id'), parse_mode=None)
            return
        # Lightweight social replies — use cached stats, never block the handler on DB failure.
        try:
            stats = await self._cached_stats()
        except Exception:
            stats = {}
        reply = self.social_reply(text, stats)
        if reply:
            await self.safe_send(chat_id, reply, reply_to=msg.get('message_id'))

    async def on_callback(self, query: dict) -> None:
        """Route every inline-button click to a visible outcome.

        Critical fixes:
        - answerCallbackQuery always (stops client spinner)
        - handle admin:back / admin:page from menu_page
        - never silent-drop unknown callback_data
        - isolate handler errors so one bad click cannot break others
        """
        callback_id = query.get('id')
        data = (query.get('data') or '').strip()
        user = query.get('from') or {}
        uid = int(user.get('id', 0) or 0)
        message = query.get('message') or {}
        chat = message.get('chat') or {}
        chat_id = int(chat.get('id', 0) or 0)
        message_id = int(message.get('message_id', 0) or 0)

        log.info(
            'CALLBACK data=%r chat=%s type=%r uid=%s mid=%s',
            data[:64], chat_id, chat.get('type'), uid, message_id,
        )

        # Always ack the click first so the client is not left loading.
        if callback_id:
            try:
                await asyncio.wait_for(self.api.answer_callback(callback_id), timeout=5.0)
            except Exception as exc:
                log.warning('answerCallbackQuery failed: %s', type(exc).__name__)

        if not data:
            await self.safe_edit(chat_id, message_id, 'دکمه نامعتبر بود.', None)
            return

        try:
            await self._route_callback(uid, chat_id, message_id, data, user, chat, query)
        except Exception:
            log.exception('Callback handler crashed data=%r uid=%s', data, uid)
            try:
                await self.safe_send(chat_id, '⚠️ خطایی در پردازش دکمه رخ داد. دوباره تلاش کن.')
            except Exception:
                pass

    async def _route_callback(
        self, uid: int, chat_id: int, message_id: int, data: str,
        user: dict, chat: dict, query: dict,
    ) -> None:
        if data == 'close':
            self.states.pop(uid, None)
            await self.safe_edit(
                chat_id, message_id,
                'این منو بسته شد. برای بازکردن دوباره از منوی اصلی استفاده کن.',
                None,
            )
            return

        if data == 'home' or data == 'admin:back:home' or data.startswith('admin:back:'):
            self.states.pop(uid, None)
            await self.safe_edit(chat_id, message_id, '*منوی اصلی MEGA BOT*', None)
            if not self._is_group_chat(chat):
                await self.safe_send(
                    chat_id,
                    'از کیبورد پایین یکی از گزینه‌ها را انتخاب کن.',
                    USER_KEYBOARD,
                )
            return

        # Group restriction for games only — never block admin panel callbacks.
        if (
            self._is_group_chat(chat)
            and uid != OWNER_ID
            and data.startswith(('ttt:', 'one:', 'coin:', 'rules:'))
        ):
            try:
                restricted = await asyncio.wait_for(
                    self.db.is_restricted('group', chat_id), timeout=2.0,
                )
            except Exception:
                restricted = None
            if restricted:
                await self.safe_send(
                    chat_id,
                    f"*⛔ این گروه توسط مدیریت محدود شده است.*\nکد پیگیری: `#{restricted['case_code']}`",
                )
                return

        if data.startswith('more:'):
            key = data.split(':', 1)[1]
            if key == 'about':
                about = await self.db.get_setting('about', 'MEGA BOT یک ربات اجتماعی برای بله است.')
                await self.safe_edit(
                    chat_id, message_id, f'*درباره MEGA BOT*\n\n{about}',
                    inline([[('↩️ برگشت', 'more:menu'), ('✖️ بستن', 'close')]]),
                )
            elif key == 'guide':
                await self.show_guide(chat_id, 0, message_id)
            elif key == 'my_cases':
                await self.my_cases(chat_id, uid)
            elif key == 'menu':
                await self.safe_edit(
                    chat_id, message_id, '*منوی بیشتر*\nبخش موردنظرت رو انتخاب کن.',
                    inline([
                        [('ℹ️ درباره ما', 'more:about'), ('📚 راهنمای ربات', 'more:guide')],
                        [('🎫 تیم من / پیگیری', 'more:my_cases')],
                        [('↩️ برگشت', 'home'), ('✖️ بستن', 'close')],
                    ]),
                )
            elif key == 'support':
                await self.safe_edit(
                    chat_id, message_id, '*پشتیبانی*\n' + SUPPORT_TEXT,
                    inline([[('↩️ برگشت', 'home'), ('✖️ بستن', 'close')]]),
                )
            elif key == 'donate':
                await self.show_donation_intro(chat_id, message_id)
            elif key == 'team':
                team = await self.db.get_team()
                body = '*تیم توسعه‌دهنده*\n\n' + (
                    '\n'.join(f"*{m['name']}* — {m['title']}\n{m['description']}" for m in team)
                    if team else 'هنوز عضوی ثبت نشده است.'
                )
                await self.safe_edit(
                    chat_id, message_id, body,
                    inline([[('↩️ برگشت', 'more:menu'), ('✖️ بستن', 'close')]]),
                )
            elif key == 'stats':
                await self.safe_edit(
                    chat_id, message_id, self.stats_text(await self.db.stats()),
                    inline([[('↩️ برگشت', 'home'), ('✖️ بستن', 'close')]]),
                )
            else:
                await self.safe_edit(
                    chat_id, message_id, 'این بخش در دسترس نیست.',
                    inline([[('↩️ برگشت', 'home'), ('✖️ بستن', 'close')]]),
                )
            return

        if data.startswith('guide:item:'):
            try:
                index = int(data.rsplit(':', 1)[1])
            except ValueError:
                await self.safe_edit(chat_id, message_id, 'آیتم نامعتبر.', None)
                return
            entries = self.GUIDE_ENTRIES
            if 0 <= index < len(entries):
                label, body = entries[index]
                await self.safe_edit(
                    chat_id, message_id, f'*{label}*\n\n{body}',
                    inline([[('↩️ برگشت', f'guide:page:{index // 2}'), ('✖️ بستن', 'close')]]),
                )
            else:
                await self.safe_edit(chat_id, message_id, 'آیتم پیدا نشد.', None)
            return

        if data.startswith('guide:page:'):
            try:
                page = int(data.rsplit(':', 1)[1])
            except ValueError:
                page = 0
            await self.show_guide(chat_id, page, message_id)
            return

        if data.startswith('rules:'):
            if data == 'rules:no':
                await self.safe_edit(chat_id, message_id, 'باشه 🌱', None)
                return
            rules = await self.db.get_setting(f'rules:{chat_id}', 'قوانینی ثبت نشده است.')
            await self.safe_edit(
                chat_id, message_id, f'*📜 قوانین گروه*\n\n{rules}',
                inline([[('✖️ بستن', 'close')]]),
            )
            return

        if data.startswith('coin:choose:'):
            parts = data.split(':')
            if len(parts) >= 4:
                gid, choice = parts[2], parts[3]
                game = self.game_cache.get(gid)
                if not game:
                    await self.safe_send(chat_id, 'این بازی دیگر فعال نیست.')
                    return
                game.setdefault('players', {})[uid] = choice
                if len(game['players']) >= 2:
                    vals = list(game['players'].items())
                    a, b = vals[0], vals[1]
                    if a[1] == b[1]:
                        result = 'مساوی شد! 🤝'
                    else:
                        # heads beats nothing special — both chose; random winner among different
                        result = f'انتخاب‌ها ثبت شد: {a[1]} vs {b[1]}'
                    await self.safe_edit(chat_id, message_id, f'*نتیجه شیر یا خط*\n{result}', None)
                    self.game_cache.pop(gid, None)
                else:
                    await self.safe_edit(chat_id, message_id, 'انتخابت ثبت شد؛ منتظر بازیکن دوم باش ⏳', None)
            return

        if data.startswith('game:cancel:'):
            gid = data.rsplit(':', 1)[-1]
            game = self.game_cache.get(gid)
            if game and int(game.get('creator', -1)) != uid and uid != OWNER_ID:
                await self.safe_send(chat_id, 'فقط سازنده می‌تواند بازی را لغو کند.')
            else:
                self.game_cache.pop(gid, None)
                await self.safe_edit(chat_id, message_id, 'بازی لغو شد.', None)
            return

        if data.startswith('ttt:'):
            await self.handle_ttt_callback(query, data)
            return
        if data.startswith('one:'):
            await self.handle_one_callback(query, data)
            return
        if data.startswith('donate:'):
            await self.handle_donation_callback(uid, chat_id, message_id, data, user)
            return
        if data.startswith('donation_review:'):
            await self.handle_donation_review(uid, chat_id, message_id, data)
            return
        if data.startswith('own:donation:') or data == 'own:donations':
            if uid != OWNER_ID:
                await self.safe_send(chat_id, '⛔ فقط مالک.')
                return
            if data == 'own:donations':
                card = await self.db.get_setting('donation_card', '')
                card_name = await self.db.get_setting('donation_card_name', '')
                username = str(await self.db.get_setting('donation_owner_username', '')).lstrip('@')
                await self.safe_edit(
                    chat_id, message_id,
                    f'*تنظیمات حمایت مالی*\n\nشماره کارت: `{card}`\nصاحب کارت: {card_name}\nآیدی مالک: @{username}',
                    inline([
                        [('💳 تغییر شماره کارت', 'own:donation:card'), ('👤 نام صاحب کارت', 'own:donation:card_name')],
                        [('🔗 آیدی مالک', 'own:donation:username')],
                        [('📝 متن معرفی', 'own:donation:intro'), ('📄 توضیحات پرداخت', 'own:donation:instructions')],
                        [('↩️ برگشت', 'admin:page:0'), ('✖️ بستن', 'close')],
                    ]),
                )
                return
            # own:donation:field — set state
            field = data.split(':', 2)[-1]
            key_map = {
                'card': 'donation_card',
                'card_name': 'donation_card_name',
                'username': 'donation_owner_username',
                'intro': 'donation_intro',
                'instructions': 'donation_instructions',
            }
            setting_key = key_map.get(field, f'donation_{field}')
            self.states[uid] = {
                'step': 'edit_donation_setting',
                'setting_key': setting_key,
                'chat_id': chat_id,
                'message_id': message_id,
            }
            labels = {
                'card': 'شماره کارت جدید را بفرست',
                'card_name': 'نام صاحب کارت را بفرست',
                'username': 'آیدی مالک (بدون @) را بفرست',
                'intro': 'متن معرفی حمایت را بفرست',
                'instructions': 'توضیحات پرداخت را بفرست',
            }
            await self.safe_edit(
                chat_id, message_id, labels.get(field, 'مقدار جدید را بفرست'),
                inline([[('↩️ برگشت', 'own:donations'), ('✖️ بستن', 'close')]]),
            )
            return

        if data == 'owner:open':
            if uid == OWNER_ID:
                await self.show_admin(chat_id, uid, True, message_id)
            else:
                await self.safe_send(chat_id, '⛔ فقط مالک ربات.')
            return

        if data.startswith('admin:page:'):
            if uid == OWNER_ID or await self.db.is_admin(uid):
                try:
                    page = int(data.rsplit(':', 1)[1])
                except ValueError:
                    page = 0
                await self.show_admin(chat_id, uid, uid == OWNER_ID, message_id, page)
            else:
                await self.safe_send(chat_id, '⛔ دسترسی پنل ادمین نداری.')
            return

        if data.startswith('adm:target:'):
            await self.handle_target_select(uid, chat_id, message_id, data)
            return

        if data.startswith('adm:') or data.startswith('own:') or data.startswith('mod:') or data.startswith('team:'):
            await self.handle_admin_callback(uid, chat_id, message_id, data)
            return

        if data.startswith('perm:'):
            await self.handle_permission_callback(uid, chat_id, message_id, data)
            return

        # Unknown callback — always visible feedback
        log.warning('Unhandled callback data=%r uid=%s', data, uid)
        await self.safe_edit(
            chat_id, message_id,
            f'این دکمه دیگر معتبر نیست.\n`{data[:40]}`',
            inline([[('↩️ منوی اصلی', 'home'), ('✖️ بستن', 'close')]]),
        )


    def _is_admin_identity(self, uid: int) -> bool:
        return uid == OWNER_ID

    # (chat_id, user_id) -> (is_admin, expires_at)
    _admin_cache: dict[tuple[int, int], tuple[bool, float]] = {}

    async def _is_chat_admin(self, chat_id: int, uid: int) -> bool:
        """Check group admin status with a short TTL cache.

        After the bot itself is promoted, groups generate more traffic and
        repeated getChatMember calls can slow or trip the API. Cache for 60s.
        """
        key = (int(chat_id), int(uid))
        now = time.time()
        cached = self._admin_cache.get(key)
        if cached and cached[1] > now:
            return cached[0]
        try:
            result = await asyncio.wait_for(
                self.api.call('getChatMember', {'chat_id': chat_id, 'user_id': uid}),
                timeout=4.0,
            )
            if not isinstance(result, dict):
                return False
            status = (result.get('status') or '').lower()
            is_admin = status in ('creator', 'administrator')
            self._admin_cache[key] = (is_admin, now + 60)
            # Bound cache size
            if len(self._admin_cache) > 2000:
                expired = [k for k, v in self._admin_cache.items() if v[1] <= now]
                for k in expired[:500]:
                    self._admin_cache.pop(k, None)
            return is_admin
        except Exception as exc:
            log.debug('getChatMember failed chat=%s user=%s: %s', chat_id, uid, exc)
            return False

    async def on_my_chat_member(self, event: dict) -> None:
        """Promote/demote: log only. Never sendMessage from this handler."""
        try:
            chat = event.get('chat') or {}
            chat_id = int(chat.get('id', 0) or 0)
            new = event.get('new_chat_member') or {}
            if not isinstance(new, dict):
                new = {}
            status = (new.get('status') or '').lower()
            old = event.get('old_chat_member') or {}
            old_status = (old.get('status') if isinstance(old, dict) else '') or ''
            log.info(
                'my_chat_member chat=%s old=%s new=%s title=%r',
                chat_id, old_status, status, chat.get('title'),
            )
            # Diagnostic map: does NOT gate message processing.
            if not hasattr(self, '_bot_member_status'):
                self._bot_member_status = {}
            if chat_id:
                self._bot_member_status[chat_id] = status
            if chat_id:
                for k in [k for k in list(self._admin_cache.keys()) if k[0] == chat_id]:
                    self._admin_cache.pop(k, None)
                try:
                    await asyncio.wait_for(self.db.upsert_group(chat), timeout=3.0)
                except Exception:
                    log.warning('upsert_group on my_chat_member skipped for %s', chat_id)
        except Exception:
            log.exception('on_my_chat_member failed')


    async def diagnose_group(self, reply_chat_id: int, group_id: int) -> None:
        """Owner-only: report DB restriction + live Bale API status for a group."""
        lines = [f'🔍 تشخیص گروه `{group_id}`']
        # DB restriction
        try:
            restr = await self.db.is_restricted('group', group_id)
            if restr:
                lines.append(f'⛔ محدودیت دیتابیس فعال — کد `#{restr.get("case_code")}` دلیل: {restr.get("reason")}')
            else:
                lines.append('✅ در دیتابیس محدودیت فعال ندارد')
        except Exception as exc:
            lines.append(f'خطای دیتابیس: {self._short(exc)}')

        # Live API
        try:
            chat = await self.api.call('getChat', {'chat_id': group_id})
            title = chat.get('title') or chat.get('username') or '?'
            ctype = chat.get('type') or '?'
            lines.append(f'✅ getChat: type=`{ctype}` title=`{title}`')
        except Exception as exc:
            lines.append(f'❌ getChat: {self._short(exc)}')
            chat = None

        bot_id = None
        try:
            me = await self.api.call('getMe')
            bot_id = int((me or {}).get('id') or 0) or None
        except Exception as exc:
            lines.append(f'❌ getMe: {self._short(exc)}')

        if bot_id:
            try:
                member = await self.api.call('getChatMember', {'chat_id': group_id, 'user_id': bot_id})
                st = (member or {}).get('status') or '?'
                lines.append(f'وضعیت ربات در گروه: `{st}`')
                # permissions if admin
                if st in ('administrator', 'creator'):
                    can_post = member.get('can_post_messages')
                    can_manage = member.get('can_restrict_members')
                    lines.append(f'can_restrict_members={can_manage} can_post_messages={can_post}')
            except Exception as exc:
                lines.append(f'❌ getChatMember: {self._short(exc)}')

        ping = await self.safe_send(group_id, 'تست ارسال از دستور /checkgroup ✅', parse_mode=None)
        if ping:
            lines.append('✅ sendMessage به گروه موفق بود')
        else:
            lines.append('❌ sendMessage به گروه ناموفق — ربات احتمالاً دسترسی ارسال ندارد یا در گروه نیست')

        await self.safe_send(reply_chat_id, '\n'.join(lines), parse_mode=None)


    @staticmethod
    def _short(exc: Exception) -> str:
        return str(exc).replace('\n',' ')[:180]

    # Rate-limit map for emoji reactions: user_id -> last reaction unix time
    _emoji_last: dict[int, float] = {}

    _EMOJI_RE = re.compile(
        "["
        "\U0001F300-\U0001F9FF"
        "\U0001FA00-\U0001FAFF"
        "\U00002600-\U000027BF"
        "\U0001F1E0-\U0001F1FF"
        "\U0000200D"
        "\U0000FE0F"
        "\U0001F000-\U0001F02F"
        "]+",
        flags=re.UNICODE,
    )

    _EMOJI_MAP = {
        '😂': ['😂', '😆', '🤣'],
        '😆': ['😆', '😂', '🤣'],
        '🤣': ['🤣', '😂', '😆'],
        '😭': ['😭', '🥹', '🥲'],
        '🥹': ['🥹', '😭', '❤️'],
        '😊': ['😊', '☺️', '💚'],
        '☺️': ['☺️', '😊', '🥰'],
        '😍': ['😍', '🥰', '❤️'],
        '🥰': ['🥰', '❤️', '😍'],
        '🤔': ['🤔', '🧐', '👀'],
        '😎': ['😎', '🔥', '✨'],
        '🗿': ['🗿', '😐', '🗿'],
        '💀': ['💀', '😂', '☠️'],
        '❤️': ['❤️', '🥰', '💚'],
        '💔': ['💔', '🥹', '😢'],
        '💚': ['💚', '👍', '✨'],
        '🔥': ['🔥', '✨', '😎'],
        '✨': ['✨', '🌟', '💚'],
        '🌹': ['🌹', '❤️', '🥰'],
        '👍': ['👍', '💚', '👏'],
        '👎': ['👎', '😐'],
        '🙏': ['🙏', '💚', '✨'],
        '🥲': ['🥲', '🥹', '😊'],
        '😡': ['😮', '😐'],
        '😴': ['😴', '💤'],
        '👏': ['👏', '🎉', '👍'],
        '🎉': ['🎉', '✨', '👏'],
        '😳': ['😳', '🤯', '😮'],
        '🤯': ['🤯', '😳', '😮'],
        '😢': ['🥹', '😭', '💔'],
    }

    def _is_emoji_only(self, text: str) -> bool:
        stripped = (text or '').strip()
        if not stripped:
            return False
        without = self._EMOJI_RE.sub('', stripped)
        without = without.replace(' ', '').replace('\u200c', '').replace('\ufe0f', '')
        return len(without) == 0 and bool(self._EMOJI_RE.search(stripped))

    def _pick_emoji_reaction(self, text: str) -> str | None:
        found = self._EMOJI_RE.findall(text or '')
        if not found:
            return None
        # Prefer the first known mapped emoji; else echo the first emoji.
        for token in found:
            base = token[0] if token else ''
            for key, opts in self._EMOJI_MAP.items():
                if key in token or token in key:
                    return random.choice(opts)
            if base in self._EMOJI_MAP:
                return random.choice(self._EMOJI_MAP[base])
        # fallback: first emoji character
        return found[0][0] if found[0] else found[0]

    def social_reply(self, text: str, stats: dict) -> str | None:
        raw = (text or '').strip()
        low = raw.lower().replace('؟', '').replace('?', '').replace('!', '').replace('‌', ' ').strip()
        low = re.sub(r'\s+', ' ', low)

        # Creator / سازنده
        if low in ('سازنده', 'سازنده کیه', 'سازنده کیه؟', 'سازنده ربات', 'کی ساختت', 'کی ساختت؟', 'developer', 'creator'):
            return 'سازنده من: @darkknight_studio هست😊'

        # Bot name mentions
        bot_names = {'مگا', 'بات', 'مگا بات', 'مگابات', 'mega', 'mega bot', 'megabot', 'ربات'}
        if low in bot_names:
            return random.choice([
                'منو میگی؟ جانم درخدمتم دستور چیه؟',
                'جانم 🌱 بگو چی کار برات انجام بدم؟',
                'بله؟ گوش به فرمانم 😄',
                'من اینجام 🍃 دستور بده!',
                'آره منم، بگو چی می‌خوای؟',
            ])

        responses = {
            'سلام ربات': ['سلام🍃 چخبر؟', 'سلام سلام 😄 خوبی؟', 'درود بهت 🌿 چه خبرا؟'],
            'سلام': ['سلاممم 🍃', 'سلام رفیق 😄', 'سلام، خوش اومدی 🌱'],
            'شب خوش': ['شب تو هم خوش 😊', 'خوابای قشنگ ببینی 🌙', 'شب آرومی داشته باشی 🍃'],
            'خوبی': ['مرسی، خوبم 😄 تو چطوری؟', 'خوبم، تو چه خبر؟ 🌿', 'دارم به پیام‌ها می‌رسم 😊'],
            'چخبر': [
                f"هیچی فقط دارم به {stats.get('groups', 0)} گروه رسیدگی میکنم⛱️",
                'خبر خاصی نیست، تو بگو 🌱',
                'همه‌چی آرومه فعلاً 🍃',
            ],
            'خدافظ': ['فعلاً، مراقب خودت باش 🌿', 'بای بای 😄', 'به امید دیدار 🍃'],
        }
        for key, values in responses.items():
            if low == key or (key == 'سلام' and low.startswith('سلام')):
                return random.choice(values)
        return None

    async def welcome_new_members(self, msg: dict, chat: dict) -> None:
        members = msg.get('new_chat_members') or []
        rules = await self.db.get_setting(f"rules:{chat.get('id')}")
        for member in members[:10]:
            await self.db.upsert_user(member)
            name = member.get('first_name') or 'دوست'
            uid = member.get('id')
            text = f"سلام [{name}](uid:{uid}) عزیز⛱️\nخیلی خوش اومدی به گروه *{chat.get('title','گروه')}*\nفقط یادت باشه اگه تونستی یه سر به قوانین گروه بزنی ✨️"
            keyboard = inline([[('📜 قوانین گروه','rules:show')]]) if rules else None
            await self.safe_send(int(chat['id']), text, keyboard)

    GUIDE_ENTRIES = [
        ('📌 دستورات عمومی',
         '• کیبورد پایین: پشتیبانی، حمایت مالی، تیم توسعه، آمار، منوی بیشتر\n'
         '• `/start` — شروع و نمایش منو\n'
         '• `راهنما ربات` یا دکمه 📚 راهنمای ربات\n'
         '• `درباره ما` — معرفی ربات\n'
         '• `تیم من` — پرونده محدودیت شخصی\n'
         'دسترسی: همه کاربران'),
        ('🧩 گفت‌وگو و واکنش',
         '• سلام / خوبی / چخبر / شب خوش / خدافظ\n'
         '• `مگا` یا `بات` یا `مگا بات` — پاسخ‌های متنوع\n'
         '• `سازنده` — @darkknight_studio\n'
         '• پیام فقط‌ایموجی → یک ایموجی واکنش (بدون متن)\n'
         'دسترسی: همه کاربران'),
        ('🎮 بازی‌ها (گروه)',
         '• `دوز` / `بازی دوز`\n'
         '• `شیر یا خط`\n'
         '• `هرکی تک بیاره`\n'
         'روش: در گروه دستور را بفرست.\n'
         'دسترسی: همه اعضای گروه'),
        ('🖼 تبدیل به گیف',
         'روی عکس/ویدئو ریپلای کن:\n'
         '• `گیف` — بدون متن\n'
         '• `گیف متن دلخواه` — با نوشته\n'
         'بدون ریپلای فقط راهنما نشان داده می‌شود.\n'
         'دسترسی: همه کاربران'),
        ('📌 پین و قوانین',
         '• `پین` — ریپلای + پین (ادمین گروه)\n'
         '• `تنظیم قوانین متن...` — ذخیره قوانین (ادمین)\n'
         '• `قوانین` — نمایش قوانین\n'
         'دسترسی: ادمین گروه برای تنظیم؛ همه برای دیدن'),
        ('🔇 سکوت (گروه)',
         'سطح: محدودیت در همان گروه با API بله.\n\n'
         'سکوت: ریپلای روی پیام کاربر + `سکوت`\n'
         'حذف سکوت: ریپلای + `حذف سکوت`\n'
         'بدون ریپلای اجرا نمی‌شود.\n'
         'مالک ربات هدف قرار نمی‌گیرد.\n'
         'دسترسی: ادمین گروه + دسترسی Restrict Members برای ربات'),
        ('⛔ بن و آن‌بن (گروه)',
         'سطح: بن در همان گروه.\n\n'
         'بن: ریپلای + `بن`\n'
         'حذف بن / انبن / آنبن: ریپلای + همان دستور\n'
         'کیک: ریپلای + `کیک` (اخراج موقت)\n'
         'مالک ربات قابل بن نیست.\n'
         'بن پنل ادمین ربات جدا از بن گروه است.\n'
         'دسترسی: ادمین گروه'),
        ('🛡️ پنل ادمین و مالک',
         '• `/admin` — پنل ادمین ربات\n'
         '• `/owner` — فقط مالک\n'
         'بن پنل: محدودیت قابلیت‌های ربات + کد ۴ رقمی (ماندگار در دیتابیس).\n'
         'دسترسی: ادمین ربات / مالک'),
        ('💚 حمایت مالی',
         'دکمه 💚 حمایت مالی\n'
         'مراحل: معرفی → مبلغ → پرداخت → نام/ناشناس → تأیید مالک → اعلام\n'
         'اعلام فقط به پیوی کاربران Start‌کرده.\n'
         'دسترسی: همه برای درخواست؛ مالک برای تأیید'),
        ('📊 آمار و پیگیری',
         '• 📊 آمار و اطلاعات\n'
         '• تیم من — پرونده شخصی\n'
         '• پنل ادمین → کد پیگیری ۴ رقمی\n'
         'دسترسی: آمار برای همه؛ جست‌وجو برای ادمین'),
    ]

    async def show_guide(self, chat_id: int, page: int, edit_id: int | None = None) -> None:
        entries = self.GUIDE_ENTRIES
        page_size = 2
        total_pages = max(1, (len(entries) + page_size - 1) // page_size)
        page = max(0, min(page, total_pages - 1))
        start = page * page_size
        text = (
            f'*📚 راهنمای MEGA BOT* — صفحه {page + 1}/{total_pages}\n'
            'یک بخش را انتخاب کن تا دستور دقیق و سطح دسترسی را ببینی.'
        )
        rows = [[(label, f'guide:item:{start + i}')] for i, (label, _) in enumerate(entries[start:start + page_size])]
        controls = []
        if page > 0:
            controls.append(('⬅️ قبلی', f'guide:page:{page - 1}'))
        if start + page_size < len(entries):
            controls.append(('بعدی ➡️', f'guide:page:{page + 1}'))
        if controls:
            rows.append(controls)
        rows.append([('↩️ برگشت', 'home'), ('✖️ بستن', 'close')])
        kb = inline(rows)
        if edit_id:
            await self.safe_edit(chat_id, edit_id, text, kb)
        else:
            await self.safe_send(chat_id, text, kb)

    @staticmethod
    def _md_escape(text: str) -> str:
        """Escape characters that break Bale Markdown in display names."""
        if not text:
            return ''
        out = []
        for ch in str(text):
            if ch in r'_*[]()`':
                out.append('\\' + ch)
            else:
                out.append(ch)
        return ''.join(out)

    def build_donation_announcement(self, donation: dict) -> str:
        """Public announcement for an approved donation (private users only).

        Named:  [Name](uid:USER_ID) مارو حمایت کرد🥹✨️
                💰 مبلغ حمایت: AMOUNT تومان
        Anonymous: یه نفر مارو حمایت کرد🥹✨️
                   💰 مبلغ حمایت: AMOUNT تومان
        """
        amount = int(donation['amount'])
        amount_line = f'💰 مبلغ حمایت: {amount:,} تومان'
        if donation.get('show_name'):
            name = (donation.get('user_name') or 'کاربر').strip() or 'کاربر'
            safe_name = self._md_escape(name)
            user_id = int(donation['user_id'])
            return f'[{safe_name}](uid:{user_id}) مارو حمایت کرد🥹✨️\n{amount_line}'
        return f'یه نفر مارو حمایت کرد🥹✨️\n{amount_line}'

    def donation_broadcast_keyboard(self) -> dict:
        """Inline button under every public donation announcement."""
        return inline([[('توهم حمایت کن 🌱', 'donate:start')]])

    async def show_donation_intro(self, chat_id: int, edit_id: int | None = None) -> None:
        intro = await self.db.get_setting(
            'donation_intro',
            'میتونی به‌صورت دلخواه از تیم ما حمایت کنی 🌱😭💞\n\nواقعاً دوست داری حمایتمون کنی؟ 😆',
        )
        text = f'*حمایت مالی MEGA BOT*\n\n{intro}'
        keyboard = inline([
            [('💚 بله، حمایت می‌کنم', 'donate:yes'), ('نه، ممنون', 'donate:cancel')],
            [('↩️ برگشت', 'home'), ('✖️ بستن', 'close')],
        ])
        if edit_id:
            await self.safe_edit(chat_id, edit_id, text, keyboard)
        else:
            await self.safe_send(chat_id, text, keyboard)

    async def handle_donation_callback(self, uid: int, chat_id: int, message_id: int, data: str, user: dict) -> None:
        # Public announcement button — open a fresh independent donation flow.
        if data == 'donate:start':
            self.states.pop(uid, None)
            await self.show_donation_intro(chat_id, message_id)
            return
        if data == 'donate:yes':
            self.states[uid] = {'step': 'donation_amount', 'chat_id': chat_id, 'user': user}
            await self.safe_edit(
                chat_id,
                message_id,
                'مقدار حمایت رو به تومان وارد کن؛ فقط عدد 😊\nمثال: `2000` یا `2.000`',
                inline([[('↩️ انصراف', 'donate:cancel'), ('✖️ بستن', 'close')]]),
            )
            return
        if data == 'donate:cancel':
            self.states.pop(uid, None)
            await self.safe_edit(chat_id, message_id, 'باشه، هر وقت خواستی می‌تونی برگردی 💚', None)
            await self.safe_send(chat_id, '*منوی اصلی MEGA BOT*', USER_KEYBOARD)
            return
        if data == 'donate:paid':
            state = self.states.get(uid) or {}
            if state.get('step') != 'donation_confirm_paid' or int(state.get('chat_id', 0)) != chat_id:
                await self.safe_send(
                    chat_id,
                    'این مرحله منقضی شده؛ اگر هنوز مایل هستی از منوی حمایت مالی دوباره شروع کن.',
                    USER_KEYBOARD,
                )
                return
            state['step'] = 'donation_visibility'
            await self.safe_edit(
                chat_id,
                message_id,
                '*حمایتت رو ثبت کنیم؟* 🌿\nاسمت رو همراه مبلغ به بقیه کاربران نمایش بدیم؟',
                inline([
                    [('بله، اسمم نمایش داده شود', 'donate:visibility:yes')],
                    [('خیر، ناشناس باشد', 'donate:visibility:no')],
                    [('↩️ پشیمان شدم / اتمام', 'donate:cancel')],
                ]),
            )
            return
        if data.startswith('donate:visibility:'):
            state = self.states.get(uid) or {}
            if state.get('step') != 'donation_visibility' or int(state.get('chat_id', 0)) != chat_id:
                await self.safe_send(
                    chat_id,
                    'این درخواست منقضی شده؛ از منوی حمایت مالی دوباره شروع کن.',
                    USER_KEYBOARD,
                )
                return
            show_name = data.endswith(':yes')
            amount = int(state['amount'])
            u = state.get('user') or user
            display_name = (u.get('first_name') or u.get('name') or 'کاربر').strip() or 'کاربر'
            donation_id = await self.db.create_donation(uid, display_name, amount, show_name)
            self.states.pop(uid, None)
            visibility_label = 'بله' if show_name else 'خیر، ناشناس'
            owner_text = (
                f'درخواست تأیید حمایت مالی\n\n'
                f'کاربر {display_name} با شناسه عددی {uid} ادعا می‌کند مبلغ {amount:,} تومان را حمایت کرده است.\n'
                f'نمایش نام در پیام همگانی: {visibility_label}\n'
                f'شناسه درخواست: #{donation_id}\n\n'
                f'آیا درست می‌گوید؟'
            )
            owner_notice = await self.safe_send(
                OWNER_ID,
                owner_text,
                inline([
                    [('✅ آره، تأیید و اعلام', 'donation_review:approve:' + str(donation_id)),
                     ('❌ خیر، رد درخواست', 'donation_review:reject:' + str(donation_id))],
                ]),
                parse_mode=None,
            )
            if not owner_notice:
                await self.db.review_donation(donation_id, False, OWNER_ID)
                await self.safe_edit(
                    chat_id,
                    message_id,
                    'ارسال درخواست به پیوی مالک انجام نشد. لطفاً بعداً دوباره تلاش کن یا از پشتیبانی کمک بگیر.',
                    None,
                )
                return
            await self.safe_edit(
                chat_id,
                message_id,
                'درخواستت برای بررسی به مالک ارسال شد 🌱\nبعد از بررسی، اگر تأیید شود طبق انتخابت اعلام می‌شود.',
                None,
            )
            return

    async def handle_donation_review(self, uid: int, chat_id: int, message_id: int, data: str) -> None:
        if uid != OWNER_ID:
            await self.safe_send(chat_id, '⛔ فقط مالک ربات می‌تواند درخواست حمایت مالی را تأیید کند.')
            return
        try:
            action, raw_id = data.rsplit(':', 1)
            donation_id = int(raw_id)
        except (ValueError, TypeError):
            return
        approve = action == 'donation_review:approve'
        # Atomic: only a pending request can transition once.
        donation = await self.db.review_donation(donation_id, approve, uid)
        if not donation:
            await self.safe_edit(chat_id, message_id, 'این درخواست قبلاً بررسی شده یا پیدا نشد.', None)
            return
        await self.db.log(
            uid,
            'donation_' + ('approved' if approve else 'rejected'),
            'donation',
            donation_id,
            {'amount': int(donation['amount']), 'show_name': bool(donation['show_name'])},
        )
        if not approve:
            await self.safe_edit(
                chat_id,
                message_id,
                f'❌ درخواست حمایت `#{donation_id}` رد شد. هیچ پیام همگانی ارسال نشد و آمار تغییر نکرد.',
                None,
            )
            return

        announcement = self.build_donation_announcement(donation)
        keyboard = self.donation_broadcast_keyboard()
        sent = failed = 0
        for target_id in await self.db.private_user_ids():
            try:
                await self.api.send_message(
                    target_id,
                    announcement,
                    reply_markup=keyboard,
                    parse_mode='Markdown',
                )
                sent += 1
            except Exception as exc:
                failed += 1
                log.debug('Donation announcement DM failed for %s: %s', target_id, exc)
            if (sent + failed) % 20 == 0:
                await asyncio.sleep(0.5)
        await self.db.set_donation_broadcast_counts(donation_id, sent, failed)
        await self.safe_edit(
            chat_id,
            message_id,
            f'✅ حمایت `#{donation_id}` تأیید شد.\nمبلغ {int(donation["amount"]):,} تومان به آمار اضافه شد.\nارسال خصوصی: {sent} موفق، {failed} ناموفق.',
            None,
        )
        # Always notify the donor on successful approval.
        try:
            await self.safe_send(
                int(donation['user_id']),
                '💚 حمایت مالی‌ات تأیید شد و مبلغ به آمار ربات اضافه شد! از همراهی‌ت ممنونیم 🌱😊',
                USER_KEYBOARD,
            )
        except Exception as exc:
            log.debug('Donor success notice failed for %s: %s', donation.get('user_id'), exc)

    def stats_text(self, s: dict) -> str:
        return (f"*📊 آمار MEGA BOT*\n\n*👤 کل کاربران:* {s['users']}\n*🆕 کاربران جدید امروز:* {s['new_today']}\n"
                f"*👥 گروه‌های ثبت‌شده:* {s['groups']}\n*🙋 کاربران استفاده‌کننده در گروه‌ها:* {s['group_users']}\n"
                f"*⛔ محدودیت فعال کاربران:* {s['banned_users']}\n*🚫 گروه‌های محدود:* {s['banned_groups']}\n"
                f"*💚 مجموع حمایت مالی:* {s['donations']} تومان\n\n*ℹ️ نسخه {BOT_VERSION}*")

    async def show_admin(self, chat_id: int, uid: int, owner: bool, edit_id: int | None = None, page: int = 0) -> None:
        if not owner:
            admin = await self.db.get_admin(uid)
            perms = admin['permissions'] if admin else {}
            items = []
            if perms.get('stats'): items.append(('📊 آمار و گزارش‌ها','adm:stats'))
            if perms.get('search'): items += [('👤 جست‌وجوی کاربر','adm:search:user'),('👥 جست‌وجوی گروه','adm:search:group')]
            if perms.get('case_lookup'): items.append(('🎫 بررسی کد پیگیری','adm:case'))
            if perms.get('ban_user') or perms.get('unban_user'): items.append(('👤 محدودیت کاربران','adm:search:user'))
            if perms.get('ban_group') or perms.get('unban_group'): items.append(('👥 محدودیت گروه‌ها','adm:search:group'))
            if perms.get('edit_about'): items.append(('📝 ویرایش درباره ما','own:about'))
            if perms.get('edit_team'): items.append(('👨‍💻 تیم توسعه','own:team'))
            if not items: items = [('📊 آمار و گزارش‌ها','adm:stats')]
        else:
            items = [
                ('📊 آمار و گزارش‌ها','adm:stats'),('👤 جست‌وجوی کاربر','adm:search:user'),('👥 جست‌وجوی گروه','adm:search:group'),
                ('🎫 بررسی کد پیگیری','adm:case'),('🧾 گزارش فعالیت‌ها','adm:logs'),('🛡️ مدیریت ادمین‌ها','own:admins'),
                ('📝 ویرایش درباره ما','own:about'),('👨‍💻 تیم توسعه‌دهنده','own:team'),('💚 تنظیمات حمایت مالی','own:donations'),('⚙️ تنظیمات مالک','own:settings')]
        # Exactly three content items per page, plus optional previous/next and back/close.
        start = page*2
        rows = [[(label,data)] for label,data in items[start:start+2]]
        controls=[]
        if page>0: controls.append(('⬅️ قبلی',f'admin:page:{page-1}'))
        if start+2<len(items): controls.append(('بعدی ➡️',f'admin:page:{page+1}'))
        if controls: rows.append(controls)
        rows.append([('↩️ برگشت','home'),('✖️ بستن','close')])
        text = '*پنل مالک MEGA BOT*\nیک بخش رو انتخاب کن.' if owner else '*پنل ادمین MEGA BOT*\nدسترسی‌ها بر اساس مجوزهای تو نمایش داده می‌شوند.'
        if edit_id: await self.safe_edit(chat_id,edit_id,text,inline(rows))
        else: await self.safe_send(chat_id,text,inline(rows))

    async def has_perm(self, uid: int, perm: str) -> bool:
        if uid == OWNER_ID: return True
        admin = await self.db.get_admin(uid)
        return bool(admin and (admin.get('permissions') or {}).get(perm, False))

    async def handle_admin_callback(self, uid: int, chat_id: int, message_id: int, data: str) -> None:
        owner = uid == OWNER_ID
        if not owner and not await self.db.is_admin(uid):
            await self.safe_send(chat_id,'⛔ دسترسی مدیریت نداری.')
            return
        if data == 'adm:stats':
            if not await self.has_perm(uid,'stats'): return await self.safe_send(chat_id,'⛔ مجوز مشاهده آمار نداری.')
            await self.safe_edit(chat_id,message_id,self.stats_text(await self.db.stats()),inline([[('↩️ برگشت','admin:page:0'),('✖️ بستن','close')]])); return
        if data in ('adm:search:user','adm:search:group'):
            if not await self.has_perm(uid,'search'): return await self.safe_send(chat_id,'⛔ مجوز جست‌وجو نداری.')
            target_type = data.rsplit(':',1)[1]
            self.states[uid]={'step':'search','target_type':target_type,'chat_id':chat_id,'message_id':message_id}
            await self.safe_edit(chat_id,message_id,f"*جست‌وجوی {'کاربر' if target_type=='user' else 'گروه'}*\nشناسه عددی یا `@username` رو در همین گفتگو بفرست.",inline([[('↩️ برگشت','admin:page:0'),('✖️ بستن','close')]])); return
        if data == 'adm:case':
            if not await self.has_perm(uid,'case_lookup'): return await self.safe_send(chat_id,'⛔ مجوز بررسی پرونده نداری.')
            self.states[uid]={'step':'case_lookup','chat_id':chat_id,'message_id':message_id}
            await self.safe_edit(chat_id,message_id,'کد پیگیری رو بفرست؛ مثال: `#5453`',inline([[('↩️ برگشت','admin:page:0'),('✖️ بستن','close')]])); return
        if data == 'adm:logs':
            if not owner and not await self.has_perm(uid,'settings'): return await self.safe_send(chat_id,'⛔ مشاهده گزارش فعالیت‌ها فقط برای مالک یا ادمین مجاز است.')
            async with self.db._pool().acquire() as c:
                rows = await c.fetch('SELECT actor_id,action,target_type,target_id,created_at FROM audit_logs ORDER BY id DESC LIMIT 10')
            body = '*آخرین فعالیت‌ها*\n\n' + ('\n'.join(f"• `{r['actor_id']}` — {r['action']} — {r['target_type'] or '-'} `{r['target_id'] or '-'}`" for r in rows) if rows else 'هنوز فعالیتی ثبت نشده.')
            await self.safe_edit(chat_id,message_id,body,inline([[('↩️ برگشت','admin:page:0'),('✖️ بستن','close')]])); return
        if data == 'own:about':
            if not await self.has_perm(uid,'edit_about'): return await self.safe_send(chat_id,'⛔ مجوز ویرایش درباره ما نداری.')
            self.states[uid]={'step':'edit_about','chat_id':chat_id,'message_id':message_id}
            current = await self.db.get_setting('about','')
            await self.safe_edit(chat_id,message_id,f"*ویرایش درباره ما*\nمتن فعلی:\n{current}\n\nحالا متن جدید رو در همین گفتگو بفرست.",inline([[('↩️ برگشت','admin:page:0'),('✖️ بستن','close')]])); return
        if data == 'own:team':
            if not await self.has_perm(uid,'edit_team'): return await self.safe_send(chat_id,'⛔ مجوز مدیریت تیم نداری.')
            team = await self.db.get_team()
            rows = [[('➕ افزودن عضو','team:add')]]
            rows += [[(f"👤 {m['name'][:25]}",f"team:member:{m['id']}")] for m in team[:3]]
            rows.append([('↩️ برگشت','admin:page:0'),('✖️ بستن','close')])
            text='*مدیریت تیم توسعه‌دهنده*\n\n'+('\n'.join(f"`{m['id']}`. *{m['name']}* — {m['title']}" for m in team) if team else 'اعضای تیم هنوز ثبت نشده‌اند.')
            await self.safe_edit(chat_id,message_id,text,inline(rows)); return
        if data.startswith('team:member:'):
            if not await self.has_perm(uid,'edit_team'): return
            member_id=int(data.rsplit(':',1)[1])
            team=await self.db.get_team(); member=next((m for m in team if int(m['id'])==member_id),None)
            if not member: return await self.safe_send(chat_id,'عضو تیم پیدا نشد.')
            await self.safe_edit(chat_id,message_id,f"*{member['name']}*\nعنوان: {member['title']}\nتوضیح: {member['description'] or 'ندارد'}\nآیدی: @{member['username'] or 'ندارد'}",inline([[('✏️ ویرایش عضو',f'team:edit:{member_id}')],[('🗑️ حذف عضو',f'team:del:{member_id}')],[('↩️ برگشت','own:team'),('✖️ بستن','close')]])); return
        if data.startswith('team:edit:'):
            if not await self.has_perm(uid,'edit_team'): return
            member_id=int(data.rsplit(':',1)[1])
            team=await self.db.get_team(); member=next((m for m in team if int(m['id'])==member_id),None)
            if not member: return await self.safe_send(chat_id,'عضو تیم پیدا نشد.')
            self.states[uid]={'step':'team_edit_name','chat_id':chat_id,'message_id':message_id,'member_id':member_id,'team_data':{}}
            await self.safe_send(chat_id,f"نام جدید عضو را بفرست. نام فعلی: {member['name']}")
            return
        if data == 'team:add':
            if not await self.has_perm(uid,'edit_team'): return
            self.states[uid]={'step':'team_add_name','chat_id':chat_id,'message_id':message_id,'team_data':{}}
            await self.safe_edit(chat_id,message_id,'نام عضو جدید تیم را بفرست.',inline([[('↩️ برگشت','own:team'),('✖️ بستن','close')]])); return
        if data.startswith('team:del:'):
            if not await self.has_perm(uid,'edit_team'): return
            member_id=int(data.rsplit(':',1)[1])
            ok=await self.db.delete_team_member(member_id,uid)
            await self.safe_edit(chat_id,message_id,'عضو حذف شد.' if ok else 'این عضو پیدا نشد.',inline([[('↩️ برگشت','own:team'),('✖️ بستن','close')]])); return
        if data == 'own:admins':
            if not owner: return await self.safe_send(chat_id,'⛔ فقط مالک می‌تواند ادمین‌ها را مدیریت کند.')
            admins=await self.db.list_admins(10,0)
            rows=[[('➕ افزودن ادمین','own:admin_add')]]
            for a in admins[:2]: rows.append([(f"⚙️ {a['display_name'] or a['user_id']}",f"own:admin:{a['user_id']}")])
            rows.append([('↩️ برگشت','admin:page:0'),('✖️ بستن','close')])
            text='*مدیریت ادمین‌ها*\n'+('\n'.join(f"• `{a['user_id']}` — {'فعال' if a['active'] else 'غیرفعال'}" for a in admins) if admins else 'هنوز ادمینی ثبت نشده.')
            await self.safe_edit(chat_id,message_id,text,inline(rows)); return
        if data == 'own:admin_add':
            if not owner: return
            self.states[uid]={'step':'admin_add_id','chat_id':chat_id,'message_id':message_id}
            await self.safe_edit(chat_id,message_id,'شناسه عددی ادمین جدید رو بفرست.',inline([[('↩️ برگشت','own:admins'),('✖️ بستن','close')]])); return
        if data.startswith('own:admin:'):
            if not owner: return
            target=int(data.rsplit(':',1)[1])
            async with self.db._pool().acquire() as c:
                row=await c.fetchrow('SELECT * FROM admins WHERE user_id=$1',target)
            if not row: return await self.safe_send(chat_id,'ادمین پیدا نشد.')
            perms=row['permissions'] or {}
            rows=[[('🗑️ حذف ادمین',f'own:admin_del:{target}')]]
            for key,label in list(PERMISSION_LABELS.items())[:2]: rows.append([( ('✅ ' if perms.get(key) else '▫️ ')+label, f'perm:toggle:{target}:{key}')])
            rows.append([('بعدی ➡️',f'own:admin_perms:{target}:1')])
            rows.append([('↩️ برگشت','own:admins'),('✖️ بستن','close')])
            await self.safe_edit(chat_id,message_id,f"*تنظیم ادمین* `{target}`\nروی هر مجوز بزن تا روشن/خاموش شود.",inline(rows)); return
        if data.startswith('own:admin_perms:'):
            if not owner: return
            _,_,target,page=data.split(':')
            target=int(target); page=int(page)
            keys=list(PERMISSION_LABELS.items()); chunk=keys[page*2:(page+1)*2]
            async with self.db._pool().acquire() as c: perms=await c.fetchval('SELECT permissions FROM admins WHERE user_id=$1',target) or {}
            rows=[[(('✅ ' if perms.get(k) else '▫️ ')+label,f'perm:toggle:{target}:{k}')] for k,label in chunk]
            if page>0: rows.append([('⬅️ قبلی',f'own:admin_perms:{target}:{page-1}')])
            if (page+1)*2 < len(keys): rows.append([('بعدی ➡️',f'own:admin_perms:{target}:{page+1}')])
            rows.append([('↩️ برگشت',f'own:admin:{target}'),('✖️ بستن','close')])
            await self.safe_edit(chat_id,message_id,f'*دسترسی‌های ادمین* `{target}`',inline(rows)); return
        if data.startswith('own:admin_del:'):
            if not owner: return
            target=int(data.rsplit(':',1)[1])
            async with self.db._pool().acquire() as c: await c.execute('DELETE FROM admins WHERE user_id=$1',target)
            await self.db.log(uid,'admin_removed','user',target)
            await self.safe_edit(chat_id,message_id,'ادمین حذف شد.',inline([[('↩️ برگشت','own:admins'),('✖️ بستن','close')]])); return
        if data == 'own:settings':
            if not owner: return
            await self.safe_edit(chat_id,message_id,'*تنظیمات مالک*\nنسخه: '+BOT_VERSION+'\nشناسه مالک: `'+str(OWNER_ID)+'`\n\nتنظیمات محرمانه را در config.py یا متغیرهای محیطی Railway تغییر بده.',inline([[('👨‍💻 تیم توسعه','own:team'),('📝 درباره ما','own:about')],[('💚 حمایت مالی','own:donations')],[('↩️ برگشت','admin:page:0'),('✖️ بستن','close')]])); return
        if data.startswith('mod:'):
            await self.handle_moderation_callback(uid,chat_id,message_id,data)

    async def handle_permission_callback(self, uid: int, chat_id: int, message_id: int, data: str) -> None:
        if uid != OWNER_ID: return
        _,action,target,key=data.split(':',3)
        target=int(target)
        async with self.db._pool().acquire() as c:
            row=await c.fetchrow('SELECT permissions FROM admins WHERE user_id=$1',target)
            if not row: return await self.safe_send(chat_id,'ادمین پیدا نشد.')
            perms=row['permissions'] or DEFAULT_PERMS.copy()
            perms[key]=not bool(perms.get(key,False))
            await c.execute('UPDATE admins SET permissions=$2 WHERE user_id=$1',target,perms)
        await self.db.log(uid,'admin_permission_changed','user',target,{'permission':key,'enabled':perms[key]})
        # Refresh the first permissions screen.
        await self.handle_admin_callback(uid,chat_id,message_id,f'own:admin:{target}')

    async def handle_target_select(self, uid: int, chat_id: int, message_id: int, data: str) -> None:
        if uid != OWNER_ID and not await self.db.is_admin(uid):
            return await self.safe_send(chat_id, '⛔ دسترسی مدیریت نداری.')
        _, _, target_type, target_id = data.split(':', 3)
        target_id = int(target_id)
        if not await self.has_perm(uid, 'search'):
            return await self.safe_send(chat_id, '⛔ مجوز جست‌وجو نداری.')
        rows = await self.db.search_target(str(target_id), target_type)
        target = rows[0] if rows else {'id': target_id, 'name': str(target_id), 'username': None}
        can_ban = await self.has_perm(uid, 'ban_user' if target_type == 'user' else 'ban_group')
        text = f"*جزئیات {'کاربر' if target_type == 'user' else 'گروه'}*\nنام: {target.get('name') or 'نامشخص'}\nشناسه: `{target_id}`\nآیدی: @{target.get('username') or 'ندارد'}"
        await self.safe_edit(chat_id, message_id, text, user_actions(target_type, target_id, can_ban))

    async def handle_moderation_callback(self, uid: int, chat_id: int, message_id: int, data: str) -> None:
        _, action, target_type, target_id = data.split(':',3)
        target_id=int(target_id)
        perm = ('ban_' if action=='ban' else 'unban_')+target_type
        if not await self.has_perm(uid,perm):
            await self.safe_send(chat_id,'⛔ این دسترسی برای حساب تو فعال نیست.')
            return
        if action == 'unban':
            case=await self.db.lift_case(target_type,target_id,uid)
            if not case:
                await self.safe_edit(chat_id,message_id,'برای این مورد محدودیت فعالی ثبت نشده است.',inline([[('↩️ برگشت','admin:page:0'),('✖️ بستن','close')]])); return
            await self.safe_edit(chat_id,message_id,f"✅ محدودیت برداشته شد.\nکد پرونده: `#{case['case_code']}`",inline([[('↩️ برگشت','admin:page:0'),('✖️ بستن','close')]])); return
        self.states[uid]={'step':'ban_reason','target_type':target_type,'target_id':target_id,'chat_id':chat_id,'message_id':message_id}
        await self.safe_edit(chat_id,message_id,'دلیل محدودیت را بفرست.',inline([[('↩️ برگشت','admin:page:0'),('✖️ بستن','close')]]))

    async def handle_state(self, chat_id: int, uid: int, user: dict, text: str, state: dict, msg: dict) -> bool:
        step=state.get('step')
        if step == 'search':
            results=await self.db.search_target(text,state['target_type'])
            if not results:
                await self.safe_send(chat_id,'موردی پیدا نشد. فقط کاربرها و گروه‌هایی قابل جست‌وجو هستند که ربات قبلاً آن‌ها را ثبت کرده باشد.',inline([[('↩️ برگشت','admin:page:0'),('✖️ بستن','close')]]))
                self.states.pop(uid,None); return True
            target_type=state['target_type']
            rows=[]
            for r in results[:3]: rows.append([(f"{r.get('name') or 'بدون نام'} · {r['id']}",f"adm:target:{target_type}:{r['id']}")])
            rows.append([('↩️ برگشت','admin:page:0'),('✖️ بستن','close')])
            await self.safe_send(chat_id,'*نتایج جست‌وجو*\nمورد رو انتخاب کن:',inline(rows))
            self.states.pop(uid,None); return True
        if step == 'case_lookup':
            match=re.search(r'(\d{4})',text)
            self.states.pop(uid,None)
            if not match:
                await self.safe_send(chat_id,'کد باید ۴ رقم داشته باشد؛ مثال `#5453`'); return True
            case=await self.db.find_case(int(match.group(1)))
            if not case:
                await self.safe_send(chat_id,'این کد پیگیری در دیتابیس پیدا نشد.'); return True
            await self.safe_send(chat_id,self.case_text(case),inline([[('↩️ برگشت','admin:page:0'),('✖️ بستن','close')]])); return True
        if step == 'donation_amount':
            digits = str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')
            normalized = text.translate(digits).replace(',', '').replace('٬', '').replace(' ', '').replace('٫', '.')
            normalized = normalized.replace('.', '')
            if not normalized.isdigit() or int(normalized) < 1 or int(normalized) > 1000000000000:
                await self.safe_send(chat_id, 'مبلغ باید فقط عدد مثبت باشد؛ مثال: `2000` یا `2.000` تومان. دوباره بفرست.')
                return True
            amount = int(normalized)
            card = await self.db.get_setting('donation_card', '6219861958356742')
            card_name = await self.db.get_setting('donation_card_name', 'آرتین گلستانی')
            owner_username = str(await self.db.get_setting('donation_owner_username', 'commander04')).lstrip('@')
            instructions = await self.db.get_setting('donation_instructions', 'دونیت کاملاً اختیاری است.')
            self.states[uid] = {'step':'donation_confirm_paid','chat_id':chat_id,'amount':amount,'user':user}
            await self.safe_send(chat_id,
                f'*مبلغ انتخابی: {amount:,} تومان*\n\n{instructions}\n\n💌 *روش اول:* به پیوی مالک برو و در صورت تمایل پاکت هدیه بفرست: `@{owner_username}`\n\n💳 *روش دوم: واریز کارت‌به‌کارت*\nشماره کارت: `{card}`\nبه نام: *{card_name}*\n\nبعد از انجام حمایت، یکی از گزینه‌های زیر رو بزن 😊🌱',
                inline([[('✅ واریز کردم','donate:paid'),('↩️ پشیمان شدم / اتمام','donate:cancel')]]))
            return True
        if step == 'edit_donation_setting':
            key = state.get('setting_key')
            if not text.strip():
                await self.safe_send(chat_id, 'مقدار خالی ذخیره نمی‌شود؛ متن یا مقدار جدید را بفرست.')
                return True
            if key == 'donation_card':
                normalized = text.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩','01234567890123456789')).replace(' ','').replace('-','')
                if not normalized.isdigit() or len(normalized) < 16 or len(normalized) > 19:
                    await self.safe_send(chat_id, 'شماره کارت باید ۱۶ تا ۱۹ رقم باشد. دوباره بفرست.')
                    return True
                text = normalized
            if key == 'donation_owner_username': text = text.strip().lstrip('@')
            await self.db.set_setting(key, text, uid)
            await self.db.log(uid, 'donation_setting_updated', details={'setting':key})
            self.states.pop(uid, None)
            await self.safe_send(chat_id, '✅ تنظیم حمایت مالی ذخیره شد.', inline([[('💚 برگشت به تنظیمات حمایت','own:donations')],[('↩️ پنل مالک','admin:page:0'),('✖️ بستن','close')]]))
            return True
        if step == 'edit_about':
            if not text: await self.safe_send(chat_id,'متن خالی قابل ذخیره نیست.'); return True
            await self.db.set_setting('about',text,uid); await self.db.log(uid,'about_updated',details={'length':len(text)})
            self.states.pop(uid,None); await self.safe_send(chat_id,'✅ متن «درباره ما» ذخیره شد.',inline([[('↩️ برگشت','admin:page:0'),('✖️ بستن','close')]])); return True
        if step == 'admin_add_id':
            if not text.isdigit(): await self.safe_send(chat_id,'شناسه باید عددی باشد؛ دوباره بفرست.'); return True
            target=int(text)
            if target==OWNER_ID: await self.safe_send(chat_id,'مالک را نمی‌توان به‌عنوان ادمین معمولی اضافه کرد.'); return True
            async with self.db._pool().acquire() as c:
                await c.execute('INSERT INTO admins(user_id,display_name,permissions,added_by) VALUES($1,$2,$3,$4) ON CONFLICT(user_id) DO UPDATE SET active=TRUE,added_by=EXCLUDED.added_by',target,'',DEFAULT_PERMS,uid)
            await self.db.log(uid,'admin_added','user',target)
            self.states.pop(uid,None); await self.safe_send(chat_id,f'✅ ادمین `{target}` اضافه شد.\nمجوز بن کاربر و گروه به‌صورت پیش‌فرض خاموش است.',inline([[('⚙️ تنظیم دسترسی‌ها',f'own:admin:{target}')],[('↩️ برگشت','own:admins'),('✖️ بستن','close')]])); return True
        if step == 'team_edit_name':
            state['team_data']['name']=text; state['step']='team_edit_title'
            await self.safe_send(chat_id,'عنوان جدید را بفرست.'); return True
        if step == 'team_edit_title':
            state['team_data']['title']=text; state['step']='team_edit_description'
            await self.safe_send(chat_id,'توضیح جدید را بفرست؛ برای خالی‌گذاشتن یک خط تیره بفرست.'); return True
        if step == 'team_edit_description':
            state['team_data']['description']='' if text=='-' else text; state['step']='team_edit_username'
            await self.safe_send(chat_id,'آیدی عمومی جدید را بفرست؛ اگر ندارد یک خط تیره بفرست.'); return True
        if step == 'team_edit_username':
            d=state['team_data']; username='' if text=='-' else text.lstrip('@'); member_id=int(state['member_id'])
            async with self.db._pool().acquire() as c:
                await c.execute('UPDATE team_members SET name=$2,title=$3,description=$4,username=$5 WHERE id=$1',member_id,d['name'],d['title'],d['description'],username)
            await self.db.log(uid,'team_member_updated','team_member',member_id)
            self.states.pop(uid,None); await self.safe_send(chat_id,'✅ اطلاعات عضو تیم ویرایش شد.',inline([[('↩️ برگشت','own:team'),('✖️ بستن','close')]])); return True
        if step == 'team_add_name':
            state['team_data']['name']=text; state['step']='team_add_title'
            await self.safe_send(chat_id,'عنوان یا نقش این عضو را بفرست.'); return True
        if step == 'team_add_title':
            state['team_data']['title']=text; state['step']='team_add_description'
            await self.safe_send(chat_id,'توضیح کوتاه عضو را بفرست؛ اگر توضیح ندارد یک خط تیره بفرست.'); return True
        if step == 'team_add_description':
            state['team_data']['description']='' if text=='-' else text; state['step']='team_add_username'
            await self.safe_send(chat_id,'آیدی عمومی عضو را بفرست؛ اگر ندارد یک خط تیره بفرست.'); return True
        if step == 'team_add_username':
            d=state['team_data']; username='' if text=='-' else text.lstrip('@')
            await self.db.add_team_member(d['name'],d['title'],d['description'],username,uid)
            self.states.pop(uid,None); await self.safe_send(chat_id,'✅ عضو تیم اضافه شد.',inline([[('↩️ برگشت','own:team'),('✖️ بستن','close')]])); return True
        if step == 'ban_reason':
            state['reason']=text or 'دلیلی ثبت نشده'; state['step']='ban_duration'
            await self.safe_send(chat_id,'مدت محدودیت رو به روز بفرست؛ برای دائمی بنویس `دائمی` و برای یک روز `1`.',inline([[('↩️ برگشت','admin:page:0'),('✖️ بستن','close')]])); return True
        if step == 'ban_duration':
            permanent=text.strip() in ('دائمی','دائم','بی پایان','بی‌پایان','permanent','forever')
            days=None
            if not permanent:
                match=re.search(r'\d+',text)
                if not match or int(match.group())<1 or int(match.group())>36500:
                    await self.safe_send(chat_id,'مدت باید تعداد روز مثبت یا «دائمی» باشد.'); return True
                days=int(match.group())
            target_type=state['target_type']; target_id=int(state['target_id'])
            info=await self.db.search_target(str(target_id),target_type)
            target=info[0] if info else {'id':target_id,'username':None,'name':str(target_id)}
            case=await self.db.create_case(target_type,target_id,target.get('username'),target.get('name') or str(target_id),state.get('reason',''),days,permanent,uid)
            self.states.pop(uid,None)
            duration='دائمی' if permanent else f'{days} روز'
            notice=f"*⛔ محدودیت MEGA BOT*\nدلیل: {case['reason']}\nمدت: {duration}\nکد پیگیری: `#{case['case_code']}`\nصادرکننده: `{uid}`"
            # For group restriction, post and try to pin the notice. For user, DM is best-effort.
            sent=None
            if target_type=='group':
                sent=await self.safe_send(target_id,notice)
                if sent:
                    try: await self.api.pin_message(target_id,int(sent['message_id']))
                    except Exception as exc: log.info('Could not pin group restriction notice: %s',exc)
                # Best-effort owner notification; if the owner has never started the bot, Bale may reject the DM.
                try:
                    members=await self.api.call('getChatAdministrators', {'chat_id':target_id})
                    creator=next((m.get('user') for m in (members or []) if m.get('status')=='creator' and m.get('user')),None)
                    if creator:
                        await self.safe_send(int(creator['id']), notice+'\n\nاین گروه در MEGA BOT محدود شده است.')
                except Exception as exc:
                    log.info('Could not notify group owner: %s',exc)
            else:
                sent=await self.safe_send(target_id,notice+'\n\nبرای پیگیری، بخش *تیم من* را باز کن.')
            await self.safe_send(chat_id,f"✅ پرونده ثبت شد.\nکد: `#{case['case_code']}`\nمدت: {duration}\nاعلان: {'ارسال شد' if sent else 'ارسال پیام ممکن نشد'}",inline([[('↩️ برگشت','admin:page:0'),('✖️ بستن','close')]]))
            return True
        return False

    def case_text(self, case: dict) -> str:
        target='کاربر' if case['target_type']=='user' else 'گروه'
        duration='دائمی' if case['permanent'] else (f"{case['duration_days']} روز" if case['duration_days'] else 'نامشخص')
        active='فعال' if case['active'] else 'پایان‌یافته / رفع‌شده'
        return (f"*پرونده #{case['case_code']}*\nنوع: {target}\nشناسه: `{case['target_id']}`\nنام: {case['target_name']}\n"
                f"دلیل: {case['reason']}\nمدت: {duration}\nوضعیت: {active}\nصادرکننده: `{case['issued_by']}`\n"
                f"تاریخ ثبت: {case['created_at'].strftime('%Y-%m-%d %H:%M UTC')}")

    async def my_cases(self, chat_id: int, uid: int) -> None:
        async with self.db._pool().acquire() as c:
            rows=await c.fetch("SELECT * FROM moderation_cases WHERE target_type='user' AND target_id=$1 ORDER BY created_at DESC LIMIT 10",uid)
        if not rows:
            await self.safe_send(chat_id,'*تیم من / پیگیری*\nپرونده‌ای برای حساب تو ثبت نشده است.',USER_KEYBOARD); return
        text='*تیم من / پیگیری پرونده‌ها*\n\n'+'\n\n'.join(f"*#{r['case_code']}* — {'فعال' if r['active'] else 'پایان‌یافته'}\nدلیل: {r['reason']}\nمدت: {'دائمی' if r['permanent'] else str(r['duration_days'])+' روز'}" for r in rows)
        await self.safe_send(chat_id,text,inline([[('↩️ برگشت','home'),('✖️ بستن','close')]]))

    async def start_ttt(self, chat_id: int, creator: int, name: str) -> None:
        gid=secrets.token_hex(3)
        game={'kind':'ttt','creator':creator,'chat_id':chat_id,'board':[' ']*9,'players':[creator,None],'turn':0}
        self.game_cache[gid]=game
        await self.safe_send(chat_id,f"*بازی دوز*\nسازنده: {name}\nنفر دوم با زدن «پیوستن» وارد بازی شود.",self.ttt_keyboard(gid,game))

    def ttt_keyboard(self, gid: str, game: dict) -> dict:
        board=game['board']; rows=[]
        for r in range(3):
            rows.append([(board[r*3+c] if board[r*3+c].strip() else '▫️',f'ttt:move:{gid}:{r*3+c}') for c in range(3)])
        rows.append([('🙋 پیوستن','ttt:join:'+gid),('✖️ لغو','game:cancel:'+gid)])
        return inline(rows)

    async def handle_ttt_callback(self, query: dict, data: str) -> None:
        msg=query.get('message') or {}; chat_id=int((msg.get('chat') or {}).get('id',0)); mid=int(msg.get('message_id',0)); uid=int((query.get('from') or {}).get('id',0))
        parts=data.split(':'); action=parts[1]; gid=parts[2]; game=self.game_cache.get(gid)
        if not game: return await self.safe_send(chat_id,'این بازی تمام شده است.')
        if action=='join':
            if uid==game['creator']: return await self.safe_send(chat_id,'برای بازی، یک نفر دیگر باید بپیوندد.')
            game['players'][1]=uid
            await self.safe_edit(chat_id,mid,'*بازی دوز شروع شد!* نفرات به ترتیب بازی می‌کنند.',self.ttt_keyboard(gid,game)); return
        if action=='move':
            if not game['players'][1]: return await self.safe_send(chat_id,'اول نفر دوم باید بپیوندد.')
            idx=int(parts[3]); player_idx=game['turn']
            if uid != game['players'][player_idx]: return await self.safe_send(chat_id,'الان نوبت تو نیست.')
            if game['board'][idx].strip(): return await self.safe_send(chat_id,'این خانه پر است.')
            mark='❌' if player_idx==0 else '⭕'; game['board'][idx]=mark; game['turn']=1-player_idx
            win=self.ttt_winner(game['board'])
            if win or all(x.strip() for x in game['board']):
                outcome='مساوی شد!' if not win else f"برنده: `{game['players'][0] if win=='❌' else game['players'][1]}`"
                await self.safe_edit(chat_id,mid,'*پایان بازی دوز*\n'+outcome,self.ttt_keyboard(gid,game)); self.game_cache.pop(gid,None)
            else: await self.safe_edit(chat_id,mid,'*بازی دوز*\nنوبت بازیکن بعدی است.',self.ttt_keyboard(gid,game))

    @staticmethod
    def ttt_winner(b: list[str]) -> str | None:
        lines=[(0,1,2),(3,4,5),(6,7,8),(0,3,6),(1,4,7),(2,5,8),(0,4,8),(2,4,6)]
        for a,c,d in lines:
            if b[a].strip() and b[a]==b[c]==b[d]: return b[a]
        return None

    async def start_one_game(self, chat_id: int, uid: int, name: str) -> None:
        gid=secrets.token_hex(3)
        self.game_cache[gid]={'kind':'one','creator':uid,'chat_id':chat_id,'players':{uid:name},'started':False}
        await self.safe_send(chat_id,f"*🎲 هرکی تک بیاره*\nسازنده: {name}\nبازیکن‌ها با عضویت وارد شوند.",inline([
            [('▶️ استارت توسط شروع‌کننده','one:start:'+gid)],
            [('🙋 عضویت در هرکی تک بیاره','one:join:'+gid),('🚪 خروج','one:leave:'+gid)],
            [('🗑️ حذف بازی','game:cancel:'+gid)],
            [('↩️ برگشت','home'),('✖️ بستن','close')]
        ]))

    async def handle_one_callback(self, query: dict, data: str) -> None:
        msg=query.get('message') or {}; chat_id=int((msg.get('chat') or {}).get('id',0)); mid=int(msg.get('message_id',0)); uid=int((query.get('from') or {}).get('id',0))
        _,action,gid=data.split(':',2); game=self.game_cache.get(gid)
        if not game: return await self.safe_send(chat_id,'این بازی دیگه فعال نیست.')
        if action=='join':
            game['players'][uid]=str(uid); await self.safe_edit(chat_id,mid,self.one_text(game),self.one_keyboard(gid,game)); return
        if action=='leave':
            game['players'].pop(uid,None); await self.safe_edit(chat_id,mid,self.one_text(game),self.one_keyboard(gid,game)); return
        if action=='start':
            if uid!=game['creator']: return await self.safe_send(chat_id,'فقط شروع‌کننده می‌تواند بازی را استارت کند.')
            if len(game['players'])<2: return await self.safe_send(chat_id,'حداقل دو بازیکن لازم است.')
            game['started']=True
            rolls={p:random.randint(1,6) for p in game['players']}
            ones=[p for p,v in rolls.items() if v==1]
            if ones: result='🏆 تک آورنده: '+', '.join(f'`{p}`' for p in ones)
            else: result='هیچکس تک نیاورد...'
            result+='\n\n'+ '\n'.join(f'`{p}`: {v}' for p,v in rolls.items())
            await self.safe_edit(chat_id,mid,'*نتیجه هرکی تک بیاره*\n'+result,inline([[('🔁 دور بعد','one:next:'+gid),('🗑️ حذف بازی','game:cancel:'+gid)],[('✖️ بستن','close')]])); return
        if action=='next':
            if uid!=game['creator']: return await self.safe_send(chat_id,'فقط شروع‌کننده می‌تواند دور بعد را اجرا کند.')
            game['started']=False; await self.safe_edit(chat_id,mid,self.one_text(game),self.one_keyboard(gid,game))

    def one_text(self, game: dict) -> str:
        players=game.get('players',{})
        return '*🎲 هرکی تک بیاره*\n\nبازیکنان:\n'+'\n'.join(f"• `{pid}` — {name}" for pid,name in players.items())+'\n\nسازنده می‌تواند بازی را شروع کند.'

    def one_keyboard(self, gid: str, game: dict) -> dict:
        return inline([[('▶️ استارت توسط شروع‌کننده','one:start:'+gid)],[('🙋 عضویت','one:join:'+gid),('🚪 خروج','one:leave:'+gid)],[('🗑️ حذف بازی','game:cancel:'+gid)],[('↩️ برگشت','home'),('✖️ بستن','close')]])
