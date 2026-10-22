basically it downloads every picture from a tiktok profile and also downloads the videos and then converts the videos to photos. but it smartly picks different frames from the video instead of getting every frame. i did this to get every frame i could from a book diy profile. then i abandoned it when i realized the acc literally uploaded the entire book in a playlist.

the script also has an optional thingy to resize pictures a bit so its not extremely heavy, that was so i could make gemini scan the badly scanned images and rewrite the book for me.

im putting this on github cuz im cleaning up my pc and im attached with my scripts folder. i told copilot to write whatever suits as readme
# TikTok profile archive and image tools
## Setup

Open PowerShell here:

```powershell
Set-Location 'E:\Scripts\tiktok profile downloader'
python -m pip install -U "yt-dlp[default,curl-cffi]" opencv-python numpy pillow
```

The downloader needs an exported TikTok `cookies.txt` next to the script. Get it with the extension "Get cookies.txt LOCALLY"

## Download posts

```powershell
python .\tiktok_downloader.py
```

It downloads posts from the `USERNAME` set near the top of the script into
`posts\`. Each post gets a dated folder named like
`2025-02-18_7472585687183723798`. A video post has `01.mp4`; a photo post has
`01.jpg`, `02.jpg`, and so on. Both get a `caption.txt`.

The script skips a post if its folder already has `caption.txt`, retries
incomplete TikTok data, waits a few seconds between posts, and writes failed
post URLs to `failed.txt`. It stops after ten failed posts in a row. Run it
again to retry incomplete posts.

To download only selected posts, create `post_ids.txt` beside the script,
put one TikTok URL or post ID on each line, and run the same command. When
that file exists it replaces the profile listing. Remove it to go back to
the configured profile.

**Heads up:** `posts\` currently contains only two sample folders, one video
and one slideshow. Running the downloader without `post_ids.txt` will start
filling the archive from the configured profile again.

## Optional: extract video frames

```powershell
python .\extract_keyframes.py
```

This keeps watching `posts\` for MP4s. After a video stops changing, it
saves scene-change images as `frame_000.jpg`, `frame_001.jpg`, etc. beside
that video. Stop it with Ctrl+C.

The watcher removes near-duplicate frames by default, including old frames
already in the folders when it starts. Set `DEDUPE_ENABLED = False` in the
script if you do not want it to delete frames. `SKIP_ALREADY_PROCESSED` is
currently `False`, so a later run can append another set of frames to a
video's folder.

## Optional: resize downloaded pictures

```powershell
python .\squoosh_watcher.py
```

This also watches `posts\` until Ctrl+C. It makes a `squooshed\` subfolder
inside each post folder and copies supported images there. Images wider than
768 pixels are resized to 768 pixels; smaller ones are copied unchanged.
Original images are left alone. Existing output filenames are skipped.

Both watchers currently point to the fixed path
`E:\Scripts\tiktok profile downloader\posts`. If you move the project,
change `ROOT_DIR` in `extract_keyframes.py` and `ROOT` in
`squoosh_watcher.py`.

## What can be recreated

The downloaded posts can be downloaded again if TikTok still makes them
available and the cookies work. Keyframes can be regenerated from the MP4s;
resized copies can be regenerated from the downloaded images. The scripts
do not guarantee TikTok will return every old post or the same media later.
