"""Static sticker prep: 512x512 transparent canvas, PNG (and WEBP if available)."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from PIL import Image

MAX_INPUT_BYTES = 30 * 1024 * 1024
SIDE = 512
MAX_STATIC_BYTES = 512 * 1024
ALLOWED_EXT = {'.png', '.jpg', '.jpeg', '.webp', '.gif'}


def _fit_on_canvas(img: Image.Image) -> Image.Image:
    """Scale image so longest side is 512, pad to exactly 512x512 with transparency."""
    img = img.convert('RGBA')
    w, h = img.size
    if w < 1 or h < 1:
        raise ValueError('ابعاد تصویر نامعتبر است.')
    scale = SIDE / max(w, h)
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    if (nw, nh) != (w, h):
        img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    canvas = Image.new('RGBA', (SIDE, SIDE), (0, 0, 0, 0))
    canvas.paste(img, ((SIDE - nw) // 2, (SIDE - nh) // 2), img)
    return canvas


def prepare_static_sticker(data: bytes, filename: str = 'sticker.png') -> tuple[bytes, str]:
    """Return sticker bytes. Prefer WEBP 512x512; fallback PNG 512x512."""
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
    canvas = _fit_on_canvas(img)

    # Try lossless WEBP (best for sendSticker on Telegram-like APIs)
    try:
        out = io.BytesIO()
        canvas.save(out, format='WEBP', lossless=True, quality=100, method=6)
        raw = out.getvalue()
        if 0 < len(raw) <= MAX_STATIC_BYTES:
            return raw, 'sticker.webp'
        out = io.BytesIO()
        canvas.save(out, format='WEBP', quality=80, method=4)
        raw = out.getvalue()
        if 0 < len(raw) <= MAX_STATIC_BYTES * 2:
            return raw, 'sticker.webp'
    except Exception:
        pass

    # PNG with optional quantization
    for colors in (None, 256, 128, 64):
        out = io.BytesIO()
        if colors:
            q = canvas.quantize(colors=colors, method=Image.Quantize.MEDIANCUT).convert('RGBA')
            q.save(out, format='PNG', optimize=True)
        else:
            canvas.save(out, format='PNG', optimize=True)
        raw = out.getvalue()
        if len(raw) <= MAX_STATIC_BYTES:
            return raw, 'sticker.png'
    return raw, 'sticker.png'


def extract_zip_images(data: bytes, max_files: int = 40) -> list[tuple[str, bytes]]:
    if len(data) > MAX_INPUT_BYTES:
        raise ValueError('حجم ZIP بیش از حد مجاز است.')
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError('فایل ZIP معتبر نیست.') from exc
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
