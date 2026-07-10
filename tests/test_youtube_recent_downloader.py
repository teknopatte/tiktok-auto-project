from datetime import UTC, datetime
import shutil
import subprocess
import tempfile
from pathlib import Path
import unittest

from src.youtube_recent_downloader import (
    ClipResult,
    RenderResult,
    VideoCandidate,
    cleanup_generated_after_publish,
    load_creator_rows,
    normalize_allowed_niches,
    parse_channel_id_from_url,
    parse_upload_datetime,
    render_vertical_shorts,
    segment_count_for_duration,
    slugify,
    split_video_into_segments,
)


class YoutubeRecentDownloaderTests(unittest.TestCase):
    def test_slugify_removes_accents_and_symbols(self):
        self.assertEqual(slugify("Humour / sketches"), "humour-sketches")
        self.assertEqual(slugify("Beauté & mode"), "beaute-mode")

    def test_parse_upload_datetime_from_yt_dlp_date(self):
        self.assertEqual(
            parse_upload_datetime({"upload_date": "20260709"}),
            datetime(2026, 7, 9, tzinfo=UTC),
        )

    def test_parse_upload_datetime_from_timestamp(self):
        timestamp = int(datetime(2026, 7, 9, tzinfo=UTC).timestamp())
        self.assertEqual(
            parse_upload_datetime({"timestamp": timestamp}),
            datetime(2026, 7, 9, tzinfo=UTC),
        )

    def test_parse_channel_id_from_url(self):
        self.assertEqual(
            parse_channel_id_from_url("https://www.youtube.com/channel/UCpWaR3gNAQGsX48cIlQC0qw/videos"),
            "UCpWaR3gNAQGsX48cIlQC0qw",
        )
        self.assertIsNone(parse_channel_id_from_url("https://www.youtube.com/@squeezie"))

    def test_segment_count_for_duration_rounds_up(self):
        self.assertEqual(segment_count_for_duration(1019.361, 60), 17)
        self.assertEqual(segment_count_for_duration(120.0, 60), 2)
        self.assertEqual(segment_count_for_duration(1.0, 60), 1)

    def test_load_creator_rows_filters_allowed_niches_before_limit(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "channels.tsv"
            path.write_text(
                "rank\tchannel\tniche_fr\n"
                "1\tBeauty A\tBeaute / mode\n"
                "2\tGaming A\tGaming\n"
                "3\tDivert A\tDivertissement pur\n",
                encoding="utf-8",
            )

            rows = load_creator_rows(path, limit=2, allowed_niches=normalize_allowed_niches("Gaming,Divertissement pur"))

            self.assertEqual([row.channel for row in rows], ["Gaming A", "Divert A"])

    def test_cleanup_generated_after_publish_removes_generated_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / "gaming" / "channel" / "demo [abc].mp4"
            source.parent.mkdir(parents=True)
            source.write_bytes(b"video")
            sidecar = source.with_name("demo [abc].info.json")
            sidecar.write_text("{}", encoding="utf-8")
            clips_dir = source.parent / "demo [abc]-clips"
            shorts_dir = source.parent / "demo [abc]-shorts"
            clips_dir.mkdir()
            shorts_dir.mkdir()
            (clips_dir / "part-001.mp4").write_bytes(b"clip")
            (shorts_dir / "short-001.mp4").write_bytes(b"short")
            candidate = VideoCandidate(
                video_id="abc",
                title="Demo",
                url="https://youtu.be/abc",
                uploaded_at=None,
                channel_name="Channel",
                niche="Gaming",
            )
            state = {"videos": {"abc": {}}}

            removed = cleanup_generated_after_publish(
                state,
                candidate,
                source_video=source,
                clip_result=ClipResult(clips_dir, [clips_dir / "part-001.mp4"], 60, 60.0),
                render_result=RenderResult(shorts_dir, [shorts_dir / "short-001.mp4"], "layout", None, []),
                output_root=root,
            )

            self.assertFalse(source.exists())
            self.assertFalse(sidecar.exists())
            self.assertFalse(clips_dir.exists())
            self.assertFalse(shorts_dir.exists())
            self.assertEqual(state["videos"]["abc"]["cleanup_status"], "cleaned")
            self.assertGreaterEqual(len(removed), 4)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg absent")
    def test_split_video_into_segments(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            video_path = Path(temp_dir) / "sample [abc123].mp4"
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc=duration=2.4:size=160x90:rate=15",
                    "-f",
                    "lavfi",
                    "-i",
                    "anullsrc=channel_layout=stereo:sample_rate=44100",
                    "-shortest",
                    "-c:v",
                    "libx264",
                    "-c:a",
                    "aac",
                    str(video_path),
                ],
                check=True,
            )
            result = split_video_into_segments(video_path, "abc123", 1)
            self.assertGreaterEqual(len(result.clips), 2)
            self.assertTrue(all(path.exists() for path in result.clips))

    def test_render_vertical_shorts_without_satisfying_video_does_not_crash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            video_path = temp_path / "sample [abc123].mp4"
            clip_path = temp_path / "clips" / "part-001.mp4"
            clip_path.parent.mkdir()
            video_path.write_bytes(b"placeholder")
            clip_path.write_bytes(b"placeholder")

            result = render_vertical_shorts(
                video_path,
                "abc123",
                [clip_path],
                temp_path / "empty-satisfying",
            )

            self.assertEqual(result.shorts, [])
            self.assertIsNone(result.satisfying_source)


if __name__ == "__main__":
    unittest.main()
