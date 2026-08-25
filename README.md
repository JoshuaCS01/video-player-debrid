# Home Media Manager

A small Windows desktop host and responsive Flask client for a local/Real-Debrid media library.

## Run from source

1. Create a virtual environment and install `requirements.txt`.
2. Keep `.env` and `playlist.json` beside `desktop.py`.
3. Run `python desktop.py`, then choose **Start server**.

The desktop window shows the LAN address to open on phones, tablets, laptops, or TVs. `python app.py` remains available as a server-only entry point.

Supported `.env` settings:

- `REAL_DEBRID_TOKEN` and `REAL_DEBRID_CUSTOM_ID`
- `SMB_FOLDER` (for example a mapped folder or UNC share)
- `PORT` (defaults to `8080`) and `HOST` (defaults to `0.0.0.0`)
- `FFMPEG_PATH` (optional explicit path to FFmpeg)
- `SECRET_KEY` (optional persistent Flask session key)

`HOME_URL` is still accepted for compatibility but is no longer required; local entries are recognized by their non-HTTP URLs.

## Thumbnails

For local/SMB videos, the app first looks for a `.jpg`, `.jpeg`, `.png`, or `.webp` beside the video with the same base filename. If FFmpeg is available, it captures a frame at 10 seconds and caches it under `.cache/thumbnails`. Set `FFMPEG_PATH`, put FFmpeg on `PATH`, or place `ffmpeg.exe` in `bin/` before packaging.

Missing thumbnails are queued automatically in two background workers. The placeholder is not cached by the browser, and the page replaces it when generation finishes. Failures and successful cache writes appear in the desktop activity log; temporary network/resource failures use a short backoff while likely-invalid media waits longer before retrying.

Real-Debrid playlist URLs are passed to FFmpeg with a 15-second network-read limit and a 35-second total process limit. FFmpeg uses HTTP range seeking when the endpoint supports it and stops after extracting one frame, so it does not intentionally download a complete remote video. If the endpoint is expired, inaccessible, or not seekable within the limits, the placeholder remains and the activity log reports why.

Only uncached thumbnails near the viewport are queued. Two FFmpeg processes may run at once, and each process is limited to one decode, filter, and encode thread. Visible pending items use one adaptive batch-status request instead of one polling timer per card. Successful cache files retain stable names and are indexed when the host starts, so refreshes, other clients, and application restarts reuse them directly.

The Real-Debrid cloud page is filtered using selected files from `/torrents/info`, not torrent display names. Only recognized video files with corresponding links are shown. Torrent details are cached in `.cache/real_debrid_details.json` and refreshed only when the list metadata changes; cold cache misses use four bounded metadata workers.

## Build the Windows executable

Install `requirements-dev.txt`, then run:

```powershell
pyinstaller --clean HomeMediaManager.spec
```

Copy `.env` and `playlist.json` beside the resulting executable. The build has no console window and bundles templates/static assets. If `bin/ffmpeg.exe` exists at build time, the spec includes it automatically. Review FFmpeg licensing and distribution terms before shipping it.

## Test

```powershell
pytest -q
```
