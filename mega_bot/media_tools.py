from __future__ import annotations
import asyncio, io, os, shutil, subprocess, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

MAX_INPUT_BYTES = 20 * 1024 * 1024
MAX_VIDEO_SECONDS = 20
MAX_OUTPUT_BYTES = 18 * 1024 * 1024


def _font(size: int):
    for candidate in ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', '/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf'):
        if os.path.exists(candidate):
            try: return ImageFont.truetype(candidate, size)
            except Exception: pass
    return ImageFont.load_default()


def _overlay(image: Image.Image, text: str) -> Image.Image:
    if not text:
        return image
    image = image.convert('RGBA')
    draw = ImageDraw.Draw(image)
    max_width = int(image.width * .92)
    size = max(12, int(image.height * .12))
    font = _font(size)
    while size > 10 and draw.textbbox((0, 0), text, font=font, stroke_width=2)[2] > max_width:
        size -= 2
        font = _font(size)
    box = draw.textbbox((0, 0), text, font=font, stroke_width=2)
    x = (image.width - (box[2] - box[0])) // 2
    y = int(image.height * .58) - (box[3] - box[1]) // 2
    draw.text((x, y), text, font=font, fill='white', stroke_width=max(1, size // 18), stroke_fill='black')
    return image.convert('RGB')


def image_to_gif(raw: bytes, text: str = '') -> bytes:
    with Image.open(io.BytesIO(raw)) as source:
        frames = []
        try:
            count = min(getattr(source, 'n_frames', 1), 60)
            for i in range(count):
                source.seek(i)
                frame = ImageOps.contain(source.convert('RGB'), (640, 640))
                if text: frame = _overlay(frame, text)
                frames.append(frame.copy())
        except EOFError:
            pass
    if not frames: raise ValueError('تصویر قابل پردازش نیست.')
    out = io.BytesIO()
    frames[0].save(out, format='GIF', save_all=True, append_images=frames[1:], duration=100, loop=0, optimize=True)
    result = out.getvalue()
    if len(result) > MAX_OUTPUT_BYTES: raise ValueError('حجم GIF بیش از حد مجاز است.')
    return result


def video_to_gif(raw: bytes, suffix: str = '.mp4', text: str = '') -> bytes:
    if not shutil.which('ffmpeg'):
        raise RuntimeError('ffmpeg روی سرور نصب نیست؛ از Dockerfile همین پروژه برای استقرار استفاده کن.')
    with tempfile.TemporaryDirectory(prefix='megabot-gif-') as tmp:
        src, dst = Path(tmp)/('input'+suffix), Path(tmp)/'output.gif'
        src.write_bytes(raw)
        vf = "fps=10,scale=640:-1:flags=lanczos"
        if text:
            # Pillow handles Persian shaping more reliably for image frames; video overlay uses drawtext.
            safe = text.replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'").replace('%', '\\%')
            font = '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
            vf += f",drawtext=fontfile={font}:text='{safe}':fontcolor=white:borderw=2:bordercolor=black:fontsize=h/12:x=(w-text_w)/2:y=h*0.58"
        palette = Path(tmp)/'palette.png'
        cmd1 = ['ffmpeg','-nostdin','-v','error','-y','-i',str(src),'-t',str(MAX_VIDEO_SECONDS),'-vf',vf+',palettegen',str(palette)]
        cmd2 = ['ffmpeg','-nostdin','-v','error','-y','-i',str(src),'-i',str(palette),'-t',str(MAX_VIDEO_SECONDS),'-filter_complex',f'{vf}[x];[x][1:v]paletteuse','-loop','0',str(dst)]
        subprocess.run(cmd1, check=True, timeout=45, capture_output=True)
        subprocess.run(cmd2, check=True, timeout=45, capture_output=True)
        result = dst.read_bytes()
        if len(result) > MAX_OUTPUT_BYTES: raise ValueError('حجم GIF بیش از حد مجاز است.')
        return result

async def convert_media(raw: bytes, filename: str, text: str = '') -> bytes:
    suffix = Path(filename).suffix.lower()
    if suffix in ('.mp4','.mov','.webm','.mkv','.avi'):
        return await asyncio.to_thread(video_to_gif, raw, suffix, text)
    return await asyncio.to_thread(image_to_gif, raw, text)
