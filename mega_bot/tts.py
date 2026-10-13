"""TTS for MEGA BOT

- English: Google Translate TTS (classic free Google voice, no API key)
- Persian: offline Piper neural voice «Amir» (male, free, unlimited, no cloud)

No espeak unless TTS_ALLOW_ESPEAK=1.
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
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_TEXT = 'تست'
MAX_CHARS = int(os.getenv('TTS_MAX_CHARS', '6000'))
GOOGLE_CHUNK = 180

# Piper Persian male voice (rhasspy community)
PIPER_MODEL_NAME = os.getenv('TTS_PIPER_MODEL', 'fa_IR-amir-medium')
PIPER_BASE = os.getenv(
    'TTS_PIPER_BASE',
    'https://huggingface.co/rhasspy/piper-voices/resolve/main/fa/fa_IR/amir/medium',
)
PIPER_DIR = Path(os.getenv('TTS_PIPER_DIR', '/tmp/mega_piper'))


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
        [ffmpeg, '-y', '-i', src, '-c:a', 'libopus', '-b:a', '32k', '-vbr', 'on', '-application', 'voip', ogg],
        capture_output=True,
        timeout=timeout,
    )
    if r.returncode != 0 or not os.path.isfile(ogg) or os.path.getsize(ogg) < 50:
        err = (r.stderr or b'').decode('utf-8', errors='replace')[-300:]
        raise RuntimeError(f'ffmpeg failed: {err}')


def _concat_files(paths: list[str], out_path: str, reencode_mp3: bool = False) -> None:
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
        codec = ['-c:a', 'libmp3lame', '-q:a', '4'] if reencode_mp3 else ['-c:a', 'pcm_s16le']
        r2 = subprocess.run(
            [ffmpeg, '-y', '-f', 'concat', '-safe', '0', '-i', list_path, *codec, out_path],
            capture_output=True,
            timeout=180,
        )
        if r2.returncode != 0 or not os.path.isfile(out_path):
            raise RuntimeError('audio concat failed')


# ── English: Google Translate TTS ───────────────────────────────────────

async def _http_get_bytes(url: str, headers: dict | None = None, timeout: float = 25.0) -> bytes:
    try:
        import aiohttp
        h = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
            ),
            'Accept': '*/*',
            **(headers or {}),
        }
        timeout_cfg = aiohttp.ClientTimeout(total=timeout, connect=10)
        async with aiohttp.ClientSession(timeout=timeout_cfg) as session:
            async with session.get(url, headers=h) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f'HTTP {resp.status}')
                data = await resp.read()
                if len(data) < 100:
                    raise RuntimeError('empty audio')
                return data
    except ImportError:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0', **(headers or {})})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if len(data) < 100:
            raise RuntimeError('empty audio')
        return data


async def _google_chunk(text: str, lang: str = 'en') -> bytes:
    hosts = [
        'https://translate.google.com/translate_tts',
        'https://translate.googleapis.com/translate_tts',
    ]
    last_err: Exception | None = None
    for host in hosts:
        for client in ('tw-ob', 'gtx'):
            params = urllib.parse.urlencode({
                'ie': 'UTF-8',
                'client': client,
                'tl': lang,
                'q': text,
                'ttsspeed': '1',
            })
            try:
                data = await _http_get_bytes(
                    f'{host}?{params}',
                    headers={'Referer': 'https://translate.google.com/'},
                    timeout=20,
                )
                if data[:3] == b'ID3' or data[:1] == b'\xff':
                    return data
                last_err = RuntimeError('non-mp3')
            except Exception as exc:
                last_err = exc
    raise RuntimeError(f'google failed: {type(last_err).__name__ if last_err else "?"}')


async def synthesize_google_en(text: str) -> bytes:
    text = normalize_tts_text(text) or 'hello'
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    chunks = _chunk_text(text, GOOGLE_CHUNK)
    with tempfile.TemporaryDirectory(prefix='mega-gen-') as td:
        parts: list[str] = []
        for i, ch in enumerate(chunks):
            data = await _google_chunk(ch, 'en')
            path = os.path.join(td, f'{i:03d}.mp3')
            with open(path, 'wb') as f:
                f.write(data)
            parts.append(path)
            if i + 1 < len(chunks):
                await asyncio.sleep(0.05)
        merged = os.path.join(td, 'm.mp3')
        _concat_files(parts, merged, reencode_mp3=True)
        ogg = os.path.join(td, 'o.ogg')
        try:
            _ffmpeg_to_ogg(merged, ogg)
            with open(ogg, 'rb') as f:
                return f.read()
        except Exception:
            with open(merged, 'rb') as f:
                return f.read()


# ── Persian: Piper offline male (Amir) ──────────────────────────────────

_piper_voice = None
_piper_lock = asyncio.Lock()


def _ensure_piper_files() -> Path:
    """Download onnx + json once into PIPER_DIR."""
    PIPER_DIR.mkdir(parents=True, exist_ok=True)
    onnx = PIPER_DIR / f'{PIPER_MODEL_NAME}.onnx'
    cfg = PIPER_DIR / f'{PIPER_MODEL_NAME}.onnx.json'
    for path, name in ((onnx, f'{PIPER_MODEL_NAME}.onnx'), (cfg, f'{PIPER_MODEL_NAME}.onnx.json')):
        if path.is_file() and path.stat().st_size > 1000:
            continue
        url = f'{PIPER_BASE.rstrip("/")}/{name}'
        log.info('Downloading Piper model %s …', name)
        tmp = path.with_suffix(path.suffix + '.part')
        urllib.request.urlretrieve(url, tmp)
        tmp.replace(path)
        log.info('Piper model ready: %s (%s bytes)', name, path.stat().st_size)
    return onnx


def _load_piper():
    global _piper_voice
    if _piper_voice is not None:
        return _piper_voice
    try:
        from piper import PiperVoice
    except ImportError as exc:
        raise RuntimeError('piper-tts not installed') from exc
    model = _ensure_piper_files()
    _piper_voice = PiperVoice.load(str(model))
    return _piper_voice


def _piper_synthesize_sync(text: str) -> bytes:
    import wave
    voice = _load_piper()
    text = normalize_tts_text(text) or DEFAULT_TEXT
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    # Piper handles long text; still chunk very long runs for memory
    chunks = _chunk_text(text, 500)
    with tempfile.TemporaryDirectory(prefix='mega-piper-') as td:
        wav_parts: list[str] = []
        for i, ch in enumerate(chunks):
            path = os.path.join(td, f'{i:03d}.wav')
            with wave.open(path, 'wb') as wf:
                voice.synthesize_wav(ch, wf)
            wav_parts.append(path)
        merged = os.path.join(td, 'm.wav')
        _concat_files(wav_parts, merged)
        ogg = os.path.join(td, 'o.ogg')
        _ffmpeg_to_ogg(merged, ogg)
        with open(ogg, 'rb') as f:
            data = f.read()
    if len(data) < 50:
        raise RuntimeError('piper empty audio')
    return data


async def synthesize_piper_fa(text: str) -> bytes:
    async with _piper_lock:
        return await asyncio.to_thread(_piper_synthesize_sync, text)


# ── Public entry ────────────────────────────────────────────────────────

async def synthesize_persian(text: str = DEFAULT_TEXT) -> tuple[bytes, str]:
    """Route by language: EN→Google, FA→Piper Amir (male offline)."""
    text = normalize_tts_text(text or DEFAULT_TEXT) or DEFAULT_TEXT
    lang = detect_lang(text)

    if lang == 'en':
        data = await synthesize_google_en(text)
        ext = 'ogg' if data[:4] == b'OggS' else 'mp3'
        log.info('TTS engine=google-en bytes=%s', len(data))
        return data, f'voice.{ext}'

    # Persian (and mixed defaulting to FA)
    try:
        data = await synthesize_piper_fa(text)
        log.info('TTS engine=piper-amir bytes=%s', len(data))
        return data, 'voice.ogg'
    except Exception as exc:
        log.exception('Piper FA failed')
        raise RuntimeError(f'فارسی: piper:{type(exc).__name__}: {exc}') from exc
