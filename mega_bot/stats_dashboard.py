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
BG = (2, 3, 10)
CARD = (8, 10, 24)
CARD_INNER = (6, 8, 20)
BORDER = (90, 60, 180)
CYAN = (0, 255, 255)
CYAN_SOFT = (0, 240, 255)
PURPLE = (200, 70, 255)
PINK = (255, 50, 160)
GREEN = (20, 255, 170)
GOLD = (255, 200, 30)
ORANGE = (255, 150, 30)
BLUE = (60, 170, 255)
TEXT = (255, 255, 255)
MUTED = (160, 175, 205)
GRID = (36, 42, 70)

W, H = 1280, 1140
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



# ── Unicode name rendering (cmap-accurate multi-font fallback) ──────────
_NAME_FONT_FILES = (
    'Vazirmatn-Bold.ttf', 'Vazirmatn-Regular.ttf',
    'NotoSansMath-Regular.ttf',
    'NotoSansEgyptianHieroglyphs-Regular.ttf',
    'NotoSansThai-Regular.ttf',
    'NotoSansSymbols2-Regular.ttf', 'NotoSansSymbols-Regular.ttf',
    'NotoSans-Bold.ttf', 'NotoSans-Regular.ttf',
    'DejaVuSans-Bold.ttf', 'DejaVuSans.ttf',
    'NotoColorEmoji.ttf',
    'unifont.otf',
    'unifont_upper.otf',
)
_SYSTEM_FONTS = (
    str(_FONT_DIR / 'unifont.otf'),
    str(_FONT_DIR / 'unifont_upper.otf'),
    '/usr/share/fonts/truetype/unifont/unifont.ttf',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
    '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
    '/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf',
    '/usr/share/fonts/truetype/symbola/Symbola.ttf',
    '/usr/share/fonts/truetype/ancient-scripts/Symbola_hint.ttf',
    '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
)
_font_obj_cache: dict = {}
_cmap_cache: dict = {}


def _cmap_for(path: str) -> set:
    if path in _cmap_cache:
        return _cmap_cache[path]
    codes: set = set()
    try:
        from fontTools.ttLib import TTFont
        tt = TTFont(path, fontNumber=0)
        codes = set((tt.getBestCmap() or {}).keys())
        tt.close()
    except Exception:
        pass
    _cmap_cache[path] = codes
    return codes


def _resolve_font_path(name: str) -> str | None:
    local = _FONT_DIR / name
    if local.is_file():
        return str(local)
    for alt in (f'/usr/share/fonts/truetype/noto/{name}', f'/usr/share/fonts/truetype/dejavu/{name}'):
        if Path(alt).is_file():
            return alt
    return None


def _load_sized(path: str, size: int):
    key = (path, size)
    if key in _font_obj_cache:
        return _font_obj_cache[key]
    try:
        sz = 109 if 'moji' in path.lower() else size
        font = ImageFont.truetype(path, sz)
        _font_obj_cache[key] = font
        return font
    except Exception:
        _font_obj_cache[key] = None
        return None


def _font_for_char(ch: str, size: int, bold: bool = True):
    o = ord(ch)
    preferred = []
    if 0x0600 <= o <= 0x06FF or 0xFB50 <= o <= 0xFDFF:
        preferred += ['Vazirmatn-Bold.ttf', 'Vazirmatn-Regular.ttf']
    if 0x1D400 <= o <= 0x1D7FF:
        preferred += ['NotoSansMath-Regular.ttf']
    if 0x13000 <= o <= 0x1342F:
        preferred += ['NotoSansEgyptianHieroglyphs-Regular.ttf']
    if 0x0E00 <= o <= 0x0E7F:
        preferred += ['NotoSansThai-Regular.ttf']
    if 0x1F300 <= o <= 0x1FAFF or 0x2600 <= o <= 0x27BF:
        preferred += ['NotoColorEmoji.ttf']
    preferred += list(_NAME_FONT_FILES)
    seen = set()
    for name in preferred:
        if name in seen:
            continue
        seen.add(name)
        path = _resolve_font_path(name)
        if not path or o not in _cmap_for(path):
            continue
        font = _load_sized(path, size)
        if font is not None:
            return font
    for path in _SYSTEM_FONTS:
        if not Path(path).is_file() or o not in _cmap_for(path):
            continue
        font = _load_sized(path, size)
        if font is not None:
            return font
    return _font(size, bold)


def _measure_mixed(text: str, size: int, bold: bool = True) -> int:
    if not text:
        return 0
    w = 0
    for ch in text:
        font = _font_for_char(ch, size, bold)
        try:
            if hasattr(font, 'getlength'):
                w += max(1, int(font.getlength(ch)))
            else:
                b = font.getbbox(ch) if hasattr(font, 'getbbox') else None
                w += max(1, (b[2] - b[0]) if b else size // 2)
        except Exception:
            w += size // 2
    return w


def _draw_mixed(draw, xy, text: str, size: int, fill=TEXT, anchor='mm', bold: bool = True, max_width: int | None = None):
    if not text:
        return
    s = str(text)
    if max_width:
        while len(s) > 1 and _measure_mixed(s, size, bold) > max_width:
            s = s[:-1]
        if len(s) < len(str(text)) and len(s) > 1:
            s = s[:-1] + '…'
    total_w = _measure_mixed(s, size, bold)
    x, y = xy
    if anchor in ('mm', 'mt', 'mb'):
        x -= total_w // 2
    elif anchor in ('rt', 'rm', 'rb'):
        x -= total_w
    cursor = float(x)
    for ch in s:
        o = ord(ch)
        draw_ch = ch
        if 0x0600 <= o <= 0x06FF:
            try:
                import arabic_reshaper
                draw_ch = arabic_reshaper.reshape(ch)
            except Exception:
                pass
        font = _font_for_char(ch, size, bold)
        try:
            draw.text((cursor, y), draw_ch, font=font, fill=fill, anchor='lm')
            if hasattr(font, 'getlength'):
                cursor += float(font.getlength(draw_ch))
            else:
                b = font.getbbox(draw_ch) if hasattr(font, 'getbbox') else None
                cursor += float((b[2] - b[0]) if b else size // 2)
        except Exception:
            cursor += size / 2



def _icon_bubble(draw, cx, cy, r, color):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=2)


def _draw_kpi_icon(draw, kind: str, x1, y1, x2, y2, color):
    """Simple vector-style icons (SVG-like paths drawn with Pillow)."""
    cx = x1 + 28
    cy = (y1 + y2) // 2
    if kind == 'msgs':
        # chat bubble
        draw.rounded_rectangle([cx - 12, cy - 10, cx + 12, cy + 6], radius=5, outline=color, width=2)
        draw.polygon([(cx - 4, cy + 6), (cx - 10, cy + 14), (cx + 2, cy + 6)], outline=color)
        draw.line([(cx - 6, cy - 2), (cx + 6, cy - 2)], fill=color, width=2)
        draw.line([(cx - 6, cy + 2), (cx + 3, cy + 2)], fill=color, width=2)
    elif kind == 'active':
        # user
        draw.ellipse([cx - 7, cy - 12, cx + 7, cy - 2], outline=color, width=2)
        draw.arc([cx - 12, cy - 2, cx + 12, cy + 14], 200, 340, fill=color, width=2)
    elif kind == 'join':
        # user +
        draw.ellipse([cx - 7, cy - 12, cx + 5, cy - 2], outline=color, width=2)
        draw.arc([cx - 12, cy - 2, cx + 8, cy + 14], 200, 340, fill=color, width=2)
        draw.line([(cx + 10, cy - 2), (cx + 10, cy + 10)], fill=color, width=2)
        draw.line([(cx + 4, cy + 4), (cx + 16, cy + 4)], fill=color, width=2)
    elif kind == 'left':
        # door/exit
        draw.rounded_rectangle([cx - 10, cy - 12, cx + 6, cy + 12], radius=2, outline=color, width=2)
        draw.line([(cx + 6, cy), (cx + 16, cy)], fill=color, width=2)
        draw.polygon([(cx + 10, cy - 5), (cx + 16, cy), (cx + 10, cy + 5)], outline=color)
    elif kind == 'members':
        # group
        draw.ellipse([cx - 10, cy - 12, cx - 2, cy - 4], outline=color, width=2)
        draw.ellipse([cx + 2, cy - 12, cx + 10, cy - 4], outline=color, width=2)
        draw.arc([cx - 14, cy - 4, cx, cy + 12], 200, 340, fill=color, width=2)
        draw.arc([cx, cy - 4, cx + 14, cy + 12], 200, 340, fill=color, width=2)
    elif kind == 'photo':
        draw.rounded_rectangle([cx - 12, cy - 10, cx + 12, cy + 10], radius=3, outline=color, width=2)
        draw.ellipse([cx - 3, cy - 3, cx + 5, cy + 5], outline=color, width=2)
        draw.polygon([(cx - 10, cy + 8), (cx - 2, cy - 2), (cx + 4, cy + 4), (cx + 10, cy + 8)], outline=color)
    elif kind == 'video':
        draw.rounded_rectangle([cx - 12, cy - 9, cx + 4, cy + 9], radius=3, outline=color, width=2)
        draw.polygon([(cx + 4, cy - 6), (cx + 14, cy), (cx + 4, cy + 6)], outline=color)
    elif kind == 'sticker':
        draw.ellipse([cx - 12, cy - 12, cx + 12, cy + 12], outline=color, width=2)
        draw.arc([cx - 6, cy - 2, cx + 6, cy + 8], 20, 160, fill=color, width=2)
        draw.ellipse([cx - 5, cy - 5, cx - 2, cy - 2], fill=color)
        draw.ellipse([cx + 2, cy - 5, cx + 5, cy - 2], fill=color)
    elif kind == 'gif':
        draw.rounded_rectangle([cx - 12, cy - 10, cx + 12, cy + 10], radius=4, outline=color, width=2)
        _t(draw, (cx, cy), 'GIF', _font(9, True), color, 'mm')
    elif kind == 'music':
        draw.ellipse([cx - 10, cy + 2, cx - 2, cy + 10], outline=color, width=2)
        draw.line([(cx - 2, cy + 6), (cx - 2, cy - 10), (cx + 10, cy - 12), (cx + 10, cy + 2)], fill=color, width=2)
        draw.ellipse([cx + 2, cy + 2, cx + 10, cy + 10], outline=color, width=2)
    elif kind == 'voice':
        draw.rounded_rectangle([cx - 5, cy - 12, cx + 5, cy + 4], radius=5, outline=color, width=2)
        draw.arc([cx - 10, cy - 4, cx + 10, cy + 12], 10, 170, fill=color, width=2)
        draw.line([(cx, cy + 12), (cx, cy + 16)], fill=color, width=2)
    elif kind == 'file':
        draw.polygon([(cx - 8, cy - 12), (cx + 4, cy - 12), (cx + 10, cy - 6), (cx + 10, cy + 12), (cx - 8, cy + 12)], outline=color)
        draw.line([(cx + 4, cy - 12), (cx + 4, cy - 6), (cx + 10, cy - 6)], fill=color, width=2)
    elif kind == 'chart':
        for i, h in enumerate((6, 12, 9, 15)):
            bx = cx - 10 + i * 6
            draw.rectangle([bx, cy + 8 - h, bx + 4, cy + 8], fill=color)


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
    g.ellipse([W - 420, -180, W + 180, 320], fill=(140, 40, 230, 85))
    g.ellipse([-120, 200, 420, 720], fill=(20, 140, 200, 70))
    g.ellipse([W // 3, H - 280, W // 3 + 500, H + 120], fill=(120, 30, 180, 65))
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
    chat_id: int | None = None,
) -> bytes:
    """Render MEGA BOT group analytics image (dark neon, Persian RTL).

    Numbers must come from real DB aggregates in ``stats``.
    """
    img = _background()
    draw = ImageDraw.Draw(img)
    avatars = avatars or {}

    f_title = _font(44, True)
    f_sub = _font(18, True)
    f_kpi = _font(36, True)
    f_kpi_lbl = _font(15, True)
    f_sec = _font(20, True)
    f_body = _font(16, True)
    f_small = _font(14, True)
    f_tiny = _font(12, True)
    f_axis = _font(12, True)

    counts = stats.get('counts') or {}
    total_msgs = int(stats.get('total_msgs') or 0)
    total_7 = int(stats.get('total_msgs_7') or total_msgs)
    total_30 = int(stats.get('total_msgs_30') or total_7)
    active = int(stats.get('active_users') or 0)
    joined = int(counts.get('joined') or 0)
    left = int(counts.get('left_count') or 0)
    warns = int(stats.get('warn_total') or 0)
    members = members_count if isinstance(members_count, int) else None
    tracking = stats.get('tracking_start')
    daily_7 = stats.get('daily_7') or []
    hourly = stats.get('hourly') or []
    top_users = (stats.get('top_users') or [])[:5]

    margin = 28
    # ── Header bar
    _panel(draw, [margin, 18, W - margin, 100], radius=20, fill=(10, 14, 32), outline=(70, 50, 140), width=2)
    # Brand
    _t(draw, (margin + 24, 40), 'MEGA BOT', f_sec, CYAN_SOFT, 'lt')
    _t(draw, (margin + 24, 68), 'داشبورد تحلیلی گروه', f_small, MUTED, 'lt')
    # Group title
    gtitle = (title or 'گروه')[:40]
    _t(draw, (W - margin - 24, 42), gtitle, f_title, TEXT, 'rt')
    id_line = f'ID: {chat_id}' if chat_id is not None else ''
    if members is not None:
        id_line = (id_line + '  ·  ' if id_line else '') + f'عضو: {fa_num(members)}'
    if id_line:
        _t(draw, (W - margin - 24, 78), id_line, f_small, MUTED, 'rt')
    # status pill
    draw.rounded_rectangle([margin + 200, 36, margin + 310, 62], radius=12, fill=(20, 60, 50), outline=GREEN)
    _t(draw, (margin + 255, 49), 'فعال', f_tiny, GREEN, 'mm')

    # ── KPI cards (4)
    kpi_y1, kpi_y2 = 118, 230
    gap = 14
    card_w = (W - 2 * margin - 3 * gap) // 4
    kpis = [
        ('پیام‌های ۲۴س', total_msgs, CYAN_SOFT, 'msg'),
        ('کاربران فعال', active, BLUE, 'user'),
        ('کاربران جدید', joined, GREEN, 'cal'),
        ('تخلفات/اخطار', warns, PINK, 'shield'),
    ]
    for i, (label, value, color, kind) in enumerate(kpis):
        x1 = margin + i * (card_w + gap)
        x2 = x1 + card_w
        _panel(draw, [x1, kpi_y1, x2, kpi_y2], radius=16, fill=CARD, outline=color, width=2)
        _draw_kpi_icon(draw, kind, x1 + 12, kpi_y1 + 14, x1 + 48, kpi_y1 + 50, color)
        _t(draw, (x2 - 16, kpi_y1 + 28), label, f_kpi_lbl, MUTED, 'rt')
        val_s = fa_num(value) if value or value == 0 else '—'
        # Missing data note only if tracking never started and zero
        _t(draw, (x2 - 16, kpi_y1 + 72), val_s, f_kpi, TEXT, 'rt')

    # ── Activity chart panel (7-day or hourly)
    chart_top, chart_bot = 248, 520
    _panel(draw, [margin, chart_top, W - margin, chart_bot], radius=18, fill=CARD, outline=BORDER, width=1)
    _t(draw, (W - margin - 20, chart_top + 22), 'روند فعالیت گروه (۷ روز گذشته)', f_sec, TEXT, 'rt')
    _t(draw, (margin + 20, chart_top + 22), 'پیام‌ها', f_small, MUTED, 'lt')

    # Prefer daily_7; else synthesize from hourly by day is not available — use hourly as fallback line
    series = []
    labels = []
    if daily_7:
        series = [int(p.get('count') or 0) for p in daily_7]
        # Persian weekday labels if 7 points
        try:
            from datetime import date as _date
            wd = ['دوشنبه', 'سه‌شنبه', 'چهارشنبه', 'پنجشنبه', 'جمعه', 'شنبه', 'یکشنبه']  # Mon=0
            for p in daily_7:
                d = _date.fromisoformat(p['day'])
                labels.append(wd[d.weekday()][:3])
        except Exception:
            labels = [p.get('label') or '' for p in daily_7]
    elif hourly:
        # bucket by hour for last points
        by_h = {int(h.get('hour') or 0): int(h.get('count') or 0) for h in hourly}
        series = [by_h.get(h, 0) for h in range(0, 24, 3)]
        labels = [fa_num(h) for h in range(0, 24, 3)]
    else:
        series = [0] * 7
        labels = ['—'] * 7

    cx1, cy1 = margin + 50, chart_top + 55
    cx2, cy2 = W - margin - 30, chart_bot - 40
    max_v = max(series) if series else 0
    if max_v <= 0:
        max_v = 1
        _t(draw, ((cx1 + cx2) // 2, (cy1 + cy2) // 2), 'دادهٔ کافی نیست — از زمان فعال‌شدن ثبت آمار', f_body, MUTED, 'mm')
    else:
        # grid
        for g in range(5):
            gy = cy1 + int((cy2 - cy1) * g / 4)
            draw.line([(cx1, gy), (cx2, gy)], fill=GRID, width=1)
            val = int(max_v * (1 - g / 4))
            _t(draw, (cx1 - 8, gy), fa_num(val), f_axis, MUTED, 'rm')
        n = len(series)
        pts = []
        for i, v in enumerate(series):
            x = cx1 + int((cx2 - cx1) * (i / max(n - 1, 1)))
            y = cy2 - int((cy2 - cy1) * (v / max_v))
            pts.append((x, y))
        # area fill
        if len(pts) >= 2:
            area = pts + [(pts[-1][0], cy2), (pts[0][0], cy2)]
            draw.polygon(area, fill=(30, 80, 180, 60))
            draw.line(pts, fill=CYAN_SOFT, width=3)
            for x, y in pts:
                draw.ellipse([x - 5, y - 5, x + 5, y + 5], fill=CYAN_SOFT, outline=TEXT)
        for i, lab in enumerate(labels):
            x = cx1 + int((cx2 - cx1) * (i / max(n - 1, 1)))
            _t(draw, (x, cy2 + 16), str(lab), f_axis, MUTED, 'mm')

    # ── Bottom row: media + top users
    bot_top, bot_bot = 540, H - 50
    half = (W - 2 * margin - gap) // 2

    # Media breakdown
    mx1, mx2 = margin, margin + half
    _panel(draw, [mx1, bot_top, mx2, bot_bot], radius=18, fill=CARD, outline=BORDER, width=1)
    _t(draw, (mx2 - 16, bot_top + 20), 'تفکیک رسانه‌ها (۲۴س)', f_sec, TEXT, 'rt')
    media_items = [
        ('متن', int(counts.get('text_count') or 0), CYAN_SOFT),
        ('عکس', int(counts.get('photo_count') or 0), BLUE),
        ('ویدیو', int(counts.get('video_count') or 0), PURPLE),
        ('استیکر', int(counts.get('sticker_count') or 0), GREEN),
        ('ویس', int(counts.get('voice_count') or 0), GOLD),
        ('فایل', int(counts.get('document_count') or 0), ORANGE),
        ('گیف', int(counts.get('animation_count') or 0), PINK),
    ]
    media_sum = sum(v for _, v, _ in media_items) or 1
    row_y = bot_top + 52
    for label, val, col in media_items:
        _t(draw, (mx2 - 16, row_y), label, f_body, MUTED, 'rt')
        _t(draw, (mx1 + 16, row_y), fa_num(val), f_body, TEXT, 'lt')
        bar_x1, bar_x2 = mx1 + 70, mx2 - 90
        draw.rounded_rectangle([bar_x1, row_y + 6, bar_x2, row_y + 14], radius=4, fill=GRID)
        fw = int((val / media_sum) * (bar_x2 - bar_x1))
        if fw > 2:
            draw.rounded_rectangle([bar_x1, row_y + 6, bar_x1 + fw, row_y + 14], radius=4, fill=col)
        row_y += 28

    # Top users
    ux1, ux2 = margin + half + gap, W - margin
    _panel(draw, [ux1, bot_top, ux2, bot_bot], radius=18, fill=CARD, outline=BORDER, width=1)
    _t(draw, (ux2 - 16, bot_top + 20), 'کاربران برتر (۲۴س)', f_sec, TEXT, 'rt')
    if not top_users:
        _t(draw, ((ux1 + ux2) // 2, (bot_top + bot_bot) // 2), 'هنوز فعالیتی ثبت نشده', f_body, MUTED, 'mm')
    else:
        accents = [GOLD, CYAN_SOFT, PINK, PURPLE, GREEN]
        max_msg = max(int(u.get('msg_count') or 0) for u in top_users) or 1
        uy = bot_top + 50
        for i, u in enumerate(top_users):
            accent = accents[i % len(accents)]
            uid = int(u.get('user_id') or 0)
            name = (u.get('name') or u.get('first_name') or u.get('username') or 'کاربر')[:28]
            cnt = int(u.get('msg_count') or 0)
            # rank
            draw.ellipse([ux2 - 36, uy - 10, ux2 - 16, uy + 10], fill=accent)
            _t(draw, (ux2 - 26, uy), fa_num(i + 1), f_tiny, TEXT, 'mm')
            # avatar small
            try:
                _circle_avatar(img, (ux1 + 36, uy), 18, avatars.get(uid), name, accent)
                draw = ImageDraw.Draw(img)
            except Exception:
                pass
            _draw_mixed(draw, (ux1 + 62, uy - 6), name, size=16, fill=TEXT, anchor='lt', bold=True, max_width=ux2 - ux1 - 160)
            _t(draw, (ux1 + 62, uy + 12), f'{fa_num(cnt)} پیام', f_tiny, MUTED, 'lt')
            # mini bar
            bx1, bx2 = ux1 + 180, ux2 - 50
            if bx2 > bx1:
                draw.rounded_rectangle([bx1, uy + 8, bx2, uy + 14], radius=3, fill=GRID)
                fw = int((cnt / max_msg) * (bx2 - bx1))
                if fw > 2:
                    draw.rounded_rectangle([bx1, uy + 8, bx1 + fw, uy + 14], radius=3, fill=accent)
            uy += 48

    # Footer
    note = 'آمار از زمان شروع ثبت فعالیت ربات'
    if tracking:
        note = f'ثبت آمار از {tracking}'
    extra = f'۷روز: {fa_num(total_7)}  ·  ۳۰روز: {fa_num(total_30)}'
    _t(draw, (margin + 10, H - 28), note, f_tiny, MUTED, 'lt')
    _t(draw, (W // 2, H - 28), extra, f_tiny, MUTED, 'mm')
    _t(draw, (W - margin - 10, H - 28), 'Powered by Dk · MEGA BOT', f_tiny, MUTED, 'rt')

    buf = BytesIO()
    # RGB without alpha for broad client support
    rgb = Image.new('RGB', img.size, BG)
    rgb.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
    rgb.save(buf, format='PNG', optimize=True, compress_level=6)
    return buf.getvalue()
