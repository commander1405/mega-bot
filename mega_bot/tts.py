"""Persian text-to-speech (free, offline, no API key, no rate limit).

Engine: espeak-ng (fa) → ffmpeg OGG Opus.
Supports arbitrary-length text (up to several minutes of speech).
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_TEXT = 'تست'
MAX_CHARS = int(os.getenv('TTS_MAX_CHARS', '6000'))
ESPEAK_SPEED = int(os.getenv('TTS_ESPEAK_SPEED', '130'))
ESPEAK_PITCH = int(os.getenv('TTS_ESPEAK_PITCH', '45'))
CHUNK_CHARS = 800


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def normalize_tts_text(text: str) -> str:
    t = (text or '').strip()
    t = t.replace('\r\n', '\n').replace('\r', '\n')
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()


def _wav_to_ogg_opus(wav_path: str, ogg_path: str, timeout: int = 180) -> None:
    ffmpeg = _which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg not found')
    r = subprocess.run(
        [
            ffmpeg, '-y', '-i', wav_path,
            '-c:a', 'libopus', '-b:a', '24k', '-vbr', 'on',
            '-application', 'voip',
            ogg_path,
        ],
        capture_output=True,
        timeout=timeout,
    )
    if r.returncode != 0 or not os.path.isfile(ogg_path) or os.path.getsize(ogg_path) < 50:
        err = (r.stderr or b'').decode('utf-8', errors='replace')[-400:]
        raise RuntimeError(f'ffmpeg opus failed: {err}')


def _chunk_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    if len(text) <= size:
        return [text]
    chunks: list[str] = []
    buf = ''
    parts = re.split(r'(?<=[.!?…؟!\n])\s+', text)
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


def _espeak_to_wav(espeak: str, text: str, wav_path: str, timeout: int = 120) -> None:
    cmd = [
        espeak,
        '-v', 'fa',
        '-s', str(ESPEAK_SPEED),
        '-p', str(ESPEAK_PITCH),
        '-w', wav_path,
        '--stdin',
    ]
    r = subprocess.run(
        cmd,
        input=text.encode('utf-8'),
        capture_output=True,
        timeout=timeout,
    )
    if r.returncode != 0 or not os.path.isfile(wav_path) or os.path.getsize(wav_path) < 44:
        err = (r.stderr or b'').decode('utf-8', errors='replace')[-300:]
        raise RuntimeError(f'espeak failed: {err or r.returncode}')


def _concat_wavs(wav_paths: list[str], out_path: str) -> None:
    if len(wav_paths) == 1:
        shutil.copyfile(wav_paths[0], out_path)
        return
    ffmpeg = _which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg not found')
    list_path = out_path + '.txt'
    with open(list_path, 'w', encoding='utf-8') as f:
        for w in wav_paths:
            safe = w.replace("'", "'\\''")
            f.write(f"file '{safe}'\n")
    r = subprocess.run(
        [ffmpeg, '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c', 'copy', out_path],
        capture_output=True,
        timeout=180,
    )
    if r.returncode != 0 or not os.path.isfile(out_path):
        r2 = subprocess.run(
            [ffmpeg, '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c:a', 'pcm_s16le', out_path],
            capture_output=True,
            timeout=180,
        )
        if r2.returncode != 0 or not os.path.isfile(out_path):
            err = (r2.stderr or r.stderr or b'').decode('utf-8', errors='replace')[-400:]
            raise RuntimeError(f'wav concat failed: {err}')


def synthesize_espeak(text: str = DEFAULT_TEXT) -> bytes:
    espeak = _which('espeak-ng') or _which('espeak')
    if not espeak:
        raise RuntimeError('espeak-ng not installed')
    text = normalize_tts_text(text or DEFAULT_TEXT) or DEFAULT_TEXT
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]

    chunks = _chunk_text(text, CHUNK_CHARS)
    per_chunk_timeout = max(60, min(300, 30 + len(text) // 20))

    with tempfile.TemporaryDirectory(prefix='mega-tts-') as td:
        wav_parts: list[str] = []
        for i, chunk in enumerate(chunks):
            part = os.path.join(td, f'part_{i:03d}.wav')
            _espeak_to_wav(espeak, chunk, part, timeout=per_chunk_timeout)
            wav_parts.append(part)

        merged = os.path.join(td, 'merged.wav')
        _concat_wavs(wav_parts, merged)

        ogg = os.path.join(td, 'out.ogg')
        ogg_timeout = max(60, min(300, 30 + len(text) // 15))
        _wav_to_ogg_opus(merged, ogg, timeout=ogg_timeout)

        with open(ogg, 'rb') as f:
            data = f.read()

    if len(data) < 50:
        raise RuntimeError('TTS produced empty audio')
    return data


async def synthesize_persian(text: str = DEFAULT_TEXT) -> tuple[bytes, str]:
    """Return (ogg_bytes, filename). Free offline espeak-ng — no key, no quota."""
    data = await asyncio.to_thread(synthesize_espeak, text)
    return data, 'voice.ogg'


def estimate_duration_sec(text: str) -> int:
    n = len(normalize_tts_text(text) or '')
    return max(1, int(n / 2.2) + 1)
