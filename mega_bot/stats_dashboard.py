"""MEGA BOT group analytics dashboard — layout matched to production reference.

Dark neon glass cards, 24h report, hourly density chart, media grid, top users.
Persian RTL with Vazirmatn. Real data only — no fabricated numbers.
"""
from __future__ import annotations

import os
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageFilter

# ── palette (matched to reference) ─────────────────────────────────────
BG = (6, 8, 18)
CARD = (14, 18, 36)
CARD2 = (18, 22, 42)
BORDER = (40, 48, 78)
CYAN = (0, 210, 255)
BLUE = (70, 140, 255)
GREEN = (40, 220, 140)
RED = (255, 70, 100)
YELLOW = (255, 200, 40)
PINK = (255, 80, 160)
ORANGE = (255, 150, 50)
PURPLE = (160, 100, 255)
TEXT = (245, 248, 255)
MUTED = (140, 155, 190)
GRID = (32, 40, 68)
WHITE = (255, 255, 255)

W, H = 1080, 1680
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
    """Simple Gregorian→Jalali for footer/header date."""
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
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30
    return f'{jy}/{jm:02d}/{jd:02d}'.translate(_FA)


def _round_rect(draw, xy, radius=20, fill=CARD, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def _background() -> Image.Image:
    img = Image.new('RGB', (W, H), BG)
    draw = ImageDraw.Draw(img)
    # subtle dot grid
    for y in range(20, H, 28):
        for x in range(20, W, 28):
            draw.ellipse([x, y, x + 2, y + 2], fill=(18, 22, 40))
    # soft glow blobs
    glow = Image.new('RGB', (W, H), BG)
    gd = ImageDraw.Draw(glow)
    gd.ellipse([-200, -100, 400, 400], fill=(30, 20, 70))
    gd.ellipse([W - 350, H - 500, W + 100, H - 50], fill=(20, 40, 80))
    img = Image.blend(img, glow, 0.35)
    return img


def _circle_avatar(base: Image.Image, center, radius: int, photo: Image.Image | None, name: str, accent):
    cx, cy = center
    draw = ImageDraw.Draw(base)
    # ring
    draw.ellipse([cx - radius - 3, cy - radius - 3, cx + radius + 3, cy + radius + 3], outline=accent, width=3)
    if photo is not None:
        try:
            av = photo.copy().convert('RGBA').resize((radius * 2, radius * 2), Image.Resampling.LANCZOS)
            mask = Image.new('L', (radius * 2, radius * 2), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, radius * 2 - 1, radius * 2 - 1], fill=255)
            base.paste(av, (cx - radius, cy - radius), mask)
            return
        except Exception:
            pass
    # fallback initial
    draw.ellipse([cx - radius, cy - radius, cx + radius, cy + radius], fill=CARD2, outline=accent, width=2)
    initial = (name or '?')[:1]
    f = _font(max(14, radius), True)
    draw.text((cx, cy), _fa(initial), font=f, fill=TEXT, anchor='mm')


def _smooth_line(pts: list[tuple[int, int]]) -> list[tuple[float, float]]:
    """Catmull-Rom-ish densify for smoother chart."""
    if len(pts) < 2:
        return pts
    out: list[tuple[float, float]] = []
    for i in range(len(pts) - 1):
        x0, y0 = pts[i]
        x1, y1 = pts[i + 1]
        steps = max(8, abs(x1 - x0) // 4)
        for s in range(steps):
            t = s / steps
            # simple ease
            t2 = t * t * (3 - 2 * t)
            out.append((x0 + (x1 - x0) * t2, y0 + (y1 - y0) * t2))
    out.append(pts[-1])
    return out


def render_group_dashboard(
    *,
    title: str,
    stats: dict[str, Any],
    members_count: int | None = None,
    avatars: dict[int, Image.Image] | None = None,
    chat_id: int | None = None,
) -> bytes:
    """Reference-faithful 24h analytics image. Only real stats values."""
    img = _background()
    draw = ImageDraw.Draw(img)
    avatars = avatars or {}

    f_h1 = _font(42, True)
    f_h2 = _font(22, True)
    f_kpi = _font(40, True)
    f_kpi_s = _font(15, True)
    f_sec = _font(18, True)
    f_body = _font(16, True)
    f_small = _font(13, True)
    f_tiny = _font(12, True)
    f_name = _font(18, True)

    counts = stats.get('counts') or {}
    total_msgs = int(stats.get('total_msgs') or 0)
    active = int(stats.get('active_users') or 0)
    joined = int(counts.get('joined') or 0)
    left = int(counts.get('left_count') or 0)
    net = joined - left
    hourly = stats.get('hourly') or []
    top_users = (stats.get('top_users') or [])[:5]
    tracking = stats.get('tracking_start')

    m = 36  # margin

    # ═══ HEADER ═══════════════════════════════════════════════════════
    _round_rect(draw, [m, 28, W - m, 140], radius=28, fill=CARD, outline=BORDER, width=2)
    # logo circle right
    lx, ly, lr = W - m - 52, 84, 36
    draw.ellipse([lx - lr, ly - lr, lx + lr, ly + lr], fill=(20, 60, 120), outline=CYAN, width=3)
    _t(draw, (lx, ly), 'MB', _font(18, True), CYAN, 'mm')
    # title
    _t(draw, (W - m - 110, 70), 'داشبورد تحلیلی', f_h1, TEXT, 'rt')
    date_s = _jalali()
    _t(draw, (W - m - 110, 112), f'گزارش جامع پویای ۲۴ ساعته  ·  {date_s}', f_body, CYAN, 'rt')
    if title:
        _t(draw, (m + 24, 84), (title or '')[:28], f_h2, MUTED, 'lt')

    # ═══ KPI ROW (4 cards) — RTL visual: rightmost = messages ═════════
    kpi_y1, kpi_y2 = 160, 310
    gap = 14
    cw = (W - 2 * m - 3 * gap) // 4
    # order left→right in image coords: net, left, join, msgs (msgs is rightmost in RTL UI)
    kpis = [
        ('رشد خالص', net if net >= 0 else net, YELLOW, f'+{fa_num(net)}' if net >= 0 else fa_num(net)),
        ('خروج کاربران', left, RED, fa_num(left)),
        ('ورود کاربران', joined, GREEN, fa_num(joined)),
        ('پیام‌های ارسالی', total_msgs, BLUE, fa_num(total_msgs)),
    ]
    for i, (label, raw, color, display) in enumerate(kpis):
        x1 = m + i * (cw + gap)
        x2 = x1 + cw
        _round_rect(draw, [x1, kpi_y1, x2, kpi_y2], radius=22, fill=CARD, outline=BORDER, width=2)
        # accent dot top-right of card
        draw.ellipse([x2 - 28, kpi_y1 + 18, x2 - 16, kpi_y1 + 30], fill=color)
        _t(draw, (x2 - 34, kpi_y1 + 24), label, f_kpi_s, MUTED, 'rt')
        _t(draw, ((x1 + x2) // 2, kpi_y1 + 88), display, f_kpi, TEXT, 'mm')
        # bottom accent line
        draw.rounded_rectangle([x1 + 28, kpi_y2 - 14, x2 - 28, kpi_y2 - 10], radius=2, fill=color)
        # small badge
        badge = 'نسبت به دیروز'
        _round_rect(draw, [x1 + 16, kpi_y2 - 48, x2 - 16, kpi_y2 - 24], radius=10, fill=(20, 26, 48), outline=color)
        _t(draw, ((x1 + x2) // 2, kpi_y2 - 36), badge, f_tiny, color, 'mm')

    # ═══ CHART SECTION ════════════════════════════════════════════════
    ch_y1, ch_y2 = 330, 780
    _round_rect(draw, [m, ch_y1, W - m, ch_y2], radius=24, fill=CARD, outline=BORDER, width=2)
    # section title pill right
    _round_rect(draw, [W - m - 280, ch_y1 + 16, W - m - 16, ch_y1 + 48], radius=12, fill=(24, 30, 55), outline=PURPLE)
    _t(draw, (W - m - 28, ch_y1 + 32), 'تحلیل و تراکم پیام‌ها در طول شبانه‌روز', f_small, TEXT, 'rt')

    # period chips from hourly buckets
    by_hour = {int(h.get('hour') or 0): int(h.get('count') or 0) for h in hourly}
    # fill 0..23
    series = [by_hour.get(h, 0) for h in range(24)]
    night = sum(series[0:6])
    morning = sum(series[6:12])
    noon = sum(series[12:18])
    evening = sum(series[18:24])
    chips = [
        (f'شب (۰–۶)', night, PURPLE),
        (f'صبح (۶–۱۲)', morning, BLUE),
        (f'ظهر (۱۲–۱۸)', noon, YELLOW),
        (f'عصر (۱۸–۲۴)', evening, PINK),
    ]
    chip_w = (W - 2 * m - 40 - 3 * 10) // 4
    for i, (lab, val, col) in enumerate(chips):
        # right-to-left placement
        x2 = W - m - 20 - i * (chip_w + 10)
        x1 = x2 - chip_w
        y = ch_y1 + 60
        _round_rect(draw, [x1, y, x2, y + 44], radius=12, fill=(20, 26, 48), outline=col)
        _t(draw, (x2 - 10, y + 14), lab, f_tiny, MUTED, 'rt')
        _t(draw, (x2 - 10, y + 32), f'{fa_num(val)} پیام', f_small, TEXT, 'rt')

    # chart area
    cx1, cy1 = m + 50, ch_y1 + 130
    cx2, cy2 = W - m - 30, ch_y2 - 50
    max_v = max(series) if series else 0
    if max_v <= 0:
        _t(draw, ((cx1 + cx2) // 2, (cy1 + cy2) // 2),
           'دادهٔ کافی نیست — از زمان فعال‌شدن ثبت آمار', f_body, MUTED, 'mm')
    else:
        for g in range(4):
            gy = cy1 + int((cy2 - cy1) * g / 3)
            draw.line([(cx1, gy), (cx2, gy)], fill=GRID, width=1)
            _t(draw, (cx1 - 8, gy), fa_num(int(max_v * (1 - g / 3))), f_tiny, MUTED, 'rm')
        pts = []
        for h in range(24):
            x = cx1 + int((cx2 - cx1) * (h / 23))
            y = cy2 - int((cy2 - cy1) * (series[h] / max_v))
            pts.append((x, y))
        smooth = _smooth_line(pts)
        # area under curve
        if len(smooth) >= 2:
            area = list(smooth) + [(smooth[-1][0], cy2), (smooth[0][0], cy2)]
            # draw on overlay for soft fill
            overlay = Image.new('RGBA', (W, H), (0, 0, 0, 0))
            od = ImageDraw.Draw(overlay)
            od.polygon(area, fill=(70, 140, 255, 55))
            img = Image.alpha_composite(img.convert('RGBA'), overlay).convert('RGB')
            draw = ImageDraw.Draw(img)
            draw.line(smooth, fill=WHITE, width=3)
            # peak marker
            peak_i = max(range(24), key=lambda i: series[i])
            if series[peak_i] > 0:
                px, py = pts[peak_i]
                draw.ellipse([px - 8, py - 8, px + 8, py + 8], outline=YELLOW, width=3, fill=BG)
                badge = f'{fa_num(series[peak_i])} پیام'
                _round_rect(draw, [px - 50, py - 40, px + 50, py - 16], radius=10, fill=YELLOW)
                _t(draw, (px, py - 28), badge, f_tiny, BG, 'mm')
        # x axis labels
        for h in (0, 2, 5, 8, 11, 14, 17, 20, 22):
            x = cx1 + int((cx2 - cx1) * (h / 23))
            _t(draw, (x, cy2 + 16), f'{h:02d}:00'.translate(_FA), f_tiny, MUTED, 'mm')
        # legend
        _t(draw, (W - m - 40, ch_y1 + 110), 'امروز', f_tiny, CYAN, 'rt')
        draw.line([(W - m - 100, ch_y1 + 110), (W - m - 70, ch_y1 + 110)], fill=WHITE, width=2)

    # ═══ MEDIA BREAKDOWN ══════════════════════════════════════════════
    med_y1, med_y2 = 800, 1060
    _round_rect(draw, [m, med_y1, W - m, med_y2], radius=24, fill=CARD, outline=BORDER, width=2)
    _round_rect(draw, [W - m - 160, med_y1 + 14, W - m - 16, med_y1 + 44], radius=12, fill=(24, 30, 55), outline=PINK)
    _t(draw, (W - m - 28, med_y1 + 29), 'تفکیک رسانه‌ها', f_sec, TEXT, 'rt')

    media = [
        ('عکس‌ها', int(counts.get('photo_count') or 0), BLUE),
        ('ویدیوها', int(counts.get('video_count') or 0), PURPLE),
        ('استیکرها', int(counts.get('sticker_count') or 0), YELLOW),
        ('فایل‌های گیف', int(counts.get('animation_count') or 0), PINK),
        ('موزیک‌ها', int(counts.get('audio_count') or 0), MUTED),
        ('ویس‌ها', int(counts.get('voice_count') or 0), CYAN),
    ]
    # 2 rows × 3 cols, RTL
    mw = (W - 2 * m - 40 - 2 * 12) // 3
    mh = 70
    for i, (lab, val, col) in enumerate(media):
        row, col_i = divmod(i, 3)
        # RTL: col 0 on the right
        x2 = W - m - 20 - col_i * (mw + 12)
        x1 = x2 - mw
        y1 = med_y1 + 60 + row * (mh + 14)
        y2 = y1 + mh
        _round_rect(draw, [x1, y1, x2, y2], radius=16, fill=CARD2, outline=BORDER, width=1)
        draw.rectangle([x2 - 6, y1 + 12, x2 - 2, y2 - 12], fill=col)
        _t(draw, (x2 - 16, y1 + 24), lab, f_body, MUTED, 'rt')
        _t(draw, (x1 + 20, (y1 + y2) // 2), fa_num(val), f_kpi, TEXT, 'lm')

    # ═══ TOP USERS ════════════════════════════════════════════════════
    top_y1 = 1080
    _round_rect(draw, [m, top_y1, W - m, H - 70], radius=24, fill=CARD, outline=BORDER, width=2)
    _round_rect(draw, [W - m - 220, top_y1 + 14, W - m - 16, top_y1 + 44], radius=12, fill=(24, 30, 55), outline=YELLOW)
    _t(draw, (W - m - 28, top_y1 + 29), 'کاربران برتر (فعال‌ترین‌ها)', f_sec, TEXT, 'rt')

    if not top_users:
        _t(draw, (W // 2, top_y1 + 160), 'هنوز فعالیتی در ۲۴ ساعت ثبت نشده', f_body, MUTED, 'mm')
    else:
        accents = [YELLOW, CYAN, ORANGE, PURPLE, GREEN]
        row_h = 78
        for i, u in enumerate(top_users[:5]):
            y1 = top_y1 + 60 + i * (row_h + 10)
            y2 = y1 + row_h
            accent = accents[i % len(accents)]
            _round_rect(draw, [m + 16, y1, W - m - 16, y2], radius=18, fill=CARD2, outline=accent, width=2)
            uid = int(u.get('user_id') or 0)
            name = (u.get('name') or u.get('first_name') or u.get('username') or 'کاربر')[:32]
            cnt = int(u.get('msg_count') or 0)
            # avatar right
            _circle_avatar(img, (W - m - 56, (y1 + y2) // 2), 26, avatars.get(uid), name, accent)
            draw = ImageDraw.Draw(img)
            # name + subtitle
            _t(draw, (W - m - 100, (y1 + y2) // 2 - 12), name, f_name, TEXT, 'rt')
            _t(draw, (W - m - 100, (y1 + y2) // 2 + 14), 'فعالیت در ۲۴ ساعت گذشته', f_tiny, MUTED, 'rt')
            # count badge left
            badge = f'{fa_num(cnt)} پیام'
            bw = max(90, len(badge) * 11)
            _round_rect(draw, [m + 28, (y1 + y2) // 2 - 16, m + 28 + bw, (y1 + y2) // 2 + 16], radius=14, fill=(30, 36, 60), outline=accent)
            _t(draw, (m + 28 + bw // 2, (y1 + y2) // 2), badge, f_small, TEXT, 'mm')

    # footer
    note = 'آمار از زمان شروع ثبت'
    if tracking:
        note = f'ثبت از {tracking}'
    _t(draw, (m + 8, H - 36), note, f_tiny, MUTED, 'lt')
    _t(draw, (W - m - 8, H - 36), 'MEGA BOT  ·  Generated stats', f_tiny, MUTED, 'rt')

    buf = BytesIO()
    img.save(buf, format='PNG', optimize=False, compress_level=3)
    return buf.getvalue()
