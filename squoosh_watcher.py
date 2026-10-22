"""
Continuously resizes images in each post folder to 768px width (never upscaling)
and saves them to <post_folder>\\squooshed\\<original_filename>.

Requires:  pip install pillow
"""

import os
import shutil
import time
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageOps

# ----------------------------- CONFIG ---------------------------------------
ROOT = Path(r"E:\Scripts\tiktok profile downloader\posts")
SQUOOSHED_NAME = "squooshed"
TARGET_WIDTH = 768
LOOP_DELAY = 10          # seconds between passes
STABLE_WAIT = 1.5        # seconds to confirm file size is stable
JPEG_QUALITY = 95        # near-lossless
# -----------------------------------------------------------------------------

FORMATS = {
    ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP",
    ".bmp": "BMP", ".tif": "TIFF", ".tiff": "TIFF",
}
TEMP_SUFFIXES = {".tmp", ".part", ".crdownload", ".download", ".partial", ".temp"}

failed = {}  # path -> mtime of a failed attempt, so errors aren't re-logged forever


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def is_ready(path: Path) -> bool:
    """Non-empty, size stable over a short interval, and not locked."""
    try:
        size1 = path.stat().st_size
        if size1 == 0:
            return False
        time.sleep(STABLE_WAIT)
        if path.stat().st_size != size1:
            return False
        with open(path, "ab"):  # fails if another process holds a lock
            pass
        return True
    except OSError:
        return False


def prepare_mode(img: Image.Image, fmt: str) -> Image.Image:
    """Convert modes safely for resizing/saving."""
    if img.mode == "P":
        img = img.convert("RGBA" if "transparency" in img.info else "RGB")
    elif img.mode == "1":
        img = img.convert("L")

    if fmt == "JPEG":
        if img.mode in ("RGBA", "LA"):
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.getchannel("A"))
            return bg
        if img.mode != "RGB" and img.mode != "L":
            return img.convert("RGB")
    elif img.mode == "CMYK" and fmt != "TIFF":
        return img.convert("RGB")
    return img


def process(src: Path, dest: Path):
    """Returns a description string of what was done."""
    fmt = FORMATS[src.suffix.lower()]
    tmp = dest.with_name(dest.name + ".part")

    with Image.open(src) as im:
        im.load()
        icc = im.info.get("icc_profile")
        img = ImageOps.exif_transpose(im)
        w, h = img.size

        if w <= TARGET_WIDTH:
            needs_resize = False
        else:
            needs_resize = True
            new_h = max(1, round(h * TARGET_WIDTH / w))
            img = prepare_mode(img, fmt)
            img = img.resize((TARGET_WIDTH, new_h), Image.LANCZOS)

            kwargs = {}
            if icc:
                kwargs["icc_profile"] = icc
            if fmt == "JPEG":
                kwargs.update(quality=JPEG_QUALITY, subsampling=0, optimize=True)
            elif fmt == "PNG":
                kwargs.update(optimize=True)
            elif fmt == "WEBP":
                kwargs.update(lossless=True, quality=100, method=6)
            img.save(tmp, format=fmt, **kwargs)

    if needs_resize:
        os.replace(tmp, dest)
        return f"{w}x{h} -> {TARGET_WIDTH}x{new_h}"

    # Already <= 768px wide: copy untouched (no upscale, no quality change)
    shutil.copy2(src, tmp)
    os.replace(tmp, dest)
    return f"{w}x{h} kept as-is (copied)"


def scan_pass():
    if not ROOT.is_dir():
        log(f"Root folder not found: {ROOT}")
        return

    for post in sorted(p for p in ROOT.iterdir() if p.is_dir()):
        out_dir = post / SQUOOSHED_NAME
        try:
            out_dir.mkdir(exist_ok=True)
            done = {
                f.stem.lower()
                for f in out_dir.iterdir()
                if f.is_file() and f.suffix.lower() not in TEMP_SUFFIXES
            }
            files = sorted(f for f in post.iterdir() if f.is_file())
        except OSError as e:
            log(f"Cannot read {post.name}: {e}")
            continue

        for src in files:
            ext = src.suffix.lower()
            if ext in TEMP_SUFFIXES or ext not in FORMATS:
                continue
            if src.stem.lower() in done:
                continue

            try:
                mtime = src.stat().st_mtime
            except OSError:
                continue
            if failed.get(src) == mtime:
                continue  # already failed on this exact version

            if not is_ready(src):
                continue  # still downloading/locked; retry next pass

            try:
                info = process(src, out_dir / src.name)
                log(f"Compressed: {post.name}\\{src.name} ({info})")
                failed.pop(src, None)
            except Exception as e:
                failed[src] = mtime
                log(f"ERROR (skipped) {post.name}\\{src.name}: {e}")
                try:
                    (out_dir / (src.name + ".part")).unlink(missing_ok=True)
                except OSError:
                    pass


def main():
    log(f"Watching: {ROOT}  (Ctrl+C to stop)")
    try:
        while True:
            try:
                scan_pass()
            except Exception as e:
                log(f"Unexpected error in scan pass: {e}")
            time.sleep(LOOP_DELAY)
    except KeyboardInterrupt:
        log("Stopped.")


if __name__ == "__main__":
    main()
