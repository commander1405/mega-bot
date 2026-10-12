"""Experimental Persian text-to-speech helpers (no API key required).

Primary engine: espeak-ng (offline, fa voice) + ffmpeg → OGG Opus.
Optional: edge-tts (Microsoft neural voices) when network allows.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import tempfile
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_TEXT = 'تست'
EDGE_VOICE = os.getenv('TTS_EDGE_VOICE', 'fa-IR-DilaraNeural')


def _which(cmd: str) -> Optional[str]:
    return shutil.which(cmd)


def _wav_to_ogg_opus(wav_path: str, ogg_path: str) -> None:
    ffmpeg = _which('ffmpeg')
    if not ffmpeg:
        raise RuntimeError('ffmpeg not found')
    r = subprocess.run(
        [ffmpeg, '-y', '-i', wav_path, '-c:a', 'libopus', '-b:a', '32k', '-vbr', 'on', ogg_path],
        capture_output=True,
        timeout=60,
    )
    if r.returncode != 0 or not os.path.isfile(ogg_path) or os.path.getsize(ogg_path) < 50:
        err = (r.stderr or b'').decode('utf-8', errors='replace')[-400:]
        raise RuntimeError(f'ffmpeg opus failed: {err}')


def synthesize_espeak(text: str = DEFAULT_TEXT) -> bytes:
    """Offline Persian TTS via espeak-ng → OGG Opus bytes."""
    espeak = _which('espeak-ng') or _which('espeak')
    if not espeak:
        raise RuntimeError('espeak-ng not installed')
    text = (text or DEFAULT_TEXT).strip() or DEFAULT_TEXT
    with tempfile.TemporaryDirectory(prefix='mega-tts-') as td:
        wav = os.path.join(td, 'out.wav')
        ogg = os.path.join(td, 'out.ogg')
        # -v fa: Persian; -s 140 slightly slower for clarity
        r = subprocess.run(
            [espeak, '-v', 'fa', '-s', '140', '-w', wav, text],
            capture_output=True,
            timeout=30,
        )
        if r.returncode != 0 or not os.path.isfile(wav):
            err = (r.stderr or b'').decode('utf-8', errors='replace')[-300:]
            raise RuntimeError(f'espeak failed: {err}')
        _wav_to_ogg_opus(wav, ogg)
        with open(ogg, 'rb') as f:
            data = f.read()
    if len(data) < 50:
        raise RuntimeError('TTS produced empty audio')
    return data


async def synthesize_edge(text: str = DEFAULT_TEXT, voice: str | None = None) -> bytes:
    """Optional neural TTS (edge-tts). Requires network; may fail outside allowed regions."""
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError('edge-tts not installed') from exc
    text = (text or DEFAULT_TEXT).strip() or DEFAULT_TEXT
    voice = voice or EDGE_VOICE
    with tempfile.TemporaryDirectory(prefix='mega-edge-') as td:
        mp3 = os.path.join(td, 'out.mp3')
        ogg = os.path.join(td, 'out.ogg')
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(mp3)
        if not os.path.isfile(mp3) or os.path.getsize(mp3) < 50:
            raise RuntimeError('edge-tts empty output')
        ffmpeg = _which('ffmpeg')
        if not ffmpeg:
            # return mp3 bytes — caller may send as audio
            with open(mp3, 'rb') as f:
                return f.read()
        r = subprocess.run(
            [ffmpeg, '-y', '-i', mp3, '-c:a', 'libopus', '-b:a', '32k', ogg],
            capture_output=True,
            timeout=60,
        )
        if r.returncode == 0 and os.path.isfile(ogg) and os.path.getsize(ogg) >= 50:
            with open(ogg, 'rb') as f:
                return f.read()
        with open(mp3, 'rb') as f:
            return f.read()


async def synthesize_persian(text: str = DEFAULT_TEXT) -> tuple[bytes, str]:
    """Return (audio_bytes, filename). Prefer edge-tts when enabled, else espeak-ng.

    Env:
      TTS_ENGINE=espeak|edge|auto  (default auto: try edge then espeak)
    """
    engine = (os.getenv('TTS_ENGINE') or 'auto').strip().lower()
    errors: list[str] = []

    async def try_edge() -> tuple[bytes, str] | None:
        try:
            data = await asyncio.wait_for(synthesize_edge(text), timeout=25)
            ext = 'ogg' if data[:4] == b'OggS' else 'mp3'
            return data, f'voice.{ext}'
        except Exception as exc:
            errors.append(f'edge:{type(exc).__name__}')
            log.info('edge-tts unavailable: %s', type(exc).__name__)
            return None

    def try_espeak() -> tuple[bytes, str] | None:
        try:
            data = synthesize_espeak(text)
            return data, 'voice.ogg'
        except Exception as exc:
            errors.append(f'espeak:{type(exc).__name__}')
            log.info('espeak-ng failed: %s', type(exc).__name__)
            return None

    if engine == 'edge':
        got = await try_edge()
        if got:
            return got
    elif engine == 'espeak':
        got = try_espeak()
        if got:
            return got
    else:
        # auto: edge first for quality, espeak fallback for reliability
        got = await try_edge()
        if got:
            return got
        got = try_espeak()
        if got:
            return got

    raise RuntimeError('TTS failed: ' + ', '.join(errors) if errors else 'no engine')
