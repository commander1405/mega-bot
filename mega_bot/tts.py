"""TTS for MEGA BOT — no edge-tts.

Persian primary: Piper neural offline (fa_IR-amir-medium) — free, unlimited, no API.
English primary: Google Translate TTS (chunked).
Fallbacks: gTTS, espeak-ng.
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
import wave
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_TEXT = 'تست'
MAX_CHARS = int(os.getenv('TTS_MAX_CHARS', '100000'))
GOOGLE_CHUNK = int(os.getenv('TTS_GOOGLE_CHUNK', '180'))
GOOGLE_PARALLEL = int(os.getenv('TTS_GOOGLE_PARALLEL', '5'))
PIPER_CHUNK = int(os.getenv('TTS_PIPER_CHUNK', '700'))

PIPER_MODEL_NAME = os.getenv('TTS_PIPER_MODEL', 'fa_IR-amir-medium')
PIPER_BASE = os.getenv(
    'TTS_PIPER_BASE',
    'https://huggingface.co/rhasspy/piper-voices/resolve/main/fa/fa_IR/amir/medium',
)
PIPER_DIR = Path(os.getenv('TTS_PIPER_DIR', '/tmp/mega_piper'))
PIPER_LENGTH_SCALE = float(os.getenv('TTS_PIPER_LENGTH', '1.35'))
PIPER_NOISE_SCALE = float(os.getenv('TTS_PIPER_NOISE', '0.4'))
PIPER_NOISE_W = float(os.getenv('TTS_PIPER_NOISE_W', '0.6'))

_piper_voice = None
_piper_lock = asyncio.Lock()


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


# ── Piper (Persian offline neural) ──────────────────────────────────────

def _ensure_piper_files() -> Path:
    PIPER_DIR.mkdir(parents=True, exist_ok=True)
    name = PIPER_MODEL_NAME
    onnx = PIPER_DIR / f'{name}.onnx'
    conf = PIPER_DIR / f'{name}.onnx.json'
    if onnx.is_file() and onnx.stat().st_size > 1000 and conf.is_file():
        return onnx
    for suffix, dest in (('.onnx', onnx), ('.onnx.json', conf)):
        url = f'{PIPER_BASE}/{name}{suffix}'
        log.info('Downloading Piper model %s', url)
        req = urllib.request.Request(url, headers={'User-Agent': 'MEGA-BOT/1.0'})
        with urllib.request.urlopen(req, timeout=180) as resp:
            dest.write_bytes(resp.read())
    if not onnx.is_file() or onnx.stat().st_size < 1000:
        raise RuntimeError('piper model download failed')
    return onnx


def _load_piper():
    global _piper_voice
    if _piper_voice is not None:
        return _piper_voice
    from piper import PiperVoice  # type: ignore
    model = _ensure_piper_files()
    _piper_voice = PiperVoice.load(str(model))
    return _piper_voice


def _piper_syn_config():
    try:
        from piper.config import SynthesisConfig  # type: ignore
        return SynthesisConfig(
            length_scale=PIPER_LENGTH_SCALE,
            noise_scale=PIPER_NOISE_SCALE,
            noise_w_scale=PIPER_NOISE_W,
        )
    except Exception:
        return None


def _piper_synthesize_sync(text: str) -> bytes:
    voice = _load_piper()
    chunks = _chunk_text(text, PIPER_CHUNK)
    syn = _piper_syn_config()
    with tempfile.TemporaryDirectory(prefix='mega-piper-') as td:
        wav_parts: list[str] = []
        for i, ch in enumerate(chunks):
            path = os.path.join(td, f'{i:04d}.wav')
            with wave.open(path, 'wb') as wf:
                if syn is not None:
                    voice.synthesize_wav(ch, wf, syn_config=syn)
                else:
                    voice.synthesize_wav(ch, wf)
            wav_parts.append(path)
        merged = os.path.join(td, 'm.wav')
        if len(wav_parts) == 1:
            shutil.copy(wav_parts[0], merged)
        else:
            # wav concat via ffmpeg
            ff = _which('ffmpeg')
            if ff and len(wav_parts) > 1:
                lst = os.path.join(td, 'list.txt')
                with open(lst, 'w') as f:
                    for p in wav_parts:
                        f.write(f"file '{p}'\n")
                subprocess.run(
                    [ff, '-y', '-f', 'concat', '-safe', '0', '-i', lst, '-c', 'copy', merged],
                    check=True, capture_output=True, timeout=300,
                )
            else:
                shutil.copy(wav_parts[0], merged)
        ogg = os.path.join(td, 'o.ogg')
        try:
            _ffmpeg_to_ogg(merged, ogg)
            with open(ogg, 'rb') as f:
                data = f.read()
        except Exception:
            with open(merged, 'rb') as f:
                data = f.read()
    if len(data) < 50:
        raise RuntimeError('piper empty')
    return data


async def synthesize_piper_fa(text: str) -> bytes:
    async with _piper_lock:
        return await asyncio.to_thread(_piper_synthesize_sync, text)


# ── Google Translate TTS ────────────────────────────────────────────────

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
                'Accept': '*/*',
            },
        )
        with urllib.request.urlopen(req, timeout=45) as resp:
            data = resp.read()
        if not data or len(data) < 50:
            raise RuntimeError('google empty chunk')
        return data

    return await asyncio.to_thread(_fetch)


async def synthesize_google(text: str, lang: str) -> bytes:
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    chunks = _chunk_text(text, GOOGLE_CHUNK)
    sem = asyncio.Semaphore(max(1, GOOGLE_PARALLEL))

    async def fetch(i: int, ch: str) -> tuple[int, bytes]:
        async with sem:
            last = None
            for attempt in range(4):
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
            path = os.path.join(td, f'{i:04d}.mp3')
            with open(path, 'wb') as f:
                f.write(data)
            paths.append(path)
        merged = os.path.join(td, 'm.mp3')
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


async def synthesize_gtts_lib(text: str, lang: str) -> bytes:
    from gtts import gTTS

    def _run() -> bytes:
        with tempfile.TemporaryDirectory(prefix='mega-gtts2-') as td:
            chunks = _chunk_text(text, 500)
            paths = []
            for i, ch in enumerate(chunks):
                path = os.path.join(td, f'{i:04d}.mp3')
                gTTS(text=ch, lang=lang, slow=False).save(path)
                paths.append(path)
            merged = os.path.join(td, 'm.mp3')
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
            cmd = [es, '-v', voice, '-s', '145', '-w', p, ch]
            await asyncio.to_thread(
                subprocess.run, cmd, check=True, capture_output=True, timeout=120,
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
            with open(ogg, 'rb') as f:
                return f.read()
        except Exception:
            with open(merged, 'rb') as f:
                return f.read()


async def synthesize_persian(text: str = DEFAULT_TEXT) -> tuple[bytes, str]:
    text = normalize_tts_text(text or DEFAULT_TEXT) or DEFAULT_TEXT
    if len(text) > MAX_CHARS:
        text = text[:MAX_CHARS]
    lang = detect_lang(text)
    errors: list[str] = []

    if lang == 'fa':
        # Piper offline neural — independent platform, no edge, no cloud quota
        try:
            data = await synthesize_piper_fa(text)
            log.info('TTS engine=piper-amir bytes=%s chars=%s', len(data), len(text))
            return data, 'voice.ogg'
        except Exception as exc:
            errors.append(f'piper:{type(exc).__name__}')
            log.warning('piper-fa failed: %s', exc)

    # EN primary, and FA fallback: Google
    try:
        data = await synthesize_google(text, lang)
        if data and len(data) > 100:
            ext = 'ogg' if data[:4] == b'OggS' else 'mp3'
            log.info('TTS engine=google-%s bytes=%s chars=%s', lang, len(data), len(text))
            return data, f'voice.{ext}'
    except Exception as exc:
        errors.append(f'google:{type(exc).__name__}')
        log.warning('google-tts failed (%s): %s', lang, exc)

    try:
        data = await synthesize_gtts_lib(text, lang)
        if data and len(data) > 100:
            ext = 'ogg' if data[:4] == b'OggS' else 'mp3'
            log.info('TTS engine=gtts-%s bytes=%s', lang, len(data))
            return data, f'voice.{ext}'
    except Exception as exc:
        errors.append(f'gtts:{type(exc).__name__}')
        log.warning('gtts failed: %s', exc)

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
