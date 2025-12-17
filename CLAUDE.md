# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Dual-implementation Snapchat Memories downloader with web and Python versions that parse `memories_history.html` exports to download media with metadata preservation.

**Web Version**: `docs/index.html` (2743 lines) - Client-side processing with FFmpeg.wasm for video overlays
**Python Version**: `download_memories.py` (1608 lines) - CLI tool with FFmpeg subprocess for video overlays

Both versions share identical architecture but differ in implementation details and feature sets.

## Development Commands

### Python Setup
```bash
./setup.sh                    # Create venv, install dependencies
source venv/bin/activate      # Activate environment
python3 download_memories.py --test  # Quick test (first 3 files)
deactivate                    # Exit venv
```

### Common Python Usage
```bash
# Basic download
python3 download_memories.py

# Resume interrupted download
python3 download_memories.py --resume

# Retry only failed items
python3 download_memories.py --retry-failed

# Merge overlays (requires FFmpeg for videos)
python3 download_memories.py --merge-overlays

# Timestamp-based naming instead of sequential
python3 download_memories.py --timestamp-filenames

# Process only videos with overlay merging
python3 download_memories.py --videos-only --merge-overlays

# Custom output directory
python3 download_memories.py -o ./my-memories
```

### Web Development
No build process - edit `docs/index.html` directly. FFmpeg.wasm artifacts are auto-synced via GitHub Actions when `package.json` dependencies update.

To manually sync FFmpeg artifacts:
```bash
npm install
# Copy from node_modules/@ffmpeg/ to docs/ffmpeg/
```

## Core Architecture

### Dual Implementation Pattern
Both versions implement the same workflow but with different runtime environments:
- **Web**: Client-side browser processing (privacy-first, no upload)
- **Python**: CLI processing (advanced features, FFmpeg CLI, full EXIF)

### Data Flow
1. Parse `memories_history.html` → extract URLs, dates, GPS coordinates
2. Initialize metadata.json with all memories as "pending"
3. For each memory:
   - Download from Snapchat URL
   - Detect ZIP files (magic bytes `PK`) containing overlays
   - Check MD5 hash for duplicates (during download, not post-process)
   - Extract `-main` and `-overlay` files from ZIP or save single file
   - Add EXIF metadata to images (GPS + timestamp)
   - Optionally merge overlays (instant for images, 1-5min for videos)
   - Set file modification time to capture date
   - Update metadata state: `pending` → `in_progress` → `success`/`failed`
4. Optional post-processing: join multi-snap videos, deduplicate

### Metadata State Machine
`metadata.json` tracks download progress enabling crash recovery:
- **States**: `pending`, `in_progress`, `success`, `failed`, `skipped`
- **Saved after every download** for resume capability
- **Same format** works for both web and Python versions
- Stores file paths, sizes, types (main/overlay/merged/single/duplicate)

### Overlay Processing Architecture

**Two strategies based on media type:**

**Images** (fast, instant):
- Python: Pillow library for alpha compositing
- Web: Canvas API for alpha compositing
- Preserves original format (JPEG, PNG, WebP, GIF, BMP, TIFF)

**Videos** (slow, 1-5 minutes each):
- Python: FFmpeg subprocess with filter chains
- Web: FFmpeg.wasm (WebAssembly port)
- FFmpeg filter: `[1:v]scale=WxH[ovr];[0:v][ovr]overlay=shortest=1[outv]`
- Handles both image overlays (with `-loop 1`) and video overlays

**Deferred Processing Pattern** (`--defer-video-overlays`):
- Downloads all content first (main + overlay files saved separately)
- Processes video merges at end in batch
- Prevents memory buildup during initial downloads
- Web version: separate ZIP per deferred video

### Memory Management (Web Only)
FFmpeg.wasm has memory leak issues, so the web version implements:
- **Auto-reset**: Terminates and reinitializes FFmpeg every N videos (5/10/20/unlimited)
- **Memory monitoring**: Chrome `performance.memory` API warns at >60% usage
- **GC pauses**: 2-second pause between deferred video processing for garbage collection

### Duplicate Detection
Runs **during download** (not post-processing) to save bandwidth:
1. Check file size match
2. Compute MD5 hash of new download
3. Compare with existing files
4. Skip download if duplicate found

Python also has `--remove-duplicates` for retroactive scanning.

### Multi-Snap Joining (Python Only)
Detects videos within 10-second time windows (indicates multi-snap stories):
- Uses FFmpeg concat demuxer: `-f concat -safe 0 -i concat.txt -c copy`
- No re-encoding (fast, lossless)
- Deletes originals after successful join

## Key Technologies

**Python Dependencies**:
- `requests` - HTTP downloads with custom User-Agent
- `Pillow` - Image overlay compositing, format preservation
- `piexif` - EXIF metadata encoding (GPS + dates in JPEG/PNG/WebP)
- `subprocess` - FFmpeg process invocation
- `zipfile` - Extract overlay ZIP files
- `hashlib` - MD5 for duplicate detection

**Web Dependencies**:
- `JSZip` 3.10.1 - ZIP file handling
- `piexifjs` 1.0.6 - EXIF for JPEG only (browser limitation)
- `FFmpeg.wasm` - WebAssembly FFmpeg (lazy-loaded)
- Vanilla JavaScript, HTML5 File API

## File Structure

```
.
├── download_memories.py      # Python CLI (1608 lines)
├── docs/
│   ├── index.html            # Web version (2743 lines, single file)
│   ├── ffmpeg/               # FFmpeg.wasm artifacts (auto-synced)
│   │   ├── ffmpeg.js
│   │   ├── ffmpeg-core.js
│   │   └── ffmpeg-core.wasm
│   └── .nojekyll             # Disable Jekyll for GitHub Pages
├── test_files/               # Test HTML exports
├── requirements.txt          # Python dependencies
├── setup.sh                  # Venv setup script
└── package.json              # FFmpeg.wasm dependencies only
```

## Non-Obvious Implementation Details

### Magic Byte Detection
Both versions detect file types by reading first bytes, not trusting extensions/MIME:
- ZIP: `50 4B` (PK)
- JPEG: `FF D8 FF`
- PNG: `89 50 4E 47`
- WebP: `52 49 46 46 ... 57 45 42 50`
- GIF: `47 49 46 38`
- MP4: `66 74 79 70` at offset 4 (ftyp box)

### Video Compression in ZIP
Videos stored with `compression: "STORE"` (no DEFLATE) to prevent browser playback issues:
```javascript
zipOptions.compression = "STORE";  // Web version
```
DEFLATE compression on already-compressed video can break browser decoders.

### EXIF Coordinate Conversion
GPS coordinates converted from decimal to DMS (degrees, minutes, seconds):
```python
def decimal_to_dms(decimal):
    degrees = int(abs(decimal))
    minutes_float = (abs(decimal) - degrees) * 60
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60
    return ((degrees, 1), (minutes, 1), (int(seconds * 100), 100))
```

### Graceful Degradation
All optional dependencies degrade gracefully:
- No FFmpeg → videos save as separate `-main`/`-overlay` files
- No Pillow → overlay merging disabled
- No piexif → EXIF metadata skipped
- Never fails due to missing optional features

### File Timestamp Preservation
Both versions set file modification time to Snapchat capture date:
- Python: `os.utime(filepath, (timestamp, timestamp))`
- Web: `zip.file(filename, data, {date: fileDate})`

Enables sorting by capture date in file explorers.

## Testing

**Python Quick Test**:
```bash
python3 download_memories.py --test  # Downloads first 3 files only
```

**Validation Features**:
- MP4 signature validation (checks for ftyp/mdat/moov/wide magic bytes)
- File size warnings (< 100 bytes indicates invalid/expired URL)
- EXIF metadata verification
- Browser console logging (Web version)

## FFmpeg Sync (GitHub Actions)

`.github/workflows/sync-ffmpeg.yml` automatically syncs FFmpeg.wasm when dependencies update:
1. Triggered on Dependabot PRs modifying `package.json`
2. Runs `npm install`
3. Copies artifacts from `node_modules/@ffmpeg/` to `docs/ffmpeg/`
4. Auto-commits changes

This keeps web version standalone without requiring npm build during deployment.

## Common Pitfalls

1. **FFmpeg.wasm memory leaks**: Always reset instance after N videos in web version
2. **ArrayBuffer slicing**: Use `.slice().buffer` not `.buffer` directly when extracting video data from Uint8Array views (prevents garbage bytes)
3. **ZIP compression**: Never use DEFLATE on videos in web version (breaks playback)
4. **EXIF limitations**: Web version only supports JPEG (piexifjs limitation)
5. **Expired URLs**: Snapchat URLs expire after ~1 year, validate file size and magic bytes
6. **Multi-snap detection**: 10-second threshold is empirically derived, may need tuning

## Feature Matrix

| Feature | Python | Web |
|---------|--------|-----|
| Overlay merge (images) | ✅ Pillow | ✅ Canvas API |
| Overlay merge (videos) | ✅ FFmpeg CLI | ✅ FFmpeg.wasm |
| EXIF metadata | ✅ Full (GPS + date) | ⚠️ JPEG only |
| Resume/retry | ✅ | ✅ |
| Duplicate detection | ✅ During download | ✅ During download |
| Multi-snap joining | ✅ FFmpeg | ❌ |
| Custom output dir | ✅ | ❌ (ZIP download) |
| Batch downloads | ❌ | ✅ (50/100/200/all) |
| Timestamp filenames | ✅ | ❌ (sequential only) |
| Deferred video overlays | ✅ | ✅ |

## Privacy Architecture

**Client-side processing** ensures privacy:
- Web version: All processing in browser, no server upload
- Python version: All processing on local machine
- No tracking, no analytics, no external API calls
- Open source for audit
