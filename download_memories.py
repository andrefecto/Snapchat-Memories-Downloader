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
from pathlib import Path
from html.parser import HTMLParser
from datetime import datetime
import zipfile
import io
import subprocess
import hashlib

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
                    # Use regex to extract URL from JavaScript function call
                    url_match = re.search(r"downloadMemories\('([^']+)'", attr_value)
                    if url_match:
                        self.current_row['url'] = url_match.group(1)

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
    
    Args:
        video_path: Path to video file
        date_str: Snapchat date string (e.g., "2025-11-30 00:31:09 UTC")
        latitude: Latitude as string (e.g., "34.052235") 
        longitude: Longitude as string (e.g., "-118.243683")
        use_local_timezone: If True, convert UTC to local timezone
        
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
            creation_time = local_datetime.isoformat()  # ISO 8601 with timezone
        else:
            # Fallback to UTC
            date_clean = date_str.replace(' UTC', '')
            try:
                dt = datetime.strptime(date_clean, '%Y-%m-%d %H:%M:%S')
                creation_time = dt.isoformat() + 'Z'  # UTC format
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
        # Restore backup if it exists
        backup_path = video_path.with_suffix(video_path.suffix + '.backup')
        if backup_path.exists():
            backup_path.rename(video_path)
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
    defer_video_overlays: bool = False,
    date_str: str = 'Unknown',
    latitude: str = 'Unknown',
    longitude: str = 'Unknown',
    overlays_only: bool = False,
    use_timestamp_filenames: bool = False,
    check_duplicates: bool = False,
    use_local_timezone: bool = False
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
        use_timestamp_filenames: If True, use YYYY.MM.DD-HH:MM:SS.ext naming
        check_duplicates: If True, skip download if identical file exists
        use_local_timezone: If True, convert UTC to local timezone in EXIF/metadata

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
    response = requests.get(url, headers=headers, timeout=30)
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
                            timestamp = parse_date_to_timestamp(date_str)
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
                        timestamp = parse_date_to_timestamp(date_str)
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


def generate_filename(date_str: str, extension: str, use_timestamp: bool = False, fallback_num: str = "00") -> str:
    """
    Generate filename based on configuration.

    Args:
        date_str: Snapchat date string (e.g., "2025-11-30 00:31:09 UTC")
        extension: File extension (e.g., ".mp4")
        use_timestamp: If True, use timestamp format; if False, use sequential number
        fallback_num: Sequential number to use if use_timestamp is False

    Returns:
        Filename string (e.g., "2025.11.30-00:31:09.mp4" or "01.mp4")
    """
    if use_timestamp:
        try:
            # Parse date string: "2025-11-30 00:31:09 UTC" -> "2025.11.30-00:31:09"
            date_str_clean = date_str.replace(' UTC', '').strip()
            # Replace first two hyphens and space with dots/hyphen
            # "2025-11-30 00:31:09" -> "2025.11.30-00:31:09"
            parts = date_str_clean.split(' ')
            if len(parts) == 2:
                date_part = parts[0].replace('-', '.')  # "2025.11.30"
                time_part = parts[1]  # "00:31:09"
                filename = f"{date_part}-{time_part}{extension}"
                return filename
            else:
                # Fallback to sequential if date format is unexpected
                print(f"    Warning: Unexpected date format '{date_str}', using sequential number")
                return f"{fallback_num}{extension}"
        except Exception as e:
            print(f"    Warning: Could not parse date for filename '{date_str}': {e}, using sequential number")
            return f"{fallback_num}{extension}"
    else:
        return f"{fallback_num}{extension}"


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


def is_duplicate_file(data: bytes, output_path: Path, check_duplicates: bool) -> tuple[bool, str | None]:
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
        Tuple of (is_duplicate: bool, existing_file_path: str | None)
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
    Returns dict with statistics: {'groups_found': int, 'videos_joined': int, 'files_deleted': int}
    """
    if not ffmpeg_available:
        print("\nWarning: FFmpeg not available, cannot join multi-snaps")
        return {'groups_found': 0, 'videos_joined': 0, 'files_deleted': 0}

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
    join_multi_snaps: bool = False,
    use_local_timezone: bool = False
) -> None:
    """Download all memories with sequential naming and metadata preservation.

    If defer_video_overlays is True, videos with overlays are saved as -main/-overlay
    files during download, then merged at the end.

    If remove_duplicates is True, duplicate files are detected during download (before saving)
    to prevent re-downloading and save bandwidth/disk space immediately.

    If join_multi_snaps is True, videos taken within 10 seconds are automatically joined.

    If use_local_timezone is True, EXIF metadata includes timezone-aware timestamps
    based on GPS coordinates, and video metadata is updated with local time.
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
                use_local_timezone
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

            # Track deferred videos for later processing
            if any(f.get('deferred') for f in files_saved):
                deferred_videos.append((file_num, metadata, files_saved))

        except (OSError, requests.RequestException, zipfile.BadZipFile) as e:
            print(f"  ERROR: {str(e)}")
            metadata['status'] = 'failed'
            metadata['error'] = str(e)

        # Save metadata after each download
        save_metadata(metadata_list, output_path)

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
                        timestamp = parse_date_to_timestamp(metadata['date'])
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
    if join_multi_snaps:
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
        help='Name files as YYYY.MM.DD-HH:MM:SS.ext based on capture date for easy sorting'
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
                    local_timezone_mode
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
            join_multi_snaps=join_multi_snaps_mode,
            use_local_timezone=local_timezone_mode
        )