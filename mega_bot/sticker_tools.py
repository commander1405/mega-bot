"""Prepare static stickers for Bale (Telegram-compatible static sticker rules)."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from PIL import Image

MAX_INPUT_BYTES = 30 * 1024 * 1024
MAX_STICKER_SIDE = 512
MAX_STATIC_BYTES = 512 * 1024
ALLOWED_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}


def prepare_static_sticker(data: bytes, filename: str = 'sticker.png') -> tuple[bytes, str]:
    """Return PNG bytes where the longer side is exactly 512px, size <= 512KB when possible."""
    if not data:
        raise ValueError('فایل خالی است.')
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError(f'حجم فایل بیش از {MAX_INPUT_BYTES // (1024 * 1024)} مگابایت است.')
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise ValueError(f'فایل تصویر معتبر نیست ({type(exc).__name__}).') from exc

    if getattr(img, 'is_animated', False):
        try:
            img.seek(0)
        except Exception:
            pass
    img = img.convert('RGBA')

    w, h = img.size
    if w < 1 or h < 1:
        raise ValueError('ابعاد تصویر نامعتبر است.')

    # Scale so the longer side is exactly 512
    if w >= h:
        nw, nh = MAX_STICKER_SIDE, max(1, round(h * (MAX_STICKER_SIDE / w)))
    else:
        nh, nw = MAX_STICKER_SIDE, max(1, round(w * (MAX_STICKER_SIDE / h)))
    if (nw, nh) != (w, h):
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)

    # PNG first (required by classic png_sticker)
    for colors in (None, 256, 128, 64):
        out = io.BytesIO()
        if colors:
            quantized = img.quantize(colors=colors, method=Image.Quantize.MEDIANCUT).convert('RGBA')
            quantized.save(out, format='PNG', optimize=True)
        else:
            img.save(out, format='PNG', optimize=True)
        raw = out.getvalue()
        if len(raw) <= MAX_STATIC_BYTES:
            return raw, 'sticker.png'

    # Last resort: WEBP under 512KB (some gateways accept as document preview)
    out = io.BytesIO()
    img.save(out, format='WEBP', quality=70, method=4)
    raw = out.getvalue()
    return raw, 'sticker.webp'


def extract_zip_images(data: bytes, max_files: int = 40) -> list[tuple[str, bytes]]:
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError('حجم ZIP بیش از حد مجاز است.')
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError('فایل ZIP معتبر نیست.') from exc
    if len(zf.namelist()) > 400:
        raise ValueError('تعداد فایل داخل ZIP زیاد است.')
    out: list[tuple[str, bytes]] = []
    total = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        name = info.filename
        if name.startswith('/') or '..' in Path(name).parts:
            continue
        if Path(name).suffix.lower() not in ALLOWED_EXT:
            continue
        if info.file_size > MAX_INPUT_BYTES:
            continue
        total += info.file_size
        if total > MAX_INPUT_BYTES * 3:
            raise ValueError('ZIP بیش از حد بزرگ است.')
        try:
            raw = zf.read(info)
        except Exception:
            continue
        if raw:
            out.append((Path(name).name, raw))
        if len(out) >= max_files:
            break
    return out
