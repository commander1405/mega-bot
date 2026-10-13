"""Render group analytics dashboard image (Pillow) — dark neon Persian UI."""
from __future__ import annotations

import math
import os
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

# Colors matching dark neon analytics dashboards
BG = (8, 10, 22)
CARD = (18, 22, 40)
CARD_BORDER = (55, 45, 110)
NEON_PURPLE = (168, 85, 247)
NEON_CYAN = (34, 211, 238)
NEON_PINK = (236, 72, 153)
NEON_GREEN = (52, 211, 153)
NEON_ORANGE = (251, 146, 60)
TEXT = (235, 238, 250)
MUTED = (140, 150, 180)
GRID = (30, 35, 55)

_FA_DIGITS = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')


def fa_num(n: int | float | str) -> str:
    return str(n).translate(_FA_DIGITS)


def _find_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        '/usr/share/fonts/SlidesCarnival/google/Noto Sans Arabic/NotoSansArabic-VariableFont_wdth,wght.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf',
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _reshape(text: str) -> str:
    """Best-effort Persian shaping for Pillow."""
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        return get_display(arabic_reshaper.reshape(text))
    except Exception:
        return text


def _jalali_str(dt: datetime | None = None) -> str:
    """Gregorian → Jalali date string (algorithm, no extra dep)."""
    dt = dt or datetime.now()
    gy, gm, gd = dt.year, dt.month, dt.day
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy = 979
        gy -= 1600
    else:
        jy = 0
        gy -= 621
    gy2 = gy + 1 if gm > 2 else gy
    days = 365 * gy + (gy2 + 3) // 4 - (gy2 + 99) // 100 + (gy2 + 399) // 400 - 80 + gd + g_d_m[gm - 1]
    jy += 33 * (days // 12053)
    days %= 12053
    jy += 4 * (days // 1461)
    days %= 1461
    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365
    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30
    months = ['فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور',
              'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند']
    return f'{fa_num(jy)}/{fa_num(jm):0>2}/{fa_num(jd):0>2} — {months[jm - 1]}'


def _rounded_rect(draw: ImageDraw.ImageDraw, xy, radius: int, fill, outline=None, width: int = 1):
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle([x1, y1, x2, y2], radius=radius, fill=fill, outline=outline, width=width)


def _dot_bg(img: Image.Image) -> None:
    draw = ImageDraw.Draw(img)
    w, h = img.size
    for y in range(0, h, 14):
        for x in range(0, w, 14):
            if (x // 14 + y // 14) % 2 == 0:
                draw.point((x, y), fill=(25, 28, 48))
    # soft purple/cyan glows
    for cx, cy, color, rad in [
        (80, 60, (80, 40, 140), 120),
        (w - 100, 180, (20, 90, 120), 140),
        (w // 2, h - 80, (90, 30, 100), 100),
    ]:
        for r in range(rad, 0, -8):
            alpha = int(18 * (r / rad))
            overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay)
            od.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, alpha))
            img.alpha_composite(overlay)


def _text(draw, xy, text: str, font, fill=TEXT, anchor='rt'):
    draw.text(xy, _reshape(text), font=font, fill=fill, anchor=anchor)


def render_group_dashboard(
    *,
    title: str,
    stats: dict[str, Any],
    members_count: int | None = None,
) -> bytes:
    """Return PNG bytes of the analytics dashboard."""
    W, H = 900, 1200
    img = Image.new('RGBA', (W, H), (*BG, 255))
    _dot_bg(img)
    draw = ImageDraw.Draw(img)

    font_title = _find_font(36, bold=True)
    font_sub = _find_font(18)
    font_card_val = _find_font(28, bold=True)
    font_card_lbl = _find_font(16)
    font_sec = _find_font(20, bold=True)
    font_small = _find_font(14)
    font_tiny = _find_font(12)

    counts = stats.get('counts') or {}
    total_msgs = int(stats.get('total_msgs') or 0)
    active = int(stats.get('active_users') or 0)
    joined = int(counts.get('joined') or 0)
    left = int(counts.get('left_count') or 0)

    # Header
    _text(draw, (W - 36, 28), 'داشبورد تحلیلی', font_title, NEON_CYAN, 'rt')
    sub = f'{_jalali_str()}  |  گزارش جامع پویای ۲۴ ساعته'
    _text(draw, (W - 36, 72), sub, font_sub, MUTED, 'rt')
    gtitle = (title or 'گروه')[:40]
    _text(draw, (W - 36, 98), gtitle, font_small, MUTED, 'rt')

    # KPI cards row
    cards = [
        ('پیام‌ها', fa_num(total_msgs), NEON_PURPLE),
        ('کاربران فعال', fa_num(active), NEON_CYAN),
        ('ورود', fa_num(joined), NEON_GREEN),
        ('خروج', fa_num(left), NEON_PINK),
    ]
    if members_count is not None:
        cards.append(('اعضا', fa_num(members_count), NEON_ORANGE))

    card_w = (W - 48 - 12 * (len(cards) - 1)) // len(cards)
    y0 = 130
    for i, (lbl, val, color) in enumerate(cards):
        x1 = 24 + i * (card_w + 12)
        x2 = x1 + card_w
        _rounded_rect(draw, (x1, y0, x2, y0 + 88), 14, CARD, color, 2)
        _text(draw, (x2 - 14, y0 + 18), val, font_card_val, color, 'rt')
        _text(draw, (x2 - 14, y0 + 56), lbl, font_card_lbl, MUTED, 'rt')

    # Hourly line chart
    chart_top = 240
    chart_h = 220
    _rounded_rect(draw, (24, chart_top, W - 24, chart_top + chart_h + 50), 16, CARD, CARD_BORDER, 1)
    _text(draw, (W - 40, chart_top + 12), 'تحلیل و تراکم پیام‌ها در شبانه‌روز', font_sec, TEXT, 'rt')

    hourly = list(stats.get('hourly') or [])
    # Build 24 slots
    series = [0] * 24
    if hourly:
        for h in hourly:
            hr = int(h.get('hour') or 0) % 24
            series[hr] = max(series[hr], int(h.get('count') or 0))
    max_v = max(series) if any(series) else 1
    plot_x1, plot_y1 = 48, chart_top + 48
    plot_x2, plot_y2 = W - 48, chart_top + chart_h + 20
    plot_w = plot_x2 - plot_x1
    plot_h = plot_y2 - plot_y1

    # grid
    for i in range(5):
        gy = plot_y1 + int(i * plot_h / 4)
        draw.line([(plot_x1, gy), (plot_x2, gy)], fill=GRID, width=1)

    points = []
    for i, v in enumerate(series):
        px = plot_x1 + int(i * plot_w / 23)
        py = plot_y2 - int((v / max_v) * (plot_h - 4))
        points.append((px, py))

    if len(points) >= 2:
        # fill under curve
        fill_pts = [(points[0][0], plot_y2)] + points + [(points[-1][0], plot_y2)]
        overlay = Image.new('RGBA', img.size, (0, 0, 0, 0))
        od = ImageDraw.Draw(overlay)
        od.polygon(fill_pts, fill=(34, 211, 238, 40))
        img.alpha_composite(overlay)
        draw = ImageDraw.Draw(img)
        draw.line(points, fill=NEON_CYAN, width=3)
        for px, py in points:
            draw.ellipse([px - 3, py - 3, px + 3, py + 3], fill=NEON_PURPLE)

    # hour labels
    for i in (0, 6, 12, 18, 23):
        px = plot_x1 + int(i * plot_w / 23)
        _text(draw, (px, plot_y2 + 8), fa_num(i), font_tiny, MUTED, 'mt')

    if not any(series):
        _text(draw, ((plot_x1 + plot_x2) // 2, (plot_y1 + plot_y2) // 2),
              'هنوز دادهٔ ساعتی کافی نیست', font_small, MUTED, 'mm')

    # Media breakdown
    media_top = chart_top + chart_h + 70
    _rounded_rect(draw, (24, media_top, W - 24, media_top + 200), 16, CARD, CARD_BORDER, 1)
    _text(draw, (W - 40, media_top + 12), 'تفکیک رسانه‌ها', font_sec, TEXT, 'rt')

    media_items = [
        ('عکس‌ها', int(counts.get('photo_count') or 0), NEON_PINK),
        ('ویدیوها', int(counts.get('video_count') or 0), NEON_PURPLE),
        ('استیکرها', int(counts.get('sticker_count') or 0), NEON_ORANGE),
        ('گیف‌ها', int(counts.get('animation_count') or 0), NEON_CYAN),
        ('موزیک‌ها', int(counts.get('audio_count') or 0), NEON_GREEN),
        ('ویس‌ها', int(counts.get('voice_count') or 0), NEON_PURPLE),
        ('فایل‌ها', int(counts.get('document_count') or 0), MUTED),
        ('متن', int(counts.get('text_count') or 0), NEON_CYAN),
    ]
    cols = 4
    for i, (lbl, val, color) in enumerate(media_items):
        row, col = divmod(i, cols)
        cw = (W - 64 - 12 * (cols - 1)) // cols
        x1 = 36 + col * (cw + 12)
        y1 = media_top + 48 + row * 70
        _rounded_rect(draw, (x1, y1, x1 + cw, y1 + 58), 12, (14, 18, 32), color, 1)
        _text(draw, (x1 + cw - 10, y1 + 10), fa_num(val), font_card_val, color, 'rt')
        _text(draw, (x1 + cw - 10, y1 + 36), lbl, font_tiny, MUTED, 'rt')

    # Top users
    users_top = media_top + 220
    _rounded_rect(draw, (24, users_top, W - 24, H - 36), 16, CARD, CARD_BORDER, 1)
    _text(draw, (W - 40, users_top + 12), 'کاربران برتر (۲۴ ساعت اخیر)', font_sec, TEXT, 'rt')

    top_users = list(stats.get('top_users') or [])[:5]
    if not top_users:
        _text(draw, (W // 2, users_top + 100), 'هنوز کاربر فعالی ثبت نشده', font_small, MUTED, 'mm')
    else:
        max_msg = max(int(u.get('msg_count') or 0) for u in top_users) or 1
        for i, u in enumerate(top_users):
            y = users_top + 50 + i * 70
            name = (u.get('first_name') or u.get('username') or f"کاربر {u.get('user_id')}")[:28]
            cnt = int(u.get('msg_count') or 0)
            # rank circle
            cx, cy = W - 56, y + 24
            draw.ellipse([cx - 16, cy - 16, cx + 16, cy + 16], fill=NEON_PURPLE if i == 0 else CARD_BORDER)
            _text(draw, (cx, cy), fa_num(i + 1), font_small, TEXT, 'mm')
            _text(draw, (W - 84, y + 8), name, font_sec, TEXT, 'rt')
            _text(draw, (W - 84, y + 34), f'{fa_num(cnt)} پیام', font_tiny, MUTED, 'rt')
            # activity bar
            bar_x2 = W - 84
            bar_x1 = 48
            bar_y = y + 52
            draw.rounded_rectangle([bar_x1, bar_y, bar_x2, bar_y + 8], radius=4, fill=GRID)
            fill_w = int((cnt / max_msg) * (bar_x2 - bar_x1))
            if fill_w > 0:
                draw.rounded_rectangle([bar_x2 - fill_w, bar_y, bar_x2, bar_y + 8], radius=4, fill=NEON_CYAN)

    # Footer note
    _text(draw, (W // 2, H - 18), 'MEGA BOT  •  آمار واقعی از دیتابیس گروه', font_tiny, MUTED, 'mm')

    rgb = img.convert('RGB')
    buf = BytesIO()
    rgb.save(buf, format='PNG', optimize=True)
    return buf.getvalue()
