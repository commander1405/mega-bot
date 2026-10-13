"""Free TTS with a distinctly non-espeak voice.

Providers (in order):
1) Google Translate TTS  — the well-known translate.google.com voice (no API key)
2) edge-tts neural       — Microsoft Dilara / Jenny (no API key)
3) gTTS package          — same Google family, alternate client
espeak is NOT used unless TTS_ALLOW_ESPEAK=1 (users hated the robotic voice).
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
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_TEXT = 'تست'
MAX_CHARS = int(os.getenv('TTS_MAX_CHARS', '6000'))
# Google TTS hard limit ~200 bytes of text per request
GOOGLE_CHUNK = 180
EDGE_CHUNK = 900

VOICE_FA_EDGE = os.getenv('TTS_VOICE_FA', 'fa-IR-DilaraNeural')
VOICE_EN_EDGE = os.getenv('TTS_VOICE_EN', 'en-US-JennyNeural')


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


def _concat_mp3(paths: list[str], out_path: str) -> None:
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
            raise RuntimeError('mp3 concat failed')


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
        # sync fallback
        import urllib.request
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            **(headers or {}),
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = r.read()
        if len(data) < 100:
            raise RuntimeError('empty audio')
        return data


async def _google_chunk(text: str, lang: str) -> bytes:
    """Fetch one Google Translate TTS chunk (no API key)."""
    # Prefer the endpoint that commonly serves the classic Google voice
    hosts = [
        'https://translate.google.com/translate_tts',
        'https://translate.googleapis.com/translate_tts',
    ]
    clients = ['tw-ob', 'gtx']
    last_err: Exception | None = None
    for host in hosts:
        for client in clients:
            params = urllib.parse.urlencode({
                'ie': 'UTF-8',
                'client': client,
                'tl': lang,
                'q': text,
                'ttsspeed': '1',
            })
            url = f'{host}?{params}'
            try:
                data = await _http_get_bytes(
                    url,
                    headers={'Referer': 'https://translate.google.com/'},
                    timeout=20,
                )
                # MP3 typically starts with 0xFF 0xF3 or ID3
                if data[:3] == b'ID3' or data[:1] == b'\xff':
                    return data
                last_err = RuntimeError('non-mp3 payload')
            except Exception as exc:
                last_err = exc
                continue
    raise RuntimeError(f'google chunk failed: {type(last_err).__name__ if last_err else "unknown"}')


async def synthesize_google(text: str, lang: str | None = None) -> bytes:
    lang = lang or detect_lang(text)
    text = normalize_tts_text(text) or DEFAULT_TEXT
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    chunks = _chunk_text(text, GOOGLE_CHUNK)
    with tempfile.TemporaryDirectory(prefix='mega-gtts-') as td:
        parts: list[str] = []
        for i, ch in enumerate(chunks):
            data = await _google_chunk(ch, lang)
            path = os.path.join(td, f'{i:03d}.mp3')
            with open(path, 'wb') as f:
                f.write(data)
            parts.append(path)
            if i + 1 < len(chunks):
                await asyncio.sleep(0.05)
        merged = os.path.join(td, 'merged.mp3')
        _concat_mp3(parts, merged)
        ogg = os.path.join(td, 'out.ogg')
        try:
            _ffmpeg_to_ogg(merged, ogg)
            with open(ogg, 'rb') as f:
                return f.read()
        except Exception:
            with open(merged, 'rb') as f:
                return f.read()


async def synthesize_gtts_lib(text: str, lang: str | None = None) -> bytes:
    """Alternate Google client via gTTS library."""
    try:
        from gtts import gTTS
    except ImportError as exc:
        raise RuntimeError('gTTS not installed') from exc
    lang = lang or detect_lang(text)
    # gTTS removed fa; map Persian → try ar is wrong; only use for en
    if lang == 'fa':
        raise RuntimeError('gTTS has no Persian')
    text = normalize_tts_text(text) or DEFAULT_TEXT
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]

    def _run() -> bytes:
        with tempfile.TemporaryDirectory(prefix='mega-gtts2-') as td:
            chunks = _chunk_text(text, GOOGLE_CHUNK)
            parts = []
            for i, ch in enumerate(chunks):
                path = os.path.join(td, f'{i:03d}.mp3')
                gTTS(text=ch, lang='en', slow=False).save(path)
                parts.append(path)
            merged = os.path.join(td, 'm.mp3')
            _concat_mp3(parts, merged)
            ogg = os.path.join(td, 'o.ogg')
            try:
                _ffmpeg_to_ogg(merged, ogg)
                with open(ogg, 'rb') as f:
                    return f.read()
            except Exception:
                with open(merged, 'rb') as f:
                    return f.read()

    return await asyncio.to_thread(_run)


async def synthesize_edge(text: str) -> bytes:
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError('edge-tts not installed') from exc
    text = normalize_tts_text(text) or DEFAULT_TEXT
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    voice = VOICE_EN_EDGE if detect_lang(text) == 'en' else VOICE_FA_EDGE
    chunks = _chunk_text(text, EDGE_CHUNK)
    with tempfile.TemporaryDirectory(prefix='mega-edge-') as td:
        parts: list[str] = []
        for i, ch in enumerate(chunks):
            path = os.path.join(td, f'{i:03d}.mp3')
            await asyncio.wait_for(edge_tts.Communicate(ch, voice).save(path), timeout=40)
            if not os.path.isfile(path) or os.path.getsize(path) < 50:
                raise RuntimeError('edge empty')
            parts.append(path)
            if i + 1 < len(chunks):
                await asyncio.sleep(0.12)
        merged = os.path.join(td, 'm.mp3')
        _concat_mp3(parts, merged)
        ogg = os.path.join(td, 'o.ogg')
        try:
            _ffmpeg_to_ogg(merged, ogg)
            with open(ogg, 'rb') as f:
                return f.read()
        except Exception:
            with open(merged, 'rb') as f:
                return f.read()


async def synthesize_persian(text: str = DEFAULT_TEXT) -> tuple[bytes, str]:
    """Return (audio_bytes, filename). Prefer Google voice, then edge neural.

    Never falls back to espeak unless TTS_ALLOW_ESPEAK=1.
    """
    text = normalize_tts_text(text or DEFAULT_TEXT) or DEFAULT_TEXT
    lang = detect_lang(text)
    prefer = (os.getenv('TTS_ENGINE') or 'auto').strip().lower()
    errors: list[str] = []

    # English: Google voice is the classic free natural one and works widely.
    # Persian: try Edge neural (Dilara) first, then Google FA.
    order: list[str]
    if prefer == 'google':
        order = ['google', 'edge', 'gtts']
    elif prefer == 'edge':
        order = ['edge', 'google', 'gtts']
    elif lang == 'en':
        order = ['google', 'gtts', 'edge']
    else:
        order = ['edge', 'google', 'gtts']

    for name in order:
        try:
            if name == 'google':
                data = await synthesize_google(text, lang=lang)
            elif name == 'edge':
                data = await synthesize_edge(text)
            elif name == 'gtts':
                data = await synthesize_gtts_lib(text, lang=lang)
            else:
                continue
            ext = 'ogg' if data[:4] == b'OggS' else 'mp3'
            log.info('TTS engine=%s lang=%s bytes=%s', name, lang, len(data))
            return data, f'voice.{ext}'
        except Exception as exc:
            errors.append(f'{name}:{type(exc).__name__}')
            log.warning('TTS provider %s failed: %s', name, type(exc).__name__)

    if os.getenv('TTS_ALLOW_ESPEAK', '').strip() in ('1', 'true', 'yes'):
        # last resort only when explicitly enabled
        espeak = _which('espeak-ng') or _which('espeak')
        if espeak:
            def _esp() -> bytes:
                with tempfile.TemporaryDirectory(prefix='mega-esp-') as td:
                    wav = os.path.join(td, 'a.wav')
                    ogg = os.path.join(td, 'a.ogg')
                    subprocess.run(
                        [espeak, '-v', 'fa' if lang == 'fa' else 'en', '-s', '130', '-w', wav, '--stdin'],
                        input=text.encode('utf-8'),
                        capture_output=True,
                        timeout=120,
                        check=False,
                    )
                    _ffmpeg_to_ogg(wav, ogg)
                    with open(ogg, 'rb') as f:
                        return f.read()
            data = await asyncio.to_thread(_esp)
            return data, 'voice.ogg'

    raise RuntimeError('TTS failed: ' + ', '.join(errors))
