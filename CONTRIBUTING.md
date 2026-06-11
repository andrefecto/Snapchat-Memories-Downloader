# Contributing

Thanks for your interest in improving the Snapchat Memories Downloader! This is a
free, privacy-first tool and contributions are welcome.

By participating you agree to abide by our [Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to contribute

- **Report a bug** — open a [bug report](https://github.com/andrefecto/Snapchat-Memories-Downloader/issues/new/choose).
- **Request a feature** — open a [feature request](https://github.com/andrefecto/Snapchat-Memories-Downloader/issues/new/choose).
- **Send a pull request** — fixes and improvements are appreciated (see below).

When reporting a problem with your export, please say whether your
`memories_history.html` has **Download links** (older format) or whether your media
is already bundled in a **`memories/` folder** (newer ~2026 format). It helps a lot
with triage. Never attach real memories, download URLs, or GPS data to an issue.

## Project layout

This repo ships two implementations that share the same workflow:

- **Python CLI** — `download_memories.py` (+ `snapchat_memories_gui.py` for the PyQt6 GUI)
- **Web** — `docs/index.html`, a single self-contained file served via GitHub Pages

See [CLAUDE.md](CLAUDE.md) for an architecture overview.

## Development setup (Python)

```bash
./setup.sh                 # create venv + install dependencies
source venv/bin/activate
pip install pytest pytest-cov pytest-xdist   # test tooling
```

Requirements: **Python 3.11+**. Video overlay merging needs **FFmpeg** installed and
on your `PATH` (`brew install ffmpeg`, `apt-get install ffmpeg`, or `choco install ffmpeg`).

### Running the tests

```bash
pytest test_download_memories.py -v
```

Please add or update tests for any behavior you change. Tests must pass on the
supported matrix (Python 3.11 / 3.12 on Linux, macOS, and Windows) — CI runs this
automatically on every pull request.

### Quick manual check

```bash
python3 download_memories.py --test          # processes the first 3 items only
python3 download_memories.py /path/to/export # auto-detects a new bundled export
```

## Development setup (Web)

There is **no build step**. Edit `docs/index.html` directly and open it in a browser
(or serve the `docs/` folder) to test. The FFmpeg.wasm artifacts under `docs/ffmpeg/`
are synced automatically by CI when `package.json` updates — don't edit them by hand.

> The web version needs cross-origin isolation for FFmpeg.wasm. If you serve it
> locally, send `Cross-Origin-Opener-Policy: same-origin` and
> `Cross-Origin-Embedder-Policy: require-corp` headers.

## Pull request guidelines

1. Fork the repo and create a branch from `main`.
2. Keep changes focused; match the style and comment density of the surrounding code.
3. If you change the Python tool, keep the **web** version in sync where it makes
   sense (and vice-versa) — they're meant to behave the same.
4. Add tests and make sure `pytest` passes locally.
5. Update the README / `CHANGELOG.md` when you add or change user-facing behavior.
6. Open the PR and fill out the template. CI must be green before merge.

## Privacy ground rules

This tool is built so that **your data never leaves your machine**. Please keep it
that way: no analytics, no telemetry, no network calls beyond downloading a user's
own memories from Snapchat's own URLs (older format) — and nothing at all for the
new bundled-export path.

Thank you for contributing! 🎉
