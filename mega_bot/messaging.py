"""Central messaging helpers for MEGA BOT — optional neon asset images.

Ordinary replies stay text-only. Moderation / system events may attach a
matching icon from ``mega_bot/assets/icons/``.

Bale supports reusing ``file_id`` from a previous ``sendPhoto`` response so the
same asset is not re-uploaded every time. File IDs are cached in the
``settings`` table under key ``asset_file_ids``.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .app import MegaBot

log = logging.getLogger('mega_bot.messaging')

ASSETS_DIR = Path(__file__).resolve().parent.parent / 'assets'

# event_key -> icon filename stem (no .png)
EVENT_ASSETS: dict[str, str] = {
    # Root assets/ — official 512 PNG pack (no .png in values; added in asset_path)
    'user_banned': 'ban',
    'user_unbanned': 'unban',
    'user_warned': 'warn',
    'user_muted': 'mute',
    'user_unmuted': 'unmute',
    'user_kicked': 'kick',
    'user_added': 'add_user',
    'broadcast': 'broadcast',
    'system_notice': 'notice',
    'schedule': 'schedule',
    'settings': 'settings',
    'stats_report': 'stats',
    'list_menu': 'menu',
    'help': 'help',
    'logo': 'bot_logo',
    'learning_list': 'menu',
    'suggestion': 'info',
    'operation_success': 'success',
    'operation_error': 'error',
    'processing': 'processing',
    'thanks': 'thanks',
    'vip': 'premium',
    'vip_members': 'premium',
    'gift': 'gift',
    'occasion': 'celebration',
    'award': 'success',
    'support': 'donation',
    'brand': 'developers',
    'shield': 'unmute',
    'cancel': 'cancel',
    'welcome': 'welcome',
    'info': 'info',
    'notice': 'notice',
}



# Events that intentionally stay text-only (documented mapping)
TEXT_ONLY_EVENTS = frozenset({
    # Reserved — PV always uses icons via pick_pv_asset / send_event
})

def is_casual_text(text: str) -> bool:
    """True for greetings / emoji / short social replies — never attach icons."""
    import re
    t = (text or '').strip()
    if not t:
        return True
    # strip markdown noise
    plain = re.sub(r'[*`_]', '', t).strip()
    low = plain.lower()
    casual_keys = (
        'سلام', 'سلامم', 'درود', 'دورد', 'خداحافظ', 'خدافظ', 'بدرود',
        'شب خوش', 'صبح بخیر', 'خوبی', 'خوبم', 'چخبر', 'چه خبر',
        'مرسی', 'ممنون', '😂', '😄', '🌿', '🌱', '🍃', '🌙',
        'رفیق', 'چه خبرا', 'حالت چطور',
    )
    # pure/short emoji or very short social
    if len(plain) <= 40:
        for k in casual_keys:
            if k in plain or k in low:
                return True
    # emoji-only or almost only emoji
    no_emoji = re.sub(r'[\U00010000-\U0010ffff\u2600-\u27bf\uFE0F]', '', plain)
    if len(plain) <= 12 and len(no_emoji.strip()) <= 2:
        return True
    return False


def should_attach_asset(text: str, event_key: str | None = None) -> bool:
    """Only important operational / menu messages get icons — not social chatter."""
    if event_key and event_key not in (
        'ordinary_reply', 'greeting', 'goodbye', 'learning_list',
        'conversation', 'callback_text', 'casual',
    ):
        # explicit operational event from caller
        if event_key in EVENT_ASSETS:
            return True
    if is_casual_text(text or ''):
        return False
    # important PV menus / results
    raw = text or ''
    important = (
        'مرکز اطلاعات', 'آمار', 'حمایت', 'تومان', 'راهنما', 'امکانات بیشتر',
        'هویت مگا', 'پرونده', 'پنل', 'مالک', 'مدیر', 'بن', 'اخطار', 'سکوت',
        'تنظیم', 'قفل', 'همگانی', 'خطا', 'موفق', 'نسخه', 'donation',
        'ارتباط با مگا', 'سازندگان', 'ویژه',
    )
    return any(k in raw for k in important)


def pick_pv_asset(text: str) -> str:
    """Choose a neon icon for important private-chat replies only."""
    s = (text or '').lower()
    raw = text or ''
    rules = [
        (('بن', 'مسدود'), 'user_banned'),
        (('رفع بن', 'آنبن', 'انبن'), 'user_unbanned'),
        (('اخطار',), 'user_warned'),
        (('حذف سکوت',), 'user_unmuted'),
        (('سکوت',), 'user_muted'),
        (('اخراج', 'کیک'), 'user_kicked'),
        (('خطا', 'ناموفق', 'مشکل'), 'operation_error'),
        (('موفق', 'ذخیره شد', 'انجام شد'), 'operation_success'),
        (('پردازش',), 'processing'),
        (('آمار', 'مرکز اطلاعات', 'داشبورد'), 'stats_report'),
        (('راهنما',), 'help'),
        (('حمایت', 'تومان'), 'support'),
        (('ویژه',), 'vip'),
        (('تنظیم',), 'settings'),
        (('زمان‌بندی', 'زمانب'), 'schedule'),
        (('همگانی',), 'broadcast'),
        (('یادگیری',), 'learning_list'),
        (('امکانات', 'فهرست', 'لیست'), 'list_menu'),
        (('محدود',), 'shield'),
        (('سازندگان', 'تیم'), 'brand'),
        (('هویت', 'درباره'), 'logo'),
        (('ارتباط', 'پشتیبانی'), 'system_notice'),
    ]
    for keys, asset in rules:
        for k in keys:
            if k in raw or k in s:
                return asset
    return 'logo'





def asset_path(event_key: str) -> Path | None:
    stem = EVENT_ASSETS.get(event_key)
    if not stem:
        return None
    p = ASSETS_DIR / f'{stem}.png'
    return p if p.is_file() else None


def extract_photo_file_id(result: Any) -> str | None:
    """Pull the largest photo file_id from a sendPhoto API result."""
    if not isinstance(result, dict):
        return None
    msg = result.get('result') if isinstance(result.get('result'), dict) else result
    if not isinstance(msg, dict):
        return None
    photos = msg.get('photo')
    if not isinstance(photos, list) or not photos:
        return None
    best = None
    best_area = -1
    for p in photos:
        if not isinstance(p, dict) or not p.get('file_id'):
            continue
        area = int(p.get('width') or 0) * int(p.get('height') or 0)
        if area >= best_area:
            best_area = area
            best = str(p['file_id'])
    return best


# Content fingerprint of assets/ PNG pack — changing any icon invalidates cache
ASSET_PACK_SIG = '7e3f62dc2788'
_SETTINGS_KEY = f'asset_file_ids_{ASSET_PACK_SIG}'


class MessageHub:
    """Send text or event photos with persistent file_id cache."""

    def __init__(self, bot: 'MegaBot') -> None:
        self.bot = bot
        self._memory: dict[str, str] = {}  # event_key -> file_id
        self._loaded = False
        log.info('MessageHub assets dir=%s pack=%s', ASSETS_DIR, ASSET_PACK_SIG)

    async def _load_cache(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = await self.bot.db.get_setting(_SETTINGS_KEY, {})
            if isinstance(raw, str):
                raw = json.loads(raw)
            if isinstance(raw, dict):
                # only keep entries for current pack
                if raw.get('_pack') == ASSET_PACK_SIG:
                    self._memory = {str(k): str(v) for k, v in raw.items() if k != '_pack' and v}
                else:
                    self._memory = {}
                    log.info('asset cache pack mismatch — will re-upload icons')
        except Exception:
            log.debug('asset file_id cache load failed', exc_info=True)
            self._memory = {}

    async def _save_cache(self) -> None:
        try:
            payload = dict(self._memory)
            payload['_pack'] = ASSET_PACK_SIG
            await self.bot.db.set_setting(_SETTINGS_KEY, payload, 0)
        except Exception:
            log.debug('asset file_id cache save failed', exc_info=True)

    async def send_text(
        self,
        chat_id: int,
        text: str,
        *,
        keyboard: dict | None = None,
        reply_to: int | None = None,
        parse_mode: str | None = 'Markdown',
        permanent: bool = False,
    ) -> dict | None:
        return await self.bot.safe_send(
            chat_id, text, keyboard=keyboard, reply_to=reply_to,
            parse_mode=parse_mode, permanent=permanent,
        )

    async def send_event(
        self,
        chat_id: int,
        event_key: str,
        caption: str,
        *,
        keyboard: dict | None = None,
        reply_to: int | None = None,
        parse_mode: str | None = None,
        permanent: bool = False,
        force_text: bool = False,
    ) -> dict | None:
        """Send an event message with optional icon.

        Falls back to plain text if the asset is missing or sendPhoto fails.
        """
        if force_text or event_key in TEXT_ONLY_EVENTS:
            return await self.send_text(
                chat_id, caption, keyboard=keyboard, reply_to=reply_to,
                parse_mode=parse_mode, permanent=permanent,
            )

        path = asset_path(event_key)
        if not path:
            return await self.send_text(
                chat_id, caption, keyboard=keyboard, reply_to=reply_to,
                parse_mode=parse_mode, permanent=permanent,
            )

        await self._load_cache()
        file_id = self._memory.get(event_key)

        # 1) Try cached file_id (no re-upload)
        if file_id:
            try:
                result = await self.bot.api.send_photo(
                    chat_id, file_id, caption=caption,
                    reply_markup=keyboard, parse_mode=parse_mode,
                    reply_to=reply_to,
                )
                return self.bot._maybe_ephemeral_result(chat_id, result, permanent=permanent)
            except Exception as exc:
                log.info('asset file_id stale for %s: %s — re-uploading', event_key, type(exc).__name__)
                self._memory.pop(event_key, None)

        # 2) Upload bytes once, cache file_id
        try:
            data = path.read_bytes()
            log.info('asset upload event=%s file=%s bytes=%s pack=%s', event_key, path.name, len(data), ASSET_PACK_SIG)
            result = await self.bot.api.send_photo_bytes(
                chat_id, data, filename=f'{event_key}.png', caption=caption,
                reply_markup=keyboard, parse_mode=parse_mode,
            )
            new_id = extract_photo_file_id(result) or extract_photo_file_id({'result': result})
            if new_id:
                self._memory[event_key] = new_id
                await self._save_cache()
            return self.bot._maybe_ephemeral_result(chat_id, result, permanent=permanent)
        except Exception as exc:
            log.warning('asset photo send failed %s: %s — text fallback', event_key, type(exc).__name__)
            return await self.send_text(
                chat_id, caption, keyboard=keyboard, reply_to=reply_to,
                parse_mode=parse_mode, permanent=permanent,
            )
