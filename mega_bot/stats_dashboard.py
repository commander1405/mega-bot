"""MEGA BOT group analytics dashboard v2 — pixel-faithful to reference layout.

Canvas ~1280×1140, dark neon cyber dashboard, Persian RTL with Vazirmatn.
"""
from __future__ import annotations

import os
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── palette (from reference) ───────────────────────────────────────────
BG = (3, 4, 12)
CARD = (10, 14, 30)
CARD_INNER = (8, 11, 24)
BORDER = (70, 55, 140)
CYAN = (0, 245, 255)
CYAN_SOFT = (20, 230, 255)
PURPLE = (190, 90, 255)
PINK = (255, 70, 170)
GREEN = (40, 255, 180)
GOLD = (255, 210, 50)
ORANGE = (255, 160, 50)
BLUE = (80, 180, 255)
TEXT = (255, 255, 255)
MUTED = (175, 185, 210)
GRID = (36, 42, 70)

W, H = 1280, 1140
_FA = str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹')
_FONT_DIR = Path(__file__).resolve().parent / 'assets' / 'fonts'

# Fallback stack for fancy Unicode names (math bold, fullwidth, symbols, emoji)
_NAME_FONT_FILES = (
    'Vazirmatn-Bold.ttf',
    'Vazirmatn-Regular.ttf',
    'NotoSans-Bold.ttf',
    'NotoSans-Regular.ttf',
    'NotoSansMath-Regular.ttf',
    'NotoSansSymbols2-Regular.ttf',
    'DejaVuSans-Bold.ttf',
    'DejaVuSans.ttf',
    'NotoColorEmoji.ttf',
)

_font_cache: dict[tuple[str, int], object] = {}


def _load_font_file(name: str, size: int):
    key = (name, size)
    if key in _font_cache:
        return _font_cache[key]
    path = _FONT_DIR / name
    if not path.is_file():
        # system fallbacks
        sys_map = {
            'NotoColorEmoji.ttf': '/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf',
            'DejaVuSans.ttf': '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            'DejaVuSans-Bold.ttf': '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        }
        alt = sys_map.get(name)
        path = Path(alt) if alt and Path(alt).is_file() else None
    if not path or not Path(path).is_file():
        _font_cache[key] = None
        return None
    try:
        # NotoColorEmoji is CBDT; Pillow often requires size 109
        sz = 109 if 'Emoji' in name else size
        font = ImageFont.truetype(str(path), sz)
        _font_cache[key] = font
        return font
    except Exception:
        _font_cache[key] = None
        return None


def _glyph_ok(font, ch: str) -> bool:
    if font is None or not ch:
        return False
    try:
        if hasattr(font, 'getbbox'):
            b = font.getbbox(ch)
            return bool(b and (b[2] - b[0]) > 0 and (b[3] - b[1]) > 0)
        m = font.getmask(ch)
        return m.size[0] > 0 and m.size[1] > 0
    except Exception:
        return False


def _font_for_char(ch: str, size: int, bold: bool = True):
    """Pick first font in stack that has a real glyph for this character."""
    # Prefer Vazirmatn for Arabic/Persian
    o = ord(ch)
    order = list(_NAME_FONT_FILES)
    if 0x0600 <= o <= 0x06FF or 0xFB50 <= o <= 0xFDFF or 0xFE70 <= o <= 0xFEFF:
        order = ['Vazirmatn-Bold.ttf' if bold else 'Vazirmatn-Regular.ttf'] + [f for f in order if 'Vazir' not in f]
    elif 'Emoji' in '' or (o >= 0x1F300) or (0x2600 <= o <= 0x27BF) or (0x1F000 <= o <= 0x1FAFF):
        order = ['NotoColorEmoji.ttf'] + [f for f in order if f != 'NotoColorEmoji.ttf']
    elif 0x1D400 <= o <= 0x1D7FF:  # mathematical alphanumeric
        order = ['NotoSansMath-Regular.ttf', 'NotoSans-Bold.ttf', 'DejaVuSans.ttf'] + order
    for name in order:
        font = _load_font_file(name, size)
        if _glyph_ok(font, ch):
            return font
    # last resort
    return _font(size, bold)


def _measure_mixed(text: str, size: int, bold: bool = True) -> int:
    """Width in pixels of mixed-script text."""
    if not text:
        return 0
    w = 0
    for ch in text:
        font = _font_for_char(ch, size, bold)
        try:
            if hasattr(font, 'getlength'):
                w += int(font.getlength(ch))
            elif hasattr(font, 'getbbox'):
                b = font.getbbox(ch)
                w += (b[2] - b[0]) if b else size // 2
            else:
                w += size // 2
        except Exception:
            w += size // 2
    return w


def _draw_mixed(draw, xy, text: str, size: int, fill=TEXT, anchor='mm', bold: bool = True, max_width: int | None = None):
    """Draw username with per-character font fallback (emoji, math bold, symbols, Persian)."""
    if not text:
        return
    # Soft truncate by pixel width
    s = str(text)
    if max_width:
        while len(s) > 1 and _measure_mixed(s, size, bold) > max_width:
            s = s[:-1]
        if s != str(text) and len(s) > 0:
            s = s[:-1] + '…' if len(s) > 1 else s

    total_w = _measure_mixed(s, size, bold)
    x, y = xy
    # anchor horizontal
    if anchor in ('mm', 'mt', 'mb'):
        x = x - total_w // 2
    elif anchor in ('rt', 'rm', 'rb'):
        x = x - total_w
    # vertical: use middle of line roughly
    cy = y
    cursor = x
    for ch in s:
        # shape Arabic letters individually only if Arabic
        o = ord(ch)
        draw_ch = ch
        if 0x0600 <= o <= 0x06FF:
            try:
                import arabic_reshaper
                draw_ch = arabic_reshaper.reshape(ch)
            except Exception:
                draw_ch = ch
        font = _font_for_char(ch, size, bold)
        try:
            # For emoji CBDT fonts size may differ; still draw at baseline
            draw.text((cursor, cy), draw_ch, font=font, fill=fill, anchor='lm')
            if hasattr(font, 'getlength'):
                cursor += int(font.getlength(draw_ch))
            else:
                b = font.getbbox(draw_ch) if hasattr(font, 'getbbox') else None
                cursor += (b[2] - b[0]) if b else size // 2
        except Exception:
            cursor += size // 2



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
    p = _FONT_DIR / name
    if p.is_file():
        return str(p)
    for alt in (
        '/usr/share/fonts/SlidesCarnival/google/Noto Sans Arabic/NotoSansArabic-VariableFont_wdth,wght.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if bold else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
    ):
        if os.path.isfile(alt):
            return alt
    return None


def _font(size: int, bold: bool = False):
    path = _font_path(bold)
    if path:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _fa(text: str) -> str:
    """Shape Persian for Pillow. Do NOT use bidi.get_display (reverses with RTL anchors)."""
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
    return f'{weekdays[dt.weekday()]} {fa_num(jy)}/{fa_num(jm):0>2}/{fa_num(jd):0>2}'


def _panel(draw, xy, radius=18, fill=CARD, outline=BORDER, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _background() -> Image.Image:
    img = Image.new('RGBA', (W, H), (*BG, 255))
    d = ImageDraw.Draw(img)
    # fine dots
    for y in range(0, H, 10):
        for x in range(0, W, 10):
            if (x // 10 + y // 10) % 3 == 0:
                d.point((x, y), fill=(26, 30, 48, 255))
    glow = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    g = ImageDraw.Draw(glow)
    # large purple / blue washes like reference
    g.ellipse([W - 420, -180, W + 180, 320], fill=(120, 50, 220, 70))
    g.ellipse([-120, 200, 420, 720], fill=(30, 120, 180, 55))
    g.ellipse([W // 3, H - 280, W // 3 + 500, H + 120], fill=(100, 40, 160, 50))
    glow = glow.filter(ImageFilter.GaussianBlur(55))
    return Image.alpha_composite(img, glow)


def _circle_avatar(base: Image.Image, center, radius: int, photo: Image.Image | None, name: str, accent):
    cx, cy = center
    size = radius * 2
    d = ImageDraw.Draw(base)
    if photo is not None:
        try:
            src = photo.convert('RGBA')
            w, h = src.size
            side = min(w, h)
            left, top = (w - side) // 2, (h - side) // 2
            src = src.crop((left, top, left + side, top + side)).resize((size, size), Image.Resampling.LANCZOS)
            mask = Image.new('L', (size, size), 0)
            ImageDraw.Draw(mask).ellipse([1, 1, size - 2, size - 2], fill=255)
            layer = Image.new('RGBA', (size, size), (0, 0, 0, 0))
            layer.paste(src, (0, 0))
            layer.putalpha(mask)
            base.paste(layer, (cx - radius, cy - radius), layer)
            d = ImageDraw.Draw(base)
            d.ellipse([cx - radius - 2, cy - radius - 2, cx + radius + 2, cy + radius + 2], outline=accent, width=3)
            return
        except Exception:
            pass
    d.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=(28, 32, 55), outline=accent, width=3)
    initial = (name or '?').strip()[:1] or '?'
    d.text((cx, cy), _fa(initial), font=_font(max(16, radius - 4), True), fill=TEXT, anchor='mm')


def render_group_dashboard(
    *,
    title: str,
    stats: dict[str, Any],
    members_count: int | None = None,
    avatars: dict[int, Image.Image] | None = None,
) -> bytes:
    img = _background()
    draw = ImageDraw.Draw(img)
    avatars = avatars or {}

    f_title = _font(48, True)
    f_sub = _font(20, True)
    f_kpi = _font(38, True)
    f_kpi_lbl = _font(16, True)
    f_sec = _font(22, True)
    f_body = _font(18, True)
    f_name = _font(20, True)
    f_msg = _font(17, True)
    f_small = _font(14, True)
    f_tiny = _font(13, True)
    f_axis = _font(13, True)

    counts = stats.get('counts') or {}
    total_msgs = int(stats.get('total_msgs') or 0)
    active = int(stats.get('active_users') or 0)
    joined = int(counts.get('joined') or 0)
    left = int(counts.get('left_count') or 0)
    members = members_count if isinstance(members_count, int) else None

    # ═══ 1) HEADER ════════════════════════════════════════════════════
    # Title top-right + circular analysis icon
    icon_r = 22
    icon_cx, icon_cy = W - 48, 42
    draw.ellipse([icon_cx - icon_r, icon_cy - icon_r, icon_cx + icon_r, icon_cy + icon_r],
                 outline=CYAN, width=2)
    # mini bars inside icon
    for i, h in enumerate((10, 16, 12)):
        bx = icon_cx - 10 + i * 8
        draw.rectangle([bx, icon_cy + 10 - h, bx + 5, icon_cy + 10], fill=CYAN)

    _t(draw, (W - 80, 28), 'داشبورد تحلیلی', f_title, CYAN, 'rt')
    _t(draw, (W - 48, 78), 'گزارش جامع پویای ۲۴ ساعته', f_sub, MUTED, 'rt')
    date_line = _jalali()
    if title:
        date_line = f'{date_line}  ·  {str(title)[:28]}'
    _t(draw, (W - 48, 104), date_line, f_small, MUTED, 'rt')

    # ═══ 2) FIVE KPI CARDS ════════════════════════════════════════════
    # RTL order from right: پیام‌ها, فعال, جدید, خارج‌شده, کل اعضا
    kpis = [
        ('تعداد پیام‌ها', total_msgs, GOLD, ORANGE),
        ('کاربران فعال', active, PURPLE, PINK),
        ('کاربران جدید', joined, GREEN, CYAN_SOFT),
        ('کاربران خارج‌شده', left, CYAN_SOFT, BLUE),
        ('کل اعضا', members if members is not None else '—', PURPLE, BLUE),
    ]
    margin = 36
    gap = 14
    n = 5
    card_w = (W - 2 * margin - gap * (n - 1)) // n
    y0 = 128
    card_h = 108
    for i, (lbl, val, c1, c2) in enumerate(kpis):
        x2 = W - margin - i * (card_w + gap)
        x1 = x2 - card_w
        _panel(draw, [x1, y0, x2, y0 + card_h], radius=16, fill=CARD, outline=c1, width=2)
        # value
        val_s = fa_num(val) if isinstance(val, (int, float)) else _fa(str(val))
        if isinstance(val, (int, float)):
            _t(draw, (x2 - 18, y0 + 18), fa_num(val), f_kpi, c1, 'rt')
        else:
            _t(draw, (x2 - 18, y0 + 18), str(val), f_kpi, c1, 'rt')
        _t(draw, (x2 - 18, y0 + 66), lbl, f_kpi_lbl, MUTED, 'rt')

    # ═══ 3) HOURLY CHART PANEL ════════════════════════════════════════
    chart_top = 250
    chart_h = 300
    _panel(draw, [margin, chart_top, W - margin, chart_top + chart_h], radius=18, fill=CARD, outline=BORDER, width=1)
    _t(draw, (W - margin - 20, chart_top + 14), 'نمودار تعداد پیام‌ها در طول شبانه‌روز', f_sec, TEXT, 'rt')

    series = [0] * 24
    for h in (stats.get('hourly') or []):
        try:
            hr = int(h.get('hour') or 0) % 24
            series[hr] += int(h.get('count') or 0)
        except Exception:
            pass
    max_v = max(series) if any(series) else 0

    px1, py1 = margin + 56, chart_top + 52
    px2, py2 = W - margin - 36, chart_top + chart_h - 40
    pw, ph = px2 - px1, py2 - py1

    # y scale
    if max_v <= 0:
        y_max = 10
    else:
        step = max(1, (max_v + 3) // 4)
        y_max = step * 4

    for i in range(5):
        gy = py1 + int(i * ph / 4)
        draw.line([(px1, gy), (px2, gy)], fill=GRID, width=1)
        _t(draw, (px1 - 10, gy), fa_num(y_max - i * (y_max // 4)), f_axis, MUTED, 'rm')

    pts = []
    for i, v in enumerate(series):
        x = px1 + int(i * pw / 23)
        y = py2 - int((v / y_max) * (ph - 4)) if y_max else py2
        pts.append((x, y))

    if max_v > 0 and len(pts) >= 2:
        fill_pts = [(pts[0][0], py2)] + pts + [(pts[-1][0], py2)]
        ov = Image.new('RGBA', (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(ov).polygon(fill_pts, fill=(0, 180, 255, 48))
        img = Image.alpha_composite(img, ov)
        draw = ImageDraw.Draw(img)
        draw.line(pts, fill=CYAN, width=3)
        for x, y in pts:
            draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=CYAN, outline=(220, 250, 255), width=1)
    else:
        _t(draw, ((px1 + px2) // 2, (py1 + py2) // 2), 'دادهٔ ساعتی هنوز ثبت نشده', f_body, MUTED, 'mm')

    for i in range(0, 24, 2):
        x = px1 + int(i * pw / 23)
        _t(draw, (x, py2 + 10), fa_num(f'{i:02d}'), f_axis, MUTED, 'mt')
    _t(draw, (px2 + 8, py2 + 10), 'ساعت', f_axis, MUTED, 'lt')

    # ═══ 4) MEDIA BREAKDOWN ═══════════════════════════════════════════
    media_top = chart_top + chart_h + 16
    media_h = 200
    _panel(draw, [margin, media_top, W - margin, media_top + media_h], radius=18, fill=CARD, outline=BORDER, width=1)
    _t(draw, (W - margin - 20, media_top + 12), 'تفکیک رسانه‌ها', f_sec, TEXT, 'rt')

    media_items = [
        ('عکس‌ها', int(counts.get('photo_count') or 0), PURPLE),
        ('ویدیوها', int(counts.get('video_count') or 0), CYAN_SOFT),
        ('استیکرها', int(counts.get('sticker_count') or 0), GOLD),
        ('گیف‌ها', int(counts.get('animation_count') or 0), BLUE),
        ('موسیقی‌ها', int(counts.get('audio_count') or 0), GREEN),
        ('ویس‌ها', int(counts.get('voice_count') or 0), PINK),
        ('فایل‌ها', int(counts.get('document_count') or 0), PURPLE),
    ]

    def _media_row(items, row_y, cols):
        gap_m = 12
        mw = (W - 2 * margin - 28 - gap_m * (cols - 1)) // cols
        for i, (lbl, val, color) in enumerate(items):
            x2 = W - margin - 14 - i * (mw + gap_m)
            x1 = x2 - mw
            _panel(draw, [x1, row_y, x2, row_y + 66], radius=14, fill=CARD_INNER, outline=color, width=2)
            _t(draw, (x2 - 14, row_y + 10), fa_num(val), f_kpi, color, 'rt')
            _t(draw, (x2 - 14, row_y + 42), lbl, f_tiny, MUTED, 'rt')

    _media_row(media_items[:4], media_top + 48, 4)
    _media_row(media_items[4:], media_top + 124, 3)

    # ═══ 5) TOP USERS ═════════════════════════════════════════════════
    users_top = media_top + media_h + 16
    users_bottom = H - 48
    _panel(draw, [margin, users_top, W - margin, users_bottom], radius=18, fill=CARD, outline=BORDER, width=1)
    _t(draw, (W - margin - 20, users_top + 12), 'کاربران برتر در ۲۴ ساعت گذشته', f_sec, TEXT, 'rt')

    top_users = list(stats.get('top_users') or [])[:5]
    total_for_pct = sum(int(u.get('msg_count') or 0) for u in top_users) or 1

    if not top_users:
        _t(draw, (W // 2, (users_top + users_bottom) // 2), 'هنوز کاربر فعالی ثبت نشده است', f_body, MUTED, 'mm')
    else:
        cols = len(top_users)
        gap_u = 12
        uw = (W - 2 * margin - 28 - gap_u * (cols - 1)) // cols
        # rank1 gold, then cyan/pink/purple/green
        accents = [GOLD, CYAN_SOFT, PINK, PURPLE, GREEN]
        max_msg = max(int(u.get('msg_count') or 0) for u in top_users) or 1
        for i, u in enumerate(top_users):
            x2 = W - margin - 14 - i * (uw + gap_u)
            x1 = x2 - uw
            y1 = users_top + 44
            y2 = users_bottom - 14
            accent = accents[i % len(accents)]
            _panel(draw, [x1, y1, x2, y2], radius=16, fill=CARD_INNER, outline=accent, width=2)

            uid = int(u.get('user_id') or 0)
            name = (u.get('first_name') or u.get('username') or 'کاربر')[:40]
            cnt = int(u.get('msg_count') or 0)
            pct = (cnt / total_for_pct) * 100.0
            cx = (x1 + x2) // 2

            # rank badge
            bx, by = x2 - 18, y1 + 16
            draw.ellipse([bx - 12, by - 12, bx + 12, by + 12], fill=accent)
            _t(draw, (bx, by), fa_num(i + 1), f_tiny, TEXT, 'mm')

            # avatar
            av_cy = y1 + 48
            _circle_avatar(img, (cx, av_cy), 28, avatars.get(uid), name, accent)
            draw = ImageDraw.Draw(img)

            # name BELOW avatar with clear gap
            ny = y1 + 92
            # Name: mixed Unicode (emoji, fancy fonts, Persian) — large bold white
            card_inner_w = x2 - x1 - 16
            _draw_mixed(draw, (cx, ny), name, size=20, fill=(255, 255, 255), anchor='mm', bold=True, max_width=card_inner_w)
            _t(draw, (cx, ny + 28), f'تعداد پیام‌ها: {fa_num(cnt)}', f_msg, (255, 255, 255), 'mm')
            _t(draw, (cx, ny + 50), f'فعالیت: {fa_num(round(pct, 1))}٪', f_tiny, MUTED, 'mm')

            # progress bar
            bar_x1, bar_x2 = x1 + 14, x2 - 14
            bar_y = y2 - 20
            draw.rounded_rectangle([bar_x1, bar_y, bar_x2, bar_y + 8], radius=4, fill=GRID)
            fill_w = int((cnt / max_msg) * (bar_x2 - bar_x1))
            if fill_w > 2:
                draw.rounded_rectangle([bar_x1, bar_y, bar_x1 + fill_w, bar_y + 8], radius=4, fill=CYAN_SOFT if i else GOLD)

    # ═══ 6) FOOTER ════════════════════════════════════════════════════
    _t(draw, (W // 2, H - 28), 'MEGA BOT  ·  مدیریت گروه‌ها، سرگرمی و امکانات بیشتر', f_tiny, MUTED, 'mm')
    _t(draw, (W - margin, H - 28), 'Powered by Dk', f_tiny, MUTED, 'rt')

    buf = BytesIO()
    img.convert('RGB').save(buf, format='PNG', optimize=False, compress_level=3)
    return buf.getvalue()
