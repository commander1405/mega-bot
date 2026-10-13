"""Group analytics dashboard — dark neon UI close to reference design."""
from __future__ import annotations

import os
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageFilter

BG = (6, 8, 20)
CARD = (16, 20, 38)
CARD2 = (12, 16, 32)
NEON_PURPLE = (168, 85, 247)
NEON_CYAN = (34, 211, 238)
NEON_PINK = (236, 72, 153)
NEON_GREEN = (52, 211, 153)
NEON_ORANGE = (251, 146, 60)
NEON_BLUE = (59, 130, 246)
TEXT = (240, 242, 255)
MUTED = (148, 156, 180)
GRID = (32, 38, 58)

_FA = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
_FONT_DIR = Path(__file__).resolve().parent / 'assets' / 'fonts'


def fa_num(n: int | float | str) -> str:
    if isinstance(n, float):
        s = f'{n:.1f}'.rstrip('0').rstrip('.')
    elif isinstance(n, int):
        s = f'{n:,}'.replace(',', '٬')
    else:
        s = str(n)
    return s.translate(_FA)


def _font_path(bold: bool = False) -> str | None:
    name = 'Vazirmatn-Bold.ttf' if bold else 'Vazirmatn-Regular.ttf'
    local = _FONT_DIR / name
    if local.is_file():
        return str(local)
    for p in (
        '/usr/share/fonts/SlidesCarnival/google/Noto Sans Arabic/NotoSansArabic-VariableFont_wdth,wght.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ):
        if os.path.isfile(p):
            return p
    return None


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = _font_path(bold)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _fa(text: str) -> str:
    """Persian shaping for Pillow WITHOUT bidi.get_display (that reverses text with anchor=rt)."""
    if not text:
        return ''
    try:
        import arabic_reshaper
        return arabic_reshaper.reshape(str(text))
    except Exception:
        return str(text)


def _t(draw, xy, text, font, fill=TEXT, anchor='rt'):
    draw.text(xy, _fa(text), font=font, fill=fill, anchor=anchor)


def _jalali(dt: datetime | None = None) -> str:
    dt = dt or datetime.now()
    gy, gm, gd = dt.year, dt.month, dt.day
    g_d_m = [0, 31, 59, 90, 120, 151, 181, 212, 243, 273, 304, 334]
    if gy > 1600:
        jy, gy = 979, gy - 1600
    else:
        jy, gy = 0, gy - 621
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
        jm, jd = 1 + days // 31, 1 + days % 31
    else:
        jm, jd = 7 + (days - 186) // 30, 1 + (days - 186) % 30
    weekdays = ['دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه', 'شنبه', 'یکشنبه']
    # Python Monday=0
    wd = weekdays[dt.weekday()]
    return f'{wd} {fa_num(jy)}/{fa_num(jm):0>2}/{fa_num(jd):0>2}'


def _bg(w: int, h: int) -> Image.Image:
    img = Image.new('RGBA', (w, h), (*BG, 255))
    d = ImageDraw.Draw(img)
    for y in range(0, h, 11):
        for x in range(0, w, 11):
            if (x // 11 + y // 11) % 3 == 0:
                d.point((x, y), fill=(28, 32, 52, 255))
    glow = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    for cx, cy, col, r in [
        (w - 80, 40, (80, 40, 180), 200),
        (60, 300, (20, 100, 140), 180),
        (w // 2, h - 40, (90, 30, 120), 160),
    ]:
        gd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*col, 40))
    glow = glow.filter(ImageFilter.GaussianBlur(50))
    return Image.alpha_composite(img, glow)


def _circle_avatar(draw_img: Image.Image, center: tuple[int, int], radius: int, photo: Image.Image | None, name: str, accent):
    """Paste circular avatar or letter badge."""
    cx, cy = center
    size = radius * 2
    if photo is not None:
        try:
            src = photo.convert('RGBA')
            # center-crop to square then resize
            w, h = src.size
            side = min(w, h)
            left = (w - side) // 2
            top = (h - side) // 2
            src = src.crop((left, top, left + side, top + side))
            av = src.resize((size, size), Image.Resampling.LANCZOS)
            mask = Image.new('L', (size, size), 0)
            ImageDraw.Draw(mask).ellipse([1, 1, size - 2, size - 2], fill=255)
            base = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            base.paste(av, (0, 0), av)
            base.putalpha(mask)
            draw_img.paste(base, (cx - radius, cy - radius), base)
            d = ImageDraw.Draw(draw_img)
            d.ellipse([cx - radius - 2, cy - radius - 2, cx + radius + 2, cy + radius + 2], outline=accent, width=3)
            return
        except Exception:
            pass
    d = ImageDraw.Draw(draw_img)
    d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=(30, 34, 55), outline=accent, width=2)
    initial = (name or '?').strip()[:1] or '?'
    font = _font(max(14, radius), bold=True)
    # letter may be Persian — shape it
    d.text((cx, cy), _fa(initial), font=font, fill=TEXT, anchor='mm')


def render_group_dashboard(
    *,
    title: str,
    stats: dict[str, Any],
    members_count: int | None = None,
    avatars: dict[int, Image.Image] | None = None,
) -> bytes:
    """PNG dashboard. avatars: optional {user_id: PIL Image} for top users."""
    W, H = 1000, 1320
    img = _bg(W, H)
    draw = ImageDraw.Draw(img)
    avatars = avatars or {}

    f_title = _font(42, True)
    f_sub = _font(17)
    f_val = _font(32, True)
    f_lbl = _font(14)
    f_sec = _font(20, True)
    f_body = _font(16)
    f_small = _font(13)
    f_tiny = _font(12)

    counts = stats.get('counts') or {}
    total_msgs = int(stats.get('total_msgs') or 0)
    active = int(stats.get('active_users') or 0)
    joined = int(counts.get('joined') or 0)
    left = int(counts.get('left_count') or 0)
    members = members_count if isinstance(members_count, int) else None

    # Header (RTL)
    _t(draw, (W - 36, 28), 'داشبورد تحلیلی', f_title, NEON_CYAN, 'rt')
    # small logo circle
    draw.ellipse([W - 36 - 210, 22, W - 36 - 170, 62], outline=NEON_PURPLE, width=2)
    _t(draw, (W - 36 - 190, 42), 'AI', _font(14, True), NEON_PURPLE, 'mm')

    sub = f'{_jalali()}  |  گزارش جامع پویای ۲۴ ساعته'
    _t(draw, (W - 36, 78), sub, f_sub, MUTED, 'rt')
    if title:
        _t(draw, (W - 36, 102), str(title)[:40], f_small, (170, 178, 200), 'rt')

    # KPI cards — order right-to-left like reference
    # Reference RTL visual order from right: پیام‌ها, فعال, جدید, خارج‌شده, کل اعضا
    cards = [
        ('تعداد پیام‌ها', total_msgs, NEON_ORANGE, '💬'),
        ('کاربران فعال', active, NEON_PURPLE, '👤'),
        ('کاربران جدید', joined, NEON_GREEN, '➕'),
        ('کاربران خارج‌شده', left, NEON_CYAN, '🚪'),
    ]
    if members is not None:
        cards.append(('کل اعضا', members, NEON_PURPLE, '👥'))

    n = len(cards)
    gap, margin = 10, 24
    card_w = (W - 2 * margin - gap * (n - 1)) // n
    y0 = 130
    for i, (lbl, val, color, _icon) in enumerate(cards):
        # place from right
        x2 = W - margin - i * (card_w + gap)
        x1 = x2 - card_w
        draw.rounded_rectangle([x1, y0, x2, y0 + 92], radius=16, fill=CARD, outline=color, width=2)
        _t(draw, (x2 - 14, y0 + 14), fa_num(val), f_val, color, 'rt')
        _t(draw, (x2 - 14, y0 + 56), lbl, f_lbl, MUTED, 'rt')

    # Chart
    chart_top = 246
    ch = 290
    draw.rounded_rectangle([margin, chart_top, W - margin, chart_top + ch], radius=18, fill=CARD, outline=CARD_BORDER if False else (55, 50, 100), width=1)
    _t(draw, (W - margin - 18, chart_top + 14), 'نمودار تعداد پیام‌ها در طول شبانه‌روز', f_sec, TEXT, 'rt')

    series = [0] * 24
    for h in (stats.get('hourly') or []):
        try:
            hr = int(h.get('hour') or 0) % 24
            series[hr] += int(h.get('count') or 0)
        except Exception:
            pass
    max_v = max(series) if any(series) else 0

    px1, py1 = margin + 50, chart_top + 50
    px2, py2 = W - margin - 28, chart_top + ch - 36
    pw, ph = px2 - px1, py2 - py1

    # y grid + labels
    y_max = max_v if max_v > 0 else 10
    # nice ceiling
    step = max(1, int((y_max + 4) // 4) or 1)
    y_max = step * 4
    for i in range(5):
        gy = py1 + int(i * ph / 4)
        draw.line([(px1, gy), (px2, gy)], fill=GRID, width=1)
        yval = y_max - i * step
        _t(draw, (px1 - 8, gy), fa_num(yval), f_tiny, MUTED, 'rm')

    pts = []
    for i, v in enumerate(series):
        x = px1 + int(i * pw / 23)
        y = py2 - int((v / y_max) * (ph - 2)) if y_max else py2
        pts.append((x, y))

    if max_v > 0:
        fill_pts = [(pts[0][0], py2)] + pts + [(pts[-1][0], py2)]
        ov = Image.new('RGBA', img.size, (0, 0, 0, 0))
        ImageDraw.Draw(ov).polygon(fill_pts, fill=(34, 211, 238, 45))
        img = Image.alpha_composite(img, ov)
        draw = ImageDraw.Draw(img)
        draw.line(pts, fill=NEON_CYAN, width=3)
        for x, y in pts:
            draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=NEON_CYAN, outline=(200, 250, 255), width=1)
    else:
        _t(draw, ((px1 + px2) // 2, (py1 + py2) // 2), 'دادهٔ ساعتی هنوز ثبت نشده', f_body, MUTED, 'mm')

    for i in range(0, 24, 2):
        x = px1 + int(i * pw / 23)
        _t(draw, (x, py2 + 8), fa_num(f'{i:02d}'), f_tiny, MUTED, 'mt')
    _t(draw, (px2 + 4, py2 + 8), 'ساعت', f_tiny, MUTED, 'lt')

    # Media
    media_top = chart_top + ch + 16
    mh = 210
    draw.rounded_rectangle([margin, media_top, W - margin, media_top + mh], radius=18, fill=CARD, outline=(55, 50, 100), width=1)
    _t(draw, (W - margin - 18, media_top + 12), 'تفکیک رسانه‌ها', f_sec, TEXT, 'rt')

    media = [
        ('عکس‌ها', int(counts.get('photo_count') or 0), NEON_PURPLE),
        ('ویدیوها', int(counts.get('video_count') or 0), NEON_CYAN),
        ('استیکرها', int(counts.get('sticker_count') or 0), NEON_ORANGE),
        ('گیف‌ها', int(counts.get('animation_count') or 0), NEON_BLUE),
        ('موزیک‌ها', int(counts.get('audio_count') or 0), NEON_GREEN),
        ('ویس‌ها', int(counts.get('voice_count') or 0), NEON_PINK),
        ('فایل‌ها', int(counts.get('document_count') or 0), NEON_PURPLE),
    ]
    # 4 on first row, 3 on second — right to left
    def place_media(items, row_y, cols):
        cw = (W - 2 * margin - 28 - 10 * (cols - 1)) // cols
        for i, (lbl, val, color) in enumerate(items):
            x2 = W - margin - 14 - i * (cw + 10)
            x1 = x2 - cw
            draw.rounded_rectangle([x1, row_y, x2, row_y + 68], radius=14, fill=CARD2, outline=color, width=2)
            _t(draw, (x2 - 12, row_y + 10), fa_num(val), f_val, color, 'rt')
            _t(draw, (x2 - 12, row_y + 44), lbl, f_tiny, MUTED, 'rt')

    place_media(media[:4], media_top + 48, 4)
    place_media(media[4:], media_top + 128, 3)

    # Top users — horizontal cards like reference
    users_top = media_top + mh + 16
    draw.rounded_rectangle([margin, users_top, W - margin, H - 36], radius=18, fill=CARD, outline=(55, 50, 100), width=1)
    _t(draw, (W - margin - 18, users_top + 12), 'کاربران برتر در ۲۴ ساعت گذشته', f_sec, TEXT, 'rt')

    top_users = list(stats.get('top_users') or [])[:5]
    total_for_pct = sum(int(u.get('msg_count') or 0) for u in top_users) or 1

    if not top_users:
        _t(draw, (W // 2, users_top + 120), 'هنوز کاربر فعالی ثبت نشده است', f_body, MUTED, 'mm')
    else:
        cols = len(top_users)
        gap_u = 10
        uw = (W - 2 * margin - 28 - gap_u * (cols - 1)) // cols
        accents = [NEON_ORANGE, NEON_CYAN, NEON_PINK, NEON_PURPLE, NEON_GREEN]
        for i, u in enumerate(top_users):
            # right-to-left: rank 1 on the right
            x2 = W - margin - 14 - i * (uw + gap_u)
            x1 = x2 - uw
            y1 = users_top + 48
            y2 = y1 + 200
            accent = accents[i % len(accents)]
            draw.rounded_rectangle([x1, y1, x2, y2], radius=14, fill=CARD2, outline=accent, width=2)

            uid = int(u.get('user_id') or 0)
            name = (u.get('first_name') or u.get('username') or f'کاربر')[:16]
            cnt = int(u.get('msg_count') or 0)
            pct = (cnt / total_for_pct) * 100.0

            # avatar
            cx = (x1 + x2) // 2
            photo = avatars.get(uid)
            _circle_avatar(img, (cx, y1 + 52), 34, photo, name, accent)
            draw = ImageDraw.Draw(img)

            # rank badge
            bx, by = x2 - 18, y1 + 14
            draw.ellipse([bx - 12, by - 12, bx + 12, by + 12], fill=accent)
            _t(draw, (bx, by), fa_num(i + 1), f_tiny, TEXT, 'mm')

            _t(draw, (cx, y1 + 90), name, f_body, TEXT, 'mm')
            _t(draw, (cx, y1 + 118), f'تعداد پیام‌ها: {fa_num(cnt)}', f_tiny, MUTED, 'mm')
            _t(draw, (cx, y1 + 140), f'فعالیت: {fa_num(round(pct, 1))}٪', f_tiny, MUTED, 'mm')

            # activity bar
            bar_x1, bar_x2 = x1 + 14, x2 - 14
            bar_y = y1 + 168
            draw.rounded_rectangle([bar_x1, bar_y, bar_x2, bar_y + 8], radius=4, fill=GRID)
            fill = int((cnt / max(int(u.get('msg_count') or 0) for u in top_users)) * (bar_x2 - bar_x1))
            if fill > 2:
                draw.rounded_rectangle([bar_x1, bar_y, bar_x1 + fill, bar_y + 8], radius=4, fill=accent)

    _t(draw, (W // 2, H - 16), 'MEGA BOT  ·  مدیریت گروه و آمار', f_tiny, MUTED, 'mm')

    buf = BytesIO()
    img.convert('RGB').save(buf, format='PNG', optimize=True)
    return buf.getvalue()
