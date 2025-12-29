# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2024-12-29

### 🔥 Critical Bug Fixes
- **Fixed Apple Photos video import showing wrong timestamps** - Apple Photos ignores generic `creation_time` metadata and requires QuickTime-specific fields. Videos now import at correct local time when using Python version with GPS-based timezone conversion.
- **Fixed joined multi-snap videos losing GPS and date metadata** - FFmpeg concat operations now preserve GPS coordinates and timestamps by reading from `metadata.json` and re-applying QuickTime metadata after joining.
- **Fixed web version videos missing GPS coordinates** - Added FFmpeg.wasm-based metadata insertion to all video save paths. Videos now show location in Apple Photos map view.

### ✨ New Features
- **GPS metadata support for web version videos** - Web version now adds GPS coordinates to MP4 files using FFmpeg.wasm (UTC timestamps only, Python version recommended for timezone conversion)
- **QuickTime metadata fields** - Both Python and web versions now set Apple-specific metadata fields (`com.apple.quicktime.creationdate`, `com.apple.quicktime.make`, `com.apple.quicktime.location.ISO6709`) for full Apple Photos compatibility

### 🐛 Bug Fixes
- **Improved FFmpeg merge error diagnostics** - Enhanced error reporting shows exit codes, file sizes, and extended stderr output to help troubleshoot video merge failures
- Fixed metadata preservation in joined videos (GPS coordinates and timestamps now retained)
- Fixed Apple Photos time zone display issues in video imports

### 📚 Documentation
- Updated code documentation explaining Apple Photos metadata requirements
- Added technical notes on QuickTime vs generic metadata field precedence
- Clarified web version limitations (UTC-only timestamps)

### 🔧 Technical Details

**Metadata Fields Now Set (Videos):**
- `creation_time` - Generic ISO 8601 UTC timestamp
- `location` - Generic ISO 6709 GPS format
- `location-eng` - Human-readable GPS coordinates
- `com.apple.quicktime.creationdate` - Apple Photos date field (with timezone offset in Python version)
- `com.apple.quicktime.make` - Device identifier ("Snapchat")
- `com.apple.quicktime.location.ISO6709` - Apple Photos GPS format

**Cross-Platform Compatibility:**
- All generic metadata fields retained for compatibility with Google Photos, Windows, Linux
- QuickTime fields added for Apple Photos/macOS compatibility
- No breaking changes to existing functionality

**Python vs Web Version:**
- Python: Full timezone conversion (GPS coordinates → local timezone) using timezonefinder library
- Web: UTC timestamps only (browser limitations prevent timezone conversion)

### 💡 Recommendations
For best Apple Photos compatibility, use the Python version:
```bash
python3 download_memories.py --merge-overlays
```

This ensures videos import with:
- Correct local timestamps (not UTC)
- Full GPS coordinates in all formats
- Proper QuickTime metadata for Apple Photos

## [1.1.0] - 2024-12-24

### 🔥 Critical Bug Fixes
- **Fixed critical timezone bug in file timestamp handling** - File modification times were incorrectly offset by the user's timezone difference from UTC (e.g., 8 hours off in PST). All file timestamps now correctly represent the actual Snapchat capture time in UTC.

### ✨ New Features
- **New `--update-timezone` command** - Retroactively fix timestamps and metadata for already-downloaded files based on GPS coordinates
- **Desktop GUI application** (contributed by @MAbbara) - PyQt6-based graphical interface for easier use
- **Parallel downloads with `--threads` flag** - Significantly faster downloads on fast connections (default: 1 thread)
- **Timezone-aware metadata** (contributed by @Elc3r) - GPS-based timezone detection with `--local-timezone` flag
  - Converts UTC timestamps to local time based on GPS coordinates
  - Adds modern EXIF offset tags (OffsetTime, OffsetTimeOriginal, OffsetTimeDigitized)
  - Updates video metadata with timezone information
- **Windows-safe filename handling** - Sanitizes invalid filename characters for cross-platform compatibility

### 🧪 Testing & Quality
- **Comprehensive test suite** - 47+ tests covering all major functionality
- **GitHub Actions CI/CD** - Automated testing on Ubuntu, macOS, and Windows with Python 3.9-3.12
- **Coverage reporting** - Track code coverage across platforms
- **Regression tests** - Prevent timezone bug from reoccurring

### 🐛 Bug Fixes
- Fixed shadowed function warnings (contributed by @Headline)
- Fixed metadata-only resume detection
- Fixed media filter bug
- Improved timestamp-based filename handling

### 📚 Documentation
- Updated README with new flags and usage examples
- Added CHANGELOG.md for tracking releases
- Improved inline code documentation

### 🙏 Contributors
Special thanks to:
- @MAbbara for the PyQt6 GUI and parallel downloads
- @Elc3r for timezone-aware metadata support
- @Headline for code quality improvements

## [1.0.0] - 2024-12-12

### Initial Release
- Download Snapchat memories from HTML export
- Merge overlays (images: instant, videos: FFmpeg)
- Embed EXIF metadata (GPS + timestamps)
- Resume/retry capability with metadata.json
- Duplicate detection during download
- Multi-snap video joining
- Timestamp-based filenames

[1.2.0]: https://github.com/andrefecto/Snapchat-Memories-Downloader/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/andrefecto/Snapchat-Memories-Downloader/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/andrefecto/Snapchat-Memories-Downloader/releases/tag/v1.0.0
