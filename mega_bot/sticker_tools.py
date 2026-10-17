"""Static sticker image prep for Bale (Telegram-compatible constraints)."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from PIL import Image

MAX_INPUT_BYTES = 30 * 1024 * 1024
MAX_STICKER_SIDE = 512
MAX_STATIC_BYTES = 512 * 1024  # API soft limit for static stickers
ALLOWED_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}


def prepare_static_sticker(data: bytes, filename: str = 'sticker.png') -> tuple[bytes, str]:
    """Resize so longest side is 512, export WEBP (lossless-ish) under size limit."""
    if not data:
        raise ValueError('فایل خالی است.')
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError(f'حجم فایل بیش از {MAX_INPUT_BYTES // (1024*1024)} مگابایت است.')
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:
        raise ValueError(f'فایل تصویر معتبر نیست: {type(exc).__name__}') from exc
    # Animated WEBP/GIF: take first frame for static set
    if getattr(img, 'is_animated', False):
        img.seek(0)
    if img.mode not in ('RGBA', 'RGB'):
        img = img.convert('RGBA')
    elif img.mode == 'RGB':
        img = img.convert('RGBA')
    w, h = img.size
    if w < 1 or h < 1:
        raise ValueError('ابعاد تصویر نامعتبر است.')
    scale = MAX_STICKER_SIDE / max(w, h)
    if scale != 1.0:
        nw, nh = max(1, int(w * scale)), max(1, int(h * scale))
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    # Prefer WEBP
    out = io.BytesIO()
    quality = 90
    while quality >= 40:
        out.seek(0)
        out.truncate(0)
        img.save(out, format='WEBP', quality=quality, method=4)
        if out.tell() <= MAX_STATIC_BYTES:
            break
        quality -= 10
    raw = out.getvalue()
    if len(raw) > MAX_STATIC_BYTES * 2:
        # fallback PNG
        out = io.BytesIO()
        img.save(out, format='PNG', optimize=True)
        raw = out.getvalue()
        return raw, 'sticker.png'
    return raw, 'sticker.webp'


def extract_zip_images(data: bytes, max_files: int = 50) -> list[tuple[str, bytes]]:
    """Safe ZIP extract of image members only (no path traversal)."""
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError('حجم ZIP بیش از حد مجاز است.')
    out: list[tuple[str, bytes]] = []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError('فایل ZIP معتبر نیست.') from exc
    names = zf.namelist()
    if len(names) > 500:
        raise ValueError('تعداد فایل‌های داخل ZIP بیش از حد است.')
    total_uncompressed = 0
    for info in zf.infolist():
        if info.is_dir():
            continue
        if info.file_size > MAX_INPUT_BYTES:
            continue
        total_uncompressed += info.file_size
        if total_uncompressed > MAX_INPUT_BYTES * 4:
            raise ValueError('ZIP مشکوک یا بیش‌ازحد بزرگ است.')
        name = info.filename
        # Zip slip
        if name.startswith('/') or '..' in Path(name).parts:
            continue
        ext = Path(name).suffix.lower()
        if ext not in ALLOWED_EXT:
            continue
        try:
            raw = zf.read(info)
        except Exception:
            continue
        if not raw:
            continue
        out.append((Path(name).name, raw))
        if len(out) >= max_files:
            break
    return out
