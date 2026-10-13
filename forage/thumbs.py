"""List thumbnails: fetch an entry's image once, shrink it to a small WebP, keep it on disk."""
import asyncio
import hashlib
import io
import time
from pathlib import Path

import httpx
from PIL import Image, ImageOps

SIZE = 112            # 56px thumbnail at 2x
CARD_W = 720          # Today card pictures keep the image's shape, within these height-to-width limits
CARD_RATIO = (0.5, 1.15)
CARD_MIN_W, CARD_MIN_H = 240, 120  # smaller sources are icons and tracking pixels, and blow up into a blank card
MAX_BYTES = 12_000_000
RETRY_AFTER = 3600    # seconds before retrying an image that failed
_sem = asyncio.Semaphore(6)


def key(image_url: str) -> str:
    return hashlib.sha1(image_url.encode()).hexdigest()


MAX_PIXELS = 30_000_000  # decoded size, not file size: a small PNG can expand to hundreds of MB


def prune(cache: Path, max_age: float = 7 * 86400) -> None:
    """Drop stale failure markers and half-written files; finished thumbnails are kept."""
    if not cache.is_dir():
        return
    for f in cache.iterdir():
        if f.suffix in (".bad", ".cbad", ".tmp") and time.time() - f.stat().st_mtime > max_age:
            f.unlink(missing_ok=True)


def _shrink(data: bytes, card: bool = False) -> bytes:
    im = Image.open(io.BytesIO(data))
    if im.width * im.height > MAX_PIXELS:
        raise ValueError("image too large to decode")
    if min(im.size) < 16 or (card and (im.width < CARD_MIN_W or im.height < CARD_MIN_H)):
        raise ValueError("too small to be a picture")  # a tracking pixel or a logo, not something to give a card
    im = ImageOps.exif_transpose(im)
    size = (SIZE, SIZE)
    if card:
        size = (CARD_W, round(CARD_W * min(CARD_RATIO[1], max(CARD_RATIO[0], im.height / im.width))))
    im = ImageOps.fit(im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB"), size)
    out = io.BytesIO()
    im.save(out, "WEBP", quality=70, method=4)
    return out.getvalue()


async def get(http: httpx.AsyncClient, cache: Path, image_url: str, card: bool = False) -> Path | None:
    """Path of the cached thumbnail (or the larger card picture), building it if needed. None when the image can't be used."""
    cache.mkdir(parents=True, exist_ok=True)
    k = key(image_url)
    done, bad = cache / (f"{k}.c3.webp" if card else f"{k}.webp"), cache / (f"{k}.cbad" if card else f"{k}.bad")
    if done.exists():
        return done
    if bad.exists() and time.time() - bad.stat().st_mtime < RETRY_AFTER:
        return None
    async with _sem:
        if done.exists():
            return done
        try:
            async with http.stream("GET", image_url) as r:
                r.raise_for_status()
                if "image" not in r.headers.get("content-type", "image") or "svg" in r.headers.get("content-type", ""):
                    raise ValueError("not a raster image")
                data = bytearray()
                async for chunk in r.aiter_bytes():
                    data += chunk
                    if len(data) > MAX_BYTES:
                        raise ValueError("too large")
            webp = await asyncio.to_thread(_shrink, bytes(data), card)
        except Exception:
            bad.touch()
            return None
        tmp = done.with_suffix(".tmp")
        tmp.write_bytes(webp)
        tmp.replace(done)
        return done


async def card_ratio(http: httpx.AsyncClient, cache: Path, image_url: str) -> float | None:
    """Height over width of the card picture, building it first if needed. None when there is no usable picture."""
    path = await get(http, cache, image_url, card=True)
    if not path:
        return None
    try:
        with Image.open(path) as im:
            return round(im.height / im.width, 4)
    except Exception:
        return None
