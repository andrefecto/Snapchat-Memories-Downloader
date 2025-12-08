# Snapchat Memories Downloader

Download all your Snapchat memories from a data export, preserving metadata
(date, location) and organizing files sequentially.

**Two ways to use:**

1. **🌐 [Web Version](https://andrefecto.github.io/snapchat-memories-downloader/)**
   - Upload your HTML file in browser (100% private, client-side)
2. **🐍 Python Script** - Command-line tool for local processing
   (instructions below)

## Features

- Downloads all memories from `memories_history.html`
- Sequential naming: `01.jpg`, `02.mp4`, `03.jpg`, etc.
- Preserves metadata: date, GPS coordinates, media type
- **Sets file timestamps to match original capture date** (Python only)
- Handles ZIP files with overlays (extracts to `-main` and `-overlay` files)
- Saves all metadata to `metadata.json`
- **Resume/Retry support** - Pick up where you left off or retry failed downloads
- Incremental metadata updates - Track download progress in real-time

---

## 🌐 Web Version (Easiest)

Visit the [web version](https://andrefecto.github.io/snapchat-memories-downloader/)
and upload your `memories_history.html` file. Everything runs in your browser -
your data never leaves your device!

### Resume/Retry

If your download gets interrupted or has failures:

1. Extract the `metadata.json` file from your downloaded ZIP
2. Go back to the web version
3. Upload both `memories_history.html` AND `metadata.json`
4. It will skip already-downloaded files and retry failed ones

**Note:** The web version downloads everything as a ZIP file. File timestamps
will be the download date, not the original capture date.

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

For memories with overlays:

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

## Requirements

- Python 3.7+
- `requests` library (installed automatically by setup.sh)

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

## 📦 Publishing to GitHub

### Initial Setup

1. Create a new GitHub repository
2. Initialize git in this directory:

   ```bash
   git init
   git add .
   git commit -m "Initial commit: Snapchat Memories Downloader"
   ```

3. Add your GitHub repository as remote:

   ```bash
   git remote add origin https://github.com/andrefecto/snapchat-memories-downloader.git
   git branch -M main
   git push -u origin main
   ```

### Enable GitHub Pages

1. Go to your repository on GitHub
2. Click **Settings** → **Pages**
3. Under "Source", select **Deploy from a branch**
4. Choose branch: **main** and folder: **/docs**
5. Click **Save**
6. Your web version will be live at:
   `https://andrefecto.github.io/snapchat-memories-downloader/`

### Automated Dependency Updates

This repository includes Dependabot configuration to automatically update
dependencies:

- **Python dependencies**: Checks weekly for updates to `requests`
- **GitHub Actions**: Keeps CI/CD workflows up to date

Dependabot will automatically create pull requests when updates are available.
Simply review and merge them to keep dependencies current.

---

## 🔒 Privacy & Security

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

## 📄 License

MIT License - feel free to use and modify as needed.

## 🤝 Contributing

Issues and pull requests are welcome!

---

**Made by [@andrefecto](https://github.com/andrefecto)**
