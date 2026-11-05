# Home Media Manager

A portable Windows desktop host and responsive Flask client for a local/Real-Debrid media library.

## Install the Windows application

1. Extract or copy the complete `HomeMediaManager` folder to a writable location such as Desktop or Documents. Do not copy `HomeMediaManager.exe` by itself; it needs the adjacent `_internal` folder.
2. Run `HomeMediaManager.exe`. If Windows Firewall asks, allow access on **Private networks** so other devices on the home network can connect.
3. On a new installation, complete the first-run setup window and select **Save and Continue**. All media integrations are optional and can be configured later. If an existing AppData profile is found, it is imported and the application may open directly instead.
4. The application starts Favorites on port `8080` and Library on port `8081`. The desktop window displays the LAN address to open from another device.

Only one instance runs per Windows login session. Starting the EXE again restores the existing window instead of starting duplicate servers. Closing the window with **X** hides it in the Windows notification area and keeps both servers running. Use the tray icon's **Show Home Media Manager** command to restore it or **Exit** to stop both servers and close the process.

### First-run setup options

| Option | Required? | What it controls and where to find it |
| --- | --- | --- |
| **Real-Debrid API token** | Optional | Enables the Real-Debrid cloud page. Sign in and copy the private token from <https://real-debrid.com/apitoken>. Treat it like a password. |
| **Real-Debrid custom ID** | Optional | Enables saving Real-Debrid files to Favorites using the account's HTTP-folder links. On <https://real-debrid.com/account>, find **Your HTTP folder**, open/copy its `https://my.real-debrid.com/ID/` link, and enter only the `ID` portion. This is not the API token or OAuth client ID and should also be kept private. |
| **Local / SMB media folder** | Optional | Root folder used by local entries in `playlist.json` (Favorites), such as `D:\Videos` or `\\MEDIA-PC\Videos`. It is also used to find/generate thumbnails for those Favorites. The PLS Library can contain its own complete paths and does not require this setting. |
| **PLS library** | Optional | Select an existing `.pls` playlist for the Library page. The application reads `FileN=` entries and optional `TitleN=` entries; it does not scan the folder or modify the selected PLS file. Leave blank if the Library page is not needed. |
| **FFmpeg executable** | Optional | Select `ffmpeg.exe` to generate video thumbnails. Leave blank when FFmpeg is bundled in `bin`, available on `PATH`, or thumbnails are not needed. FFmpeg downloads and licensing information are available at <https://ffmpeg.org/download.html>. |
| **Listen host** | Normally leave unchanged | `0.0.0.0` allows phones, headsets, and other LAN devices to connect. `127.0.0.1` restricts access to the same PC. |
| **Favorites port** | Normally leave unchanged | Port for the Favorites website and Favorites HereSphere Web Stream. Default: `8080`. |
| **Library port** | Normally leave unchanged | Separate origin for the Library website and Library HereSphere Web Stream. Default: `8081`; it must differ from the Favorites port. |

Use **Settings** in the main desktop window to select another PLS file or change any option later. Saved changes take effect after exiting Home Media Manager from the tray and reopening it.

### Portable data and uninstalling

The packaged application uses a portable `userdata` folder beside `HomeMediaManager.exe`:

- `userdata\config.json` contains non-secret settings.
- `userdata\credentials.bin` contains the Real-Debrid values and Flask secret encrypted with Windows DPAPI for the current Windows user.
- `userdata\data\playlist.json` contains Favorites.
- `userdata\data\.cache` contains generated thumbnails and Real-Debrid metadata cache.

Do not install this portable build under `Program Files`, where Windows may prevent it from creating or updating `userdata`. To uninstall a new portable installation, first choose **Exit** from the tray and then delete the complete `HomeMediaManager` folder. Externally selected PLS files and the original media files are never deleted by uninstalling the application.

Existing AppData installations are copied into `userdata` automatically on the first portable launch. The original `%LOCALAPPDATA%\HomeMediaManager` profile is retained as a safety backup; after confirming the portable copy contains the expected Favorites and thumbnails, it may be removed manually. A legacy `.env`, `playlist.json`, and `.cache` beside the EXE are also imported without deleting the originals. An existing `fullNew.pls` remains in place and is referenced rather than copied.

## Run from source

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python desktop.py
```

Source mode stores private runtime data under `%LOCALAPPDATA%\HomeMediaManager` rather than in the repository. `python app.py` remains available as a server-only entry point. Environment variables override saved values for development and future headless deployments:

- `REAL_DEBRID_TOKEN` and `REAL_DEBRID_CUSTOM_ID`
- `SMB_FOLDER` (for example a mapped folder or UNC share)
- `PORT` (Favorites, defaults to `8080`), `LIBRARY_PORT` (defaults to `8081`), and `HOST` (defaults to `0.0.0.0`)
- `FFMPEG_PATH` (optional explicit path to FFmpeg)
- `LIBRARY_PLAYLIST` (optional explicit path to `fullNew.pls`)
- `SECRET_KEY` (optional persistent Flask session key)
- `HOME_MEDIA_MANAGER_CONFIG_DIR` (optional override for the configuration root, useful for a future service/container deployment)

`HOME_URL` is still accepted for compatibility but is no longer required; local entries are recognized by their non-HTTP URLs.

## Thumbnails

For local/SMB videos, the app first looks for a `.jpg`, `.jpeg`, `.png`, or `.webp` beside the video with the same base filename. If FFmpeg is available, it captures a frame at 10 seconds and caches it under `userdata\data\.cache\thumbnails` in the packaged application. Select FFmpeg in Settings, put it on `PATH`, or place `ffmpeg.exe` in `bin/` before packaging.

Missing thumbnails are queued automatically in two background workers. The placeholder is not cached by the browser, and the page replaces it when generation finishes. Failures and successful cache writes appear in the desktop activity log; temporary network/resource failures use a short backoff while likely-invalid media waits longer before retrying.

Real-Debrid playlist URLs are passed to FFmpeg with a 15-second network-read limit and a 35-second total process limit. FFmpeg uses HTTP range seeking when the endpoint supports it and stops after extracting one frame, so it does not intentionally download a complete remote video. If the endpoint is expired, inaccessible, or not seekable within the limits, the placeholder remains and the activity log reports why.

Only uncached thumbnails near the viewport are queued. Two FFmpeg processes may run at once, and each process is limited to one decode, filter, and encode thread. Visible pending items use one adaptive batch-status request instead of one polling timer per card. Successful cache files retain stable names and are indexed when the host starts, so refreshes, other clients, and application restarts reuse them directly.

The Real-Debrid cloud page is filtered using selected files from `/torrents/info`, not torrent display names. Only recognized video files with corresponding links are shown. Torrent details are cached in `.cache/real_debrid_details.json` and refreshed only when the list metadata changes; cold cache misses use four bounded metadata workers.

## Favorites and full Library

The main page is **Favorites**, backed by `userdata\data\playlist.json` in the packaged application. Removing a Favorite uses the card's confirmation form and atomically removes only that JSON entry; it never deletes the referenced media file.

The separate **Library** page reads `FileN=` and optional `TitleN=` entries from the PLS file selected in the wizard or Settings, in numeric playlist order. Duplicate entries remain separate, paths may point to different folders, and missing files stay visible as unavailable. The app never scans drives or folders for Library media. If no PLS file is selected, the default expected path is `userdata\data\fullNew.pls`.

Local Library playback uses opaque `/library/stream/<id>` URLs that resolve only to entries already present in the PLS file. HTTP(S) PLS entries retain their direct URLs. `fullNew.pls` is ignored by Git because it can contain private filesystem paths.

## HereSphere playback and library discovery

The main page supports HereSphere's JSON web API discovery. In HereSphere, open the main page and press the **Web Stream** button in the upper-left. HereSphere POSTs `/heresphere` at the current browser origin, receives the library index, and then loads the per-video data URLs. `/heresphere` is an API endpoint rather than a page to open directly.

API responses use `Content-Type: application/json` and `HereSphere-JSON-Version: 1`. The index uses HereSphere's `access` and `library` fields; each library entry points to video data containing `thumbnailImage`, `media`, and direct source URLs. This is the HereSphere schema, not the older DeoVR `scenes`/`video_url` schema. Projection and stereo fields are left unset when the playlist has no authoritative metadata so HereSphere can use its filename detection and user settings.

Card links remain direct `/play/<id>` links. When the page is running inside HereSphere, JavaScript marks them as download links so HereSphere sends them to its native player instead of the embedded browser. Normal browsers retain their existing link behavior. The site does not redirect card clicks to JSON and does not implement browser-driven next-video events.

Favorites and Library use separate local origins because HereSphere discovers the root `/heresphere` endpoint for the current origin. Favorites remains on `PORT` (8080 by default). Opening Library redirects the browser to `LIBRARY_PORT` (8081 by default), where `/heresphere` returns only `PLSLibrary.all()` entries. Both listeners run in the same Python process over the same Flask application and shared stores. After Web Stream imports either collection, HereSphere owns the playback queue, automatic next-video behavior, VR projection, and shuffle. `/library/heresphere` remains available for diagnostics but is not used for native discovery. `/debrid` is unchanged.

## Build the Windows executable

Activate the project's virtual environment, install the development requirements, and run:

```powershell
python -m pip install -r requirements-dev.txt
python -m PyInstaller --clean --noconfirm HomeMediaManager.spec
```

Distribute the complete `dist/HomeMediaManager` folder. Users configure it through the first-run window, and the application creates its portable `userdata` folder on first launch. The folder-based build avoids a second one-file extraction process, has no console window, starts both listeners automatically after setup, and bundles templates/static assets. Private playlists, configuration, caches, thumbnails, and import/source lists are not bundled at build time. If `bin/ffmpeg.exe` exists at build time, the spec includes it automatically; otherwise FFmpeg can be selected in Settings or found through `PATH`. `ffprobe.exe` is not currently required. Review FFmpeg licensing and distribution terms before shipping it.

## Test

```powershell
pytest -q
```
