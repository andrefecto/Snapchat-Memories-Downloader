# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[1.1.0]: https://github.com/andrefecto/Snapchat-Memories-Downloader/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/andrefecto/Snapchat-Memories-Downloader/releases/tag/v1.0.0
