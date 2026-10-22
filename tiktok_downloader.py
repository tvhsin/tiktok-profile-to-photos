#!/usr/bin/env python3
"""
Download all posts (videos AND photo slideshows) from your own TikTok account.

Each post gets a folder, named chronologically:
    posts/2025-02-18_7472585687183723798/01.jpg, 02.jpg, ... + caption.txt
    posts/2025-03-02_7690643783167331606/01.mp4 + caption.txt

Setup:
    pip install -U "yt-dlp[default,curl-cffi]"
Needs:
    cookies.txt (exported from your logged-in browser) next to this script

Re-running is safe: finished posts are skipped, failed ones are retried.
Optional: put post URLs or IDs (one per line) in post_ids.txt to download
only those instead of the whole profile.
"""

import random
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import yt_dlp

# ---- Settings ---------------------------------------------------------------
USERNAME = "readmewhenyouneedme"
OUTPUT_FOLDER_NAME = "posts"
COOKIES_FILE = "cookies.txt"
SLEEP_RANGE = (4, 8)         # seconds between posts (raise if you get blocked)
STOP_AFTER_FAILURES = 10     # stop early if this many posts fail in a row
MAX_ATTEMPTS = 3             # tries per post when TikTok returns incomplete data
# -----------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / OUTPUT_FOLDER_NAME
COOKIES = SCRIPT_DIR / COOKIES_FILE
FAILED_FILE = SCRIPT_DIR / "failed.txt"
MANUAL_LIST = SCRIPT_DIR / "post_ids.txt"
TMP_DIR = Path(tempfile.mkdtemp(prefix="tt_"))

ID_RE = re.compile(r"(\d{15,25})")
AUDIO_EXTS = {".m4a", ".mp3", ".aac", ".wav", ".ogg", ".opus"}


class IncompleteData(RuntimeError):
    """TikTok returned a page without images or a video stream (worth retrying)."""


def make_ydl() -> yt_dlp.YoutubeDL:
    return yt_dlp.YoutubeDL({
        "cookiefile": str(COOKIES),
        "outtmpl": str(TMP_DIR / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
    })


def cleanup_audio_only() -> None:
    """Remove folders from earlier runs that only contain a soundtrack."""
    removed = 0
    for d in OUTPUT_DIR.glob("*_*"):
        if not d.is_dir():
            continue
        files = [p for p in d.iterdir() if p.name != "caption.txt"]
        if files and all(p.suffix.lower() in AUDIO_EXTS for p in files):
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    if removed:
        print(f"Removed {removed} audio-only folders from earlier runs (they will be redone).")


def list_post_ids(ydl: yt_dlp.YoutubeDL) -> list[str]:
    """Return every post ID on the profile (videos and slideshows)."""
    if MANUAL_LIST.exists():
        lines = MANUAL_LIST.read_text(encoding="utf-8").splitlines()
        ids = [m.group(1) for ln in lines if (m := ID_RE.search(ln))]
        print(f"Using {len(ids)} IDs from {MANUAL_LIST.name}")
        return list(dict.fromkeys(ids))

    info = ydl.extract_info(
        f"https://www.tiktok.com/@{USERNAME}", download=False,
        process=False,  # flat listing only
    )
    ids = []
    for e in info.get("entries") or []:
        if not e:
            continue
        pid = str(e.get("id") or "")
        if not ID_RE.fullmatch(pid):
            m = ID_RE.search(e.get("url") or "")
            pid = m.group(1) if m else ""
        if pid:
            ids.append(pid)
    return list(dict.fromkeys(ids))


def already_done(post_id: str) -> bool:
    return any((d / "caption.txt").exists() for d in OUTPUT_DIR.glob(f"*_{post_id}"))


def get_post_data(ydl: yt_dlp.YoutubeDL, url: str, post_id: str) -> dict:
    """Load the post page (this solves TikTok's JS challenge) and return its data."""
    ie = ydl.get_info_extractor("TikTok")
    ie.set_downloader(ydl)
    data, status = ie._extract_web_data_and_status(url, post_id)
    if status != 0 or not data:
        raise RuntimeError(f"TikTok returned status {status} (deleted/private/blocked?)")
    return data


def folder_for(data: dict, post_id: str) -> Path:
    ts = int(data.get("createTime") or 0)
    date = datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "unknown-date"
    folder = OUTPUT_DIR / f"{date}_{post_id}"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def download_image(ydl: yt_dlp.YoutubeDL, url_list: list[str], dest_stem: Path) -> None:
    last_err = None
    for u in url_list:
        try:
            resp = ydl.urlopen(yt_dlp.networking.Request(
                u, headers={"Referer": "https://www.tiktok.com/"}))
            blob = resp.read()
            if not blob:
                continue
            ext = Path(u.split("?")[0]).suffix.lower().lstrip(".") or "jpg"
            if ext == "jpeg":
                ext = "jpg"
            dest_stem.with_suffix(f".{ext}").write_bytes(blob)
            return
        except Exception as e:  # try the next mirror URL
            last_err = e
    raise RuntimeError(f"all image URLs failed ({last_err})")


def download_post(ydl: yt_dlp.YoutubeDL, post_id: str) -> None:
    url = f"https://www.tiktok.com/@{USERNAME}/video/{post_id}"
    data = get_post_data(ydl, url, post_id)
    caption = data.get("desc") or ""

    images = (data.get("imagePost") or {}).get("images") or []
    vid = data.get("video") or {}
    has_video = bool(vid.get("duration")) or bool(vid.get("playAddr"))

    if images:
        folder = folder_for(data, post_id)
        for n, img in enumerate(images, 1):
            urls = (img.get("imageURL") or {}).get("urlList") or []
            if not urls:
                raise RuntimeError(f"image {n} has no URL")
            download_image(ydl, urls, folder / f"{n:02d}")
    elif has_video:
        info = ydl.extract_info(url, download=True)
        dl = (info.get("requested_downloads") or [{}])[0].get("filepath")
        src = Path(dl) if dl else Path(ydl.prepare_filename(info))
        if not src.exists():
            raise RuntimeError("video file not found after download")
        if src.suffix.lower() in AUDIO_EXTS:
            src.unlink(missing_ok=True)
            raise IncompleteData("got audio only")
        folder = folder_for(data, post_id)
        shutil.move(str(src), folder / f"01{src.suffix}")
    else:
        raise IncompleteData("page had no images and no video")

    # written last: its existence marks the post as complete
    (folder / "caption.txt").write_text(caption, encoding="utf-8")


def main() -> int:
    if not COOKIES.exists():
        print(f"Missing {COOKIES}. Export your TikTok cookies first.")
        return 1
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_audio_only()

    failed: list[str] = []
    streak = 0
    ydl = None
    try:
        print("Listing posts...")
        with make_ydl() as lister:
            ids = list_post_ids(lister)
        print(f"Found {len(ids)} posts.")
        if not ids:
            print("Listing came back empty. Put post URLs in post_ids.txt and rerun.")
            return 1

        ydl = make_ydl()
        for i, pid in enumerate(ids, 1):
            if already_done(pid):
                continue
            print(f"[{i}/{len(ids)}] {pid} ... ", end="", flush=True)

            error = None
            for attempt in range(1, MAX_ATTEMPTS + 1):
                try:
                    download_post(ydl, pid)
                    error = None
                    break
                except IncompleteData as e:
                    error = e
                    if attempt < MAX_ATTEMPTS:
                        print(f"incomplete ({e}), retry {attempt}... ", end="", flush=True)
                        ydl.close()
                        time.sleep(10 * attempt)
                        ydl = make_ydl()  # fresh session
                except Exception as e:
                    error = e
                    break

            if error is None:
                print("OK")
                streak = 0
            else:
                print(f"FAILED ({str(error)[:100]})")
                failed.append(pid)
                streak += 1
                if streak >= STOP_AFTER_FAILURES:
                    print(f"\n{streak} failures in a row, so TikTok is probably "
                          "blocking you. Wait an hour (or change network) and rerun.")
                    break
            time.sleep(random.uniform(*SLEEP_RANGE))
    except KeyboardInterrupt:
        print("\nStopped by you. Rerun to continue where you left off.")
    finally:
        if ydl is not None:
            ydl.close()
        FAILED_FILE.write_text(
            "\n".join(f"https://www.tiktok.com/@{USERNAME}/video/{p}" for p in failed),
            encoding="utf-8")
        shutil.rmtree(TMP_DIR, ignore_errors=True)

    print(f"\nDone. {len(failed)} failed this run (see failed.txt). Rerun to retry them.")
    return 0


if __name__ == "__main__":
    sys.exit(main())