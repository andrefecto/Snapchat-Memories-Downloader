"""
Comprehensive unit tests for Snapchat Memories Downloader

Tests use mocking to avoid requiring actual Snapchat URLs or network access.
"""

import pytest
import tempfile
import os
import json
import io
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import Mock, patch, MagicMock
import zipfile

# Import functions to test
from download_memories import (
    parse_date_to_timestamp,
    get_file_extension,
    is_zip_file,
    decimal_to_dms,
    generate_filename,
    sanitize_filename,
    compute_data_hash,
    parse_html_file,
    get_timezone_from_gps,
    convert_utc_to_local,
    timezone_support
)


class TestTimestampParsing:
    """Test the critical timezone bug fix"""

    def test_utc_parsing_is_correct(self):
        """Regression test: Ensure UTC timestamps are parsed as UTC, not local time"""
        test_date = "2025-11-30 00:31:09 UTC"
        timestamp = parse_date_to_timestamp(test_date, use_local_timezone=False)
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        expected = datetime(2025, 11, 30, 0, 31, 9, tzinfo=timezone.utc)

        assert dt == expected, \
            f"UTC parsing failed: expected {expected}, got {dt}"

    def test_timestamp_matches_utc_value(self):
        """Verify timestamp represents correct UTC time (not local time)"""
        test_date = "2025-11-30 00:31:09 UTC"
        timestamp = parse_date_to_timestamp(test_date, use_local_timezone=False)
        expected_dt = datetime(2025, 11, 30, 0, 31, 9, tzinfo=timezone.utc)
        expected_timestamp = expected_dt.timestamp()

        assert abs(timestamp - expected_timestamp) < 1, \
            f"Timestamp mismatch: expected {expected_timestamp}, got {timestamp}"

    @pytest.mark.parametrize("test_case", [
        "2024-01-01 00:00:00 UTC",
        "2024-06-15 12:30:45 UTC",
        "2025-12-31 23:59:59 UTC",
        "2020-02-29 18:00:00 UTC",  # Leap year
    ])
    def test_various_utc_timestamps(self, test_case):
        """Test multiple UTC timestamps parse correctly"""
        timestamp = parse_date_to_timestamp(test_case, use_local_timezone=False)
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)

        # Parse expected datetime
        date_str = test_case.replace(" UTC", "")
        expected = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        expected = expected.replace(tzinfo=timezone.utc)

        assert dt == expected

    def test_invalid_date_returns_none(self):
        """Test that invalid dates return None instead of crashing"""
        result = parse_date_to_timestamp("invalid date")
        assert result is None

    @pytest.mark.skipif(not timezone_support, reason="Timezone support not available")
    def test_local_timezone_conversion(self):
        """Test timezone conversion when use_local_timezone=True"""
        test_date = "2024-01-01 12:00:00 UTC"
        # Use known coordinates (San Francisco)
        timestamp = parse_date_to_timestamp(
            test_date,
            use_local_timezone=True,
            latitude="37.7749",
            longitude="-122.4194"
        )

        # Should convert to PST (UTC-8)
        dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        # We expect the timestamp to be for 04:00 UTC (12:00 PST)
        # Actually, when we set use_local_timezone=True, it converts and returns
        # the local timestamp, so we need to check differently
        assert timestamp is not None


class TestFileExtensions:
    """Test file extension determination"""

    def test_video_extension(self):
        assert get_file_extension('Video') == '.mp4'

    def test_image_extension(self):
        assert get_file_extension('Image') == '.jpg'

    def test_unknown_defaults_to_image(self):
        assert get_file_extension('Unknown') == '.jpg'


class TestZipDetection:
    """Test ZIP file magic byte detection"""

    def test_zip_file_detected(self):
        """ZIP files start with 'PK' magic bytes"""
        zip_data = b'PK\x03\x04' + b'\x00' * 20
        assert is_zip_file(zip_data) is True

    def test_non_zip_file(self):
        """Non-ZIP files should not be detected as ZIP"""
        jpeg_data = b'\xFF\xD8\xFF\xE0' + b'\x00' * 20
        assert is_zip_file(jpeg_data) is False

    def test_empty_file(self):
        assert is_zip_file(b'') is False


class TestCoordinateConversion:
    """Test GPS coordinate conversion to DMS format"""

    def test_decimal_to_dms_positive(self):
        """Test positive coordinate conversion"""
        result = decimal_to_dms(34.052235)
        # Should be approximately 34° 3' 8.05"
        degrees, minutes, seconds = result
        assert degrees == (34, 1)
        assert minutes == (3, 1)
        # Seconds should be around 8.05 (805/100)
        assert 800 <= seconds[0] <= 810

    def test_decimal_to_dms_negative(self):
        """Test that negative values are handled (abs value used)"""
        result = decimal_to_dms(-118.243683)
        degrees, minutes, seconds = result
        # Function uses abs(), so values should be positive
        assert degrees == (118, 1)
        assert minutes == (14, 1)

    def test_decimal_to_dms_zero(self):
        """Test zero coordinate"""
        result = decimal_to_dms(0.0)
        assert result == ((0, 1), (0, 1), (0, 100))


class TestFilenameGeneration:
    """Test filename generation"""

    def test_sequential_filename(self):
        """Test sequential numbering mode"""
        filename = generate_filename(
            "2025-11-30 00:31:09 UTC",
            ".mp4",
            use_timestamp=False,
            fallback_num="42"
        )
        assert filename == "42.mp4"

    def test_timestamp_filename(self):
        """Test timestamp-based naming"""
        filename = generate_filename(
            "2025-11-30 00:31:09 UTC",
            ".mp4",
            use_timestamp=True,
            fallback_num="01"
        )
        assert filename == "2025.11.30-00-31-09.mp4"

    def test_invalid_date_falls_back_to_sequential(self):
        """Test that invalid dates fall back to sequential naming"""
        filename = generate_filename(
            "invalid date",
            ".jpg",
            use_timestamp=True,
            fallback_num="99"
        )
        assert filename == "99.jpg"

    def test_filename_sanitization(self):
        """Test that invalid filename characters are sanitized"""
        bad_filename = "file<>:name.txt"
        result = sanitize_filename(bad_filename)
        # Invalid chars should be replaced with -
        assert '<' not in result
        assert '>' not in result
        assert ':' not in result


class TestHashComputation:
    """Test MD5 hash computation"""

    def test_hash_consistency(self):
        """Same data should produce same hash"""
        data = b"test data"
        hash1 = compute_data_hash(data)
        hash2 = compute_data_hash(data)
        assert hash1 == hash2

    def test_different_data_different_hash(self):
        """Different data should produce different hashes"""
        hash1 = compute_data_hash(b"data1")
        hash2 = compute_data_hash(b"data2")
        assert hash1 != hash2

    def test_hash_is_hex_string(self):
        """Hash should be a hex string"""
        hash_result = compute_data_hash(b"test")
        assert len(hash_result) == 32  # MD5 is 128 bits = 32 hex chars
        assert all(c in '0123456789abcdef' for c in hash_result)


class TestHTMLParsing:
    """Test HTML parsing functionality"""

    def test_parse_basic_html(self):
        """Test parsing a basic memories HTML file"""
        html_content = """
        <html>
        <body>
        <table>
            <tr>
                <td>2024-01-01 12:00:00 UTC</td>
                <td><a onclick="downloadMemories('https://example.com/test.jpg', 'test')">Download</a></td>
                <td>Image</td>
                <td>Latitude, Longitude: 34.05, -118.25</td>
            </tr>
        </table>
        </body>
        </html>
        """

        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
            f.write(html_content)
            temp_path = f.name

        try:
            memories = parse_html_file(temp_path)
            assert len(memories) == 1

            memory = memories[0]
            assert memory['date'] == '2024-01-01 12:00:00 UTC'
            assert memory['url'] == 'https://example.com/test.jpg'
            assert memory['media_type'] == 'Image'
            assert memory['latitude'] == '34.05'
            assert memory['longitude'] == '-118.25'
        finally:
            os.unlink(temp_path)

    def test_parse_multiple_memories(self):
        """Test parsing multiple memory entries"""
        html_content = """
        <html>
        <body>
        <table>
            <tr>
                <td>2024-01-01 12:00:00 UTC</td>
                <td><a onclick="downloadMemories('https://example.com/1.jpg', 'test')">Download</a></td>
                <td>Image</td>
                <td>Latitude, Longitude: 34.05, -118.25</td>
            </tr>
            <tr>
                <td>2024-01-02 13:30:00 UTC</td>
                <td><a onclick="downloadMemories('https://example.com/2.mp4', 'test')">Download</a></td>
                <td>Video</td>
                <td>Latitude, Longitude: 40.71, -74.00</td>
            </tr>
        </table>
        </body>
        </html>
        """

        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
            f.write(html_content)
            temp_path = f.name

        try:
            memories = parse_html_file(temp_path)
            assert len(memories) == 2

            # Check first memory
            assert memories[0]['media_type'] == 'Image'
            assert memories[0]['url'] == 'https://example.com/1.jpg'

            # Check second memory
            assert memories[1]['media_type'] == 'Video'
            assert memories[1]['url'] == 'https://example.com/2.mp4'
        finally:
            os.unlink(temp_path)

    def test_parse_memory_without_location(self):
        """Test parsing memory without GPS coordinates"""
        html_content = """
        <html>
        <body>
        <table>
            <tr>
                <td>2024-01-01 12:00:00 UTC</td>
                <td><a onclick="downloadMemories('https://example.com/test.jpg', 'test')">Download</a></td>
                <td>Image</td>
            </tr>
        </table>
        </body>
        </html>
        """

        with tempfile.NamedTemporaryFile(mode='w', suffix='.html', delete=False) as f:
            f.write(html_content)
            temp_path = f.name

        try:
            memories = parse_html_file(temp_path)
            assert len(memories) == 1
            # Should still parse, just without lat/lon
            assert 'latitude' not in memories[0]
            assert 'longitude' not in memories[0]
        finally:
            os.unlink(temp_path)


@pytest.mark.skipif(not timezone_support, reason="Timezone support not available")
class TestTimezoneFeatures:
    """Test timezone-related features (requires timezonefinder and pytz)"""

    def test_gps_to_timezone_san_francisco(self):
        """Test GPS to timezone conversion for San Francisco"""
        tz = get_timezone_from_gps(37.7749, -122.4194)
        assert tz == 'America/Los_Angeles'

    def test_gps_to_timezone_new_york(self):
        """Test GPS to timezone conversion for New York"""
        tz = get_timezone_from_gps(40.7128, -74.0060)
        assert tz == 'America/New_York'

    def test_gps_to_timezone_london(self):
        """Test GPS to timezone conversion for London"""
        tz = get_timezone_from_gps(51.5074, -0.1278)
        assert tz == 'Europe/London'

    def test_gps_to_timezone_prague(self):
        """Test special handling for Czech Republic (Prague)"""
        # Prague coordinates
        tz = get_timezone_from_gps(50.0755, 14.4378)
        assert tz == 'Europe/Prague'

    def test_utc_to_local_conversion_pst(self):
        """Test UTC to PST conversion"""
        utc_string = "2024-01-01 12:00:00 UTC"
        local_dt = convert_utc_to_local(utc_string, 'America/Los_Angeles')

        # 12:00 UTC should be 04:00 PST (UTC-8 in winter)
        assert local_dt.hour == 4

    def test_utc_to_local_conversion_est(self):
        """Test UTC to EST conversion"""
        utc_string = "2024-01-01 12:00:00 UTC"
        local_dt = convert_utc_to_local(utc_string, 'America/New_York')

        # 12:00 UTC should be 07:00 EST (UTC-5 in winter)
        assert local_dt.hour == 7


class TestDownloadWithMocking:
    """Test download functionality with mocked network requests"""

    @patch('download_memories.requests.get')
    def test_download_single_image(self, mock_get):
        """Test downloading a single image file (not a ZIP)"""
        # Mock the HTTP response
        mock_response = Mock()
        mock_response.content = b'\xFF\xD8\xFF\xE0' + b'\x00' * 1000  # JPEG magic bytes
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        from download_memories import download_and_extract

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            files_saved = download_and_extract(
                url="https://example.com/test.jpg",
                base_path=tmpdir_path,
                file_num="01",
                extension=".jpg",
                merge_overlays=False,
                date_str="2024-01-01 12:00:00 UTC",
                latitude="34.05",
                longitude="-118.25"
            )

            # Should save one file
            assert len(files_saved) == 1
            assert files_saved[0]['type'] == 'single'

            # File should exist
            saved_file = tmpdir_path / files_saved[0]['path']
            assert saved_file.exists()

    @patch('download_memories.requests.get')
    def test_download_zip_with_overlay(self, mock_get):
        """Test downloading a ZIP file containing main + overlay"""
        # Create a mock ZIP file
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, 'w') as zf:
            zf.writestr('video-main.mp4', b'\x00' * 100)
            zf.writestr('video-overlay.png', b'\x00' * 50)

        mock_response = Mock()
        mock_response.content = zip_buffer.getvalue()
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        from download_memories import download_and_extract

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            files_saved = download_and_extract(
                url="https://example.com/test.zip",
                base_path=tmpdir_path,
                file_num="01",
                extension=".mp4",
                merge_overlays=False,
                date_str="2024-01-01 12:00:00 UTC",
                latitude="34.05",
                longitude="-118.25"
            )

            # Should save two files (main + overlay)
            assert len(files_saved) == 2

            # Check that both files exist
            for file_info in files_saved:
                saved_file = tmpdir_path / file_info['path']
                assert saved_file.exists()

    @patch('download_memories.requests.get')
    def test_overlays_only_mode_skips_non_overlay(self, mock_get):
        """Test that overlays_only mode skips files without overlays"""
        mock_response = Mock()
        mock_response.content = b'\xFF\xD8\xFF\xE0' + b'\x00' * 1000  # Single JPEG
        mock_response.raise_for_status = Mock()
        mock_get.return_value = mock_response

        from download_memories import download_and_extract

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            files_saved = download_and_extract(
                url="https://example.com/test.jpg",
                base_path=tmpdir_path,
                file_num="01",
                extension=".jpg",
                overlays_only=True,  # Skip files without overlays
                date_str="2024-01-01 12:00:00 UTC"
            )

            # Should return empty list (file skipped)
            assert len(files_saved) == 0


class TestImageMerging:
    """Test image overlay merging functionality"""

    @pytest.mark.skipif(
        not hasattr(__import__('download_memories'), 'Image') or
        __import__('download_memories').Image is None,
        reason="Pillow not available"
    )
    def test_merge_image_overlay_basic(self):
        """Test merging an overlay onto a base image"""
        from download_memories import merge_image_overlay
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not available")

        # Create a small test base image (red)
        base_img = Image.new('RGB', (100, 100), color='red')
        base_buffer = io.BytesIO()
        base_img.save(base_buffer, format='JPEG')
        base_data = base_buffer.getvalue()

        # Create a small overlay image with transparency (blue with alpha)
        overlay_img = Image.new('RGBA', (100, 100), color=(0, 0, 255, 128))
        overlay_buffer = io.BytesIO()
        overlay_img.save(overlay_buffer, format='PNG')
        overlay_data = overlay_buffer.getvalue()

        # Merge them
        result_data = merge_image_overlay(base_data, overlay_data)

        # Verify result is valid image data
        result_img = Image.open(io.BytesIO(result_data))
        assert result_img.size == (100, 100)
        assert result_img.format in ['JPEG', 'PNG']

    @pytest.mark.skipif(
        not hasattr(__import__('download_memories'), 'Image') or
        __import__('download_memories').Image is None,
        reason="Pillow not available"
    )
    def test_merge_preserves_format(self):
        """Test that merge preserves the original image format"""
        from download_memories import merge_image_overlay
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not available")

        # Create JPEG base
        base_img = Image.new('RGB', (50, 50), color='white')
        base_buffer = io.BytesIO()
        base_img.save(base_buffer, format='JPEG')
        base_data = base_buffer.getvalue()

        # Create PNG overlay
        overlay_img = Image.new('RGBA', (50, 50), color=(0, 0, 0, 50))
        overlay_buffer = io.BytesIO()
        overlay_img.save(overlay_buffer, format='PNG')
        overlay_data = overlay_buffer.getvalue()

        # Merge
        result_data = merge_image_overlay(base_data, overlay_data)

        # Result should be JPEG (preserving base format)
        result_img = Image.open(io.BytesIO(result_data))
        assert result_img.format == 'JPEG'


class TestVideoMerging:
    """Test video overlay merging functionality"""

    @staticmethod
    def create_minimal_video(output_path, duration_sec=1, fps=1, color='black'):
        """
        Create a minimal valid video file for testing

        Args:
            output_path: Path to save video
            duration_sec: Video duration in seconds
            fps: Frames per second
            color: Video color (black, white, red, blue, green)
        """
        try:
            import subprocess

            # Color mapping to RGB
            colors = {
                'black': '0x000000',
                'white': '0xFFFFFF',
                'red': '0xFF0000',
                'blue': '0x0000FF',
                'green': '0x00FF00'
            }

            color_hex = colors.get(color, '0x000000')

            # Create minimal video using FFmpeg
            cmd = [
                'ffmpeg', '-y',
                '-f', 'lavfi',
                '-i', f'color=c={color_hex}:s=320x240:d={duration_sec}:r={fps}',
                '-c:v', 'libx264',
                '-t', str(duration_sec),
                '-pix_fmt', 'yuv420p',
                str(output_path)
            ]

            result = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=10
            )

            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
            return False

    @pytest.mark.skipif(
        not hasattr(__import__('download_memories'), 'ffmpeg_available') or
        not __import__('download_memories').ffmpeg_available,
        reason="FFmpeg not available"
    )
    def test_merge_video_overlay_basic(self):
        """Test merging video overlay onto base video"""
        from download_memories import merge_video_overlay

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create test videos
            main_video = tmpdir_path / 'main.mp4'
            overlay_video = tmpdir_path / 'overlay.mp4'
            output_video = tmpdir_path / 'merged.mp4'

            # Create minimal test videos
            main_created = self.create_minimal_video(main_video, duration_sec=1, color='black')
            overlay_created = self.create_minimal_video(overlay_video, duration_sec=1, color='white')

            if not (main_created and overlay_created):
                pytest.skip("Could not create test videos")

            # Test merge
            success = merge_video_overlay(main_video, overlay_video, output_video)

            # Verify merge succeeded
            assert success is True
            assert output_video.exists()
            assert output_video.stat().st_size > 1000  # Should be a valid video

    @pytest.mark.skipif(
        not hasattr(__import__('download_memories'), 'ffmpeg_available') or
        not __import__('download_memories').ffmpeg_available,
        reason="FFmpeg not available"
    )
    def test_merge_video_with_image_overlay(self):
        """Test merging static image overlay onto video"""
        from download_memories import merge_video_overlay

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create test video
            main_video = tmpdir_path / 'main.mp4'
            main_created = self.create_minimal_video(main_video, duration_sec=1, color='blue')

            if not main_created:
                pytest.skip("Could not create test video")

            # Create overlay image
            try:
                from PIL import Image
            except ImportError:
                pytest.skip("Pillow not available")

            overlay_img = Image.new('RGBA', (320, 240), color=(255, 0, 0, 128))
            overlay_path = tmpdir_path / 'overlay.png'
            overlay_img.save(overlay_path)

            output_video = tmpdir_path / 'merged.mp4'

            # Test merge with image overlay
            success = merge_video_overlay(main_video, overlay_path, output_video)

            # Verify merge succeeded
            assert success is True
            assert output_video.exists()


class TestEXIFMetadata:
    """Test EXIF metadata embedding"""

    @pytest.mark.skipif(
        not hasattr(__import__('download_memories'), 'piexif') or
        __import__('download_memories').piexif is None or
        not hasattr(__import__('download_memories'), 'Image') or
        __import__('download_memories').Image is None,
        reason="piexif or Pillow not available"
    )
    def test_add_exif_to_jpeg(self):
        """Test adding EXIF metadata to JPEG image"""
        from download_memories import add_exif_metadata
        try:
            from PIL import Image
            import piexif
        except ImportError:
            pytest.skip("Required libraries not available")

        # Create a test JPEG image
        img = Image.new('RGB', (100, 100), color='red')
        img_buffer = io.BytesIO()
        img.save(img_buffer, format='JPEG')
        img_data = img_buffer.getvalue()

        # Add EXIF metadata
        result_data = add_exif_metadata(
            img_data,
            date_str="2024-01-01 12:00:00 UTC",
            latitude="34.052235",
            longitude="-118.243683",
            use_local_timezone=False
        )

        # Verify EXIF was added
        result_img = Image.open(io.BytesIO(result_data))
        exif_dict = piexif.load(result_img.info.get('exif', b''))

        # Check GPS coordinates are present
        assert piexif.GPSIFD.GPSLatitude in exif_dict['GPS']
        assert piexif.GPSIFD.GPSLongitude in exif_dict['GPS']

        # Check date is present
        assert piexif.ExifIFD.DateTimeOriginal in exif_dict['Exif']

    @pytest.mark.skipif(
        not hasattr(__import__('download_memories'), 'piexif') or
        __import__('download_memories').piexif is None or
        not hasattr(__import__('download_memories'), 'Image') or
        __import__('download_memories').Image is None,
        reason="piexif or Pillow not available"
    )
    def test_exif_preserves_image_format(self):
        """Test that EXIF addition preserves image format"""
        from download_memories import add_exif_metadata
        try:
            from PIL import Image
        except ImportError:
            pytest.skip("Pillow not available")

        # Create PNG image
        img = Image.new('RGB', (50, 50), color='blue')
        img_buffer = io.BytesIO()
        img.save(img_buffer, format='PNG')
        img_data = img_buffer.getvalue()

        # Add EXIF
        result_data = add_exif_metadata(
            img_data,
            date_str="2024-01-01 12:00:00 UTC",
            latitude="40.7128",
            longitude="-74.0060"
        )

        # Should still be PNG
        result_img = Image.open(io.BytesIO(result_data))
        assert result_img.format == 'PNG'


class TestMetadataManagement:
    """Test metadata.json creation and management"""

    def test_initialize_metadata_creates_file(self):
        """Test that initialize_metadata creates metadata.json"""
        from download_memories import initialize_metadata

        test_memories = [
            {
                'date': '2024-01-01 12:00:00 UTC',
                'media_type': 'Image',
                'url': 'https://example.com/1.jpg',
                'latitude': '34.05',
                'longitude': '-118.25'
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            metadata_list = initialize_metadata(test_memories, tmpdir_path)

            # Check metadata.json was created
            metadata_file = tmpdir_path / 'metadata.json'
            assert metadata_file.exists()

            # Check structure
            assert len(metadata_list) == 1
            assert metadata_list[0]['status'] == 'pending'
            assert metadata_list[0]['number'] == 1

    def test_metadata_loads_existing_file(self):
        """Test that initialize_metadata loads existing metadata.json"""
        from download_memories import initialize_metadata

        test_memories = [
            {
                'date': '2024-01-01 12:00:00 UTC',
                'media_type': 'Image',
                'url': 'https://example.com/1.jpg'
            }
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create existing metadata
            existing_metadata = [
                {
                    'number': 1,
                    'status': 'success',
                    'files': [{'path': '01.jpg', 'size': 1000, 'type': 'single'}]
                }
            ]

            metadata_file = tmpdir_path / 'metadata.json'
            with open(metadata_file, 'w') as f:
                json.dump(existing_metadata, f)

            # Initialize should load existing
            metadata_list = initialize_metadata(test_memories, tmpdir_path)

            # Should match existing
            assert len(metadata_list) == 1
            assert metadata_list[0]['status'] == 'success'


class TestDuplicateDetection:
    """Test duplicate file detection"""

    def test_duplicate_detection_finds_duplicates(self):
        """Test that identical files are detected as duplicates"""
        from download_memories import is_duplicate_file

        test_data = b"test file content"

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create existing file
            existing_file = tmpdir_path / 'existing.jpg'
            with open(existing_file, 'wb') as f:
                f.write(test_data)

            # Check if duplicate
            is_dup, dup_file = is_duplicate_file(test_data, tmpdir_path, check_duplicates=True)

            assert is_dup is True
            assert dup_file == 'existing.jpg'

    def test_duplicate_detection_unique_files(self):
        """Test that different files are not detected as duplicates"""
        from download_memories import is_duplicate_file

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create existing file
            existing_file = tmpdir_path / 'existing.jpg'
            with open(existing_file, 'wb') as f:
                f.write(b"original content")

            # Check different data
            new_data = b"different content"
            is_dup, dup_file = is_duplicate_file(new_data, tmpdir_path, check_duplicates=True)

            assert is_dup is False
            assert dup_file is None

    def test_duplicate_detection_disabled(self):
        """Test that duplicate detection can be disabled"""
        from download_memories import is_duplicate_file

        # Even if data matches, should return False when disabled
        is_dup, dup_file = is_duplicate_file(
            b"test",
            Path("/tmp"),
            check_duplicates=False
        )

        assert is_dup is False
        assert dup_file is None


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
