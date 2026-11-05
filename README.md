# Home Media Manager

A small Windows desktop host and responsive Flask client for a local/Real-Debrid media library.

## Run from source

1. Create a virtual environment and install `requirements.txt`.
2. Run `python desktop.py`.
3. Complete the first-run setup window. Every media feature is optional, so you can skip Real-Debrid, SMB, the PLS library, or FFmpeg and add it later.

The setup window appears before either server starts. The desktop window then shows the LAN address to open on phones, tablets, laptops, or TVs. Use **Settings** in the desktop window to update the same configuration later; restart Home Media Manager after saving settings. `python app.py` remains available as a server-only entry point.

Only one desktop instance runs per Windows login session. Launching the shortcut again signals the existing process to restore its window instead of starting more listeners. Closing the window hides it to the notification area; use the tray icon's **Show Home Media Manager** command to restore it or **Exit** to stop both listeners and terminate the application.

The packaged application uses a portable `userdata` folder beside `HomeMediaManager.exe`:

- `userdata\config.json` contains non-secret settings.
- `userdata\credentials.bin` contains the Real-Debrid values and Flask secret encrypted with Windows DPAPI for the current Windows user.
- `userdata\data` contains Favorites, caches, generated thumbnails, and other runtime data.

Keep the complete application folder in a writable location such as Desktop or Documents, not `Program Files`. Deleting that folder removes the packaged application and all data created by a new portable installation. Source-mode development continues to use `%LOCALAPPDATA%\HomeMediaManager` so private runtime files do not appear in the repository.

Existing AppData installations are copied into `userdata` automatically on the first portable launch. The original AppData profile is retained as a safety backup until the user removes it after verifying the portable copy. A legacy `.env` is imported into the encrypted credential store; `playlist.json` and `.cache` are copied without deleting or changing their originals. An existing `fullNew.pls` remains at its current location and is referenced there. Environment variables continue to override saved values for source/headless use:

- `REAL_DEBRID_TOKEN` and `REAL_DEBRID_CUSTOM_ID`
- `SMB_FOLDER` (for example a mapped folder or UNC share)
- `PORT` (Favorites, defaults to `8080`), `LIBRARY_PORT` (defaults to `8081`), and `HOST` (defaults to `0.0.0.0`)
- `FFMPEG_PATH` (optional explicit path to FFmpeg)
- `LIBRARY_PLAYLIST` (optional explicit path to `fullNew.pls`)
- `SECRET_KEY` (optional persistent Flask session key)
- `HOME_MEDIA_MANAGER_CONFIG_DIR` (optional override for the configuration root, useful for a future service/container deployment)

`HOME_URL` is still accepted for compatibility but is no longer required; local entries are recognized by their non-HTTP URLs.

## Thumbnails

For local/SMB videos, the app first looks for a `.jpg`, `.jpeg`, `.png`, or `.webp` beside the video with the same base filename. If FFmpeg is available, it captures a frame at 10 seconds and caches it under `.cache/thumbnails`. Set `FFMPEG_PATH`, put FFmpeg on `PATH`, or place `ffmpeg.exe` in `bin/` before packaging.

Missing thumbnails are queued automatically in two background workers. The placeholder is not cached by the browser, and the page replaces it when generation finishes. Failures and successful cache writes appear in the desktop activity log; temporary network/resource failures use a short backoff while likely-invalid media waits longer before retrying.

Real-Debrid playlist URLs are passed to FFmpeg with a 15-second network-read limit and a 35-second total process limit. FFmpeg uses HTTP range seeking when the endpoint supports it and stops after extracting one frame, so it does not intentionally download a complete remote video. If the endpoint is expired, inaccessible, or not seekable within the limits, the placeholder remains and the activity log reports why.

Only uncached thumbnails near the viewport are queued. Two FFmpeg processes may run at once, and each process is limited to one decode, filter, and encode thread. Visible pending items use one adaptive batch-status request instead of one polling timer per card. Successful cache files retain stable names and are indexed when the host starts, so refreshes, other clients, and application restarts reuse them directly.

The Real-Debrid cloud page is filtered using selected files from `/torrents/info`, not torrent display names. Only recognized video files with corresponding links are shown. Torrent details are cached in `.cache/real_debrid_details.json` and refreshed only when the list metadata changes; cold cache misses use four bounded metadata workers.

## Favorites and full Library

The main page is **Favorites**, backed by the existing `playlist.json`. Removing a Favorite uses the card's confirmation form and atomically removes only that JSON entry; it never deletes the referenced media file.

The separate **Library** page reads `FileN=` and optional `TitleN=` entries from `fullNew.pls` in numeric playlist order. Duplicate playlist entries remain separate, paths may point to different folders, and missing files stay visible as unavailable. The app never scans drives or folders for Library media. By default, place `fullNew.pls` beside the application; set `LIBRARY_PLAYLIST` to an explicit PLS path when it lives elsewhere.

Local Library playback uses opaque `/library/stream/<id>` URLs that resolve only to entries already present in the PLS file. HTTP(S) PLS entries retain their direct URLs. `fullNew.pls` is ignored by Git because it can contain private filesystem paths.

## HereSphere playback and library discovery

The main page supports HereSphere's JSON web API discovery. In HereSphere, open the main page and press the **Web Stream** button in the upper-left. HereSphere POSTs `/heresphere` at the current browser origin, receives the library index, and then loads the per-video data URLs. `/heresphere` is an API endpoint rather than a page to open directly.

API responses use `Content-Type: application/json` and `HereSphere-JSON-Version: 1`. The index uses HereSphere's `access` and `library` fields; each library entry points to video data containing `thumbnailImage`, `media`, and direct source URLs. This is the HereSphere schema, not the older DeoVR `scenes`/`video_url` schema. Projection and stereo fields are left unset when the playlist has no authoritative metadata so HereSphere can use its filename detection and user settings.

Card links remain direct `/play/<id>` links. When the page is running inside HereSphere, JavaScript marks them as download links so HereSphere sends them to its native player instead of the embedded browser. Normal browsers retain their existing link behavior. The site does not redirect card clicks to JSON and does not implement browser-driven next-video events.

Favorites and Library use separate local origins because HereSphere discovers the root `/heresphere` endpoint for the current origin. Favorites remains on `PORT` (8080 by default). Opening Library redirects the browser to `LIBRARY_PORT` (8081 by default), where `/heresphere` returns only `PLSLibrary.all()` entries. Both listeners run in the same Python process over the same Flask application and shared stores. After Web Stream imports either collection, HereSphere owns the playback queue, automatic next-video behavior, VR projection, and shuffle. `/library/heresphere` remains available for diagnostics but is not used for native discovery. `/debrid` is unchanged.

## Build the Windows executable

Install `requirements-dev.txt`, then run:

```powershell
pyinstaller --clean HomeMediaManager.spec
```

Distribute the complete `dist/HomeMediaManager` folder. Users configure it through the first-run window, and the application creates its portable `userdata` folder on first launch. The folder-based build avoids a second one-file extraction process, has no console window, starts both listeners automatically after setup, and bundles templates/static assets. Private playlists, configuration, caches, thumbnails, and import/source lists are not bundled at build time. If `bin/ffmpeg.exe` exists at build time, the spec includes it automatically; otherwise FFmpeg can be selected in Settings or found through `PATH`. `ffprobe.exe` is not currently required. Review FFmpeg licensing and distribution terms before shipping it.

## Test

```powershell
pytest -q
```
