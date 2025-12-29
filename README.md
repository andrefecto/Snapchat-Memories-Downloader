# Free Snapchat Memories Downloader

**⚠️ Save your memories before Snapchat starts charging for storage!**

Download ALL your Snapchat memories completely FREE - don't pay for paid
services. This tool runs entirely on your computer, preserving all metadata
(dates, GPS locations) while keeping your data 100% private.

**Three ways to use:**

1. **🌐 [Web Version](https://andrefecto.github.io/Snapchat-Memories-Downloader/)**
   - Upload your HTML file in browser (100% private, client-side)
2. **🐍 Python Script** - Command-line tool for local processing
   (instructions below)
3. **Desktop GUI (PyQt6)** - Windowed interface for the Python script
   (instructions below)

## 📥 Getting Your Snapchat Data

Before using this tool, you need to download your data from Snapchat:

1. **Login** to [Snapchat's website](https://accounts.snapchat.com/)
2. Click the **menu** in the top left corner
   - **Mobile**: Tap "Accounts"
   - **Desktop**: Click "Account Settings"
3. Click **"My Data"**
4. Select the data you want to download:
   - ✅ **Memories** (required for this tool)
   - ✅ **Chat Media** (optional - now separate from Memories)
   - ✅ **Shared Stories** (optional - now separate from Memories)
   - Review all 8 types and select what you need
5. Click **"Submit Request"**
6. Wait for Snapchat to email you (can take 24-48 hours)
7. Download the ZIP file from the email
8. Extract it - you'll find `memories_history.html` in the `html/` folder

**Note:** Chat Media and Shared Stories used to be included with Memories,
but are now separate options at the bottom of the list.

[More info on Reddit](https://www.reddit.com/r/techsupport/comments/18mkfvv/is_there_a_way_of_exporting_all_snapchat/)

## Why Use This FREE Tool?

### 💰 Don't Pay for What Should Be Free

- **100% FREE** - Other services charge money. This costs nothing.
- **Save Before Fees** - Download now before Snapchat implements storage charges
- **Open Source** - Inspect the code yourself on GitHub

### 🔒 Privacy & Security

- **No Upload Required** - Your data never leaves your computer
- **Runs Locally** - Everything processes in your browser or on your machine
- **No Account Needed** - No signup, no email, no tracking
- **Safer Than Paid Services** - Don't trust your memories to third-party servers

### ✨ Features

- Downloads all memories from `memories_history.html`
- Sequential naming: `01.jpg`, `02.mp4`, `03.jpg`, etc.
- **Timestamp-based filenames** (Python) - Name files as `YYYY.MM.DD-HH-MM-SS.ext` for easy sorting by date
- **Preserves ALL metadata**: dates, GPS coordinates, media type
- **Embeds EXIF metadata into images** - GPS coordinates and dates show up in Photos apps
- **Timezone-aware metadata** - Converts UTC timestamps to local time based on GPS coordinates
- **Sets file timestamps to match original capture date**
- Handles ZIP files with overlays (extracts to `-main` and `-overlay` files)
- **Optional overlay merging** - Combine overlay on top of main content (images and videos)
  - Images: Fast, instant processing (supports JPG, PNG, WebP, GIF, BMP, TIFF)
  - Videos: Requires FFmpeg, may take 1-5 minutes per video
- **Merge existing files** - Retroactively merge already-downloaded `-main`/`-overlay` files without re-downloading
- **Duplicate detection** (Python) - Automatically find and remove duplicate files based on MD5 hash, filesize, and date
- **Multi-snap joining** (Python) - Automatically detect and concatenate videos taken within 10 seconds of each other
- Saves complete `metadata.json` with all information
- **Resume/Retry support** - Pick up where you left off or retry failed downloads
- Incremental metadata updates - Track download progress in real-time

---

## 🌐 Web Version (Easiest)

Visit the [web version](https://andrefecto.github.io/Snapchat-Memories-Downloader/)
and upload your `memories_history.html` file. Everything runs in your browser -
your data never leaves your device!

### Options

- **Merge overlays**: Combine overlay content with main files (images and videos)
  - Images: Fast processing in browser
  - Videos: Uses FFmpeg.wasm (slower, may take 1-5 minutes per video)
- **Videos only**: Only download and process videos (useful for re-processing with overlay merging)
- **Pictures only**: Only download and process pictures (useful for re-processing with overlay merging)
- **Batch size**: Choose how many files per ZIP (50, 100, 200, or all in one)
  - Batching helps with large collections and allows easier recovery if interrupted
  - Each batch is downloaded as a separate ZIP: `snapchat-memories-batch-1.zip`, `snapchat-memories-batch-2.zip`, etc.

### Resume/Retry

If your download gets interrupted or has failures:

1. Extract the `metadata.json` file from any downloaded batch ZIP
2. Go back to the web version
3. Upload both `memories_history.html` AND `metadata.json`
4. It will skip already-downloaded files and retry failed ones

**Note:** The web version sets timestamps within the ZIP file and embeds EXIF metadata into images
(GPS and capture dates). When you extract the files, most modern extraction tools will preserve these timestamps.

---

## 🐍 Python Script

### Quick Start

```bash
# 1. Setup (one-time)
./setup.sh
source venv/bin/activate

# 2. Download your memories
python download_memories.py

# 3. Done! Files saved to ./memories/
```

### Setup

1. Run the setup script:

   ```bash
   ./setup.sh
   ```

   This creates a virtual environment and installs dependencies.

2. **(Optional)** Install FFmpeg for video overlay merging:

   | Platform              | Command                                                      |
   |-----------------------|--------------------------------------------------------------|
   | macOS                 | `brew install ffmpeg`                                        |
   | Ubuntu/Debian         | `sudo apt-get install ffmpeg`                                |
   | Windows (Chocolatey)  | `choco install ffmpeg`                                       |
   | Windows (manual)      | Download from [ffmpeg.org](https://ffmpeg.org/download.html) |

   **Note:** FFmpeg is only needed for merging video overlays.
   Without it, videos are saved as separate `-main` and `-overlay` files.

### Desktop GUI (PyQt6)

If you prefer a windowed app, run:

```bash
source venv/bin/activate
python snapchat_memories_gui.py
```

The GUI exposes all CLI options with explanations. It still requires
`memories_history.html` and uses `metadata.json` in the output folder for
resume/retry.

### Basic Usage

**Activate environment** (do this each time you open a new terminal):

```bash
source venv/bin/activate
```

**Test mode** (download first 3 files only):

```bash
python download_memories.py --test
```

**Full download**:

```bash
python download_memories.py
```

**Custom HTML file path**:

```bash
# Direct file path
python download_memories.py /path/to/memories_history.html

# Or folder containing the HTML
python download_memories.py /path/to/html/folder
```

**Resume/Retry**:

```bash
# Resume interrupted download
python download_memories.py --resume

# Retry only failed downloads
python download_memories.py --retry-failed
```

### Advanced Features

#### 🌍 Timezone-Aware Metadata

Convert UTC timestamps to local timezone based on GPS coordinates:

```bash
python download_memories.py --local-timezone
```

**What this does:**

- ✅ Automatically detects local timezone from GPS coordinates
- ✅ Converts all EXIF timestamps from UTC to local time
- ✅ Embeds modern EXIF offset tags (`OffsetTime`, `OffsetTimeOriginal`, `OffsetTimeDigitized`)
- ✅ Updates video metadata with local time and timezone info
- ✅ Photos show correct local capture time in photo library apps

**Example:**

If a photo was taken at 15:30 UTC in New York (EST = UTC-5), it will be stored as:
- EXIF timestamp: `2024:12:15 10:30:00`
- EXIF offset: `-05:00`

**Requirements:**

- `timezonefinder` library (installed by setup.sh)
- `pytz` library (installed by setup.sh)
- Valid GPS coordinates in Snapchat data

**Special handling:**

- Czech Republic coordinates automatically use `Europe/Prague` timezone
- Graceful fallback to UTC if GPS data is missing
- Works with all image formats (JPEG, PNG, WebP, etc.)

**Combine with other features:**

```bash
# Timezone-aware + overlay merging + timestamp filenames
python download_memories.py --local-timezone --merge-overlays --timestamp-filenames

# Timezone-aware for re-processing existing videos
python download_memories.py --videos-only --local-timezone
```

#### Merge Overlays

Combine overlay files with main content:

```bash
python download_memories.py --merge-overlays
```

- **Images:** Fast, instant processing
- **Videos:** Requires FFmpeg, 1-5 minutes per video

**Re-process specific media type:**

```bash
# Videos only
python download_memories.py --videos-only --merge-overlays

# Pictures only
python download_memories.py --pictures-only --merge-overlays
```

**Merge already-downloaded files:**

```bash
python download_memories.py --merge-existing ./memories
```

Creates merged versions without deleting originals.

#### Custom Output Directory

```bash
python download_memories.py -o /path/to/output
```

Saves files to a custom location instead of `./memories/`

#### Timestamp-Based Filenames

```bash
python download_memories.py --timestamp-filenames
```

Names files as `2024.11.30-14-30-45.jpg` instead of `01.jpg`

- ✅ Files sort by date in file managers
- ✅ Easy to identify when memories were taken

#### Parallel Downloads

```bash
python download_memories.py --threads 4
```

Downloads multiple memories at once for faster throughput. Use `--threads 1`
for sequential mode. Higher values use more bandwidth/CPU (especially if
video overlay merging is enabled).

#### Remove Duplicate Files

```bash
python download_memories.py --remove-duplicates
```

Checks MD5 hash before saving each file and skips duplicates.

- ✅ Saves bandwidth - doesn't re-download existing files
- ✅ Perfect for resuming or re-running the script
- ✅ Handles cases where Snapchat exports the same memory multiple times

#### Join Multi-Snap Videos

```bash
python download_memories.py --join-multi-snaps
```

Detects videos taken within 10 seconds and concatenates them.

- ✅ Stitches long stories back together
- ✅ Requires FFmpeg
- ✅ Deletes originals after successful join

### Combining Features

```bash
# All the features!
python download_memories.py \
  -o ~/Desktop/memories \
  --local-timezone \
  --timestamp-filenames \
  --remove-duplicates \
  --merge-overlays \
  --join-multi-snaps

# Resume with duplicate detection and timezone support
python download_memories.py --resume --remove-duplicates --local-timezone

# Re-process videos with timezone and overlay merging
python download_memories.py --videos-only --local-timezone --merge-overlays
```

### Getting Help

```bash
# View all options
python download_memories.py --help

# When done, deactivate virtual environment
deactivate
```

## Output

### Files

- All memories are saved to the `memories/` directory (or custom directory specified with `--output`)
- **Sequential naming (default)**: `01.jpg`, `02.mp4`, `03.jpg`, etc.
- **Timestamp naming (with `--timestamp-filenames`)**: `2024.11.30-14-30-45.jpg`, `2024.12.15-09-22-13.mp4`, etc.
- Files with overlays are extracted as `XX-main.ext` and `XX-overlay.ext` (or `YYYY.MM.DD-HH-MM-SS-main.ext` with timestamp naming)

### Metadata

A `metadata.json` file is created with information about each memory:

```json
{
  "number": 1,
  "date": "2025-11-30 00:31:09 UTC",
  "media_type": "Video",
  "latitude": "44.273846",
  "longitude": "-105.43944",
  "url": "https://...",
  "status": "success",
  "files": [
    {
      "path": "01.mp4",
      "size": 4884414,
      "type": "single"
    }
  ]
}
```

For memories with overlays (not merged):

```json
{
  "number": 42,
  "date": "2024-06-15 14:30:00 UTC",
  "media_type": "Image",
  "latitude": "40.7128",
  "longitude": "-74.0060",
  "url": "https://...",
  "status": "success",
  "files": [
    {
      "path": "42-main.jpg",
      "size": 450123,
      "type": "main"
    },
    {
      "path": "42-overlay.jpg",
      "size": 125456,
      "type": "overlay"
    }
  ]
}
```

For merged overlays (when using `--merge-overlays` flag):

```json
{
  "number": 42,
  "date": "2024-06-15 14:30:00 UTC",
  "media_type": "Image",
  "latitude": "40.7128",
  "longitude": "-74.0060",
  "url": "https://...",
  "status": "success",
  "files": [
    {
      "path": "42.jpg",
      "size": 487932,
      "type": "merged"
    }
  ]
}
```

## Requirements

- Python 3.7+
- `requests` library (installed automatically by setup.sh)
- `Pillow` library (for overlay merging and EXIF metadata, installed automatically by setup.sh)
- `piexif` library (for EXIF metadata embedding, installed automatically by setup.sh)
- `timezonefinder` library (for timezone detection, installed automatically by setup.sh)
- `pytz` library (for timezone handling, installed automatically by setup.sh)

**Optional:**
- FFmpeg (for video overlay merging and multi-snap joining)

## File Structure

```text
.
├── docs/
│   └── index.html               # Web version (GitHub Pages)
├── html/
│   └── memories_history.html    # Snapchat export HTML file (not included)
├── memories/                     # Downloaded files (default output directory, created by script)
│   ├── 01.mp4                    # Sequential naming (default)
│   ├── 02.jpg                    # or 2024.11.30-14-30-45.jpg (with --timestamp-filenames)
│   ├── 03.jpg
│   └── metadata.json
├── venv/                        # Python virtual environment
├── download_memories.py         # Main Python script
├── requirements.txt             # Python dependencies
├── setup.sh                     # Setup script
├── .gitignore                   # Git ignore file
└── README.md                    # This file
```

## How Resume/Retry Works

### Python Script

1. **First Run**: Creates `metadata.json` with all memories marked as "pending"
2. **During Download**: Updates each item to "in_progress" → "success" or "failed"
3. **Interrupted?** Run with `--resume` to continue from where you left off
4. **Failures?** Run with `--retry-failed` to retry only failed items

The metadata.json is saved after EACH download, so you can safely interrupt
and resume at any time!

### Web Version

1. Upload `memories_history.html` to start
2. Download includes `metadata.json` with status tracking
3. To resume: Upload both HTML and `metadata.json`
4. Skips successful downloads, retries pending/failed ones

## Notes

- Downloads may take a while depending on file sizes and internet speed
- Failed downloads are logged in the metadata with error messages
- The script uses sequential numbering starting from 01
- **File timestamps are set to match the original capture date**, so when you
  sort by date modified in Finder/Explorer, you'll see them in chronological
  order by when they were taken, not when they were downloaded
- **With `--local-timezone` flag**: EXIF metadata shows local time of capture,
  not UTC. This helps photo libraries display memories with correct local timestamps.

---

## 🔐 How Your Data Stays Private

- **Web Version**: All processing happens in your browser. No data is sent to
  any server.
- **Python Script**: Everything runs locally on your computer. No network
  requests except to Snapchat's download URLs.
- Your memories are downloaded directly from Snapchat's servers using the URLs
  in your data export.

---

## ☕ Support

If this tool helped you recover your memories, consider [buying me a coffee](https://buymeacoffee.com/andrefecto)!

---

## 🔧 Troubleshooting

### Videos Won't Play on Windows - Need HEVC Codec?

**Problem:** Videos download successfully but won't play in Windows Media Player or Photos app.

**Cause:** Many Snapchat videos (especially from iPhones) use HEVC (H.265) codec, which Windows doesn't include by default.

**Solution:**

1. **Free Method (Recommended):** Install "HEVC Video Extensions from Device Manufacturer" from [Microsoft Store](https://apps.microsoft.com/detail/9n4wgh0z6vhq) (FREE)
2. **Alternative:** Install [VLC Media Player](https://www.videolan.org/vlc/) - plays HEVC videos without codec installation
3. **Verify:** Right-click video → Properties → Details tab → check if "Video codec" shows HEVC or H265

**Note:** This is NOT a bug with the downloader - the videos are downloading correctly. It's a Windows limitation that requires the HEVC codec to be installed separately.

### Videos Download as Blank/Black Files

If your videos are downloading but show as blank or black when you try to play them:

**Cause:** The download URLs from Snapchat may have expired,
or the server is returning error pages instead of video files.

**How to tell:**

- The tool will show warnings like:
  `WARNING: File may not be a valid video (invalid MP4 signature)`
- Very small file sizes (under 100 bytes) indicate invalid files
- The log will show the first bytes of the file to help diagnose the issue

**Solutions:**

1. **Request a fresh data export from Snapchat**
   - URLs expire after some time
2. **Download sooner**
   - Process your Snapchat export as soon as you receive it
3. **Check the warnings**
   - The tool will alert you to potentially invalid files during download

**Note:** The tool validates video files and warns you about potential issues,
but it cannot fix expired or invalid URLs from Snapchat's servers.

### FFmpeg Not Found (Python)

If you see: `Warning: ffmpeg not found. Video overlay merging will be disabled.`

**Solution:** Install FFmpeg:

- **macOS**: `brew install ffmpeg`
- **Ubuntu/Debian**: `sudo apt-get install ffmpeg`
- **Windows**: `choco install ffmpeg` or download from
  [ffmpeg.org](https://ffmpeg.org/download.html)

The tool will still work without FFmpeg - videos will be saved as separate
`-main` and `-overlay` files.

### Web Version: FFmpeg.wasm CORS Error

If you see security/CORS errors in the browser console related to FFmpeg, this has been fixed in the latest version. Hard refresh the page: `Ctrl+Shift+R` (Windows/Linux) or `Cmd+Shift+R` (Mac).

## 📄 License

MIT License - feel free to use and modify as needed.

## 🤝 Contributing

Issues and pull requests are welcome!

---

**Made by [@andrefecto](https://github.com/andrefecto)**
