#!/usr/bin/env python3
"""
Snapchat Memories Downloader
Downloads all memories from Snapchat export HTML file with metadata preservation.
"""

import re
import json
import os
import sys
import argparse
from pathlib import Path
from html.parser import HTMLParser
from datetime import datetime
import zipfile
import io
import subprocess

try:
    import requests
except ImportError:
    print("Error: requests library not found!")
    print("Please install it with: pip install -r requirements.txt")
    sys.exit(1)

try:
    from PIL import Image
except ImportError:
    print("Warning: Pillow not found. Overlay merging will be disabled.")
    print("Install with: pip install -r requirements.txt")
    Image = None

try:
    import piexif
except ImportError:
    print("Warning: piexif not found. EXIF metadata writing will be disabled.")
    print("Install with: pip install -r requirements.txt")
    piexif = None

# Check if ffmpeg is available for video overlay merging
try:
    ffmpeg_available = subprocess.run(
        ['ffmpeg', '-version'],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=5,
        check=False
    ).returncode == 0
except (FileNotFoundError, subprocess.TimeoutExpired):
    ffmpeg_available = False

if not ffmpeg_available:
    print("Warning: ffmpeg not found. Video overlay merging will be disabled.")
    print("Install: brew install ffmpeg (macOS) or apt-get install ffmpeg (Linux)")


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


def decimal_to_dms(decimal: float) -> tuple:
    """
    Convert decimal coordinates to degrees, minutes, seconds format for EXIF.
    Returns: ((degrees, 1), (minutes, 1), (seconds, 100))
    """
    decimal = abs(decimal)

    degrees = int(decimal)
    minutes_decimal = (decimal - degrees) * 60
    minutes = int(minutes_decimal)
    seconds = (minutes_decimal - minutes) * 60

    # EXIF uses rational numbers (numerator, denominator)
    # Multiply seconds by 100 to preserve precision
    return (
        (degrees, 1),
        (minutes, 1),
        (int(seconds * 100), 100)
    )


def add_exif_metadata(
    image_data: bytes,
    date_str: str,
    latitude: str,
    longitude: str
) -> bytes:
    """
    Add EXIF metadata (GPS and date) to JPEG image data.
    Returns new image data with EXIF embedded.
    """
    if piexif is None or Image is None:
        return image_data

    try:
        # Parse coordinates
        lat = float(latitude) if latitude != 'Unknown' else None
        lon = float(longitude) if longitude != 'Unknown' else None

        # Load image
        img = Image.open(io.BytesIO(image_data))

        # Create EXIF dict
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}}

        # Add date/time
        if date_str and date_str != 'Unknown':
            # Parse Snapchat date: "2025-11-30 00:31:09 UTC"
            date_clean = date_str.replace(' UTC', '')
            try:
                dt = datetime.strptime(date_clean, '%Y-%m-%d %H:%M:%S')
                exif_date = dt.strftime('%Y:%m:%d %H:%M:%S')
                exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = exif_date.encode()
                exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = exif_date.encode()
                exif_dict["0th"][piexif.ImageIFD.DateTime] = exif_date.encode()
            except ValueError:
                pass

        # Add GPS coordinates
        if lat is not None and lon is not None:
            # GPS latitude
            lat_dms = decimal_to_dms(lat)
            exif_dict["GPS"][piexif.GPSIFD.GPSLatitude] = lat_dms
            exif_dict["GPS"][piexif.GPSIFD.GPSLatitudeRef] = b'N' if lat >= 0 else b'S'

            # GPS longitude
            lon_dms = decimal_to_dms(lon)
            exif_dict["GPS"][piexif.GPSIFD.GPSLongitude] = lon_dms
            exif_dict["GPS"][piexif.GPSIFD.GPSLongitudeRef] = b'E' if lon >= 0 else b'W'

        # Convert to bytes
        exif_bytes = piexif.dump(exif_dict)

        # Save image with EXIF
        output = io.BytesIO()
        img.save(output, format='JPEG', quality=95, exif=exif_bytes)
        return output.getvalue()

    except Exception as e:
        print(f"    Warning: Could not add EXIF metadata: {e}")
        return image_data


def merge_image_overlay(main_data: bytes, overlay_data: bytes) -> bytes:
    """Merge overlay image on top of main image using PIL."""
    if Image is None:
        raise ImportError("Pillow is required for overlay merging")

    # Load images
    main_img = Image.open(io.BytesIO(main_data))
    overlay_img = Image.open(io.BytesIO(overlay_data))

    # Ensure overlay has alpha channel
    if overlay_img.mode != 'RGBA':
        overlay_img = overlay_img.convert('RGBA')

    # Ensure main image is in RGB mode
    if main_img.mode != 'RGB':
        main_img = main_img.convert('RGB')

    # Resize overlay to match main image if needed
    if overlay_img.size != main_img.size:
        overlay_img = overlay_img.resize(main_img.size, Image.Resampling.LANCZOS)

    # Composite overlay onto main
    main_img.paste(overlay_img, (0, 0), overlay_img)

    # Save to bytes
    output = io.BytesIO()
    main_img.save(output, format='JPEG', quality=95)
    return output.getvalue()


def merge_video_overlay(
    main_path: Path,
    overlay_path: Path,
    output_path: Path
) -> bool:
    """
    Merge overlay video on top of main video using FFmpeg.

    Args:
        main_path: Path to main video file (MP4)
        overlay_path: Path to overlay video file (MP4)
        output_path: Path where merged video should be saved

    Returns:
        True if merge successful, False otherwise
    """
    if not ffmpeg_available:
        raise RuntimeError("FFmpeg is not available")

    try:
        # Build FFmpeg command
        # Use scale2ref to match overlay dimensions to main video
        cmd = [
            'ffmpeg',
            '-i', str(main_path),
            '-i', str(overlay_path),
            '-filter_complex',
            (
                '[0:v]fps=30,setsar=1[base];'
                '[1:v]fps=30,setsar=1,'
                'loop=loop=-1:size=32767:start=0,setpts=N/FRAME_RATE/TB[ovr_tmp];'
                '[ovr_tmp][base]scale2ref[ovr][base];'
                '[base][ovr]overlay=format=auto:shortest=1[outv]'
            ),
            '-map', '[outv]',
            '-map', '0:a?',
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
            '-c:a', 'copy',
            '-movflags', '+faststart',
            '-y',
            str(output_path)
        ]

        # Run FFmpeg with error capture
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,  # 5 minute timeout for long videos
            check=False
        )

        # Check if output file was created and has reasonable size
        if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1000:
            return True
        else:
            # Log error for debugging
            error_msg = result.stderr.decode('utf-8', errors='ignore')
            print(f"    FFmpeg error: {error_msg[-500:]}")  # Last 500 chars
            return False

    except subprocess.TimeoutExpired:
        print("    FFmpeg timeout: video processing took too long")
        return False
    except Exception as e:
        print(f"    FFmpeg exception: {e}")
        return False


def download_and_extract(
    url: str,
    base_path: Path,
    file_num: str,
    extension: str,
    merge_overlays: bool = False,
    date_str: str = 'Unknown',
    latitude: str = 'Unknown',
    longitude: str = 'Unknown',
    overlays_only: bool = False
) -> list:
    """
    Download a file from URL. If it's a ZIP with overlay, extract and optionally merge.
    Adds EXIF metadata (GPS and date) to images.
    Returns list of dicts with file info: [{'path': path, 'size': size, 'type': 'main'/'overlay'/'merged'}]
    Returns empty list if overlays_only=True and file has no overlay.
    """
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    content = response.content
    files_saved = []

    # Validate downloaded content
    if len(content) < 100:
        print(f"    WARNING: Downloaded file is very small ({len(content)} bytes) - may be invalid or expired URL")

    # For videos, check if it looks like a valid MP4 file
    is_video = extension.lower() in ['.mp4', '.mov', '.avi']
    if is_video and len(content) >= 8:
        # Check for MP4 magic bytes (ftyp box)
        # Valid MP4 files typically have 'ftyp' at bytes 4-8
        if content[4:8] not in [b'ftyp', b'mdat', b'moov', b'wide']:
            print("    WARNING: File may not be a valid video (invalid MP4 signature)")
            print(f"    First 20 bytes: {content[:20]}")
            print("    This might be an HTML error page or expired download link")

    # Check if it's a ZIP file
    if is_zip_file(content):
        # Extract ZIP contents
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            filenames = zf.namelist()

            # Check if we have both main and overlay
            has_overlay = any('-overlay' in f.lower() for f in filenames)

            # If overlays_only mode is enabled and this file has no overlay, skip it
            if overlays_only and not has_overlay:
                return []

            main_file = None
            overlay_file = None

            # Extract files and preserve original filenames/extensions
            extracted_files = {}
            for zip_info in filenames:
                file_data = zf.read(zip_info)
                # Get the original file extension from the ZIP filename
                original_ext = Path(zip_info).suffix
                if '-overlay' in zip_info.lower():
                    overlay_file = file_data
                    extracted_files['overlay'] = {'data': file_data, 'ext': original_ext}
                else:
                    main_file = file_data
                    extracted_files['main'] = {'data': file_data, 'ext': original_ext}

            # Check media type
            is_image = extension.lower() in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.tif']
            is_video = extension.lower() in ['.mp4', '.mov', '.avi']
            merge_attempted = False

            # If merge_overlays is True and we have both files
            if merge_overlays and has_overlay and main_file and overlay_file:
                if is_image and Image is not None:
                    try:
                        # Merge the images
                        merged_data = merge_image_overlay(main_file, overlay_file)

                        # Add EXIF metadata to merged image
                        merged_data = add_exif_metadata(merged_data, date_str, latitude, longitude)

                        output_filename = f"{file_num}{extension}"
                        output_path = base_path / output_filename

                        with open(output_path, 'wb') as f:
                            f.write(merged_data)

                        files_saved.append({
                            'path': output_filename,
                            'size': len(merged_data),
                            'type': 'merged'
                        })
                        merge_attempted = True
                    except Exception as e:
                        print(f"    Warning: Failed to merge image overlay: {e}")
                        print("    Saving separate files instead...")
                        # Fall back to saving separately
                        merge_overlays = False

                elif is_video and ffmpeg_available:
                    try:
                        # Create temporary files for main and overlay
                        temp_main = base_path / f"{file_num}-temp-main{extension}"
                        temp_overlay = base_path / f"{file_num}-temp-overlay{extension}"
                        output_filename = f"{file_num}{extension}"
                        output_path = base_path / output_filename

                        # Write temporary files
                        with open(temp_main, 'wb') as f:
                            f.write(main_file)
                        with open(temp_overlay, 'wb') as f:
                            f.write(overlay_file)

                        # Merge videos
                        print("    Merging video overlay (this may take a while)...")
                        success = merge_video_overlay(temp_main, temp_overlay, output_path)

                        if success:
                            files_saved.append({
                                'path': output_filename,
                                'size': output_path.stat().st_size,
                                'type': 'merged'
                            })
                            print(f"    Merged video: {output_filename}")

                            # Set file timestamp to match original Snapchat date
                            timestamp = parse_date_to_timestamp(date_str)
                            set_file_timestamp(output_path, timestamp)

                            # Delete any previously saved -main/-overlay files
                            main_file = base_path / f"{file_num}-main{extension}"
                            overlay_file = base_path / f"{file_num}-overlay{extension}"
                            if main_file.exists():
                                main_file.unlink()
                                print(f"    Deleted separate file: {file_num}-main{extension}")
                            if overlay_file.exists():
                                overlay_file.unlink()
                                print(f"    Deleted separate file: {file_num}-overlay{extension}")

                            merge_attempted = True
                        else:
                            print("    Warning: Video merge failed, saving separate files instead...")
                            merge_overlays = False

                        # Clean up temp files
                        temp_main.unlink(missing_ok=True)
                        temp_overlay.unlink(missing_ok=True)

                    except Exception as e:
                        print(f"    Warning: Failed to merge video overlay: {e}")
                        print("    Saving separate files instead...")
                        # Clean up temp files on error
                        if 'temp_main' in locals():
                            temp_main.unlink(missing_ok=True)
                        if 'temp_overlay' in locals():
                            temp_overlay.unlink(missing_ok=True)
                        merge_overlays = False

            # If not merging or merge failed, save separately
            if not merge_attempted:
                for file_type, file_info in extracted_files.items():
                    file_data = file_info['data']
                    file_ext = file_info['ext']

                    if file_type == 'overlay':
                        output_filename = f"{file_num}-overlay{file_ext}"
                    else:
                        output_filename = f"{file_num}-main{file_ext}"

                    # Add EXIF metadata to images
                    is_image = file_ext.lower() in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.tif']
                    if is_image:
                        file_data = add_exif_metadata(file_data, date_str, latitude, longitude)

                    output_path = base_path / output_filename

                    with open(output_path, 'wb') as f:
                        f.write(file_data)

                    # Set file timestamp to match original Snapchat date
                    timestamp = parse_date_to_timestamp(date_str)
                    set_file_timestamp(output_path, timestamp)

                    files_saved.append({
                        'path': output_filename,
                        'size': len(file_data),
                        'type': file_type
                    })

    else:
        # Not a ZIP - no overlay present
        # If overlays_only mode is enabled, skip non-ZIP files
        if overlays_only:
            return []

        # Save as regular file
        output_filename = f"{file_num}{extension}"
        output_path = base_path / output_filename

        # Add EXIF metadata to images
        is_image = extension.lower() in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.tif']
        if is_image:
            content = add_exif_metadata(content, date_str, latitude, longitude)

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


def merge_existing_files(folder_path: str) -> None:
    """
    Scan a folder for -main/-overlay file pairs and merge them.
    Does NOT delete the original -main/-overlay files.

    Args:
        folder_path: Path to folder containing -main/-overlay files
    """
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        print(f"Error: {folder_path} is not a valid directory!")
        return

    print(f"Scanning {folder_path} for -main/-overlay pairs...")
    print("=" * 60)

    # Find all -main files
    main_files = list(folder.glob('*-main.*'))

    if not main_files:
        print("No -main files found in the specified folder!")
        return

    print(f"Found {len(main_files)} -main files")

    merged_count = 0
    skipped_count = 0
    error_count = 0

    for main_file in main_files:
        # Extract base filename and extension
        # e.g., "05-main.mp4" -> "05" and ".mp4"
        filename = main_file.name
        if '-main' not in filename:
            continue

        base_name = filename.replace('-main', '')
        extension = main_file.suffix

        # Look for corresponding overlay file
        overlay_file = folder / filename.replace('-main', '-overlay')

        if not overlay_file.exists():
            print(f"\n[SKIP] {filename}")
            print("  No matching overlay file found")
            skipped_count += 1
            continue

        # Determine output filename (without -main suffix)
        output_file = folder / base_name

        print(f"\n[{merged_count + skipped_count + error_count + 1}/{len(main_files)}] Merging: {filename}")
        print(f"  Main: {main_file.name} ({main_file.stat().st_size:,} bytes)")
        print(f"  Overlay: {overlay_file.name} ({overlay_file.stat().st_size:,} bytes)")

        try:
            # Check file type
            is_video = extension.lower() in ['.mp4', '.mov', '.avi']
            is_image = extension.lower() in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.tif']

            if is_video:
                if not ffmpeg_available:
                    print("  ERROR: FFmpeg not available for video merging")
                    error_count += 1
                    continue

                print("  Merging videos (this may take a while)...")
                success = merge_video_overlay(main_file, overlay_file, output_file)

                if success:
                    print(f"  Success: {base_name} ({output_file.stat().st_size:,} bytes)")
                    # Copy timestamp from main file to merged file
                    main_stat = main_file.stat()
                    os.utime(output_file, (main_stat.st_atime, main_stat.st_mtime))
                    merged_count += 1
                else:
                    print("  ERROR: Video merge failed")
                    error_count += 1

            elif is_image:
                if Image is None:
                    print("  ERROR: Pillow not available for image merging")
                    error_count += 1
                    continue

                # Read both files
                with open(main_file, 'rb') as f:
                    main_data = f.read()
                with open(overlay_file, 'rb') as f:
                    overlay_data = f.read()

                # Merge images
                merged_data = merge_image_overlay(main_data, overlay_data)

                # Save merged image
                with open(output_file, 'wb') as f:
                    f.write(merged_data)

                print(f"  Success: {base_name} ({len(merged_data):,} bytes)")

                # Copy timestamp from main file to merged file
                main_stat = main_file.stat()
                os.utime(output_file, (main_stat.st_atime, main_stat.st_mtime))
                merged_count += 1
            else:
                print(f"  ERROR: Unknown file type {extension}")
                error_count += 1

        except Exception as e:
            print(f"  ERROR: {str(e)}")
            error_count += 1

    print("\n" + "=" * 60)
    print("Merge complete!")
    print(f"Summary: {merged_count} merged, {skipped_count} skipped, {error_count} errors")
    print("\nNote: Original -main and -overlay files were NOT deleted")


def download_all_memories(
    html_path: str,
    output_dir: str = 'memories',
    resume: bool = False,
    retry_failed: bool = False,
    merge_overlays: bool = False,
    videos_only: bool = False,
    pictures_only: bool = False,
    overlays_only: bool = False
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
    if videos_only:
        items_to_download = [
            (i, m) for i, m in enumerate(metadata_list)
            if m.get('media_type') == 'Video'
        ]
        print(f"\nProcessing videos only: {len(items_to_download)} videos to download")
    elif pictures_only:
        items_to_download = [
            (i, m) for i, m in enumerate(metadata_list)
            if m.get('media_type') == 'Image'
        ]
        print(f"\nProcessing pictures only: {len(items_to_download)} pictures to download")
    elif resume:
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

        # Skip if already successful (unless videos_only or pictures_only mode)
        if metadata.get('status') == 'success' and metadata.get('files') and not videos_only and not pictures_only:
            print("  Already downloaded, skipping...")
            continue

        # Mark as in progress
        metadata['status'] = 'in_progress'
        save_metadata(metadata_list, output_path)

        try:
            # Download and extract file(s)
            files_saved = download_and_extract(
                memory['url'], output_path, file_num, extension, merge_overlays,
                metadata['date'], metadata['latitude'], metadata['longitude'],
                overlays_only
            )

            # Check if file was skipped due to overlays_only mode
            if len(files_saved) == 0:
                print("  Skipped: No overlay detected (overlays-only mode)")
                metadata['status'] = 'skipped'
                metadata['skip_reason'] = 'no_overlay'
                continue

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
    # Parse command-line arguments
    parser = argparse.ArgumentParser(
        description='Download Snapchat memories with metadata preservation'
    )
    parser.add_argument(
        'html_file',
        nargs='?',
        default='html/memories_history.html',
        help='Path to memories_history.html file or folder containing it (default: html/memories_history.html)'
    )
    parser.add_argument(
        '--resume',
        action='store_true',
        help='Resume interrupted download'
    )
    parser.add_argument(
        '--retry-failed',
        action='store_true',
        help='Retry only failed downloads'
    )
    parser.add_argument(
        '--test',
        action='store_true',
        help='Test mode: download only first 3 files'
    )
    parser.add_argument(
        '--merge-overlays',
        action='store_true',
        help='Merge overlay images and videos on top of main content (requires FFmpeg for videos)'
    )
    parser.add_argument(
        '--videos-only',
        action='store_true',
        help='Only download and process videos (skip pictures). Useful for re-processing existing downloads.'
    )
    parser.add_argument(
        '--pictures-only',
        action='store_true',
        help='Only download and process pictures (skip videos). Useful for re-processing existing downloads.'
    )
    parser.add_argument(
        '--overlays-only',
        action='store_true',
        help='Only keep memories that have overlays (skip memories without -main/-overlay pairs)'
    )
    parser.add_argument(
        '--merge-existing',
        type=str,
        metavar='FOLDER',
        help='Merge existing -main/-overlay file pairs in the specified folder (does NOT delete originals)'
    )

    args = parser.parse_args()

    # Handle --merge-existing mode (separate from normal download mode)
    if args.merge_existing:
        merge_existing_files(args.merge_existing)
        sys.exit(0)

    html_path = args.html_file

    # If path is a directory, look for memories_history.html inside it
    if os.path.isdir(html_path):
        html_path = os.path.join(html_path, 'memories_history.html')
        print(f"Looking for memories_history.html in directory: {html_path}")

    HTML_FILE = html_path

    if not os.path.exists(HTML_FILE):
        print(f"Error: {HTML_FILE} not found!")
        print("Usage: python download_memories.py [path/to/file_or_folder] [options]")
        print("Run 'python download_memories.py --help' for more information.")
        sys.exit(1)

    # Extract flags
    resume_mode = args.resume
    retry_failed_mode = args.retry_failed
    test_mode = args.test
    merge_overlays_mode = args.merge_overlays
    videos_only_mode = args.videos_only
    pictures_only_mode = args.pictures_only
    overlays_only_mode = args.overlays_only

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
                files_saved = download_and_extract(
                    memory['url'], output_path, file_num, extension, merge_overlays_mode,
                    metadata['date'], metadata['latitude'], metadata['longitude'],
                    False  # overlays_only not used in test mode
                )

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
        download_all_memories(
            HTML_FILE,
            resume=resume_mode,
            retry_failed=retry_failed_mode,
            merge_overlays=merge_overlays_mode,
            videos_only=videos_only_mode,
            pictures_only=pictures_only_mode,
            overlays_only=overlays_only_mode
        )
