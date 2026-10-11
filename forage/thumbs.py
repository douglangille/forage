"""List thumbnails: fetch an entry's image once, shrink it to a small WebP, keep it on disk."""
import asyncio
import hashlib
import io
import time
from pathlib import Path

import httpx
from PIL import Image, ImageOps

SIZE = 112            # 56px thumbnail at 2x
MAX_BYTES = 12_000_000
RETRY_AFTER = 3600    # seconds before retrying an image that failed
_sem = asyncio.Semaphore(6)


def key(image_url: str) -> str:
    return hashlib.sha1(image_url.encode()).hexdigest()


def _shrink(data: bytes) -> bytes:
    im = Image.open(io.BytesIO(data))
    im = ImageOps.exif_transpose(im)
    im = ImageOps.fit(im.convert("RGBA" if "A" in im.getbands() or im.mode == "P" else "RGB"), (SIZE, SIZE))
    out = io.BytesIO()
    im.save(out, "WEBP", quality=70, method=4)
    return out.getvalue()


async def get(http: httpx.AsyncClient, cache: Path, image_url: str) -> Path | None:
    """Path of the cached thumbnail, building it if needed. None when the image can't be used."""
    cache.mkdir(parents=True, exist_ok=True)
    k = key(image_url)
    done, bad = cache / f"{k}.webp", cache / f"{k}.bad"
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
                data = b""
                async for chunk in r.aiter_bytes():
                    data += chunk
                    if len(data) > MAX_BYTES:
                        raise ValueError("too large")
            webp = await asyncio.to_thread(_shrink, data)
        except Exception:
            bad.touch()
            return None
        tmp = done.with_suffix(".tmp")
        tmp.write_bytes(webp)
        tmp.replace(done)
        return done
