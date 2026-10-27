"""TTS for MEGA BOT — primary: gTTS (famous free Google Text-to-Speech library).

Not Piper / not edge-tts / not HuggingFace MMS.

Order:
  1) gTTS  — well-known free Python library (FA + EN), chunked for long text
  2) Google translate_tts HTTP — same family, alternate client path
  3) espeak-ng — offline last resort

Soft ceiling: TTS_MAX_CHARS (default 100000). No product word-limit.
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
MAX_CHARS = int(os.getenv('TTS_MAX_CHARS', '100000'))
GTTS_CHUNK = int(os.getenv('TTS_GTTS_CHUNK', '500'))
GOOGLE_CHUNK = int(os.getenv('TTS_GOOGLE_CHUNK', '180'))
GOOGLE_PARALLEL = int(os.getenv('TTS_GOOGLE_PARALLEL', '5'))


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
        if len(part) > size:
            if buf:
                chunks.append(buf.strip())
                buf = ''
            for i in range(0, len(part), size):
                chunks.append(part[i:i + size])
            continue
        if len(buf) + len(part) + 1 <= size:
            buf = f'{buf} {part}'.strip()
        else:
            if buf:
                chunks.append(buf.strip())
            buf = part
    if buf.strip():
        chunks.append(buf.strip())
    return chunks or [text[:size]]


def _ffmpeg_to_ogg(src: str, dst: str) -> None:
    ff = _which('ffmpeg')
    if not ff:
        raise RuntimeError('ffmpeg not found')
    subprocess.run(
        [ff, '-y', '-i', src, '-c:a', 'libopus', '-b:a', '48k', '-vbr', 'on',
         '-application', 'voip', dst],
        check=True, capture_output=True, timeout=300,
    )


def _concat_audio_files(paths: list[str], out_path: str) -> None:
    ff = _which('ffmpeg')
    if not ff:
        with open(out_path, 'wb') as out:
            for p in paths:
                out.write(open(p, 'rb').read())
        return
    lst = out_path + '.txt'
    with open(lst, 'w', encoding='utf-8') as f:
        for p in paths:
            f.write(f"file '{p}'\n")
    try:
        subprocess.run(
            [ff, '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy', out_path],
            check=True, capture_output=True, timeout=300,
        )
    except Exception:
        subprocess.run(
            [ff, '-y', '-f', 'concat', '-safe', '0', '-i', lst,
             '-c:a', 'libmp3lame', '-q:a', '4', out_path],
            check=True, capture_output=True, timeout=300,
        )


def _finalize_mp3_dir(paths: list[str], td: str) -> bytes:
    merged = os.path.join(td, 'm.mp3')
    if len(paths) == 1:
        shutil.copy(paths[0], merged)
    else:
        _concat_audio_files(paths, merged)
    ogg = os.path.join(td, 'o.ogg')
    try:
        _ffmpeg_to_ogg(merged, ogg)
        return open(ogg, 'rb').read()
    except Exception:
        return open(merged, 'rb').read()


# ── 1) gTTS (famous free library) ───────────────────────────────────────

async def synthesize_gtts(text: str, lang: str) -> bytes:
    from gtts import gTTS
    import socket

    def _run() -> bytes:
        socket.setdefaulttimeout(25)
        with tempfile.TemporaryDirectory(prefix='mega-gtts-') as td:
            chunks = _chunk_text(text, GTTS_CHUNK)
            paths: list[str] = []
            for i, ch in enumerate(chunks):
                path = os.path.join(td, f'{i:04d}.mp3')
                # tld=com — standard endpoint
                tts = gTTS(text=ch, lang=lang, slow=False, lang_check=False)
                tts.save(path)
                if os.path.getsize(path) < 50:
                    raise RuntimeError('gTTS empty chunk')
                paths.append(path)
            return _finalize_mp3_dir(paths, td)

    return await asyncio.to_thread(_run)


# ── 2) Google translate_tts HTTP ────────────────────────────────────────

async def _google_one_chunk(text: str, lang: str) -> bytes:
    q = urllib.parse.quote(text)
    # alternate host sometimes works when translate.google.com is flaky
    urls = [
        f'https://translate.googleapis.com/translate_tts?ie=UTF-8&client=gtx&q={q}&tl={lang}',
        f'https://translate.google.com/translate_tts?ie=UTF-8&client=tw-ob&q={q}&tl={lang}',
    ]

    def _fetch() -> bytes:
        last = None
        for url in urls:
            try:
                req = urllib.request.Request(
                    url,
                    headers={
                        'User-Agent': (
                            'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                            '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
                        ),
                        'Referer': 'https://translate.google.com/',
                        'Accept': '*/*',
                    },
                )
                with urllib.request.urlopen(req, timeout=45) as resp:
                    data = resp.read()
                if data and len(data) >= 50:
                    return data
                last = RuntimeError('empty')
            except Exception as exc:
                last = exc
        raise last or RuntimeError('google failed')

    return await asyncio.to_thread(_fetch)


async def synthesize_google(text: str, lang: str) -> bytes:
    chunks = _chunk_text(text, GOOGLE_CHUNK)
    sem = asyncio.Semaphore(max(1, GOOGLE_PARALLEL))

    async def fetch(i: int, ch: str) -> tuple[int, bytes]:
        async with sem:
            last = None
            for attempt in range(3):
                try:
                    return i, await _google_one_chunk(ch, lang)
                except Exception as exc:
                    last = exc
                    await asyncio.sleep(0.5 * (attempt + 1))
            raise last or RuntimeError('google failed')

    results = await asyncio.gather(*[fetch(i, ch) for i, ch in enumerate(chunks)])
    results.sort(key=lambda x: x[0])
    with tempfile.TemporaryDirectory(prefix='mega-ghttp-') as td:
        paths = []
        for i, data in results:
            p = os.path.join(td, f'{i:04d}.mp3')
            open(p, 'wb').write(data)
            paths.append(p)
        return _finalize_mp3_dir(paths, td)


# ── 3) espeak ───────────────────────────────────────────────────────────

async def synthesize_espeak(text: str, lang: str) -> bytes:
    es = _which('espeak-ng') or _which('espeak')
    if not es:
        raise RuntimeError('espeak not found')
    voice = 'fa' if lang == 'fa' else 'en'
    with tempfile.TemporaryDirectory(prefix='mega-esp-') as td:
        chunks = _chunk_text(text, 800)
        wavs = []
        for i, ch in enumerate(chunks):
            p = os.path.join(td, f'{i}.wav')
            await asyncio.to_thread(
                subprocess.run,
                [es, '-v', voice, '-s', '145', '-w', p, ch],
                check=True, capture_output=True, timeout=120,
            )
            wavs.append(p)
        merged = os.path.join(td, 'm.wav')
        if len(wavs) == 1:
            shutil.copy(wavs[0], merged)
        else:
            _concat_audio_files(wavs, merged)
        ogg = os.path.join(td, 'o.ogg')
        try:
            _ffmpeg_to_ogg(merged, ogg)
            return open(ogg, 'rb').read()
        except Exception:
            return open(merged, 'rb').read()


async def synthesize_persian(text: str = DEFAULT_TEXT) -> tuple[bytes, str]:
    text = normalize_tts_text(text or DEFAULT_TEXT) or DEFAULT_TEXT
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    lang = detect_lang(text)
    errors: list[str] = []

    # 1) gTTS — famous free TTS library
    try:
        data = await synthesize_gtts(text, lang)
        if data and len(data) > 100:
            ext = 'ogg' if data[:4] == b'OggS' else 'mp3'
            log.info('TTS engine=gTTS-%s bytes=%s chars=%s', lang, len(data), len(text))
            return data, f'voice.{ext}'
    except Exception as exc:
        errors.append(f'gTTS:{type(exc).__name__}')
        log.warning('gTTS failed (%s): %s', lang, exc)

    # 2) Google HTTP
    try:
        data = await synthesize_google(text, lang)
        if data and len(data) > 100:
            ext = 'ogg' if data[:4] == b'OggS' else 'mp3'
            log.info('TTS engine=google-http-%s bytes=%s', lang, len(data))
            return data, f'voice.{ext}'
    except Exception as exc:
        errors.append(f'google:{type(exc).__name__}')
        log.warning('google-http failed: %s', exp if False else exc)

    # 3) espeak
    try:
        data = await synthesize_espeak(text, lang)
        if data and len(data) > 50:
            ext = 'ogg' if data[:4] == b'OggS' else 'wav'
            log.info('TTS engine=espeak-%s bytes=%s', lang, len(data))
            return data, f'voice.{ext}'
    except Exception as exc:
        errors.append(f'espeak:{type(exc).__name__}')
        log.warning('espeak failed: %s', exc)

    raise RuntimeError(f'ساخت ویس ناموفق: {", ".join(errors) or "unknown"}')
