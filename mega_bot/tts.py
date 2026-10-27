"""TTS for MEGA BOT — HuggingFace MMS-TTS (not Piper / not edge).

Primary: Facebook MMS-TTS via HuggingFace Inference API
  - Persian: facebook/mms-tts-fas
  - English: facebook/mms-tts-eng
Free public endpoint; optional HF_TOKEN for higher limits.

Fallbacks: Google Translate TTS → gTTS → espeak-ng.
Long text is sentence-chunked and merged with ffmpeg.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import os
import re
import shutil
import struct
import subprocess
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import wave
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_TEXT = 'تست'
MAX_CHARS = int(os.getenv('TTS_MAX_CHARS', '100000'))
GOOGLE_CHUNK = int(os.getenv('TTS_GOOGLE_CHUNK', '180'))
GOOGLE_PARALLEL = int(os.getenv('TTS_GOOGLE_PARALLEL', '5'))
HF_CHUNK = int(os.getenv('TTS_HF_CHUNK', '400'))
HF_TOKEN = (os.getenv('HF_TOKEN') or os.getenv('HUGGINGFACE_TOKEN') or '').strip()
HF_FA_MODEL = os.getenv('TTS_HF_FA_MODEL', 'facebook/mms-tts-fas')
HF_EN_MODEL = os.getenv('TTS_HF_EN_MODEL', 'facebook/mms-tts-eng')
HF_API = os.getenv('TTS_HF_API', 'https://api-inference.huggingface.co/models')


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
    cmd = [
        ff, '-y', '-i', src,
        '-c:a', 'libopus', '-b:a', '48k', '-vbr', 'on',
        '-application', 'voip', dst,
    ]
    subprocess.run(cmd, check=True, capture_output=True, timeout=300)


def _concat_audio_files(paths: list[str], out_path: str) -> None:
    ff = _which('ffmpeg')
    if not ff:
        with open(out_path, 'wb') as out:
            for p in paths:
                with open(p, 'rb') as f:
                    out.write(f.read())
        return
    lst = out_path + '.txt'
    with open(lst, 'w', encoding='utf-8') as f:
        for p in paths:
            f.write(f"file '{p}'\n")
    cmd = [ff, '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy', out_path]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=300)
    except Exception:
        cmd2 = [
            ff, '-y', '-f', 'concat', '-safe', '0', '-i', lst,
            '-c:a', 'libmp3lame', '-q:a', '4', out_path,
        ]
        subprocess.run(cmd2, check=True, capture_output=True, timeout=300)


def _float_array_to_wav(floats: list[float], rate: int = 16000) -> bytes:
    """PCM float32 samples -1..1 → WAV bytes."""
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        frames = bytearray()
        for x in floats:
            s = max(-1.0, min(1.0, float(x)))
            frames += struct.pack('<h', int(s * 32767))
        wf.writeframes(frames)
    return buf.getvalue()


# ── HuggingFace MMS-TTS ─────────────────────────────────────────────────

def _hf_headers() -> dict:
    h = {
        'Content-Type': 'application/json',
        'User-Agent': 'MEGA-BOT/1.2',
        'Accept': 'application/json, audio/wav, audio/flac, audio/mpeg, */*',
    }
    if HF_TOKEN:
        h['Authorization'] = f'Bearer {HF_TOKEN}'
    return h


def _hf_parse_response(raw: bytes, content_type: str) -> bytes:
    ct = (content_type or '').lower()
    if 'audio' in ct or raw[:4] == b'RIFF' or raw[:4] == b'fLaC' or raw[:3] == b'ID3':
        return raw
    # JSON: {audio: base64|list, sampling_rate: N}
    try:
        payload = json.loads(raw.decode('utf-8', errors='replace'))
    except Exception as exc:
        if len(raw) > 200:
            return raw  # binary unknown
        raise RuntimeError(f'HF bad payload: {exc}') from exc
    if isinstance(payload, dict) and payload.get('error'):
        raise RuntimeError(str(payload.get('error')))
    rate = 16000
    audio = None
    if isinstance(payload, dict):
        rate = int(payload.get('sampling_rate') or payload.get('sample_rate') or 16000)
        audio = payload.get('audio') or payload.get('wav') or payload.get('data')
    if isinstance(audio, str):
        try:
            return base64.b64decode(audio)
        except Exception:
            pass
    if isinstance(audio, list) and audio and isinstance(audio[0], (int, float)):
        return _float_array_to_wav([float(x) for x in audio], rate)
    if isinstance(payload, list) and payload and isinstance(payload[0], (int, float)):
        return _float_array_to_wav([float(x) for x in payload], rate)
    raise RuntimeError('HF audio missing in response')


async def _hf_one_chunk(text: str, model: str) -> bytes:
    url = f'{HF_API.rstrip("/")}/{model}'
    body = json.dumps({'inputs': text}).encode('utf-8')

    def _fetch() -> bytes:
        req = urllib.request.Request(url, data=body, headers=_hf_headers(), method='POST')
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read()
                ct = resp.headers.get('Content-Type', '')
                return _hf_parse_response(raw, ct)
        except urllib.error.HTTPError as e:
            err_body = e.read().decode('utf-8', errors='replace')[:300]
            # model loading
            if e.code in (503, 529) or 'loading' in err_body.lower():
                raise RuntimeError(f'HF model loading: {err_body}') from e
            raise RuntimeError(f'HF HTTP {e.code}: {err_body}') from e

    # retry while model loads
    last = None
    for attempt in range(5):
        try:
            data = await asyncio.to_thread(_fetch)
            if data and len(data) > 100:
                return data
            last = RuntimeError('HF empty')
        except Exception as exc:
            last = exc
            msg = str(exc).lower()
            wait = 3.0 + attempt * 2.5
            if 'loading' in msg or '503' in msg:
                wait = 8.0 + attempt * 4.0
            await asyncio.sleep(wait)
    raise last or RuntimeError('HF failed')


async def synthesize_hf(text: str, lang: str) -> bytes:
    model = HF_FA_MODEL if lang == 'fa' else HF_EN_MODEL
    chunks = _chunk_text(text, HF_CHUNK)
    with tempfile.TemporaryDirectory(prefix='mega-hf-') as td:
        paths: list[str] = []
        for i, ch in enumerate(chunks):
            raw = await _hf_one_chunk(ch, model)
            # normalize each chunk to wav/mp3 file
            ext = 'wav'
            if raw[:4] == b'RIFF':
                ext = 'wav'
            elif raw[:3] == b'ID3' or raw[:2] == b'\xff\xfb':
                ext = 'mp3'
            elif raw[:4] == b'fLaC':
                ext = 'flac'
            path = os.path.join(td, f'{i:04d}.{ext}')
            with open(path, 'wb') as f:
                f.write(raw)
            # convert to wav for stable concat
            wav = os.path.join(td, f'{i:04d}.wav')
            ff = _which('ffmpeg')
            if ff and ext != 'wav':
                subprocess.run(
                    [ff, '-y', '-i', path, wav],
                    check=True, capture_output=True, timeout=120,
                )
                paths.append(wav)
            else:
                paths.append(path)
        merged = os.path.join(td, 'm.wav')
        if len(paths) == 1:
            shutil.copy(paths[0], merged)
        else:
            _concat_audio_files(paths, merged)
        ogg = os.path.join(td, 'o.ogg')
        try:
            _ffmpeg_to_ogg(merged, ogg)
            with open(ogg, 'rb') as f:
                return f.read()
        except Exception:
            with open(merged, 'rb') as f:
                return f.read()


# ── Google fallback ─────────────────────────────────────────────────────

async def _google_one_chunk(text: str, lang: str) -> bytes:
    q = urllib.parse.quote(text)
    url = (
        f'https://translate.google.com/translate_tts'
        f'?ie=UTF-8&client=tw-ob&q={q}&tl={lang}'
    )

    def _fetch() -> bytes:
        req = urllib.request.Request(
            url,
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
                    '(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
                ),
                'Referer': 'https://translate.google.com/',
            },
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = resp.read()
        if not data or len(data) < 50:
            raise RuntimeError('google empty')
        return data

    return await asyncio.to_thread(_fetch)


async def synthesize_google(text: str, lang: str) -> bytes:
    chunks = _chunk_text(text[:MAX_CHARS], GOOGLE_CHUNK)
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
    with tempfile.TemporaryDirectory(prefix='mega-gtts-') as td:
        paths = []
        for i, data in results:
            p = os.path.join(td, f'{i:04d}.mp3')
            open(p, 'wb').write(data)
            paths.append(p)
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


async def synthesize_gtts_lib(text: str, lang: str) -> bytes:
    from gtts import gTTS

    def _run() -> bytes:
        with tempfile.TemporaryDirectory(prefix='mega-gtts2-') as td:
            chunks = _chunk_text(text, 500)
            paths = []
            for i, ch in enumerate(chunks):
                p = os.path.join(td, f'{i:04d}.mp3')
                gTTS(text=ch, lang=lang, slow=False).save(p)
                paths.append(p)
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

    return await asyncio.to_thread(_run)


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

    # 1) HuggingFace MMS-TTS (new platform — not Piper, not edge)
    try:
        data = await synthesize_hf(text, lang)
        if data and len(data) > 100:
            ext = 'ogg' if data[:4] == b'OggS' else ('wav' if data[:4] == b'RIFF' else 'mp3')
            log.info('TTS engine=hf-mms-%s bytes=%s chars=%s', lang, len(data), len(text))
            return data, f'voice.{ext}'
    except Exception as exc:
        errors.append(f'hf:{type(exc).__name__}')
        log.warning('HF MMS-TTS failed (%s): %s', lang, exp if False else exc)

    # 2) Google
    try:
        data = await synthesize_google(text, lang)
        if data and len(data) > 100:
            ext = 'ogg' if data[:4] == b'OggS' else 'mp3'
            log.info('TTS engine=google-%s bytes=%s', lang, len(data))
            return data, f'voice.{ext}'
    except Exception as exc:
        errors.append(f'google:{type(exc).__name__}')
        log.warning('google failed: %s', exc)

    # 3) gTTS
    try:
        data = await synthesize_gtts_lib(text, lang)
        if data and len(data) > 100:
            ext = 'ogg' if data[:4] == b'OggS' else 'mp3'
            log.info('TTS engine=gtts-%s bytes=%s', lang, len(data))
            return data, f'voice.{ext}'
    except Exception as exc:
        errors.append(f'gtts:{type(exc).__name__}')
        log.warning('gtts failed: %s', exc)

    # 4) espeak
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
