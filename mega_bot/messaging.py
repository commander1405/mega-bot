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

ASSETS_DIR = Path(__file__).resolve().parent / 'assets' / 'icons'

# event_key -> icon filename stem (no .png)
EVENT_ASSETS: dict[str, str] = {
    # Official MEGA BOT / DarkKnight asset pack (cropped from design sheet)
    'user_banned': 'user_banned',
    'user_unbanned': 'user_unbanned',
    'user_warned': 'user_warned',
    'user_muted': 'user_muted',
    'user_unmuted': 'user_unmuted',
    'user_kicked': 'user_kicked',
    'user_added': 'user_added',
    'broadcast': 'broadcast',
    'system_notice': 'system_notice',
    'schedule': 'schedule',
    'settings': 'settings',
    'stats_report': 'stats_report',
    'list_menu': 'list_menu',
    'help': 'help',
    'logo': 'logo',
    'learning_list': 'learning_list',
    'suggestion': 'suggestion',
    'operation_success': 'operation_success',
    'operation_error': 'operation_error',
    'processing': 'processing',
    'thanks': 'thanks',
    'vip': 'vip',
    'vip_members': 'vip_members',
    'gift': 'gift',
    'occasion': 'occasion',
    'award': 'award',
    'support': 'support',
    'brand': 'brand',
    # aliases
    'shield': 'user_unmuted',
}


# Events that intentionally stay text-only (documented mapping)
TEXT_ONLY_EVENTS = frozenset({
    # Reserved — PV always uses icons via pick_pv_asset / send_event
})

def pick_pv_asset(text: str) -> str:
    """Choose a neon icon for private-chat replies based on caption content."""
    s = (text or '').lower()
    raw = text or ''
    rules = [
        (('بن', 'مسدود', 'ban'), 'user_banned'),
        (('رفع بن', 'آنبن', 'انبن', 'unban'), 'user_unbanned'),
        (('اخطار', 'warn'), 'user_warned'),
        (('سکوت', 'mute'), 'user_muted'),
        (('حذف سکوت', 'unmute'), 'user_unmuted'),
        (('اخراج', 'کیک', 'kick'), 'user_kicked'),
        (('خطا', 'ناموفق', '❌', 'مشکل'), 'operation_error'),
        (('موفق', '✅', 'ذخیره شد', 'انجام شد'), 'operation_success'),
        (('صبر', 'پردازش', '⏳', 'در حال'), 'processing'),
        (('آمار', 'مرکز اطلاعات', 'داشبورد'), 'stats_report'),
        (('راهنما', 'راهنمای'), 'help'),
        (('حمایت', 'تومان', 'donate'), 'support'),
        (('تشکر', 'ممنون'), 'thanks'),
        (('ویژه', 'vip'), 'vip'),
        (('تنظیم', 'settings'), 'settings'),
        (('زمان', 'زمان‌بندی'), 'schedule'),
        (('همگانی', 'broadcast'), 'broadcast'),
        (('لیست یادگیری', 'یادگیری'), 'learning_list'),
        (('پیشنهاد',), 'suggestion'),
        (('لیست', 'فهرست', 'امکانات'), 'list_menu'),
        (('سلام', 'خوش آمد'), 'logo'),
        (('محدود', '⛔'), 'shield'),
        (('هدیه', 'تخفیف'), 'gift'),
        (('تقدیر', 'ستاره'), 'award'),
    ]
    for keys, asset in rules:
        for k in keys:
            if k.lower() in s or k in raw:
                return asset
    return 'logo'



_SETTINGS_KEY = 'asset_file_ids'


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


class MessageHub:
    """Send text or event photos with persistent file_id cache."""

    def __init__(self, bot: 'MegaBot') -> None:
        self.bot = bot
        self._memory: dict[str, str] = {}  # event_key -> file_id
        self._loaded = False

    async def _load_cache(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        try:
            raw = await self.bot.db.get_setting(_SETTINGS_KEY, {})
            if isinstance(raw, str):
                raw = json.loads(raw)
            if isinstance(raw, dict):
                self._memory = {str(k): str(v) for k, v in raw.items() if v}
        except Exception:
            log.debug('asset file_id cache load failed', exc_info=True)
            self._memory = {}

    async def _save_cache(self) -> None:
        try:
            await self.bot.db.set_setting(_SETTINGS_KEY, self._memory, 0)
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
