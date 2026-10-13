"""Natural free TTS via Microsoft Edge online voices (edge-tts).

No API key. Neural voices (much less robotic than espeak).
- Persian: fa-IR-DilaraNeural (default) / fa-IR-FaridNeural
- English: en-US-JennyNeural (default)

Falls back to espeak-ng only if edge-tts is completely unavailable.
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
CHUNK_CHARS = int(os.getenv('TTS_CHUNK_CHARS', '900'))

# Famous natural voices (Microsoft neural — same family many free TTS sites use)
VOICE_FA = os.getenv('TTS_VOICE_FA', 'fa-IR-DilaraNeural')
VOICE_FA_MALE = os.getenv('TTS_VOICE_FA_MALE', 'fa-IR-FaridNeural')
VOICE_EN = os.getenv('TTS_VOICE_EN', 'en-US-JennyNeural')
VOICE_EN_MALE = os.getenv('TTS_VOICE_EN_MALE', 'en-US-GuyNeural')


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def normalize_tts_text(text: str) -> str:
    t = (text or '').strip()
    t = t.replace('\r\n', '\n').replace('\r', '\n')
    t = re.sub(r'[ \t]+', ' ', t)
    t = re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()


def detect_lang(text: str) -> str:
    """Simple fa vs en detection for voice selection."""
    latin = len(re.findall(r'[A-Za-z]', text or ''))
    arabic = len(re.findall(r'[\u0600-\u06FF]', text or ''))
    if latin > arabic * 1.2 and latin >= 3:
        return 'en'
    return 'fa'


def pick_voice(text: str) -> str:
    lang = detect_lang(text)
    gender = (os.getenv('TTS_GENDER') or 'female').strip().lower()
    if lang == 'en':
        return VOICE_EN_MALE if gender in ('male', 'm', 'مرد') else VOICE_EN
    return VOICE_FA_MALE if gender in ('male', 'm', 'مرد') else VOICE_FA


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


def _ffmpeg_to_ogg(src: str, ogg: str, timeout: int = 180) -> None:
    ffmpeg = _which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg not found')
    r = subprocess.run(
        [
            ffmpeg, '-y', '-i', src,
            '-c:a', 'libopus', '-b:a', '32k', '-vbr', 'on',
            '-application', 'voip', ogg,
        ],
        capture_output=True,
        timeout=timeout,
    )
    if r.returncode != 0 or not os.path.isfile(ogg) or os.path.getsize(ogg) < 50:
        err = (r.stderr or b'').decode('utf-8', errors='replace')[-400:]
        raise RuntimeError(f'ffmpeg failed: {err}')


def _concat_media(paths: list[str], out_path: str) -> None:
    if len(paths) == 1:
        shutil.copyfile(paths[0], out_path)
        return
    ffmpeg = _which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg not found')
    list_path = out_path + '.txt'
    with open(list_path, 'w', encoding='utf-8') as f:
        for w in paths:
            safe = w.replace("'", "'\\''")
            f.write(f"file '{safe}'\n")
    r = subprocess.run(
        [ffmpeg, '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c', 'copy', out_path],
        capture_output=True,
        timeout=180,
    )
    if r.returncode != 0 or not os.path.isfile(out_path):
        r2 = subprocess.run(
            [ffmpeg, '-y', '-f', 'concat', '-safe', '0', '-i', list_path, '-c:a', 'libmp3lame', '-q:a', '4', out_path],
            capture_output=True,
            timeout=180,
        )
        if r2.returncode != 0 or not os.path.isfile(out_path):
            err = (r2.stderr or r.stderr or b'').decode('utf-8', errors='replace')[-400:]
            raise RuntimeError(f'concat failed: {err}')


async def _edge_chunk_to_mp3(text: str, voice: str, mp3_path: str) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice)
    await communicate.save(mp3_path)
    if not os.path.isfile(mp3_path) or os.path.getsize(mp3_path) < 50:
        raise RuntimeError('edge-tts empty output')


async def synthesize_edge(text: str, voice: str | None = None) -> bytes:
    """Natural neural TTS (Microsoft Edge voices) — free, no API key."""
    try:
        import edge_tts  # noqa: F401
    except ImportError as exc:
        raise RuntimeError('edge-tts not installed') from exc

    text = normalize_tts_text(text or DEFAULT_TEXT) or DEFAULT_TEXT
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    voice = voice or pick_voice(text)
    chunks = _chunk_text(text, CHUNK_CHARS)

    with tempfile.TemporaryDirectory(prefix='mega-edge-') as td:
        parts: list[str] = []
        for i, chunk in enumerate(chunks):
            part = os.path.join(td, f'p{i:03d}.mp3')
            try:
                await asyncio.wait_for(_edge_chunk_to_mp3(chunk, voice, part), timeout=45)
            except Exception:
                # brief retry once (transient network)
                await asyncio.sleep(0.4)
                await asyncio.wait_for(_edge_chunk_to_mp3(chunk, voice, part), timeout=45)
            parts.append(part)
            # gentle pacing to reduce throttling on long jobs
            if i + 1 < len(chunks):
                await asyncio.sleep(0.15)

        merged = os.path.join(td, 'merged.mp3')
        _concat_media(parts, merged)

        ogg = os.path.join(td, 'out.ogg')
        try:
            _ffmpeg_to_ogg(merged, ogg)
            with open(ogg, 'rb') as f:
                data = f.read()
            if len(data) >= 50:
                return data
        except Exception as exc:
            log.info('ogg convert failed, using mp3: %s', type(exc).__name__)
        with open(merged, 'rb') as f:
            return f.read()


def synthesize_espeak(text: str = DEFAULT_TEXT) -> bytes:
    """Emergency offline fallback (robotic)."""
    espeak = _which('espeak-ng') or _which('espeak')
    if not espeak:
        raise RuntimeError('espeak-ng not installed')
    text = normalize_tts_text(text or DEFAULT_TEXT) or DEFAULT_TEXT
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    with tempfile.TemporaryDirectory(prefix='mega-espeak-') as td:
        wav = os.path.join(td, 'out.wav')
        ogg = os.path.join(td, 'out.ogg')
        r = subprocess.run(
            [espeak, '-v', 'fa' if detect_lang(text) == 'fa' else 'en', '-s', '130', '-w', wav, '--stdin'],
            input=text.encode('utf-8'),
            capture_output=True,
            timeout=max(60, min(300, 30 + len(text) // 20)),
        )
        if r.returncode != 0 or not os.path.isfile(wav):
            raise RuntimeError('espeak failed')
        _ffmpeg_to_ogg(wav, ogg)
        with open(ogg, 'rb') as f:
            return f.read()


async def synthesize_persian(text: str = DEFAULT_TEXT) -> tuple[bytes, str]:
    """Primary: edge neural voice. Fallback: espeak.

    Env:
      TTS_ENGINE=edge|espeak|auto (default edge)
      TTS_VOICE_FA / TTS_VOICE_EN / TTS_GENDER=female|male
    """
    engine = (os.getenv('TTS_ENGINE') or 'edge').strip().lower()
    text = normalize_tts_text(text or DEFAULT_TEXT) or DEFAULT_TEXT
    voice = pick_voice(text)
    errors: list[str] = []

    if engine in ('edge', 'auto', ''):
        try:
            data = await synthesize_edge(text, voice=voice)
            ext = 'ogg' if data[:4] == b'OggS' else 'mp3'
            return data, f'voice.{ext}'
        except Exception as exc:
            errors.append(f'edge:{type(exc).__name__}:{exc}')
            log.warning('edge-tts failed: %s', type(exc).__name__)
            if engine == 'edge':
                # still try espeak rather than hard-fail if available
                pass

    if engine in ('espeak', 'auto', 'edge', ''):
        try:
            data = await asyncio.to_thread(synthesize_espeak, text)
            return data, 'voice.ogg'
        except Exception as exc:
            errors.append(f'espeak:{type(exc).__name__}')

    raise RuntimeError('TTS failed: ' + '; '.join(errors) if errors else 'no engine')


def estimate_duration_sec(text: str) -> int:
    n = len(normalize_tts_text(text) or '')
    # neural speech ~14–16 chars/sec mixed heuristic
    return max(1, int(n / 14) + 1)
