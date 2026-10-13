"""TTS for MEGA BOT

- Persian: Microsoft neural voice fa-IR-FaridNeural (male)
  via edge-tts (same Microsoft Edge/Azure neural engine, no API key)
- English: Google Translate TTS (free classic Google voice)

Optional env:
  TTS_VOICE_FA=fa-IR-FaridNeural
  TTS_MAX_CHARS=6000
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
import urllib.request
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_TEXT = 'تست'
MAX_CHARS = int(os.getenv('TTS_MAX_CHARS', '6000'))
GOOGLE_CHUNK = 180
# Microsoft Azure / Edge neural male Persian
EDGE_VOICE_FA = os.getenv('TTS_VOICE_FA', 'fa-IR-FaridNeural')
EDGE_VOICE_EN = os.getenv('TTS_VOICE_EN', 'en-US-GuyNeural')


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def normalize_tts_text(text: str) -> str:
    t = (text or '').strip()
    t = t.replace('\r\n', '\n').replace('\r', '\n')
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()


def detect_lang(text: str) -> str:
    latin = len(re.findall(r'[A-Za-z]', text or ''))
    arabic = len(re.findall(r'[\u0600-\u06FF]', text or ''))
    if latin > arabic * 1.2 and latin >= 3:
        return 'en'
    return 'fa'


def estimate_duration_sec(text: str) -> int:
    n = len(normalize_tts_text(text) or '')
    return max(1, int(n / 14) + 1)


def _chunk_text(text: str, size: int) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    buf = ''
    parts = re.split(r'(?<=[.!?…؟!\n،,;؛:])\s+', text)
    for part in parts:
        if not part:
            continue
        if len(buf) + len(part) + 1 <= size:
            buf = f'{buf} {part}'.strip() if buf else part
        else:
            if buf:
                chunks.append(buf)
            if len(part) <= size:
                buf = part
            else:
                for i in range(0, len(part), size):
                    chunks.append(part[i:i + size])
                buf = ''
    if buf:
        chunks.append(buf)
    return chunks or [text[:size]]


def _ffmpeg_to_ogg(src: str, ogg: str, timeout: int = 180) -> None:
    ffmpeg = _which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg not found')
    r = subprocess.run(
        [
            ffmpeg, '-y', '-i', src,
            '-c:a', 'libopus', '-b:a', '32k',
            '-vbr', 'on', '-compression_level', '10',
            ogg,
        ],
        capture_output=True,
        timeout=timeout,
    )
    if r.returncode != 0 or not os.path.isfile(ogg) or os.path.getsize(ogg) < 40:
        err = (r.stderr or b'')[-300:].decode('utf-8', 'ignore')
        raise RuntimeError(f'ffmpeg ogg failed: {err}')


def _concat_files(paths: list[str], out: str) -> None:
    if len(paths) == 1:
        shutil.copyfile(paths[0], out)
        return
    ffmpeg = _which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg not found')
    with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as lst:
        for p in paths:
            lst.write(f"file '{p}'\n")
        list_path = lst.name
    try:
        r = subprocess.run(
            [ffmpeg, '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c', 'copy', out],
            capture_output=True,
            timeout=180,
        )
        if r.returncode != 0 or not os.path.isfile(out):
            # fallback: re-encode merge
            r2 = subprocess.run(
                [ffmpeg, '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c:a', 'libopus', '-b:a', '32k', out],
                capture_output=True,
                timeout=180,
            )
            if r2.returncode != 0 or not os.path.isfile(out):
                raise RuntimeError('ffmpeg concat failed')
    finally:
        try:
            os.unlink(list_path)
        except Exception:
            pass


# ── Google EN ───────────────────────────────────────────────────────────

def _google_mp3_sync(text: str, lang: str = 'en') -> bytes:
    text = normalize_tts_text(text) or DEFAULT_TEXT
    chunks = _chunk_text(text, GOOGLE_CHUNK)
    parts: list[bytes] = []
    for ch in chunks:
        q = urllib.parse.urlencode({'ie': 'UTF-8', 'q': ch, 'tl': lang, 'client': 'tw-ob'})
        url = f'https://translate.google.com/translate_tts?{q}'
        req = urllib.request.Request(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; MEGA-BOT/1.0)'},
        )
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = resp.read()
        if len(data) < 50:
            raise RuntimeError('google empty chunk')
        parts.append(data)
    return b''.join(parts) if len(parts) == 1 else _merge_mp3_bytes(parts)


def _merge_mp3_bytes(parts: list[bytes]) -> bytes:
    with tempfile.TemporaryDirectory(prefix='mega-gtts-') as td:
        paths = []
        for i, data in enumerate(parts):
            path = os.path.join(td, f'{i:03d}.mp3')
            with open(path, 'wb') as f:
                f.write(data)
            paths.append(path)
        merged = os.path.join(td, 'm.mp3')
        _concat_files(paths, merged)
        with open(merged, 'rb') as f:
            return f.read()


async def synthesize_google_en(text: str) -> bytes:
    raw = await asyncio.to_thread(_google_mp3_sync, text, 'en')
    with tempfile.TemporaryDirectory(prefix='mega-gen-') as td:
        mp3 = os.path.join(td, 'a.mp3')
        ogg = os.path.join(td, 'a.ogg')
        with open(mp3, 'wb') as f:
            f.write(raw)
        try:
            _ffmpeg_to_ogg(mp3, ogg)
            with open(ogg, 'rb') as f:
                return f.read()
        except Exception:
            return raw


# ── Microsoft Edge neural (Azure-class voices) ──────────────────────────

async def _edge_communicate_to_file(text: str, voice: str, out_mp3: str) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(out_mp3)


async def synthesize_edge(text: str, voice: str) -> bytes:
    """Microsoft neural TTS via edge-tts → OGG/Opus for Bale voice notes."""
    text = normalize_tts_text(text) or DEFAULT_TEXT
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    # edge-tts handles long text reasonably; still soft-chunk very long runs
    chunks = _chunk_text(text, 1200)
    with tempfile.TemporaryDirectory(prefix='mega-edge-') as td:
        mp3_parts: list[str] = []
        for i, ch in enumerate(chunks):
            path = os.path.join(td, f'{i:03d}.mp3')
            try:
                await _edge_communicate_to_file(ch, voice, path)
            except Exception as exc:
                raise RuntimeError(f'edge:{type(exc).__name__}: {exc}') from exc
            if not os.path.isfile(path) or os.path.getsize(path) < 40:
                raise RuntimeError('edge empty audio')
            mp3_parts.append(path)
        merged_mp3 = os.path.join(td, 'm.mp3')
        _concat_files(mp3_parts, merged_mp3)
        ogg = os.path.join(td, 'o.ogg')
        try:
            _ffmpeg_to_ogg(merged_mp3, ogg)
            with open(ogg, 'rb') as f:
                data = f.read()
        except Exception:
            with open(merged_mp3, 'rb') as f:
                data = f.read()
    if len(data) < 50:
        raise RuntimeError('edge empty final audio')
    return data


async def synthesize_farid_fa(text: str) -> bytes:
    """fa-IR-FaridNeural — Microsoft male Persian neural voice."""
    return await synthesize_edge(text, EDGE_VOICE_FA)


# ── Public entry ────────────────────────────────────────────────────────

async def synthesize_persian(text: str = DEFAULT_TEXT) -> tuple[bytes, str]:
    """FA → FaridNeural (Microsoft), EN → Google."""
    text = normalize_tts_text(text or DEFAULT_TEXT) or DEFAULT_TEXT
    lang = detect_lang(text)

    if lang == 'en':
        data = await synthesize_google_en(text)
        ext = 'ogg' if data[:4] == b'OggS' else 'mp3'
        log.info('TTS engine=google-en bytes=%s', len(data))
        return data, f'voice.{ext}'

    try:
        data = await synthesize_farid_fa(text)
        log.info('TTS engine=edge-farid voice=%s bytes=%s', EDGE_VOICE_FA, len(data))
        return data, 'voice.ogg'
    except Exception as exc:
        log.exception('FaridNeural FA failed')
        raise RuntimeError(f'فارسی FaridNeural: {type(exc).__name__}: {exc}') from exc
