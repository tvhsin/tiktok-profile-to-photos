"""
Continuously extract keyframes from MP4 videos using scene-change detection,
and delete near-duplicate frames (oldest one goes) using the same sensitivity.

Usage:
    pip install opencv-python numpy
    python extract_keyframes.py        (leave it running; Ctrl+C to stop)
"""

import json
import re
import sys
import time
from pathlib import Path

import cv2
import numpy as np

# ---------------- Settings ----------------
ROOT_DIR = Path(r"E:\Scripts\tiktok profile downloader\posts")

# Mean absolute pixel difference (0-255 scale). Two frames whose difference is
# ABOVE this count as "different"; at or below it they count as "the same".
# Used both for picking keyframes and for deleting duplicates.
DIFF_THRESHOLD = 28.0

MIN_GAP_SECONDS = 0.0          # min time between saved frames in one video
COMPARE_TO_LAST_SAVED = True   # False = compare to previous frame instead
DUPLICATE_EPSILON = 1.0        # skip final frame if nearly identical to last saved

# Watch mode
POLL_SECONDS = 5              # how often to rescan the folder
STABLE_SECONDS = 10            # video must be untouched this long (download finished)

# True  = skip videos that were already processed (normal behaviour).
# False = ignore the "already done" record and process every video again
#         (use temporarily; frames are appended, old ones are not deleted).
SKIP_ALREADY_PROCESSED = False

# Dedupe: delete the OLDER of two neighbouring frames that match.
DEDUPE_ENABLED = True
DEDUPE_EXISTING_ON_START = True  # also clean frames from earlier runs

JPEG_QUALITY = 92
COMPARE_WIDTH = 160
STATE_FILE = ROOT_DIR / "keyframe_state.json"
# ------------------------------------------

FRAME_RE = re.compile(r"^frame_(\d+)\.jpg$", re.IGNORECASE)


# ---------- image helpers ----------
def prep(frame):
    h, w = frame.shape[:2]
    scale = COMPARE_WIDTH / w
    small = cv2.resize(frame, (COMPARE_WIDTH, max(1, int(h * scale))),
                       interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (5, 5), 0).astype(np.float32)


def diff(a, b):
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_AREA)
    return float(np.mean(np.abs(a - b)))


def save_jpg(path: Path, frame):
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise RuntimeError(f"Could not encode {path}")
    buf.tofile(str(path))


def load_jpg(path: Path):
    data = np.fromfile(str(path), dtype=np.uint8)
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


# ---------- folder helpers ----------
def frame_files(folder: Path):
    """Frame files in a folder, sorted oldest -> newest by frame number."""
    found = []
    for p in folder.iterdir():
        m = FRAME_RE.match(p.name)
        if m:
            found.append((int(m.group(1)), p))
    found.sort()
    return [p for _, p in found]


def next_index(folder: Path):
    files = frame_files(folder)
    if not files:
        return 0
    return int(FRAME_RE.match(files[-1].name).group(1)) + 1


# ---------- state (remembers which videos are done) ----------
def video_key(p: Path):
    st = p.stat()
    return f"{p}|{st.st_size}|{int(st.st_mtime)}"


def load_state():
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return None


def save_state(done):
    STATE_FILE.write_text(json.dumps(sorted(done)), encoding="utf-8")


# ---------- core work ----------
def process_video(video: Path) -> list:
    out_dir = video.parent
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError("could not open video")

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    min_gap_frames = max(1, int(round(fps * MIN_GAP_SECONDS)))

    number = next_index(out_dir)
    saved_paths = []
    last_saved_idx = -10**9
    ref = None
    last_saved_small = None
    last_frame = None
    last_idx = -1
    idx = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        small = prep(frame)

        if idx == 0:
            path = out_dir / f"frame_{number:03d}.jpg"
            save_jpg(path, frame)
            saved_paths.append(path)
            number += 1
            last_saved_idx, last_saved_small, ref = idx, small, small
        else:
            if diff(small, ref) > DIFF_THRESHOLD and (idx - last_saved_idx) >= min_gap_frames:
                path = out_dir / f"frame_{number:03d}.jpg"
                save_jpg(path, frame)
                saved_paths.append(path)
                number += 1
                last_saved_idx, last_saved_small, ref = idx, small, small
            elif not COMPARE_TO_LAST_SAVED:
                ref = small

        last_frame, last_idx = frame, idx
        idx += 1

    cap.release()

    if idx == 0:
        raise RuntimeError("no frames could be read (file incomplete?)")

    if last_idx != last_saved_idx:
        if diff(prep(last_frame), last_saved_small) > DUPLICATE_EPSILON:
            path = out_dir / f"frame_{number:03d}.jpg"
            save_jpg(path, last_frame)
            saved_paths.append(path)

    return saved_paths


def dedupe_paths(paths) -> int:
    """paths must be sorted oldest -> newest. Every frame is compared against
    every newer frame that is being kept; if any match (difference <=
    DIFF_THRESHOLD) the OLDER frame is deleted."""
    kept = []  # (path, small) of newer frames that survive
    removed = 0

    for path in reversed(paths):  # newest first
        img = load_jpg(path)
        if img is None:
            continue
        small = prep(img)
        if any(diff(small, k_small) <= DIFF_THRESHOLD for _, k_small in kept):
            try:
                path.unlink()
                removed += 1
            except OSError as e:
                print(f"   ! Could not delete {path.name}: {e}")
        else:
            kept.append((path, small))

    return removed


def dedupe_folder(folder: Path) -> int:
    return dedupe_paths(frame_files(folder))


def dedupe_all():
    folders = sorted({p.parent for p in ROOT_DIR.rglob("frame_*.jpg")})
    total = 0
    for folder in folders:
        n = dedupe_folder(folder)
        if n:
            print(f"   dedupe: {folder.relative_to(ROOT_DIR)} -> removed {n}")
        total += n
    return total


# ---------- watch loop ----------
def main():
    if not ROOT_DIR.exists():
        sys.exit(f"Directory not found: {ROOT_DIR}")

    done = load_state()
    first_run = done is None
    if first_run:
        done = set()
        # Videos in folders that already have frames from an earlier run
        # are treated as finished so they aren't extracted a second time.
        for v in ROOT_DIR.rglob("*.mp4"):
            if frame_files(v.parent):
                done.add(video_key(v))
        save_state(done)

    if DEDUPE_ENABLED and DEDUPE_EXISTING_ON_START:
        print("Cleaning duplicates in existing frames...")
        removed = dedupe_all()
        print(f"Startup dedupe removed {removed} frame(s).\n")

    print(f"Watching {ROOT_DIR}")
    print(f"Threshold {DIFF_THRESHOLD}, scanning every {POLL_SECONDS}s. Ctrl+C to stop.\n")

    last_sizes = {}
    processed_this_run = set()  # stops endless reprocessing when skipping is off

    try:
        while True:
            for video in sorted(ROOT_DIR.rglob("*.mp4")):
                try:
                    st = video.stat()
                except OSError:
                    continue

                key = video_key(video)
                if key in processed_this_run:
                    continue
                if SKIP_ALREADY_PROCESSED and key in done:
                    continue

                # Make sure the download has finished.
                prev_size = last_sizes.get(video)
                last_sizes[video] = st.st_size
                age = time.time() - st.st_mtime
                if prev_size != st.st_size or age < STABLE_SECONDS:
                    continue

                rel = video.relative_to(ROOT_DIR)
                print(f"Processing {rel}")
                try:
                    new_frames = process_video(video)
                    print(f"   -> {len(new_frames)} keyframe(s) saved")
                    if DEDUPE_ENABLED:
                        r = dedupe_paths(new_frames)
                        print(f"   dedupe: removed {r} duplicate(s), "
                              f"{len(new_frames) - r} kept")
                    done.add(key)
                    processed_this_run.add(key)
                    save_state(done)
                except Exception as e:
                    print(f"   ! {e} (will retry later)")

            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()