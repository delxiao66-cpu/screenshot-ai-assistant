import io
import warnings
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


def preprocess(raw: bytes) -> tuple[bytes, int, int]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as source:
                if source.format not in {'PNG', 'JPEG', 'WEBP'}:
                    raise ValueError('仅支持 PNG、JPEG 和 WebP 图片。')
                source.load()
                oriented = ImageOps.exif_transpose(source)
                rgba = oriented.convert('RGBA')
                image = Image.new('RGB', rgba.size, 'white')
                image.paste(rgba, mask=rgba.getchannel('A'))
                # Works for 1–20 images, including the provider's >=15-image limit.
                image.thumbnail((3072, 3072), Image.Resampling.LANCZOS)
                output = io.BytesIO()
                image.save(output, 'JPEG', quality=92, subsampling=0, optimize=True)
                return output.getvalue(), image.width, image.height
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError('图片无法解码或像素过大，请选择有效的 PNG/JPEG/WebP 图片。') from exc


def read_images(paths: list[Path]) -> list[bytes]:
    return [path.read_bytes() for path in paths]
