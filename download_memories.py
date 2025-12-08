#!/usr/bin/env python3
"""
Snapchat Memories Downloader
Downloads all memories from Snapchat export HTML file with metadata preservation.
"""

import re
import json
import os
import sys
from pathlib import Path
from html.parser import HTMLParser
from datetime import datetime
import zipfile
import io

try:
    import requests
except ImportError:
    print("Error: requests library not found!")
    print("Please install it with: pip install -r requirements.txt")
    sys.exit(1)


class MemoriesParser(HTMLParser):
    """Parse Snapchat memories_history.html to extract memory data."""

    def __init__(self):
        super().__init__()
        self.memories = []
        self.current_row = {}
        self.current_tag = None
        self.in_table_row = False
        self.cell_index = 0

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.in_table_row = True
            self.current_row = {}
            self.cell_index = 0
        elif tag == 'td' and self.in_table_row:
            self.current_tag = 'td'
        elif tag == 'a' and self.in_table_row:
            # Extract URL from onclick attribute
            for attr_name, attr_value in attrs:
                if attr_name == 'onclick' and attr_value and 'downloadMemories' in attr_value:
                    # Extract URL from onclick="downloadMemories('URL', ...)"
                    url_match = re.search(r"downloadMemories\('([^']+)'", attr_value)
                    if url_match:
                        self.current_row['url'] = url_match.group(1)

    def handle_data(self, data):
        if self.current_tag == 'td' and data.strip():
            # Determine which column based on content
            data = data.strip()

            # Date column (contains UTC timestamp)
            if 'UTC' in data:
                self.current_row['date'] = data
            # Media type column
            elif data in ['Image', 'Video']:
                self.current_row['media_type'] = data
            # Location column
            elif 'Latitude, Longitude:' in data:
                # Extract lat/lon
                coords = data.replace('Latitude, Longitude:', '').strip()
                lat_lon = coords.split(',')
                if len(lat_lon) == 2:
                    self.current_row['latitude'] = lat_lon[0].strip()
                    self.current_row['longitude'] = lat_lon[1].strip()

    def handle_endtag(self, tag):
        if tag == 'td':
            self.current_tag = None
        elif tag == 'tr' and self.in_table_row:
            # Save row if it has required data
            if 'url' in self.current_row and 'date' in self.current_row:
                self.memories.append(self.current_row.copy())
            self.in_table_row = False
            self.current_row = {}


def parse_html_file(html_path: str) -> list:
    """Parse the HTML file and extract all memories."""
    print(f"Parsing {html_path}...")

    with open(html_path, 'r', encoding='utf-8') as f:
        html_content = f.read()

    parser = MemoriesParser()
    parser.feed(html_content)

    print(f"Found {len(parser.memories)} memories")
    return parser.memories


def is_zip_file(content: bytes) -> bool:
    """Check if content is a ZIP file."""
    return content[:2] == b'PK'


def download_and_extract(url: str, base_path: Path, file_num: str, extension: str) -> list:
    """
    Download a file from URL. If it's a ZIP with overlay, extract both files.
    Returns list of dicts with file info: [{'path': path, 'size': size, 'type': 'main'/'overlay'}]
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    content = response.content
    files_saved = []

    # Check if it's a ZIP file
    if is_zip_file(content):
        # Extract ZIP contents
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            for zip_info in zf.namelist():
                # Read file from zip
                file_data = zf.read(zip_info)

                # Determine if it's main or overlay based on filename
                if '-overlay' in zip_info.lower():
                    file_type = 'overlay'
                    output_filename = f"{file_num}-overlay{extension}"
                else:
                    # Assume it's the main file
                    file_type = 'main'
                    output_filename = f"{file_num}-main{extension}"

                output_path = base_path / output_filename

                # Save file
                with open(output_path, 'wb') as f:
                    f.write(file_data)

                files_saved.append({
                    'path': output_filename,
                    'size': len(file_data),
                    'type': file_type
                })

    else:
        # Not a ZIP - save as regular file
        output_filename = f"{file_num}{extension}"
        output_path = base_path / output_filename

        with open(output_path, 'wb') as f:
            f.write(content)

        files_saved.append({
            'path': output_filename,
            'size': len(content),
            'type': 'single'
        })

    return files_saved


def get_file_extension(media_type: str) -> str:
    """Determine file extension based on media type."""
    if media_type == 'Video':
        return '.mp4'
    # Image
    return '.jpg'


def parse_date_to_timestamp(date_str: str) -> float | None:
    """
    Parse Snapchat date string to Unix timestamp.
    Format: "2025-11-30 00:31:09 UTC"
    """
    try:
        # Remove " UTC" suffix and parse
        date_str_clean = date_str.replace(' UTC', '')
        dt = datetime.strptime(date_str_clean, '%Y-%m-%d %H:%M:%S')
        # Convert to timestamp
        return dt.timestamp()
    except (ValueError, AttributeError) as e:
        print(f"    Warning: Could not parse date '{date_str}': {e}")
        return None


def set_file_timestamp(file_path: Path, timestamp: float | None) -> None:
    """Set file modification and access times to the given timestamp."""
    if timestamp:
        os.utime(file_path, (timestamp, timestamp))


def initialize_metadata(memories: list, output_path: Path) -> list:
    """
    Initialize metadata for all memories with pending status.
    Returns metadata list, either loaded from existing file or newly created.
    """
    metadata_file = output_path / 'metadata.json'

    # Try to load existing metadata
    if metadata_file.exists():
        print("Found existing metadata.json, loading...")
        with open(metadata_file, 'r', encoding='utf-8') as f:
            return json.load(f)

    # Create new metadata for all memories
    print("Creating initial metadata...")
    metadata_list = []

    for idx, memory in enumerate(memories, start=1):
        file_num = f"{idx:02d}"
        extension = get_file_extension(memory.get('media_type', 'Image'))

        metadata_list.append({
            'number': idx,
            'date': memory.get('date', 'Unknown'),
            'media_type': memory.get('media_type', 'Unknown'),
            'latitude': memory.get('latitude', 'Unknown'),
            'longitude': memory.get('longitude', 'Unknown'),
            'url': memory.get('url', ''),
            'status': 'pending',
            'files': []
        })

    # Save initial metadata
    with open(metadata_file, 'w', encoding='utf-8') as f:
        json.dump(metadata_list, f, indent=2, ensure_ascii=False)

    print(f"Initialized metadata for {len(metadata_list)} memories")
    return metadata_list


def save_metadata(metadata_list: list, output_path: Path) -> None:
    """Save metadata to JSON file."""
    metadata_file = output_path / 'metadata.json'
    with open(metadata_file, 'w', encoding='utf-8') as f:
        json.dump(metadata_list, f, indent=2, ensure_ascii=False)


def download_all_memories(
    html_path: str,
    output_dir: str = 'memories',
    resume: bool = False,
    retry_failed: bool = False
) -> None:
    """Download all memories with sequential naming and metadata preservation."""

    # Parse HTML to get all memories
    memories = parse_html_file(html_path)

    if not memories:
        print("No memories found in HTML file!")
        return

    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)

    # Initialize or load metadata
    metadata_list = initialize_metadata(memories, output_path)

    # Determine which items to download
    if resume:
        items_to_download = [
            (i, m) for i, m in enumerate(metadata_list)
            if m.get('status') in ['pending', 'in_progress', 'failed']
        ]
        print(f"\nResuming: {len(items_to_download)} items to download")
    elif retry_failed:
        items_to_download = [
            (i, m) for i, m in enumerate(metadata_list)
            if m.get('status') == 'failed'
        ]
        print(f"\nRetrying: {len(items_to_download)} failed items")
    else:
        items_to_download = list(enumerate(metadata_list))
        print(f"\nDownloading {len(items_to_download)} memories to {output_dir}/")

    if not items_to_download:
        print("No items to download!")
        return

    print("=" * 60)

    total_items = len(items_to_download)

    for count, (idx, metadata) in enumerate(items_to_download, start=1):
        memory = memories[idx]
        file_num = f"{metadata['number']:02d}"
        extension = get_file_extension(metadata.get('media_type', 'Image'))

        print(f"\n[{count}/{total_items}] #{metadata['number']}")
        print(f"  Date: {metadata['date']}")
        print(f"  Type: {metadata['media_type']}")
        print(f"  Location: {metadata['latitude']}, {metadata['longitude']}")

        # Skip if already successful
        if metadata.get('status') == 'success' and metadata.get('files'):
            print("  Already downloaded, skipping...")
            continue

        # Mark as in progress
        metadata['status'] = 'in_progress'
        save_metadata(metadata_list, output_path)

        try:
            # Download and extract file(s)
            files_saved = download_and_extract(memory['url'], output_path, file_num, extension)

            # Display what was downloaded
            if len(files_saved) > 1:
                print(f"  ZIP extracted: {len(files_saved)} files")
                for file_info in files_saved:
                    print(f"    - {file_info['path']} ({file_info['size']:,} bytes)")
            else:
                downloaded_file = files_saved[0]
                print(
                    f"  Downloaded: {downloaded_file['path']} "
                    f"({downloaded_file['size']:,} bytes)"
                )

            # Set file timestamp to match the original date
            timestamp = parse_date_to_timestamp(metadata['date'])
            if timestamp:
                for file_info in files_saved:
                    file_path = output_path / file_info['path']
                    set_file_timestamp(file_path, timestamp)
                print(f"  Timestamp set to: {metadata['date']}")

            # Update metadata with file info
            metadata['status'] = 'success'
            metadata['files'] = files_saved

        except (OSError, requests.RequestException, zipfile.BadZipFile) as e:
            print(f"  ERROR: {str(e)}")
            metadata['status'] = 'failed'
            metadata['error'] = str(e)

        # Save metadata after each download
        save_metadata(metadata_list, output_path)

    # Final save
    metadata_file = output_path / 'metadata.json'
    save_metadata(metadata_list, output_path)

    print("\n" + "=" * 60)
    print("Download complete!")
    print(f"Files saved to: {output_path.absolute()}")
    print(f"Metadata saved to: {metadata_file.absolute()}")

    # Summary
    successful = sum(1 for m in metadata_list if m.get('status') == 'success')
    failed = sum(1 for m in metadata_list if m.get('status') == 'failed')
    pending = sum(1 for m in metadata_list if m.get('status') == 'pending')
    total_files = sum(
        len(m.get('files', []))
        for m in metadata_list
        if m.get('status') == 'success'
    )
    print(
        f"\nSummary: {successful} successful, {failed} failed, "
        f"{pending} pending, {total_files} total files"
    )

    if failed > 0:
        print("\nTo retry failed downloads, run:")
        print("  python download_memories.py --retry-failed")
    if pending > 0:
        print("\nTo resume incomplete downloads, run:")
        print("  python download_memories.py --resume")


if __name__ == '__main__':
    HTML_FILE = 'html/memories_history.html'

    if not os.path.exists(HTML_FILE):
        print(f"Error: {HTML_FILE} not found!")
        print("Please run this script from the directory containing the 'html' folder.")
        sys.exit(1)

    # Check for flags
    resume_mode = '--resume' in sys.argv
    retry_failed_mode = '--retry-failed' in sys.argv
    test_mode = '--test' in sys.argv

    # Optional: limit number of downloads for testing
    # Pass --test to download only first 3 files
    if test_mode:
        print("TEST MODE: Downloading only first 3 memories\n")
        memories = parse_html_file(HTML_FILE)
        memories = memories[:3]  # Limit to first 3

        output_path = Path('memories')
        output_path.mkdir(exist_ok=True)
        metadata_list = []

        for idx, memory in enumerate(memories, start=1):
            file_num = f"{idx:02d}"
            extension = get_file_extension(memory.get('media_type', 'Image'))

            metadata = {
                'number': idx,
                'date': memory.get('date', 'Unknown'),
                'media_type': memory.get('media_type', 'Unknown'),
                'latitude': memory.get('latitude', 'Unknown'),
                'longitude': memory.get('longitude', 'Unknown'),
                'url': memory.get('url', '')
            }

            print(f"[{idx}/3]")
            print(f"  Date: {metadata['date']}")
            print(f"  Type: {metadata['media_type']}")
            print(f"  Location: {metadata['latitude']}, {metadata['longitude']}")

            try:
                files_saved = download_and_extract(memory['url'], output_path, file_num, extension)

                if len(files_saved) > 1:
                    print(f"  ZIP extracted: {len(files_saved)} files")
                    for file_info in files_saved:
                        print(f"    - {file_info['path']} ({file_info['size']:,} bytes)")
                else:
                    downloaded_file = files_saved[0]
                    print(
                        f"  Downloaded: {downloaded_file['path']} "
                        f"({downloaded_file['size']:,} bytes)"
                    )

                # Set file timestamp to match the original date
                timestamp = parse_date_to_timestamp(metadata['date'])
                if timestamp:
                    for file_info in files_saved:
                        file_path = output_path / file_info['path']
                        set_file_timestamp(file_path, timestamp)
                    print(f"  Timestamp set to: {metadata['date']}")
                print()

                metadata['status'] = 'success'
                metadata['files'] = files_saved
            except (OSError, requests.RequestException, zipfile.BadZipFile) as e:
                print(f"  ERROR: {str(e)}\n")
                metadata['status'] = 'failed'
                metadata['error'] = str(e)

            metadata_list.append(metadata)

        metadata_file = output_path / 'metadata.json'
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata_list, f, indent=2, ensure_ascii=False)

        print("Test complete!")
    else:
        download_all_memories(HTML_FILE, resume=resume_mode, retry_failed=retry_failed_mode)
