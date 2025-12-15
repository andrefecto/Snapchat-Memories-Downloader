# Free Snapchat Memories Downloader

**⚠️ Save your memories before Snapchat starts charging for storage!**

Download ALL your Snapchat memories completely FREE - don't pay for paid
services. This tool runs entirely on your computer, preserving all metadata
(dates, GPS locations) while keeping your data 100% private.

**Two ways to use:**

1. **🌐 [Web Version](https://andrefecto.github.io/Snapchat-Memories-Downloader/)**
   - Upload your HTML file in browser (100% private, client-side)
2. **🐍 Python Script** - Command-line tool for local processing
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
- **Preserves ALL metadata**: dates, GPS coordinates, media type
- **Embeds EXIF metadata into images** - GPS coordinates and dates show up in Photos apps
- **Sets file timestamps to match original capture date**
- Handles ZIP files with overlays (extracts to `-main` and `-overlay` files)
- **Optional overlay merging** - Combine overlay on top of main content (images and videos)
  - Images: Fast, instant processing (supports JPG, PNG, WebP, GIF, BMP, TIFF)
  - Videos: Requires FFmpeg, may take 1-5 minutes per video
- **Merge existing files** - Retroactively merge already-downloaded `-main`/`-overlay` files without re-downloading
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

## Setup

1. Run the setup script:

   ```bash
   ./setup.sh
   ```

This will:

- Create a Python virtual environment
- Install required dependencies (requests)

### Optional: FFmpeg for Video Overlay Merging

To enable video overlay merging, install FFmpeg:

**macOS:**
```bash
brew install ffmpeg
```

**Ubuntu/Debian:**
```bash
sudo apt-get install ffmpeg
```

**Windows (via Chocolatey):**
```bash
choco install ffmpeg
```

**Windows (manual):**
- Download from [https://ffmpeg.org/download.html](https://ffmpeg.org/download.html)

**Note:** FFmpeg is only required if you want to merge video overlays. The tool works without it (videos will be saved as separate `-main` and `-overlay` files).

## Usage

### Activate Virtual Environment

```bash
source venv/bin/activate
```

### Test Mode (Download first 3 files)

```bash
python download_memories.py --test
```

### Full Download

```bash
python download_memories.py
```

### Custom HTML File Path

By default, the script looks for `html/memories_history.html`. You can specify a different path:

**Specify the HTML file directly:**
```bash
python download_memories.py /path/to/your/memories_history.html
```

**Or specify the folder containing the HTML file:**
```bash
python download_memories.py /path/to/html/folder
```

**Combine with other flags:**
```bash
python download_memories.py ~/Downloads/snapchat-data/html --merge-overlays
```

### Resume Interrupted Download

If your download gets interrupted, resume from where you left off:

```bash
python download_memories.py --resume
```

### Retry Failed Downloads

To retry only the failed downloads:

```bash
python download_memories.py --retry-failed
```

### Merge Overlays

To merge overlay files on top of main files (images and videos):

```bash
python download_memories.py --merge-overlays
```

This will composite the `-overlay` content on top of the `-main` content and save only the merged result.

**Requirements:**
- Images: No additional requirements (uses Pillow)
- Videos: Requires FFmpeg (see installation instructions above)

**Performance:**
- Images: Fast, instant processing
- Videos: May take 1-5 minutes per video depending on length and resolution

If FFmpeg is not installed, videos will be saved as separate `-main` and `-overlay` files.

### Re-process Videos Only

If you already downloaded your memories before video merging was available, you can re-process only the videos:

```bash
python download_memories.py --videos-only --merge-overlays
```

This will:
- Skip all pictures (already downloaded)
- Re-download and process only videos
- Merge video overlays if `--merge-overlays` is specified

### Re-process Pictures Only

Similarly, to re-process only pictures:

```bash
python download_memories.py --pictures-only --merge-overlays
```

This will:
- Skip all videos (already downloaded)
- Re-download and process only pictures
- Merge picture overlays if `--merge-overlays` is specified

### Merge Already-Downloaded Files

If you already have `-main` and `-overlay` files downloaded and want to merge them without re-downloading:

```bash
python download_memories.py --merge-existing ./memories
```

This will:
- Scan the specified folder for `-main` and `-overlay` file pairs
- Merge them using FFmpeg (videos) or Pillow (images)
- Create merged files WITHOUT deleting the original `-main`/`-overlay` files
- Support all image formats: JPG, PNG, WebP, GIF, BMP, TIFF

**Use case**: You previously downloaded your memories without `--merge-overlays`, and now you want to create merged versions while keeping the original separate files.

**Note**: To get `-main` and `-overlay` files in the first place, download without the `--merge-overlays` flag.

### View All Options

To see all available options:

```bash
python download_memories.py --help
```

### Deactivate Virtual Environment

```bash
deactivate
```

## Output

### Files

- All memories are saved to the `memories/` directory
- Named sequentially: `01.jpg`, `02.mp4`, `03.jpg`, etc.
- Files with overlays are extracted as `XX-main.ext` and `XX-overlay.ext`

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

## File Structure

```text
.
├── docs/
│   └── index.html               # Web version (GitHub Pages)
├── html/
│   └── memories_history.html    # Snapchat export HTML file (not included)
├── memories/                     # Downloaded files (created by script)
│   ├── 01.mp4
│   ├── 02.jpg
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

### Videos Download as Blank/Black Files

If your videos are downloading but show as blank or black when you try to play them:

**Cause:** The download URLs from Snapchat may have expired, or the server is returning error pages instead of video files.

**How to tell:**
- The tool will now show warnings like: `WARNING: File may not be a valid video (invalid MP4 signature)`
- Very small file sizes (under 100 bytes) indicate invalid files
- The log will show the first bytes of the file to help diagnose the issue

**Solutions:**
1. **Request a fresh data export from Snapchat** - URLs expire after some time
2. **Download sooner** - Process your Snapchat export as soon as you receive it
3. **Check the warnings** - The tool will now alert you to potentially invalid files during download

**Note:** The tool validates video files and warns you about potential issues, but it cannot fix expired or invalid URLs from Snapchat's servers.

### FFmpeg Not Found (Python)

If you see: `Warning: ffmpeg not found. Video overlay merging will be disabled.`

**Solution:** Install FFmpeg:
- **macOS**: `brew install ffmpeg`
- **Ubuntu/Debian**: `sudo apt-get install ffmpeg`
- **Windows**: `choco install ffmpeg` or download from [ffmpeg.org](https://ffmpeg.org/download.html)

The tool will still work without FFmpeg - videos will be saved as separate `-main` and `-overlay` files.

### Web Version: FFmpeg.wasm CORS Error

If you see security/CORS errors in the browser console related to FFmpeg, this has been fixed in the latest version. Hard refresh the page: `Ctrl+Shift+R` (Windows/Linux) or `Cmd+Shift+R` (Mac).

## 📄 License

MIT License - feel free to use and modify as needed.

## 🤝 Contributing

Issues and pull requests are welcome!

---

**Made by [@andrefecto](https://github.com/andrefecto)**
