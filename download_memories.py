#!/usr/bin/env python3
"""
Snapchat Memories Downloader
Downloads all memories from Snapchat export HTML file with metadata preservation.

Architecture:
1. Parse memories_history.html to extract URLs, dates, GPS coordinates
2. Download files from Snapchat CDN (may be ZIPs containing overlays)
3. Optionally merge overlays onto main content (images: instant, videos: FFmpeg)
4. Embed EXIF metadata (GPS + timestamp) into images
5. Track progress in metadata.json for resume/retry capability
6. Set file timestamps to match original Snapchat capture dates

Key Design Patterns:
- Metadata state machine: pending → in_progress → success/failed/skipped
- Duplicate detection happens DURING download (not post-process) to save bandwidth
- Deferred video processing: downloads all first, merges videos at end (memory optimization)
- Graceful degradation: optional dependencies (Pillow, piexif, FFmpeg) disable features vs failing
"""

import re
import json
import os
import sys
import argparse
import bisect
from pathlib import Path
from html.parser import HTMLParser
from datetime import datetime, timedelta
from typing import Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import zipfile
import io
import subprocess
import hashlib
import shutil
from collections import defaultdict, deque

INVALID_FILENAME_CHARS = '<>:"/\\|?*'

# === DEPENDENCY CHECKS ===
# All dependencies use graceful degradation - missing deps disable features, not crash

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
    Image = None  # Setting to None allows feature checks with "if Image is not None"

try:
    import piexif
except ImportError:
    print("Warning: piexif not found. EXIF metadata writing will be disabled.")
    print("Install with: pip install -r requirements.txt")
    piexif = None  # Setting to None allows feature checks

# Timezone dependencies - optional for enhanced metadata
try:
    from timezonefinder import TimezoneFinder
    import pytz
    timezone_support = True
except ImportError:
    print("Warning: timezonefinder/pytz not found. Timezone-aware metadata will be disabled.")
    print("Install with: pip install -r requirements.txt")
    timezone_support = False

# Check if ffmpeg is available for video overlay merging
# Note: ffmpeg must be installed separately (not a Python package)
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
    """
    Parse Snapchat memories_history.html to extract memory data.

    Snapchat's HTML format:
    - Table rows (<tr>) contain memory entries
    - Each row has cells (<td>) with: date, media type, location
    - Download link is in <a onclick="downloadMemories('URL', ...)">

    Extraction strategy:
    - Track table rows (<tr>) as containers for memory data
    - Extract data from <td> cells based on content patterns (not column order)
    - Parse onclick attribute to get download URL

    Example HTML structure:
    <tr>
      <td>2025-11-30 00:31:09 UTC</td>
      <td><a onclick="downloadMemories('https://...', ...)">Download</a></td>
      <td>Video</td>
      <td>Latitude, Longitude: 34.05, -118.25</td>
    </tr>
    """

    def __init__(self):
        super().__init__()
        self.memories = []  # List of extracted memory dicts
        self.current_row = {}  # Currently parsing row data
        self.current_tag = None  # Currently parsing tag type
        self.in_table_row = False  # Whether we're inside a <tr>
        self.cell_index = 0  # Track cell position (currently unused)

    def handle_starttag(self, tag, attrs):
        """Called when parser encounters an opening tag like <tr> or <td>."""
        if tag == 'tr':
            # Start of new table row - reset row data
            self.in_table_row = True
            self.current_row = {}
            self.cell_index = 0
        elif tag == 'td' and self.in_table_row:
            # Table cell - content will come in handle_data()
            self.current_tag = 'td'
        elif tag == 'a' and self.in_table_row:
            # Extract URL from onclick attribute
            # Format: onclick="downloadMemories('https://...', ...)"
            for attr_name, attr_value in attrs:
                if attr_name == 'onclick' and attr_value and 'downloadMemories' in attr_value:
                    # Try full format: downloadMemories('URL', this, true/false)
                    full_match = re.search(r"downloadMemories\('([^']+)',\s*this,\s*(true|false)\)", attr_value)
                    if full_match:
                        self.current_row['url'] = full_match.group(1)
                        self.current_row['is_get_request'] = full_match.group(2) == 'true'
                    else:
                        # Fallback for older exports: downloadMemories('URL')
                        url_match = re.search(r"downloadMemories\('([^']+)'", attr_value)
                        if url_match:
                            self.current_row['url'] = url_match.group(1)
                            self.current_row['is_get_request'] = True

    def handle_data(self, data):
        """
        Called when parser encounters text content between tags.
        Uses content-based detection (not column order) for robustness.
        """
        if self.current_tag == 'td' and data.strip():
            # Determine which column based on content patterns
            data = data.strip()

            # Date column: Contains "UTC" string
            # Example: "2025-11-30 00:31:09 UTC"
            if 'UTC' in data:
                self.current_row['date'] = data
            # Media type column: Exactly "Image" or "Video"
            elif data in ['Image', 'Video']:
                self.current_row['media_type'] = data
            # Location column: Contains "Latitude, Longitude:" prefix
            # Example: "Latitude, Longitude: 34.052235, -118.243683"
            elif 'Latitude, Longitude:' in data:
                # Extract coordinates
                coords = data.replace('Latitude, Longitude:', '').strip()
                lat_lon = coords.split(',')
                if len(lat_lon) == 2:
                    self.current_row['latitude'] = lat_lon[0].strip()
                    self.current_row['longitude'] = lat_lon[1].strip()

    def handle_endtag(self, tag):
        """Called when parser encounters a closing tag like </tr> or </td>."""
        if tag == 'td':
            self.current_tag = None
        elif tag == 'tr' and self.in_table_row:
            # End of table row - save if we got minimum required data
            # Minimum requirement: URL and date (other fields can be missing)
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
    """
    Check if content is a ZIP file by examining magic bytes.

    ZIP files start with "PK" (0x50 0x4B) - named after Phil Katz, ZIP creator.
    This is more reliable than file extensions which can be misleading.

    Snapchat uses ZIP files to bundle main content + overlay files together.
    Example: A video with text overlay comes as ZIP containing:
      - video-main.mp4 (original video)
      - video-overlay.mp4 or .png (overlay content)
    """
    return content[:2] == b'PK'


def get_timezone_from_gps(latitude: float, longitude: float) -> str:
    """
    Determine timezone from GPS coordinates using timezonefinder.
    
    Special handling for Czech Republic to use Europe/Prague instead of Europe/Paris
    which timezonefinder sometimes returns for border areas.
    
    Args:
        latitude: GPS latitude coordinate
        longitude: GPS longitude coordinate
        
    Returns:
        Timezone string (e.g., 'Europe/Prague', 'America/New_York') or 'UTC' as fallback
    """
    if not timezone_support:
        return "UTC"
        
    try:
        tf = TimezoneFinder()
        timezone_str = tf.timezone_at(lat=latitude, lng=longitude)
        
        # Special case: Czech Republic coordinates should use Prague timezone
        # Bounding box for Czech Republic: lat 48.5-51.1, lng 12.0-18.9
        if (timezone_str == "Europe/Paris" and 
            48.5 <= latitude <= 51.1 and 
            12.0 <= longitude <= 18.9):
            timezone_str = "Europe/Prague"
        
        return timezone_str or "UTC"
        
    except Exception as e:
        print(f"    Warning: Could not determine timezone for {latitude}, {longitude}: {e}")
        return "UTC"


def convert_utc_to_local(utc_string: str, timezone_str: str) -> datetime:
    """
    Convert UTC datetime string to local timezone-aware datetime.
    
    Args:
        utc_string: UTC datetime string in format "2025-12-12 01:08:38 UTC"
        timezone_str: Target timezone string (e.g., 'Europe/Prague')
        
    Returns:
        Timezone-aware datetime object in local time
    """
    if not timezone_support:
        # Fallback: return naive UTC datetime
        try:
            return datetime.strptime(utc_string, "%Y-%m-%d %H:%M:%S UTC")
        except ValueError:
            return datetime.utcnow()
    
    try:
        # Parse UTC string
        dt_utc = datetime.strptime(utc_string, "%Y-%m-%d %H:%M:%S UTC")
        
        # Convert to timezone-aware objects
        utc_tz = pytz.UTC
        local_tz = pytz.timezone(timezone_str)
        
        # Localize to UTC and convert to local timezone
        dt_utc = utc_tz.localize(dt_utc)
        dt_local = dt_utc.astimezone(local_tz)
        
        return dt_local
        
    except Exception as e:
        print(f"    Warning: Error converting time '{utc_string}' to {timezone_str}: {e}")
        # Fallback: use UTC
        try:
            dt_utc = datetime.strptime(utc_string, "%Y-%m-%d %H:%M:%S UTC")
            return pytz.UTC.localize(dt_utc)
        except Exception:
            return pytz.UTC.localize(datetime.utcnow())


def format_exif_datetime(dt: datetime) -> str:
    """Format datetime for EXIF metadata (YYYY:MM:DD HH:MM:SS format)."""
    return dt.strftime("%Y:%m:%d %H:%M:%S")


def format_exif_offset(dt: datetime) -> str:
    """
    Format timezone offset for EXIF metadata (+01:00 or -05:00 format).
    
    Modern EXIF standard uses ISO 8601 offset format with colon separator.
    """
    offset = dt.strftime("%z")  # Returns "+0100" or "-0500"
    if len(offset) == 5:  # "+0100"
        return f"{offset[:3]}:{offset[3:]}"  # "+01:00"
    return offset


def decimal_to_dms(decimal: float) -> tuple:
    """
    Convert decimal coordinates to degrees, minutes, seconds (DMS) format for EXIF.

    EXIF GPS coordinates use DMS format with rational numbers (fraction tuples).
    Example: 34.052235° becomes ((34, 1), (3, 1), (808, 100))
                                  = 34° 3' 8.08"

    Args:
        decimal: Coordinate as decimal float (e.g., 34.052235)

    Returns:
        Tuple of 3 rational numbers: ((degrees, 1), (minutes, 1), (seconds, 100))
        The denominators preserve precision:
        - degrees and minutes use denominator 1 (integers)
        - seconds use denominator 100 (preserves 2 decimal places)
    """
    decimal = abs(decimal)  # Work with absolute value; sign handled separately in EXIF

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
    longitude: str,
    use_local_timezone: bool = False
) -> bytes:
    """
    Add EXIF metadata (GPS coordinates + timestamp) to image data.

    CRITICAL: Preserves original image format (JPEG, PNG, WebP, etc.) to avoid quality loss.
    Format-specific handling:
    - JPEG: Full EXIF support (GPS + timestamp), convert RGBA→RGB (JPEG doesn't support alpha)
    - PNG: Limited EXIF support (varies by encoder), some may only preserve timestamp
    - WebP: Full EXIF support (GPS + timestamp)
    - Other formats: Return original data unchanged to avoid errors

    EXIF structure:
    - "0th" IFD: General image data (DateTime)
    - "Exif" IFD: Extended data (DateTimeOriginal, DateTimeDigitized, OffsetTime*)
    - "GPS" IFD: GPS coordinates in DMS format + direction refs (N/S, E/W)

    Args:
        image_data: Raw image bytes
        date_str: Snapchat date string (e.g., "2025-11-30 00:31:09 UTC")
        latitude: Latitude as string (e.g., "34.052235")
        longitude: Longitude as string (e.g., "-118.243683")
        use_local_timezone: If True, convert UTC to local timezone and add modern EXIF offset tags

    Returns:
        Image bytes with EXIF embedded, or original bytes if EXIF fails
    """
    if piexif is None or Image is None:
        return image_data

    try:
        # Parse coordinates (may be 'Unknown' if location not available)
        lat = float(latitude) if latitude != 'Unknown' else None
        lon = float(longitude) if longitude != 'Unknown' else None

        # Load image and detect format
        img = Image.open(io.BytesIO(image_data))
        original_format = img.format  # CRITICAL: Preserve to avoid quality loss

        # Create EXIF dict with 3 IFDs (Image File Directories)
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}}

        # Handle timezone conversion if requested
        if use_local_timezone and timezone_support and date_str and date_str != 'Unknown':
            if lat is not None and lon is not None:
                # Get local timezone and convert UTC time
                timezone_str = get_timezone_from_gps(lat, lon)
                local_datetime = convert_utc_to_local(date_str, timezone_str)
                
                # Format for EXIF with modern offset tags
                exif_date = format_exif_datetime(local_datetime)
                offset_str = format_exif_offset(local_datetime)
                
                # Add date/time with timezone information
                exif_dict["0th"][piexif.ImageIFD.DateTime] = exif_date.encode()
                exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = exif_date.encode()
                exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = exif_date.encode()
                
                # Modern EXIF timezone offset tags (EXIF 2.31+)
                exif_dict["Exif"][piexif.ExifIFD.OffsetTime] = offset_str.encode()           # for DateTime
                exif_dict["Exif"][piexif.ExifIFD.OffsetTimeOriginal] = offset_str.encode()   # for DateTimeOriginal  
                exif_dict["Exif"][piexif.ExifIFD.OffsetTimeDigitized] = offset_str.encode()  # for DateTimeDigitized
                
                print(f"    Timezone-aware EXIF: {exif_date} {offset_str} (timezone: {timezone_str})")
            else:
                # No GPS coordinates, fall back to UTC
                use_local_timezone = False
        
        # Fallback to UTC time handling (original behavior)
        if not use_local_timezone and date_str and date_str != 'Unknown':
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

        # Save image with EXIF, preserving original format
        output = io.BytesIO()

        # JPEG supports full EXIF (GPS + timestamp)
        if original_format in ['JPEG', 'JPG']:
            # Convert RGBA to RGB if needed (JPEG doesn't support alpha)
            if img.mode == 'RGBA':
                img = img.convert('RGB')
            img.save(output, format='JPEG', quality=95, exif=exif_bytes)
        # PNG: Limited EXIF support (timestamp only in older versions, full eXIf chunk in PNG 3.0+)
        # Try to add EXIF, but it may only preserve timestamp, not GPS
        elif original_format == 'PNG':
            try:
                img.save(output, format='PNG', exif=exif_bytes)
            except Exception:
                # If EXIF fails, save without it (some PNG encoders don't support eXIf chunk)
                img.save(output, format='PNG')
        # WebP supports full EXIF
        elif original_format == 'WEBP':
            img.save(output, format='WEBP', quality=95, exif=exif_bytes)
        # For other formats, save without EXIF to avoid errors
        else:
            return image_data

        return output.getvalue()

    except Exception as e:
        print(f"    Warning: Could not add EXIF metadata: {e}")
        return image_data


def update_video_metadata(
    video_path: Path,
    date_str: str,
    latitude: str = 'Unknown',
    longitude: str = 'Unknown',
    use_local_timezone: bool = False
) -> bool:
    """
    Update video metadata using FFmpeg with timezone-aware timestamps and GPS coordinates.

    Adds creation_time metadata with proper timezone handling and GPS location data
    in multiple formats for maximum compatibility across different video players.

    CRITICAL for Apple Photos:
    - Sets com.apple.quicktime.creationdate with timezone offset (e.g., "2024-08-10T15:03:03-0700")
    - Apple Photos ignores container creation_time and uses QuickTime fields instead
    - Without this, videos import at wrong local time even when GPS is present

    Args:
        video_path: Path to video file
        date_str: Snapchat date string (e.g., "2025-11-30 00:31:09 UTC")
        latitude: Latitude as string (e.g., "34.052235")
        longitude: Longitude as string (e.g., "-118.243683")
        use_local_timezone: If True, convert UTC to local timezone based on GPS

    Returns:
        True if metadata update successful, False otherwise
    """
    if not ffmpeg_available:
        print(f"    Warning: FFmpeg not available, skipping video metadata for {video_path.name}")
        return False
        
    try:
        # Parse coordinates
        lat = float(latitude) if latitude != 'Unknown' else None
        lon = float(longitude) if longitude != 'Unknown' else None
        
        # Handle timezone conversion
        if use_local_timezone and timezone_support and lat is not None and lon is not None:
            timezone_str = get_timezone_from_gps(lat, lon)
            local_datetime = convert_utc_to_local(date_str, timezone_str)
            creation_time = local_datetime.isoformat()  # ISO 8601 with timezone (e.g., 2024-08-10T15:03:03-07:00)

            # QuickTime date format: "2024-08-10T15:03:03-0700" (no colon in timezone offset)
            # Convert ISO 8601 "2024-08-10T15:03:03-07:00" -> QuickTime "2024-08-10T15:03:03-0700"
            if creation_time[-3] == ':' and (creation_time[-6] in ['+', '-']):
                # Remove the colon from timezone offset
                quicktime_date = creation_time[:-3] + creation_time[-2:]
            else:
                quicktime_date = creation_time
        else:
            # Fallback to UTC
            date_clean = date_str.replace(' UTC', '')
            try:
                dt = datetime.strptime(date_clean, '%Y-%m-%d %H:%M:%S')
                creation_time = dt.isoformat() + 'Z'  # UTC format (e.g., 2024-08-10T22:03:03Z)
                quicktime_date = creation_time  # QuickTime accepts Z suffix for UTC
            except ValueError:
                print(f"    Warning: Could not parse date '{date_str}' for video metadata")
                return False
        
        # Create backup
        backup_path = video_path.with_suffix(video_path.suffix + '.backup')
        video_path.rename(backup_path)
        
        # Build FFmpeg command
        cmd = [
            "ffmpeg", "-i", str(backup_path),
            "-metadata", f"creation_time={creation_time}",
            # QuickTime date fields - CRITICAL for Apple Photos
            "-metadata", f"com.apple.quicktime.creationdate={quicktime_date}",
            "-metadata", f"com.apple.quicktime.make=Snapchat",
        ]

        # Add GPS metadata if available
        if lat is not None and lon is not None:
            # Multiple location formats for compatibility
            cmd.extend([
                "-metadata", f"location={lat:+.6f}{lon:+.6f}/",  # ISO 6709 format
                "-metadata", f"location-eng={lat}, {lon}",       # Human readable
                "-metadata", f"com.apple.quicktime.location.ISO6709={lat:+08.4f}{lon:+09.4f}/",  # Apple format
            ])
        
        cmd.extend([
            "-codec", "copy",  # Copy streams without re-encoding
            "-y",  # Overwrite output
            str(video_path)
        ])
        
        # Run FFmpeg
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,  # 1 minute timeout for metadata-only operation
            check=False
        )
        
        if result.returncode == 0:
            # Success - remove backup
            backup_path.unlink()
            gps_info = f", GPS: {lat}, {lon}" if lat and lon else ""
            print(f"    Updated video metadata: {creation_time}{gps_info}")
            return True
        else:
            # Error - restore backup
            backup_path.rename(video_path)
            error_msg = result.stderr.decode('utf-8', errors='ignore')
            print(f"    Warning: FFmpeg error updating video metadata: {error_msg[-200:]}")
            return False
            
    except Exception as e:
        # Restore backup if it exists (only if we got far enough to create it)
        try:
            backup_path = video_path.with_suffix(video_path.suffix + '.backup')
            if backup_path.exists():
                backup_path.rename(video_path)
        except:
            # backup_path might not be defined if exception occurred early
            pass
        print(f"    Warning: Error updating video metadata for {video_path.name}: {e}")
        return False


def merge_image_overlay(main_data: bytes, overlay_data: bytes) -> bytes:
    """Merge overlay image on top of main image using PIL.
    Preserves the original format of the main image.
    """
    if Image is None:
        raise ImportError("Pillow is required for overlay merging")

    # Load images
    main_img = Image.open(io.BytesIO(main_data))
    overlay_img = Image.open(io.BytesIO(overlay_data))

    # Preserve original format
    original_format = main_img.format or 'JPEG'

    # Ensure overlay has alpha channel
    if overlay_img.mode != 'RGBA':
        overlay_img = overlay_img.convert('RGBA')

    # Ensure main image is in RGB or RGBA mode
    if main_img.mode not in ['RGB', 'RGBA']:
        main_img = main_img.convert('RGB')

    # Resize overlay to match main image if needed
    if overlay_img.size != main_img.size:
        overlay_img = overlay_img.resize(main_img.size, Image.Resampling.LANCZOS)

    # Composite overlay onto main
    main_img.paste(overlay_img, (0, 0), overlay_img)

    # Save to bytes, preserving original format
    output = io.BytesIO()

    if original_format in ['JPEG', 'JPG']:
        # Convert RGBA to RGB if needed (JPEG doesn't support alpha)
        if main_img.mode == 'RGBA':
            main_img = main_img.convert('RGB')
        main_img.save(output, format='JPEG', quality=95)
    elif original_format == 'PNG':
        main_img.save(output, format='PNG')
    elif original_format == 'WEBP':
        main_img.save(output, format='WEBP', quality=95)
    elif original_format in ['GIF', 'BMP', 'TIFF']:
        # Convert to RGB for these formats (they don't support RGBA well)
        if main_img.mode == 'RGBA':
            main_img = main_img.convert('RGB')
        main_img.save(output, format=original_format)
    else:
        # Default to JPEG for unknown formats
        if main_img.mode == 'RGBA':
            main_img = main_img.convert('RGB')
        main_img.save(output, format='JPEG', quality=95)

    return output.getvalue()


def merge_video_overlay(
    main_path: Path,
    overlay_path: Path,
    output_path: Path
) -> bool:
    """
    Merge overlay video on top of main video using FFmpeg.

    This is the slowest operation in the entire program (1-5 minutes per video).
    Uses complex FFmpeg filter chain to handle overlay synchronization and scaling.

    FFmpeg filter chain explanation:
    1. [0:v]fps=30,setsar=1[base]
       - Normalize main video to 30fps, set sample aspect ratio to 1:1
    2. [1:v]fps=30,setsar=1,loop...[ovr_tmp]
       - Normalize overlay to 30fps
       - Loop overlay indefinitely (loop=-1) to match main video length
       - Reset timestamps for synchronization
    3. [ovr_tmp][base]scale2ref[ovr][base]
       - Scale overlay to exactly match main video dimensions
       - scale2ref ensures perfect size match (critical for overlay positioning)
    4. [base][ovr]overlay=format=auto:shortest=1[outv]
       - Composite overlay on top of main video
       - shortest=1 stops when main video ends (even if overlay longer)

    Common failure modes:
    - FFmpeg not installed → RuntimeError
    - Timeout (>5 min) → Returns False
    - Output file < 1000 bytes → Returns False (indicates encoding failure)
    - FFmpeg error → Returns False (stderr logged for debugging)

    Args:
        main_path: Path to main video file (MP4)
        overlay_path: Path to overlay video file (MP4 or image)
        output_path: Path where merged video should be saved

    Returns:
        True if merge successful, False otherwise
    """
    if not ffmpeg_available:
        raise RuntimeError("FFmpeg is not available")

    try:
        # Build FFmpeg command with complex filter chain
        # Scale2ref ensures overlay matches main video dimensions exactly
        cmd = [
            'ffmpeg',
            '-i', str(main_path),       # Input 0: Main video
            '-i', str(overlay_path),    # Input 1: Overlay video/image
            '-filter_complex',
            (
                # Step 1: Normalize main video framerate and aspect ratio
                '[0:v]fps=30,setsar=1[base];'
                # Step 2: Normalize overlay, loop it to match main duration
                '[1:v]fps=30,setsar=1,'
                'loop=loop=-1:size=32767:start=0,setpts=N/FRAME_RATE/TB[ovr_tmp];'
                # Step 3: Scale overlay to match main video size
                '[ovr_tmp][base]scale2ref[ovr][base];'
                # Step 4: Composite overlay on top, stop at shortest input
                '[base][ovr]overlay=format=auto:shortest=1[outv]'
            ),
            '-map', '[outv]',         # Use filtered video output
            '-map', '0:a?',           # Copy audio from main (? = optional)
            '-c:v', 'libx264',        # Encode video with H.264
            '-preset', 'medium',      # Encoding speed vs quality tradeoff
            '-crf', '23',             # Quality: 23 is good default (lower = better)
            '-pix_fmt', 'yuv420p',    # Pixel format for compatibility
            '-c:a', 'copy',           # Copy audio without re-encoding
            '-movflags', '+faststart', # Enable streaming (moov atom at start)
            '-y',                     # Overwrite output file if exists
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
            # Log error for debugging with more context
            error_msg = result.stderr.decode('utf-8', errors='ignore')
            print(f"    FFmpeg exit code: {result.returncode}")

            # Check if file was created but is too small
            if output_path.exists():
                file_size = output_path.stat().st_size
                print(f"    Output file size: {file_size} bytes (need > 1000)")
                if file_size < 1000:
                    print(f"    File too small, likely encoding failure")
            else:
                print(f"    Output file not created")

            # Print last part of stderr (where actual errors usually are)
            print(f"    FFmpeg stderr (last 600 chars):")
            print(f"    {error_msg[-600:]}")
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
    defer_video_overlays: bool = False,
    date_str: str = 'Unknown',
    latitude: str = 'Unknown',
    longitude: str = 'Unknown',
    overlays_only: bool = False,
    use_timestamp_filenames: bool = False,
    check_duplicates: bool = False,
    use_local_timezone: bool = False,
    is_get_request: bool = True
) -> list:
    """
    Download and process a single memory file from Snapchat CDN.

    This is the CORE function that handles all download logic including:
    - Downloading from Snapchat URLs (may expire after ~1 year)
    - Detecting ZIP files containing overlays (via magic bytes)
    - Extracting and optionally merging overlay content
    - Adding EXIF metadata (GPS + timestamp) to images
    - Duplicate detection DURING download (saves bandwidth vs post-processing)
    - Deferred video overlay processing (downloads first, merges later)

    File type detection:
    - ZIP file (magic bytes 'PK'): Contains main + overlay files
    - Single file: Standalone image or video without overlay

    Overlay merge modes:
    1. Images: Instant merge using Pillow (alpha compositing)
    2. Videos: FFmpeg merge (1-5 min) or defer until end if defer_video_overlays=True
    3. No merge: Save as separate -main and -overlay files

    Args:
        url: Snapchat CDN URL (may expire)
        base_path: Output directory path
        file_num: Sequential number for filename (e.g., "01", "02")
        extension: File extension based on media type (.mp4 or .jpg)
        merge_overlays: If True, composite overlay on top of main content
        defer_video_overlays: If True, skip video merging now (process later in batch)
        date_str: Snapchat date string for EXIF and timestamps
        latitude: GPS latitude for EXIF
        longitude: GPS longitude for EXIF
        overlays_only: If True, skip files without overlays
        use_timestamp_filenames: If True, use YYYY.MM.DD-HH-MM-SS.ext naming
        check_duplicates: If True, skip download if identical file exists
        use_local_timezone: If True, convert UTC to local timezone in EXIF/metadata
        is_get_request: If True, use GET request; if False, use POST flow (POST to
            get CDN URL, then GET file from CDN)

    Returns:
        List of dicts with file info: [{'path': str, 'size': int, 'type': str}]
        Returns empty list if overlays_only=True and file has no overlay
        Type can be: 'main', 'overlay', 'merged', 'single', 'duplicate'
    """
    # Use Mozilla User-Agent to avoid bot detection
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
    }

    # Download file from Snapchat CDN
    if is_get_request:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
    else:
        # POST flow: split URL at ?, POST query string to base URL to get CDN URL
        parts = url.split('?', 1)
        base_url = parts[0]
        query_string = parts[1] if len(parts) > 1 else ''
        post_headers = {**headers, 'Content-type': 'application/x-www-form-urlencoded'}
        post_response = requests.post(base_url, data=query_string, headers=post_headers, timeout=30)
        post_response.raise_for_status()
        cdn_url = post_response.text.strip()
        response = requests.get(cdn_url, headers=headers, timeout=30)
        response.raise_for_status()

    content = response.content
    files_saved = []

    # Validate downloaded content size
    # Files < 100 bytes are likely error pages or expired URL responses
    if len(content) < 100:
        print(f"    WARNING: Downloaded file is very small ({len(content)} bytes) - may be invalid or expired URL")

    # Check if it's a ZIP file (contains overlay content)
    # ZIP magic bytes = 'PK' (0x50 0x4B)
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
                        merged_data = add_exif_metadata(
                            merged_data, date_str, latitude, longitude, use_local_timezone
                        )

                        # Check for duplicates
                        is_dup, dup_file = is_duplicate_file(merged_data, base_path, check_duplicates)
                        if is_dup:
                            print(f"    Skipped: Duplicate of existing file '{dup_file}'")
                            files_saved.append({
                                'path': dup_file,
                                'size': len(merged_data),
                                'type': 'duplicate',
                                'duplicate_of': dup_file
                            })
                            merge_attempted = True
                        else:
                            output_filename = generate_filename(date_str, extension, use_timestamp_filenames, file_num)
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

                elif is_video and ffmpeg_available and not defer_video_overlays:
                    try:
                        # Create temporary files for main and overlay
                        temp_main = base_path / f"{file_num}-temp-main{extension}"
                        temp_overlay = base_path / f"{file_num}-temp-overlay{extension}"
                        output_filename = generate_filename(date_str, extension, use_timestamp_filenames, file_num)
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
                            # Update video metadata with timezone support
                            if use_local_timezone:
                                update_video_metadata(output_path, date_str, latitude, longitude, use_local_timezone)

                            files_saved.append({
                                'path': output_filename,
                                'size': output_path.stat().st_size,
                                'type': 'merged'
                            })
                            print(f"    Merged video: {output_filename}")

                            # Set file timestamp to match original Snapchat date
                            timestamp = parse_date_to_timestamp(date_str, use_local_timezone, latitude, longitude)
                            set_file_timestamp(output_path, timestamp)

                            # Delete any previously saved -main/-overlay files
                            # Generate base filename to construct the -main and -overlay filenames
                            base_filename = generate_filename(date_str, extension, use_timestamp_filenames, file_num)
                            base_name_no_ext = base_filename.rsplit('.', 1)[0]  # Remove extension

                            # Check for main file with any extension
                            for potential_main in base_path.glob(f"{base_name_no_ext}-main.*"):
                                potential_main.unlink()
                                print(f"    Deleted separate file: {potential_main.name}")

                            # Check for overlay file with any extension
                            for potential_overlay in base_path.glob(f"{base_name_no_ext}-overlay.*"):
                                potential_overlay.unlink()
                                print(f"    Deleted separate file: {potential_overlay.name}")

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
                # Check if this is a deferred video
                is_deferred = is_video and has_overlay and defer_video_overlays and merge_overlays
                if is_deferred:
                    print("    Deferring video overlay merge until end")

                for file_type, file_info in extracted_files.items():
                    file_data = file_info['data']
                    file_ext = file_info['ext']

                    # Add EXIF metadata to images (preserves original format)
                    is_image_file = file_ext.lower() in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.tif']
                    if is_image_file:
                        file_data = add_exif_metadata(
                            file_data, date_str, latitude, longitude, use_local_timezone
                        )

                    # Check for duplicates
                    is_dup, dup_file = is_duplicate_file(file_data, base_path, check_duplicates)
                    if is_dup:
                        print(f"    Skipped: Duplicate of existing file '{dup_file}'")
                        file_info_dict = {
                            'path': dup_file,
                            'size': len(file_data),
                            'type': 'duplicate',
                            'duplicate_of': dup_file
                        }
                        files_saved.append(file_info_dict)
                    else:
                        # Generate base filename, then add -main/-overlay suffix
                        base_filename = generate_filename(date_str, file_ext, use_timestamp_filenames, file_num)
                        base_name_no_ext = base_filename.rsplit('.', 1)[0]  # Remove extension

                        if file_type == 'overlay':
                            output_filename = f"{base_name_no_ext}-overlay{file_ext}"
                        else:
                            output_filename = f"{base_name_no_ext}-main{file_ext}"

                        output_path = base_path / output_filename

                        with open(output_path, 'wb') as f:
                            f.write(file_data)

                        # Update video metadata if applicable
                        is_video_file = file_ext.lower() in ['.mp4', '.mov', '.avi']
                        if is_video_file and use_local_timezone:
                            update_video_metadata(output_path, date_str, latitude, longitude, use_local_timezone)

                        # Set file timestamp to match original Snapchat date
                        timestamp = parse_date_to_timestamp(date_str, use_local_timezone, latitude, longitude)
                        set_file_timestamp(output_path, timestamp)

                        file_info_dict = {
                            'path': output_filename,
                            'size': len(file_data),
                            'type': file_type
                        }

                        # Mark as deferred if applicable
                        if is_deferred:
                            file_info_dict['deferred'] = True

                        files_saved.append(file_info_dict)

    else:
        # Not a ZIP - no overlay present
        # If overlays_only mode is enabled, skip non-ZIP files
        if overlays_only:
            return []

        # For standalone videos, validate MP4 signature
        is_video = extension.lower() in ['.mp4', '.mov', '.avi']
        if is_video and len(content) >= 8:
            # Check for MP4 magic bytes (ftyp box)
            # Valid MP4 files typically have 'ftyp' at bytes 4-8
            if content[4:8] not in [b'ftyp', b'mdat', b'moov', b'wide']:
                print("    WARNING: File may not be a valid video (invalid MP4 signature)")
                print(f"    First 20 bytes: {content[:20]}")
                print("    This might be an HTML error page or expired download link")

        # Add EXIF metadata to images
        is_image = extension.lower() in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.tif']
        if is_image:
            content = add_exif_metadata(content, date_str, latitude, longitude, use_local_timezone)

        # Check for duplicates
        is_dup, dup_file = is_duplicate_file(content, base_path, check_duplicates)
        if is_dup:
            print(f"    Skipped: Duplicate of existing file '{dup_file}'")
            files_saved.append({
                'path': dup_file,
                'size': len(content),
                'type': 'duplicate',
                'duplicate_of': dup_file
            })
        else:
            # Save as regular file
            output_filename = generate_filename(date_str, extension, use_timestamp_filenames, file_num)
            output_path = base_path / output_filename

            with open(output_path, 'wb') as f:
                f.write(content)

            # Update video metadata if applicable
            if is_video and use_local_timezone:
                update_video_metadata(output_path, date_str, latitude, longitude, use_local_timezone)

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


def parse_date_to_timestamp(date_str: str, use_local_timezone: bool = False,
                           latitude: str = 'Unknown', longitude: str = 'Unknown') -> Optional[float]:
    """
    Parse Snapchat date string to Unix timestamp.

    CRITICAL: Snapchat dates are always in UTC format: "2025-11-30 00:31:09 UTC"
    This function must parse them as UTC, not local time!

    Args:
        date_str: Snapchat UTC date string (e.g., "2025-11-30 00:31:09 UTC")
        use_local_timezone: If True, convert to local timezone based on GPS coordinates
        latitude: GPS latitude (required if use_local_timezone=True)
        longitude: GPS longitude (required if use_local_timezone=True)

    Returns:
        Unix timestamp (seconds since epoch)
    """
    try:
        # Remove " UTC" suffix and parse
        date_str_clean = date_str.replace(' UTC', '')

        # CRITICAL: Must parse as UTC, not local time!
        # Using datetime.fromisoformat() with 'Z' suffix would be cleaner, but Snapchat format
        # doesn't use ISO 8601, so we need to manually set UTC timezone
        from datetime import timezone
        dt_utc = datetime.strptime(date_str_clean, '%Y-%m-%d %H:%M:%S')
        dt_utc = dt_utc.replace(tzinfo=timezone.utc)

        # If local timezone conversion requested, convert based on GPS coordinates
        if use_local_timezone and timezone_support:
            try:
                lat = float(latitude) if latitude != 'Unknown' else None
                lon = float(longitude) if longitude != 'Unknown' else None

                if lat is not None and lon is not None:
                    # Get local timezone and convert
                    timezone_str = get_timezone_from_gps(lat, lon)
                    local_datetime = convert_utc_to_local(date_str, timezone_str)
                    return local_datetime.timestamp()
            except (ValueError, TypeError):
                pass  # Fall through to UTC timestamp

        # Return UTC timestamp
        return dt_utc.timestamp()

    except (ValueError, AttributeError) as e:
        print(f"    Warning: Could not parse date '{date_str}': {e}")
        return None


def set_file_timestamp(file_path: Path, timestamp: Optional[float]) -> None:
    """Set file modification and access times to the given timestamp."""
    if timestamp:
        os.utime(file_path, (timestamp, timestamp))


def sanitize_filename(filename: str) -> str:
    sanitized = ''.join('-' if ch in INVALID_FILENAME_CHARS else ch for ch in filename)
    sanitized = sanitized.rstrip(' .')
    return sanitized or "file"


def generate_filename(date_str: str, extension: str, use_timestamp: bool = False, fallback_num: str = "00") -> str:
    """
    Generate filename based on configuration.

    Args:
        date_str: Snapchat date string (e.g., "2025-11-30 00:31:09 UTC")
        extension: File extension (e.g., ".mp4")
        use_timestamp: If True, use timestamp format; if False, use sequential number
        fallback_num: Sequential number to use if use_timestamp is False

    Returns:
        Filename string (e.g., "2025.11.30-00-31-09.mp4" or "01.mp4")
    """
    if use_timestamp:
        try:
            # Parse date string: "2025-11-30 00:31:09 UTC" -> "2025.11.30-00-31-09"
            date_str_clean = date_str.replace(' UTC', '').strip()
            # Replace first two hyphens and space with dots/hyphen
            # "2025-11-30 00:31:09" -> "2025.11.30-00-31-09"
            parts = date_str_clean.split(' ')
            if len(parts) == 2:
                date_part = parts[0].replace('-', '.')  # "2025.11.30"
                time_part = parts[1].replace(':', '-')  # "00-31-09"
                filename = f"{date_part}-{time_part}{extension}"
                return sanitize_filename(filename)
            else:
                # Fallback to sequential if date format is unexpected
                print(f"    Warning: Unexpected date format '{date_str}', using sequential number")
                return sanitize_filename(f"{fallback_num}{extension}")
        except Exception as e:
            print(f"    Warning: Could not parse date for filename '{date_str}': {e}, using sequential number")
            return sanitize_filename(f"{fallback_num}{extension}")
    else:
        return sanitize_filename(f"{fallback_num}{extension}")


def compute_file_hash(file_path: Path) -> str:
    """Compute MD5 hash of a file."""
    md5_hash = hashlib.md5()
    with open(file_path, 'rb') as f:
        # Read file in chunks to handle large files
        for chunk in iter(lambda: f.read(8192), b''):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()


def compute_data_hash(data: bytes) -> str:
    """Compute MD5 hash of byte data."""
    return hashlib.md5(data).hexdigest()


def is_duplicate_file(
    data: bytes,
    output_path: Path,
    check_duplicates: bool
) -> Tuple[bool, Optional[str]]:
    """
    Check if data is a duplicate of any existing file in the output directory.

    CRITICAL DESIGN: This runs DURING download (not post-processing) to immediately
    save bandwidth and disk space. When a duplicate is detected, download is skipped.

    Two-stage detection for performance:
    1. Quick size check (fast, eliminates most non-duplicates)
    2. MD5 hash comparison (only if size matches)

    This is more efficient than checking MD5 first since:
    - File size check is O(1) filesystem metadata read
    - MD5 requires reading entire file content

    Args:
        data: Downloaded file bytes to check
        output_path: Directory containing existing files
        check_duplicates: If False, skip duplicate detection entirely

    Returns:
        Tuple of (is_duplicate: bool, existing_file_path: Optional[str])
        If duplicate found, returns (True, "existing_filename.ext")
        If unique, returns (False, None)
    """
    if not check_duplicates:
        return (False, None)

    # Compute hash of newly downloaded data
    new_hash = compute_data_hash(data)
    new_size = len(data)

    # Check all existing files in output directory
    for existing_file in output_path.iterdir():
        if existing_file.is_file() and existing_file.name != 'metadata.json':
            try:
                existing_size = existing_file.stat().st_size
                # Quick size check first (fast, eliminates most non-matches)
                if existing_size == new_size:
                    # Size matches, compute hash (slower but necessary)
                    existing_hash = compute_file_hash(existing_file)
                    if existing_hash == new_hash:
                        # Exact duplicate found!
                        return (True, existing_file.name)
            except Exception:
                # Ignore errors reading files (permissions, deleted files, etc.)
                continue

    return (False, None)


def detect_and_remove_duplicates(folder_path: Path) -> dict:
    """
    Detect and remove duplicate files based on MD5 hash, filesize, and modification date.
    Returns dict with statistics: {'duplicates_found': int, 'files_deleted': int, 'space_saved': int}
    """
    print("\n" + "=" * 60)
    print("Scanning for duplicate files...")
    print("=" * 60)

    # Get all files in the folder (excluding metadata.json)
    all_files = [f for f in folder_path.iterdir() if f.is_file() and f.name != 'metadata.json']

    if not all_files:
        print("No files found to check for duplicates")
        return {'duplicates_found': 0, 'files_deleted': 0, 'space_saved': 0}

    # Build file info: {file_path: {'md5': str, 'size': int, 'mtime': float}}
    file_info = {}
    print(f"Analyzing {len(all_files)} files...")

    for file_path in all_files:
        try:
            stat = file_path.stat()
            md5 = compute_file_hash(file_path)
            file_info[file_path] = {
                'md5': md5,
                'size': stat.st_size,
                'mtime': stat.st_mtime
            }
        except Exception as e:
            print(f"  Warning: Could not analyze {file_path.name}: {e}")

    # Group files by (md5, size, mtime)
    groups = {}
    for file_path, info in file_info.items():
        key = (info['md5'], info['size'], info['mtime'])
        if key not in groups:
            groups[key] = []
        groups[key].append(file_path)

    # Find duplicate groups (groups with more than 1 file)
    duplicate_groups = {k: v for k, v in groups.items() if len(v) > 1}

    if not duplicate_groups:
        print("No duplicate files found!")
        return {'duplicates_found': 0, 'files_deleted': 0, 'space_saved': 0}

    # Process duplicates: keep first file in each group, delete the rest
    total_duplicates = 0
    files_deleted = 0
    space_saved = 0

    print(f"\nFound {len(duplicate_groups)} duplicate group(s):")

    for (md5, size, mtime), file_list in duplicate_groups.items():
        total_duplicates += len(file_list)
        print(f"\n  Duplicate group (MD5: {md5[:8]}..., Size: {size:,} bytes):")

        # Keep the first file, delete the rest
        keep_file = file_list[0]
        print(f"    KEEP: {keep_file.name}")

        for dup_file in file_list[1:]:
            try:
                dup_file.unlink()
                files_deleted += 1
                space_saved += size
                print(f"    DELETED: {dup_file.name}")
            except Exception as e:
                print(f"    ERROR deleting {dup_file.name}: {e}")

    print("\n" + "=" * 60)
    print(f"Duplicate removal complete!")
    print(f"  Duplicate files found: {total_duplicates}")
    print(f"  Files deleted: {files_deleted}")
    print(f"  Space saved: {space_saved:,} bytes ({space_saved / (1024*1024):.2f} MB)")
    print("=" * 60)

    return {
        'duplicates_found': total_duplicates,
        'files_deleted': files_deleted,
        'space_saved': space_saved
    }


def join_multi_snaps(folder_path: Path, time_threshold_seconds: int = 10) -> dict:
    """
    Detect and join videos that were part of multi-snap stories.
    Groups videos by timestamp (within time_threshold_seconds) and concatenates them.

    IMPORTANT: Preserves metadata from first video in group (GPS + timestamp).
    Reads metadata.json to restore GPS coordinates and date information that would
    otherwise be lost during FFmpeg concat operation.

    Returns dict with statistics: {'groups_found': int, 'videos_joined': int, 'files_deleted': int}
    """
    if not ffmpeg_available:
        print("\nWarning: FFmpeg not available, cannot join multi-snaps")
        return {'groups_found': 0, 'videos_joined': 0, 'files_deleted': 0}

    # Try to load metadata.json to preserve GPS and date information
    metadata_file = folder_path / 'metadata.json'
    metadata_dict = {}
    if metadata_file.exists():
        try:
            import json
            with open(metadata_file, 'r', encoding='utf-8') as f:
                metadata_content = json.load(f)
                # Handle both old format (list) and new format (dict with 'memories' key)
                memories = metadata_content.get('memories', metadata_content) if isinstance(metadata_content, dict) else metadata_content
                # Create a lookup dict by filename
                for memory in memories:
                    if memory.get('files'):
                        for file_info in memory['files']:
                            filename = file_info.get('path', '')
                            if filename:
                                metadata_dict[filename] = {
                                    'date': memory.get('date', 'Unknown'),
                                    'latitude': memory.get('latitude', 'Unknown'),
                                    'longitude': memory.get('longitude', 'Unknown')
                                }
        except Exception as e:
            print(f"Note: Could not load metadata.json: {e}")
            print("Joined videos will not have GPS/date metadata")

    print("\n" + "=" * 60)
    print("Detecting multi-snap videos...")
    print("=" * 60)

    # Get all video files in the folder
    video_extensions = ['.mp4', '.mov', '.avi']
    all_videos = [
        f for f in folder_path.iterdir()
        if f.is_file() and f.suffix.lower() in video_extensions
    ]

    if len(all_videos) < 2:
        print("Not enough videos to check for multi-snaps")
        return {'groups_found': 0, 'videos_joined': 0, 'files_deleted': 0}

    # Get video timestamps (modification time)
    video_info = []
    for video_path in all_videos:
        stat = video_path.stat()
        video_info.append({
            'path': video_path,
            'mtime': stat.st_mtime
        })

    # Sort by timestamp
    video_info.sort(key=lambda x: x['mtime'])

    # Group videos by timestamp proximity (within time_threshold_seconds)
    groups = []
    current_group = [video_info[0]]

    for i in range(1, len(video_info)):
        time_diff = abs(video_info[i]['mtime'] - current_group[-1]['mtime'])

        if time_diff <= time_threshold_seconds:
            # Add to current group
            current_group.append(video_info[i])
        else:
            # Save current group and start new one
            if len(current_group) > 1:
                groups.append(current_group)
            current_group = [video_info[i]]

    # Don't forget the last group
    if len(current_group) > 1:
        groups.append(current_group)

    if not groups:
        print("No multi-snap video groups found")
        return {'groups_found': 0, 'videos_joined': 0, 'files_deleted': 0}

    print(f"\nFound {len(groups)} multi-snap group(s):")

    total_videos_joined = 0
    files_deleted = 0

    for group_idx, group in enumerate(groups, start=1):
        print(f"\n  Group {group_idx} ({len(group)} videos):")
        for video in group:
            print(f"    - {video['path'].name}")

        # Create output filename from first video in group
        first_video = group[0]['path']
        output_name = first_video.stem + '-joined' + first_video.suffix
        output_path = folder_path / output_name

        # Create concat file list for FFmpeg
        concat_list_path = folder_path / f'concat_list_{group_idx}.txt'
        try:
            with open(concat_list_path, 'w', encoding='utf-8') as f:
                for video in group:
                    # FFmpeg concat demuxer requires escaped paths
                    escaped_path = str(video['path'].absolute()).replace("'", "'\\''")
                    f.write(f"file '{escaped_path}'\n")

            # Run FFmpeg to concatenate videos
            cmd = [
                'ffmpeg',
                '-f', 'concat',
                '-safe', '0',
                '-i', str(concat_list_path),
                '-c', 'copy',  # Copy streams without re-encoding
                '-y',
                str(output_path)
            ]

            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=300,
                check=False
            )

            # Check if output was created successfully
            if result.returncode == 0 and output_path.exists() and output_path.stat().st_size > 1000:
                print(f"    Joined: {output_name} ({output_path.stat().st_size:,} bytes)")

                # Set timestamp to match first video
                first_stat = first_video.stat()
                os.utime(output_path, (first_stat.st_atime, first_stat.st_mtime))

                # Restore GPS and date metadata from first video (CRITICAL for Apple Photos)
                first_video_name = first_video.name
                if first_video_name in metadata_dict:
                    metadata_info = metadata_dict[first_video_name]
                    print(f"    Restoring metadata from: {first_video_name}")
                    success = update_video_metadata(
                        output_path,
                        metadata_info['date'],
                        metadata_info['latitude'],
                        metadata_info['longitude'],
                        use_local_timezone=True  # Use local timezone for proper Apple Photos import
                    )
                    if success:
                        print(f"    GPS and date metadata restored to joined video")
                    else:
                        print(f"    Warning: Could not restore metadata to joined video")
                else:
                    print(f"    Note: No metadata found for {first_video_name}, joined video will lack GPS/date info")

                # Delete original videos
                for video in group:
                    video['path'].unlink()
                    files_deleted += 1

                total_videos_joined += len(group)
            else:
                error_msg = result.stderr.decode('utf-8', errors='ignore')
                print(f"    ERROR: Failed to join videos")
                print(f"    FFmpeg error: {error_msg[-200:]}")

        except Exception as e:
            print(f"    ERROR: {str(e)}")
        finally:
            # Clean up concat list file
            if concat_list_path.exists():
                concat_list_path.unlink()

    print("\n" + "=" * 60)
    print(f"Multi-snap joining complete!")
    print(f"  Groups found: {len(groups)}")
    print(f"  Videos joined: {total_videos_joined}")
    print(f"  Files deleted: {files_deleted}")
    print("=" * 60)

    return {
        'groups_found': len(groups),
        'videos_joined': total_videos_joined,
        'files_deleted': files_deleted
    }


def update_existing_timezone_metadata(folder_path: str) -> None:
    """
    Update timezone metadata for already-downloaded files based on GPS coordinates.
    Reads metadata.json and updates:
    - File modification timestamps (converts UTC to local time)
    - EXIF metadata in images (adds timezone-aware timestamps)
    - Video metadata (adds timezone-aware creation time)

    Args:
        folder_path: Path to folder containing downloaded files and metadata.json
    """
    folder = Path(folder_path)
    if not folder.exists() or not folder.is_dir():
        print(f"Error: {folder_path} is not a valid directory!")
        return

    metadata_file = folder / 'metadata.json'
    if not metadata_file.exists():
        print(f"Error: metadata.json not found in {folder_path}!")
        print("This command only works on folders that were created by this script.")
        return

    if not timezone_support:
        print("Error: Timezone support not available!")
        print("Install required packages: pip install timezonefinder pytz")
        return

    print("=" * 60)
    print("Updating timezone metadata for existing files...")
    print("=" * 60)

    # Load metadata
    with open(metadata_file, 'r', encoding='utf-8') as f:
        metadata_list = json.load(f)

    updated_images = 0
    updated_videos = 0
    updated_timestamps = 0
    errors = 0

    for metadata in metadata_list:
        if metadata.get('status') != 'success':
            continue

        files = metadata.get('files', [])
        if not files:
            continue

        date_str = metadata.get('date', 'Unknown')
        latitude = metadata.get('latitude', 'Unknown')
        longitude = metadata.get('longitude', 'Unknown')

        # Skip if no GPS coordinates
        if latitude == 'Unknown' or longitude == 'Unknown':
            continue

        try:
            lat = float(latitude)
            lon = float(longitude)
        except (ValueError, TypeError):
            continue

        # Get timezone for this location
        timezone_str = get_timezone_from_gps(lat, lon)
        local_datetime = convert_utc_to_local(date_str, timezone_str)
        local_timestamp = local_datetime.timestamp()

        print(f"\n#{metadata['number']} - {date_str}")
        print(f"  Location: {lat}, {lon}")
        print(f"  Timezone: {timezone_str}")
        print(f"  Local time: {format_exif_datetime(local_datetime)} {format_exif_offset(local_datetime)}")

        for file_info in files:
            file_path = folder / file_info['path']

            if not file_path.exists():
                print(f"  WARNING: File not found: {file_info['path']}")
                continue

            file_ext = file_path.suffix.lower()
            is_image = file_ext in ['.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.tiff', '.tif']
            is_video = file_ext in ['.mp4', '.mov', '.avi']

            try:
                # Update EXIF for images
                if is_image and piexif is not None and Image is not None:
                    with open(file_path, 'rb') as f:
                        image_data = f.read()

                    # Load image
                    img = Image.open(io.BytesIO(image_data))
                    original_format = img.format

                    # Create EXIF dict
                    exif_dict = {"0th": {}, "Exif": {}, "GPS": {}}

                    # Add timezone-aware datetime
                    exif_date = format_exif_datetime(local_datetime)
                    offset_str = format_exif_offset(local_datetime)

                    exif_dict["0th"][piexif.ImageIFD.DateTime] = exif_date.encode()
                    exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = exif_date.encode()
                    exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = exif_date.encode()
                    exif_dict["Exif"][piexif.ExifIFD.OffsetTime] = offset_str.encode()
                    exif_dict["Exif"][piexif.ExifIFD.OffsetTimeOriginal] = offset_str.encode()
                    exif_dict["Exif"][piexif.ExifIFD.OffsetTimeDigitized] = offset_str.encode()

                    # Add GPS coordinates
                    lat_dms = decimal_to_dms(lat)
                    lon_dms = decimal_to_dms(lon)
                    exif_dict["GPS"][piexif.GPSIFD.GPSLatitude] = lat_dms
                    exif_dict["GPS"][piexif.GPSIFD.GPSLatitudeRef] = b'N' if lat >= 0 else b'S'
                    exif_dict["GPS"][piexif.GPSIFD.GPSLongitude] = lon_dms
                    exif_dict["GPS"][piexif.GPSIFD.GPSLongitudeRef] = b'E' if lon >= 0 else b'W'

                    # Save with EXIF
                    exif_bytes = piexif.dump(exif_dict)
                    output = io.BytesIO()

                    if original_format in ['JPEG', 'JPG']:
                        if img.mode == 'RGBA':
                            img = img.convert('RGB')
                        img.save(output, format='JPEG', quality=95, exif=exif_bytes)
                    elif original_format == 'PNG':
                        try:
                            img.save(output, format='PNG', exif=exif_bytes)
                        except Exception:
                            img.save(output, format='PNG')
                    elif original_format == 'WEBP':
                        img.save(output, format='WEBP', quality=95, exif=exif_bytes)
                    else:
                        # Skip unsupported formats
                        print(f"  Skipping {file_path.name} (format: {original_format})")
                        continue

                    # Write back to file
                    with open(file_path, 'wb') as f:
                        f.write(output.getvalue())

                    print(f"  ✓ Updated EXIF: {file_path.name}")
                    updated_images += 1

                # Update metadata for videos
                elif is_video:
                    success = update_video_metadata(file_path, date_str, latitude, longitude, use_local_timezone=True)
                    if success:
                        updated_videos += 1

                # Update file timestamp
                os.utime(file_path, (local_timestamp, local_timestamp))
                print(f"  ✓ Updated timestamp: {file_path.name}")
                updated_timestamps += 1

            except Exception as e:
                print(f"  ERROR updating {file_path.name}: {e}")
                errors += 1

    print("\n" + "=" * 60)
    print("Update complete!")
    print(f"  Images updated: {updated_images}")
    print(f"  Videos updated: {updated_videos}")
    print(f"  File timestamps updated: {updated_timestamps}")
    if errors > 0:
        print(f"  Errors: {errors}")
    print("=" * 60)


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
            'is_get_request': memory.get('is_get_request', True),
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
        overlay_file = list(folder.glob(f"{base_name.replace(extension, '')}-overlay.*"))

        if not overlay_file:
            print(f"\n[SKIP] {filename}")
            print("  No matching overlay file found")
            skipped_count += 1
            continue

        overlay_file = overlay_file[0]

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


# ============================================================================
# NEW EXPORT FORMAT (2026+) - locally-bundled media
# ============================================================================
# As of ~2026 Snapchat ships Memories already downloaded INSIDE the export
# instead of providing per-row download URLs. The old html/memories_history.html
# still exists, but its download column now reads "N/A" - which is why the
# URL-based parser reports "No memories found" (GitHub issue #23).
#
# New export layout (after unzipping):
#   <root>/index.html
#   <root>/html/memories_history.html    (table; download column is now "N/A")
#   <root>/json/memories_history.json    ({"Saved Media": [{Date, Media Type, Location, ...}]})
#   <root>/memories/memories.html        (gallery referencing local files)
#   <root>/memories/<YYYY-MM-DD>_<UUID>-main.<ext>   (+ optional matching -overlay)
#
# This path processes that local export with NO network access: it pairs each
# -main file with its -overlay (filename convention, same as --merge-existing),
# merges overlays (still the tool's core value-add - Snapchat ships them
# UNmerged), and enriches EXIF/timestamps from json/memories_history.json.

NEW_EXPORT_MEDIA_DIRNAME = 'memories'
VIDEO_SUFFIXES = ('.mp4', '.mov', '.avi', '.m4v')


def _media_type_from_suffix(suffix: str) -> str:
    """Best-effort media type from a file extension."""
    return 'Video' if suffix.lower() in VIDEO_SUFFIXES else 'Image'


def _date_prefix_from_name(name: str) -> Optional[str]:
    """Extract the leading YYYY-MM-DD date from a memory filename, if present."""
    match = re.match(r'(\d{4}-\d{2}-\d{2})', name)
    return match.group(1) if match else None


# Timestamp-join tolerance. A correct extraction of the export preserves each
# file's mtime as its capture time, which equals the JSON "Date" (UTC wall-clock)
# within the ZIP's 2-second (DOS) resolution.
LOCAL_JOIN_TOLERANCE = timedelta(seconds=2)


def _parse_memory_datetime(date_str: str) -> Optional[datetime]:
    """Parse a memories_history 'Date' (e.g. '2016-07-09 20:48:02 UTC').

    Returns a naive datetime holding the UTC wall-clock, or None. It is compared
    against each file's local mtime, which a correct extraction sets to the same
    wall-clock value (DOS zip timestamps are tz-naive wall-clock).
    """
    if not date_str or date_str == 'Unknown':
        return None
    s = date_str.strip()
    if s.endswith('UTC'):
        s = s[:-3].strip()
    try:
        return datetime.strptime(s, '%Y-%m-%d %H:%M:%S')
    except ValueError:
        return None


def locate_export_layout(input_path: str) -> Optional[dict]:
    """Locate the pieces of a new-format (bundled-media) Snapchat export.

    Accepts a path to the export root, the memories/ folder, or any of the
    bundled HTML files (index.html / memories.html / memories_history.html).

    Returns {root, media_dir, gallery_html, json_file} if the path looks like a
    new-format export (a folder containing *-main.* media files), else None.
    """
    p = Path(input_path)

    # Build a list of candidate base directories to inspect.
    candidates = []
    if p.is_file():
        candidates += [p.parent, p.parent.parent]
    elif p.is_dir():
        candidates += [p, p.parent]
    else:
        return None

    for base in candidates:
        if base is None:
            continue
        # The media directory is either a memories/ subfolder or the base itself,
        # identified by the presence of at least one *-main.* file.
        media_dir = None
        for cand in (base / NEW_EXPORT_MEDIA_DIRNAME, base):
            if cand.is_dir() and next(cand.glob('*-main.*'), None) is not None:
                media_dir = cand
                break
        if media_dir is None:
            continue

        root = media_dir.parent if media_dir.name == NEW_EXPORT_MEDIA_DIRNAME else media_dir
        gallery = media_dir / 'memories.html'
        json_file = None
        for cand in (
            root / 'json' / 'memories_history.json',
            root / 'memories_history.json',
            media_dir / 'memories_history.json',
        ):
            if cand.is_file():
                json_file = cand
                break

        return {
            'root': root,
            'media_dir': media_dir,
            'gallery_html': gallery if gallery.is_file() else None,
            'json_file': json_file,
        }

    return None


def parse_memories_json(json_path: Path) -> list:
    """Parse json/memories_history.json into normalized metadata dicts.

    Output keys match the rest of the tool: date, media_type, latitude, longitude.
    The new export leaves the download fields empty, so they are ignored here.
    """
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        print(f"  Warning: Could not read {json_path}: {e}")
        return []

    entries = data.get('Saved Media', []) if isinstance(data, dict) else []
    memories = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        date = (entry.get('Date') or 'Unknown').strip() or 'Unknown'
        media_type = (entry.get('Media Type') or 'Unknown').strip() or 'Unknown'
        latitude = longitude = 'Unknown'
        location = entry.get('Location') or ''
        loc_match = re.search(r'Latitude,\s*Longitude:\s*([-\d.]+),\s*([-\d.]+)', location)
        if loc_match:
            latitude, longitude = loc_match.group(1), loc_match.group(2)
        memories.append({
            'date': date,
            'media_type': media_type,
            'latitude': latitude,
            'longitude': longitude,
        })
    return memories


def parse_gallery_main_order(gallery_html: Path) -> list:
    """Return the ordered list of -main filenames referenced in memories.html.

    This preserves the order in which Snapchat lists memories, which is used to
    align local files with json/memories_history.json entries (the JSON has no
    filename field). Only the *-main.* references are needed; overlays are paired
    later by filename convention.
    """
    try:
        text = gallery_html.read_text(encoding='utf-8', errors='ignore')
    except OSError:
        return []
    order = []
    for match in re.finditer(r'src\s*=\s*"([^"]*?-main\.[^"]+)"', text):
        order.append(os.path.basename(match.group(1)))
    return order


def find_overlay_for_main(main_file: Path) -> Optional[Path]:
    """Find the -overlay file matching a -main file by shared base name.

    e.g. 2026-05-13_<UUID>-main.mp4  ->  2026-05-13_<UUID>-overlay.png
    The overlay may use a different extension (often .png for image overlays).
    """
    name = main_file.name
    marker = '-main'
    if marker not in name:
        return None
    base = name[:name.rindex(marker)]  # date + UUID portion (no glob metachars)
    return next(iter(sorted(main_file.parent.glob(f'{base}-overlay.*'))), None)


def build_local_memories(layout: dict) -> list:
    """Build the ordered list of memories to process from a new-format export.

    Each item: {main_file, overlay_file, date, media_type, latitude, longitude}.

    Files are ordered by the gallery (memories.html, chronological) when available,
    else by filename. Each file is matched to a JSON metadata entry that shares its
    capture DATE (the JSON has no filename field, and - importantly - its entries are
    ordered newest-first while the gallery/files are oldest-first, so positional
    alignment would mismatch everything). Matching by date is direction-agnostic:
    within a date, entries are consumed in chronological order, which lines up with
    the gallery's chronological order. Files with no matching JSON entry fall back to
    the filename's date (no GPS), so timestamps stay correct regardless.
    """
    media_dir = layout['media_dir']
    main_files = sorted(media_dir.glob('*-main.*'))
    by_name = {f.name: f for f in main_files}

    # Order main files by the gallery first, then append any not referenced there.
    ordered_mains = []
    seen = set()
    if layout['gallery_html']:
        for name in parse_gallery_main_order(layout['gallery_html']):
            f = by_name.get(name)
            if f is not None and f.name not in seen:
                ordered_mains.append(f)
                seen.add(f.name)
    for f in main_files:
        if f.name not in seen:
            ordered_mains.append(f)
            seen.add(f.name)

    # Match each media file to its JSON entry.
    #
    # Primary key: the file's capture timestamp. A correct extraction preserves
    # each file's mtime as its capture time, which equals the UTC wall-clock in
    # the JSON "Date" field (verified across a full export part: 100% of files
    # fall within 2s of a JSON entry). Matching on the timestamp instead of by
    # date-bucket ordering fixes GPS mis-assignment on days with multiple
    # memories, where the gallery order does not track the JSON's chronological
    # order within the day.
    #
    # Fallback (per file): if no unused JSON entry lies within the timestamp
    # tolerance (e.g. the archive was extracted without preserving mtimes), we
    # revert to the original date-grouped chronological queue, so behavior is
    # never worse than the previous approach.
    meta_entries = parse_memories_json(layout['json_file']) if layout['json_file'] else []

    records = []                    # each: {'m': entry, 'dt': datetime|None, 'used': False}
    by_date = defaultdict(deque)    # fallback: YYYY-MM-DD -> chronological queue of records
    for m in sorted(meta_entries, key=lambda e: e['date']):
        rec = {'m': m, 'dt': _parse_memory_datetime(m['date']), 'used': False}
        records.append(rec)
        day = _date_prefix_from_name(m['date'])
        if day:
            by_date[day].append(rec)
    dated = sorted((r for r in records if r['dt'] is not None), key=lambda r: r['dt'])
    dated_times = [r['dt'] for r in dated]

    def _match_by_timestamp(main_file):
        try:
            mt = datetime.fromtimestamp(main_file.stat().st_mtime)
        except OSError:
            return None
        lo = bisect.bisect_left(dated_times, mt - LOCAL_JOIN_TOLERANCE)
        hi = bisect.bisect_right(dated_times, mt + LOCAL_JOIN_TOLERANCE)
        want = _media_type_from_suffix(main_file.suffix)
        best = None
        best_rank = None
        for r in dated[lo:hi]:
            if r['used']:
                continue
            delta = abs((r['dt'] - mt).total_seconds())
            # Prefer a matching media type first, then the nearest timestamp.
            rank = (0 if r['m'].get('media_type') == want else 1, delta)
            if best_rank is None or rank < best_rank:
                best, best_rank = r, rank
        return best

    memories = []
    ts_matched = fb_matched = unmatched = 0
    for main_file in ordered_mains:
        rec = _match_by_timestamp(main_file)
        if rec is not None:
            ts_matched += 1
        else:
            # Fallback: next unused JSON entry sharing the file's date.
            file_day = _date_prefix_from_name(main_file.name)
            queue = by_date.get(file_day)
            while queue:
                cand = queue.popleft()
                if not cand['used']:
                    rec = cand
                    fb_matched += 1
                    break

        meta = {}
        if rec is not None:
            rec['used'] = True
            meta = rec['m']
        elif meta_entries:
            unmatched += 1

        file_day = _date_prefix_from_name(main_file.name)
        date = meta.get('date', 'Unknown')
        if date == 'Unknown' and file_day:
            date = f"{file_day} 00:00:00 UTC"

        memories.append({
            'main_file': main_file,
            'overlay_file': find_overlay_for_main(main_file),
            'date': date,
            'media_type': meta.get('media_type') or _media_type_from_suffix(main_file.suffix),
            'latitude': meta.get('latitude', 'Unknown'),
            'longitude': meta.get('longitude', 'Unknown'),
        })

    if fb_matched:
        print(f"  Note: {fb_matched} file(s) matched by date fallback "
              f"(mtimes not preserved on extraction?).")
    if unmatched:
        print(f"  Warning: {unmatched} media file(s) had no JSON entry; "
              f"used the filename date only (no GPS).")
    leftover = sum(1 for r in records if not r['used'])
    if leftover:
        print(f"  Note: {leftover} JSON metadata entr(ies) had no matching media file.")
    return memories


def process_local_export(
    input_path: str,
    output_dir: str = 'processed_memories',
    merge_overlays: bool = True,
    use_timestamp_filenames: bool = False,
    use_local_timezone: bool = False,
    videos_only: bool = False,
    pictures_only: bool = False,
    overlays_only: bool = False,
) -> bool:
    """Process a new-format (bundled-media) Snapchat export.

    No network access: pairs -main/-overlay files, merges overlays, embeds
    EXIF/video metadata and timestamps from json/memories_history.json, and
    writes results to output_dir. Originals in the export are never modified.

    Returns True if the input was a new-format export (and was processed),
    False if it did not look like one (so the caller can fall back).
    """
    layout = locate_export_layout(input_path)
    if layout is None:
        return False

    print("Detected new Snapchat export format (media bundled locally).")
    print(f"  Export root: {layout['root']}")
    print(f"  Media dir:   {layout['media_dir']}")
    print(f"  Metadata:    {layout['json_file'] or '(none - using filename dates)'}")
    print("=" * 60)

    memories = build_local_memories(layout)
    if not memories:
        print("No -main media files found in the export!")
        return True

    if use_local_timezone and not timezone_support:
        print("⚠ Timezone support disabled (missing timezonefinder/pytz); using UTC")
        use_local_timezone = False

    output_path = Path(output_dir)
    output_path.mkdir(exist_ok=True)

    metadata_list = []
    merged_count = copied_count = skipped_count = error_count = 0

    for idx, mem in enumerate(memories, start=1):
        main_file = mem['main_file']
        overlay_file = mem['overlay_file']
        media_type = mem['media_type']
        is_video = main_file.suffix.lower() in VIDEO_SUFFIXES

        # Apply media-type / overlay filters.
        if (videos_only and not is_video) or (pictures_only and is_video) or \
                (overlays_only and overlay_file is None):
            skipped_count += 1
            continue

        # Output filename: timestamp-based, or the export's clean base (no -main).
        if use_timestamp_filenames and mem['date'] != 'Unknown':
            out_name = generate_filename(mem['date'], main_file.suffix, True, f"{idx:02d}")
        else:
            out_name = main_file.name.replace('-main', '')
        out_file = output_path / out_name

        # Log only the on-disk filename (which already carries the date) + overlay;
        # the date/GPS/timestamp are recorded in metadata.json and embedded in the
        # files. Avoid echoing parsed date/location to stdout.
        print(f"\n[{idx}/{len(memories)}] {main_file.name}")
        if overlay_file:
            print(f"  Overlay: {overlay_file.name}")

        entry = {
            'number': idx,
            'date': mem['date'],
            'media_type': media_type,
            'latitude': mem['latitude'],
            'longitude': mem['longitude'],
            'source_main': main_file.name,
            'source_overlay': overlay_file.name if overlay_file else None,
        }

        try:
            did_merge = False
            if is_video:
                if overlay_file and merge_overlays:
                    if ffmpeg_available:
                        print("  Merging video overlay (this may take a while)...")
                        if merge_video_overlay(main_file, overlay_file, out_file):
                            did_merge = True
                        else:
                            print("  Video merge failed; copying main file instead")
                            shutil.copy2(main_file, out_file)
                    else:
                        print("  FFmpeg unavailable; copying main file (overlay kept separate)")
                        shutil.copy2(main_file, out_file)
                        shutil.copy2(overlay_file, output_path / overlay_file.name.replace('-main', ''))
                else:
                    shutil.copy2(main_file, out_file)
                # Embed creation time + GPS into the video container.
                update_video_metadata(out_file, mem['date'], mem['latitude'],
                                      mem['longitude'], use_local_timezone)
            else:
                with open(main_file, 'rb') as f:
                    image_data = f.read()
                if overlay_file and merge_overlays:
                    if Image is not None:
                        with open(overlay_file, 'rb') as f:
                            overlay_data = f.read()
                        image_data = merge_image_overlay(image_data, overlay_data)
                        did_merge = True
                    else:
                        print("  Pillow unavailable; copying main image without merge")
                # Embed EXIF (GPS + timestamp) regardless of merge.
                image_data = add_exif_metadata(image_data, mem['date'], mem['latitude'],
                                               mem['longitude'], use_local_timezone)
                with open(out_file, 'wb') as f:
                    f.write(image_data)

            # Preserve capture date as the file's modification time.
            timestamp = parse_date_to_timestamp(mem['date'], use_local_timezone,
                                                mem['latitude'], mem['longitude'])
            if timestamp:
                set_file_timestamp(out_file, timestamp)
            else:
                # Fall back to the original file's mtime.
                src_stat = main_file.stat()
                os.utime(out_file, (src_stat.st_atime, src_stat.st_mtime))

            size = out_file.stat().st_size
            print(f"  {'Merged' if did_merge else 'Saved'} ({size:,} bytes)")
            entry['status'] = 'success'
            entry['type'] = 'merged' if did_merge else 'single'
            entry['files'] = [{'path': out_name, 'size': size,
                               'type': 'merged' if did_merge else 'single'}]
            if did_merge:
                merged_count += 1
            else:
                copied_count += 1
        except Exception as e:  # noqa: BLE001 - never let one bad file abort the batch
            print(f"  ERROR: {e}")
            entry['status'] = 'failed'
            entry['error'] = str(e)
            error_count += 1

        metadata_list.append(entry)

    save_metadata(metadata_list, output_path)

    print("\n" + "=" * 60)
    print("Local export processing complete!")
    print(f"Summary: {merged_count} merged, {copied_count} copied, "
          f"{skipped_count} skipped, {error_count} errors")
    print(f"Output: {output_path}/  (originals in the export were not modified)")
    return True


def download_all_memories(
    html_path: str,
    output_dir: str = 'memories',
    resume: bool = False,
    retry_failed: bool = False,
    merge_overlays: bool = False,
    defer_video_overlays: bool = False,
    videos_only: bool = False,
    pictures_only: bool = False,
    overlays_only: bool = False,
    use_timestamp_filenames: bool = False,
    remove_duplicates: bool = False,
    threads: int = 2,
    should_join_multi_snaps: bool = False,
    use_local_timezone: bool = False
) -> None:
    """Download all memories with sequential naming and metadata preservation.

    If defer_video_overlays is True, videos with overlays are saved as -main/-overlay
    files during download, then merged at the end.

    If remove_duplicates is True, duplicate files are detected during download (before saving)
    to prevent re-downloading and save bandwidth/disk space immediately.

    If should_join_multi_snaps is True, videos taken within 10 seconds are automatically joined.

    If use_local_timezone is True, EXIF metadata includes timezone-aware timestamps
    based on GPS coordinates, and video metadata is updated with local time.

    If threads > 1, downloads are processed in parallel (best for fast connections).
    """

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

    # Show timezone support status
    if use_local_timezone:
        if timezone_support:
            print("✓ Timezone-aware metadata enabled")
            print("  EXIF timestamps will be converted to local time based on GPS coordinates")
            print("  Video metadata will include timezone information")
        else:
            print("⚠ Warning: Timezone support disabled (missing timezonefinder/pytz)")
            print("  Install with: pip install -r requirements.txt")
            print("  Falling back to UTC timestamps")
            use_local_timezone = False

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
    deferred_videos = []  # Track videos to merge later
    threads = max(1, int(threads))

    if threads <= 1:
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
                    defer_video_overlays,
                    metadata['date'], metadata['latitude'], metadata['longitude'],
                    overlays_only,
                    use_timestamp_filenames,
                    remove_duplicates,
                    use_local_timezone,
                    memory.get('is_get_request', True)
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
                timestamp = parse_date_to_timestamp(
                    metadata['date'], use_local_timezone,
                    metadata.get('latitude', 'Unknown'), metadata.get('longitude', 'Unknown')
                )
                if timestamp:
                    for file_info in files_saved:
                        file_path = output_path / file_info['path']
                        set_file_timestamp(file_path, timestamp)
                    print(f"  Timestamp set to: {metadata['date']}")

                # Update metadata with file info
                metadata['status'] = 'success'
                metadata['files'] = files_saved

                # Track deferred videos for later processing
                if any(f.get('deferred') for f in files_saved):
                    deferred_videos.append((file_num, metadata, files_saved))

            except (OSError, requests.RequestException, zipfile.BadZipFile) as e:
                print(f"  ERROR: {str(e)}")
                metadata['status'] = 'failed'
                metadata['error'] = str(e)

            # Save metadata after each download
            save_metadata(metadata_list, output_path)
    else:
        print(f"Using {threads} threads for downloads")
        completed = 0

        def download_worker(idx: int, metadata: dict) -> dict:
            memory = memories[idx]
            file_num = f"{metadata['number']:02d}"
            extension = get_file_extension(metadata.get('media_type', 'Image'))

            print(f"\nStarting #{metadata['number']}")
            print(f"  Date: {metadata['date']}")
            print(f"  Type: {metadata['media_type']}")
            print(f"  Location: {metadata['latitude']}, {metadata['longitude']}")

            try:
                files_saved = download_and_extract(
                    memory['url'], output_path, file_num, extension, merge_overlays,
                    defer_video_overlays,
                    metadata['date'], metadata['latitude'], metadata['longitude'],
                    overlays_only,
                    use_timestamp_filenames,
                    remove_duplicates,
                    use_local_timezone,
                    memory.get('is_get_request', True)
                )

                if len(files_saved) == 0:
                    print("  Skipped: No overlay detected (overlays-only mode)")
                    return {
                        'status': 'skipped',
                        'skip_reason': 'no_overlay',
                        'file_num': file_num
                    }

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

                timestamp = parse_date_to_timestamp(
                    metadata['date'], use_local_timezone,
                    metadata.get('latitude', 'Unknown'), metadata.get('longitude', 'Unknown')
                )
                if timestamp:
                    for file_info in files_saved:
                        file_path = output_path / file_info['path']
                        set_file_timestamp(file_path, timestamp)
                    print(f"  Timestamp set to: {metadata['date']}")

                return {
                    'status': 'success',
                    'files_saved': files_saved,
                    'file_num': file_num,
                    'deferred': any(f.get('deferred') for f in files_saved)
                }

            except (OSError, requests.RequestException, zipfile.BadZipFile) as e:
                print(f"  ERROR: {str(e)}")
                return {
                    'status': 'failed',
                    'error': str(e),
                    'file_num': file_num
                }

        futures = {}
        with ThreadPoolExecutor(max_workers=threads) as executor:
            for idx, metadata in items_to_download:
                if metadata.get('status') == 'success' and metadata.get('files') and not videos_only and not pictures_only:
                    completed += 1
                    print(f"[{completed}/{total_items}] #{metadata['number']} Already downloaded, skipping...")
                    continue

                metadata['status'] = 'in_progress'
                save_metadata(metadata_list, output_path)
                futures[executor.submit(download_worker, idx, metadata)] = (idx, metadata)

            for future in as_completed(futures):
                idx, metadata = futures[future]
                try:
                    result = future.result()
                except Exception as e:
                    result = {
                        'status': 'failed',
                        'error': str(e),
                        'file_num': f"{metadata.get('number', 0):02d}"
                    }

                status = result.get('status')
                if status == 'success':
                    metadata['status'] = 'success'
                    metadata['files'] = result.get('files_saved', [])
                    if result.get('deferred'):
                        deferred_videos.append((result.get('file_num'), metadata, result.get('files_saved', [])))
                elif status == 'skipped':
                    metadata['status'] = 'skipped'
                    metadata['skip_reason'] = result.get('skip_reason', 'no_overlay')
                else:
                    metadata['status'] = 'failed'
                    metadata['error'] = result.get('error', 'Unknown error')

                save_metadata(metadata_list, output_path)
                completed += 1

                if status == 'success':
                    summary = "Completed"
                elif status == 'skipped':
                    summary = "Skipped"
                else:
                    summary = f"Failed: {metadata.get('error', 'Unknown error')}"

                print(f"[{completed}/{total_items}] #{metadata['number']} {summary}")

    # Process deferred video overlays
    if deferred_videos:
        print("\n" + "=" * 60)
        print(f"Processing {len(deferred_videos)} deferred video overlay(s)...")
        print("=" * 60)

        for i, (file_num, metadata, files_saved) in enumerate(deferred_videos, start=1):
            print(f"\n[{i}/{len(deferred_videos)}] Processing deferred video #{metadata['number']}")

            # Find main and overlay files
            main_file = None
            overlay_file = None
            for file_info in files_saved:
                file_path = output_path / file_info['path']
                if file_info['type'] == 'main':
                    main_file = file_path
                elif file_info['type'] == 'overlay':
                    overlay_file = file_path

            if main_file and overlay_file:
                try:
                    # Determine output filename
                    extension = main_file.suffix
                    output_filename = generate_filename(metadata['date'], extension, use_timestamp_filenames, file_num)
                    merged_file = output_path / output_filename

                    # Merge videos
                    print("  Merging video overlay (this may take a while)...")
                    success = merge_video_overlay(main_file, overlay_file, merged_file)

                    if success:
                        # Update video metadata with timezone support
                        if use_local_timezone:
                            update_video_metadata(
                                merged_file, metadata['date'], 
                                metadata['latitude'], metadata['longitude'], 
                                use_local_timezone
                            )

                        # Update metadata to reflect merged file
                        metadata['files'] = [{
                            'path': output_filename,
                            'size': merged_file.stat().st_size,
                            'type': 'merged'
                        }]

                        # Set timestamp
                        timestamp = parse_date_to_timestamp(
                            metadata['date'], use_local_timezone,
                            metadata.get('latitude', 'Unknown'), metadata.get('longitude', 'Unknown')
                        )
                        if timestamp:
                            set_file_timestamp(merged_file, timestamp)

                        # Delete -main and -overlay files
                        if main_file.exists():
                            main_file.unlink()
                            print(f"  Deleted: {main_file.name}")
                        if overlay_file.exists():
                            overlay_file.unlink()
                            print(f"  Deleted: {overlay_file.name}")

                        print(f"  Success: {output_filename} ({merged_file.stat().st_size:,} bytes)")
                    else:
                        print("  ERROR: Video merge failed, keeping separate files")

                except Exception as e:
                    print(f"  ERROR: {str(e)}")
                    print("  Keeping separate -main/-overlay files")

        # Save metadata after deferred processing
        save_metadata(metadata_list, output_path)
        print("\n" + "=" * 60)
        print("Deferred video processing complete!")

    # Final save
    metadata_file = output_path / 'metadata.json'
    save_metadata(metadata_list, output_path)

    print("\n" + "=" * 60)
    print("Download complete!")
    print(f"Files saved to: {output_path.absolute()}")
    print(f"Metadata saved to: {metadata_file.absolute()}")

    # Note: Duplicate detection happens during download when --remove-duplicates is enabled
    # This prevents re-downloading and saves bandwidth/disk space immediately

    # Join multi-snaps if requested
    if should_join_multi_snaps:
        join_multi_snaps(output_path)

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
    if use_local_timezone and timezone_support:
        print("\nTimezone-aware metadata has been embedded in all images and videos!")
        print("Photos should now display with correct local timestamps in your photo library.")


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
        '-o', '--output',
        type=str,
        default='memories',
        metavar='DIR',
        help='Output directory for downloaded files (default: memories)'
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
        '--threads',
        type=int,
        default=1,
        metavar='N',
        help='Number of parallel downloads (default: 1)'
    )
    parser.add_argument(
        '--merge-overlays',
        action='store_true',
        help='Merge overlay images and videos on top of main content (requires FFmpeg for videos)'
    )
    parser.add_argument(
        '--defer-video-overlays',
        action='store_true',
        help='Download all memories first, then process video overlays at the end. Only applies when --merge-overlays is enabled.'
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
    parser.add_argument(
        '--timestamp-filenames',
        action='store_true',
        help='Name files as YYYY.MM.DD-HH-MM-SS.ext based on capture date for easy sorting'
    )
    parser.add_argument(
        '--remove-duplicates',
        action='store_true',
        help='Automatically detect and remove duplicate files based on MD5 hash, filesize, and date'
    )
    parser.add_argument(
        '--join-multi-snaps',
        action='store_true',
        help='Automatically detect and join multi-snap videos (videos taken within 10 seconds of each other)'
    )
    parser.add_argument(
        '--local-timezone',
        action='store_true',
        help='Enable timezone-aware EXIF metadata based on GPS coordinates (requires timezonefinder and pytz)'
    )
    parser.add_argument(
        '--update-timezone',
        type=str,
        metavar='FOLDER',
        help='Update timezone metadata for already-downloaded files (requires timezonefinder and pytz)'
    )
    parser.add_argument(
        '--local-export',
        action='store_true',
        help='Process a new-format Snapchat export whose media is bundled locally '
             '(no download URLs). Auto-detected when an export folder is provided.'
    )
    parser.add_argument(
        '--no-merge',
        action='store_true',
        help='In --local-export mode, keep -main/-overlay files separate instead of merging them'
    )

    args = parser.parse_args()

    # Handle --merge-existing mode (separate from normal download mode)
    if args.merge_existing:
        merge_existing_files(args.merge_existing)
        sys.exit(0)

    # Handle --update-timezone mode (separate from normal download mode)
    if args.update_timezone:
        update_existing_timezone_metadata(args.update_timezone)
        sys.exit(0)

    # Handle new export format (2026+): media bundled locally, no download URLs.
    # Forced with --local-export, or auto-detected for a genuine new-format export
    # (a folder with *-main.* media plus a memories.html gallery or JSON metadata).
    export_layout = locate_export_layout(args.html_file)
    is_new_export = export_layout is not None and (
        export_layout['gallery_html'] is not None or export_layout['json_file'] is not None
    )
    if args.local_export or is_new_export:
        if export_layout is None:
            print(f"Error: '{args.html_file}' does not look like a new-format export "
                  f"(no '*-main.*' media files found).")
            sys.exit(1)
        process_local_export(
            args.html_file,
            output_dir=(args.output if args.output != 'memories' else 'processed_memories'),
            merge_overlays=not args.no_merge,
            use_timestamp_filenames=args.timestamp_filenames,
            use_local_timezone=args.local_timezone,
            videos_only=args.videos_only,
            pictures_only=args.pictures_only,
            overlays_only=args.overlays_only,
        )
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
    output_dir = args.output
    resume_mode = args.resume
    retry_failed_mode = args.retry_failed
    test_mode = args.test
    merge_overlays_mode = args.merge_overlays
    defer_video_overlays_mode = args.defer_video_overlays
    videos_only_mode = args.videos_only
    pictures_only_mode = args.pictures_only
    overlays_only_mode = args.overlays_only
    timestamp_filenames_mode = args.timestamp_filenames
    remove_duplicates_mode = args.remove_duplicates
    threads_mode = max(1, int(args.threads))
    join_multi_snaps_mode = args.join_multi_snaps
    local_timezone_mode = args.local_timezone

    # Optional: limit number of downloads for testing
    # Pass --test to download only first 3 files
    if test_mode:
        print("TEST MODE: Downloading only first 3 memories\n")
        memories = parse_html_file(HTML_FILE)
        memories = memories[:3]  # Limit to first 3

        output_path = Path(output_dir)
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
                    defer_video_overlays_mode,
                    metadata['date'], metadata['latitude'], metadata['longitude'],
                    False,  # overlays_only not used in test mode
                    timestamp_filenames_mode,
                    remove_duplicates_mode,
                    local_timezone_mode,
                    memory.get('is_get_request', True)
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
                timestamp = parse_date_to_timestamp(
                    metadata['date'], local_timezone_mode,
                    metadata.get('latitude', 'Unknown'), metadata.get('longitude', 'Unknown')
                )
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
            output_dir=output_dir,
            resume=resume_mode,
            retry_failed=retry_failed_mode,
            merge_overlays=merge_overlays_mode,
            defer_video_overlays=defer_video_overlays_mode,
            videos_only=videos_only_mode,
            pictures_only=pictures_only_mode,
            overlays_only=overlays_only_mode,
            use_timestamp_filenames=timestamp_filenames_mode,
            remove_duplicates=remove_duplicates_mode,
            threads=threads_mode,
            should_join_multi_snaps=join_multi_snaps_mode,
            use_local_timezone=local_timezone_mode
        )
