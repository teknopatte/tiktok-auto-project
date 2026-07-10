import unittest
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.control_app import (
    DEFAULT_LOOP_PAYLOAD,
    analysis_summary,
    build_analysis_command,
    build_download_command,
    build_satisfying_command,
    folder_target,
    load_analysis_page,
    parse_analysis_durations,
    safe_int,
    summarize_dashboard,
)


class ControlAppTests(unittest.TestCase):
    def test_parse_analysis_durations_validates_bounds(self):
        self.assertEqual(parse_analysis_durations("60,75,90"), (60.0, 75.0, 90.0))
        with self.assertRaises(ValueError):
            parse_analysis_durations("")
        with self.assertRaises(ValueError):
            parse_analysis_durations("0,60")

    def test_build_analysis_command_uses_local_module_and_never_publishes(self):
        with tempfile.TemporaryDirectory() as directory:
            video = Path(directory) / "source.mp4"
            video.write_bytes(b"fixture")
            command = build_analysis_command(
                {
                    "videoPath": str(video),
                    "durations": "60,90",
                    "step": 4,
                    "silenceThresholdDb": -32,
                    "model": "tiny",
                    "device": "cpu",
                    "computeType": "int8",
                }
            )
        self.assertEqual(command[1:4], ["-m", "src.candidate_analysis", "analyze"])
        self.assertIn(str(video.resolve()), command)
        self.assertIn("60,90", command)
        self.assertIn("-32", command)
        self.assertNotIn("--auto-publish-tiktok", command)

    def test_build_analysis_command_rejects_missing_or_non_video_file(self):
        with self.assertRaisesRegex(ValueError, "introuvable"):
            build_analysis_command({"videoPath": "missing.mp4"})
        with tempfile.TemporaryDirectory() as directory:
            document = Path(directory) / "notes.txt"
            document.write_text("not a video", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Format video"):
                build_analysis_command({"videoPath": str(document)})

    def test_analysis_result_is_summarized_and_paginated(self):
        payload = {
            "source_video": "video.mp4",
            "analysis_version": "1.0",
            "config": {"step_seconds": 3},
            "global_analysis": {"duration_seconds": 180},
            "candidates": [{"candidate_id": f"clip_{index:04d}"} for index in range(5)],
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "latest.json"
            output.write_text(json.dumps(payload), encoding="utf-8")
            with patch("src.control_app.ANALYSIS_OUTPUT_FILE", output):
                summary = analysis_summary()
                page = load_analysis_page(offset=2, limit=2)
        self.assertTrue(summary["available"])
        self.assertEqual(summary["candidate_count"], 5)
        self.assertEqual([item["candidate_id"] for item in page["candidates"]], ["clip_0002", "clip_0003"])
        self.assertEqual(page["pagination"], {"offset": 2, "limit": 2, "total": 5, "has_more": True})

    def test_analysis_result_handles_missing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "missing.json"
            with patch("src.control_app.ANALYSIS_OUTPUT_FILE", output):
                self.assertEqual(analysis_summary(), {"available": False, "candidate_count": 0})
                self.assertFalse(load_analysis_page()["ok"])

    def test_safe_int_clamps_values(self):
        self.assertEqual(safe_int("2", 10, minimum=5, maximum=20), 5)
        self.assertEqual(safe_int("30", 10, minimum=5, maximum=20), 20)
        self.assertEqual(safe_int("bad", 10, minimum=5, maximum=20), 10)

    def test_build_download_command_keeps_dry_run_and_limit(self):
        command = build_download_command(
            {
                "dryRun": True,
                "sinceHours": 24,
                "maxVideosPerChannel": 1,
                "limit": 3,
                "cookiesFromBrowser": "",
                "outputRoot": "",
            }
        )
        self.assertIn("--dry-run", command)
        self.assertIn("--limit", command)
        self.assertIn("3", command)
        self.assertIn("--clip-segment-seconds", command)
        self.assertIn("60", command)
        self.assertIn("--tiktok-privacy-level", command)
        self.assertIn("SELF_ONLY", command)
        self.assertNotIn("--auto-publish-tiktok", command)

    def test_default_loop_payload_keeps_tiktok_publish_disabled(self):
        self.assertFalse(DEFAULT_LOOP_PAYLOAD["autoPublishTikTok"])
        self.assertEqual(DEFAULT_LOOP_PAYLOAD["tiktokPrivacyLevel"], "SELF_ONLY")
        self.assertEqual(DEFAULT_LOOP_PAYLOAD["tiktokPublishLimit"], 1)
        self.assertEqual(DEFAULT_LOOP_PAYLOAD["tiktokCaptionTemplate"], "{title} #{niche} #fyp")
        self.assertEqual(DEFAULT_LOOP_PAYLOAD["tiktokPublishDelayMinSeconds"], 600)
        self.assertEqual(DEFAULT_LOOP_PAYLOAD["tiktokPublishDelayMaxSeconds"], 1200)
        self.assertEqual(DEFAULT_LOOP_PAYLOAD["allowedNiches"], ["Divertissement pur", "Gaming"])

    def test_build_download_command_adds_allowed_niches(self):
        command = build_download_command(
            {
                "dryRun": True,
                "allowedNiches": ["Divertissement pur", "Gaming"],
            }
        )
        self.assertIn("--allowed-niches", command)
        self.assertIn("Divertissement pur,Gaming", command)

    def test_build_download_command_adds_tiktok_publish_only_when_enabled(self):
        command = build_download_command(
            {
                "dryRun": False,
                "autoPublishTikTok": True,
                "tiktokPrivacyLevel": "SELF_ONLY",
                "tiktokCaptionTemplate": "{title}",
                "tiktokPublishLimit": 2,
                "tiktokPublishDelayMinSeconds": 10,
                "tiktokPublishDelayMaxSeconds": 20,
            }
        )
        self.assertIn("--auto-publish-tiktok", command)
        self.assertIn("--tiktok-caption-template", command)
        self.assertIn("{title}", command)
        self.assertIn("--tiktok-publish-limit", command)
        self.assertIn("2", command)
        self.assertIn("--tiktok-publish-delay-min-seconds", command)
        self.assertIn("10", command)
        self.assertIn("--tiktok-publish-delay-max-seconds", command)
        self.assertIn("20", command)

    def test_build_download_command_accepts_manual_video_url(self):
        command = build_download_command(
            {
                "dryRun": False,
                "videoUrl": "https://www.youtube.com/watch?v=abc",
                "manualChannel": "Manual Channel",
                "manualNiche": "Gaming",
            }
        )
        self.assertIn("--video-url", command)
        self.assertIn("https://www.youtube.com/watch?v=abc", command)
        self.assertIn("--manual-channel", command)
        self.assertIn("Manual Channel", command)
        self.assertIn("--manual-niche", command)
        self.assertIn("Gaming", command)

    def test_build_satisfying_command_downloads_into_satisfying_folder(self):
        command = build_satisfying_command({"videoUrl": "https://www.youtube.com/watch?v=xyz"})
        self.assertIn("-m", command)
        self.assertIn("yt_dlp", command)
        self.assertIn("--merge-output-format", command)
        self.assertIn("https://www.youtube.com/watch?v=xyz", command)

    def test_folder_target_allows_known_folders_only(self):
        self.assertTrue(str(folder_target("satisfying")).endswith("videos_satisfaisantes"))
        with self.assertRaises(ValueError):
            folder_target("secret")

    def test_dashboard_exposes_tiktok_stats_and_video_status(self):
        rows = [SimpleNamespace(rank="1", channel="Channel A", niche="Gaming")]
        state = {
            "downloaded_video_ids": ["abc"],
            "channels": {
                "Channel A": {
                    "last_checked_at": "2026-07-09T10:00:00+00:00",
                    "last_result": "recent_video_found",
                    "last_video_id": "abc",
                    "last_video_title": "Demo",
                    "last_video_url": "https://youtu.be/abc",
                    "last_video_uploaded_at": "2026-07-09T09:00:00+00:00",
                }
            },
            "videos": {
                "abc": {
                    "video_id": "abc",
                    "channel": "Channel A",
                    "niche": "Gaming",
                    "title": "Demo",
                    "status": "downloaded",
                    "clips_count": 3,
                    "shorts_count": 2,
                    "tiktok_publish_count": 1,
                    "tiktok_publish_status": "published",
                    "tiktok_publish_records": [{"view_count": 42}],
                    "last_seen_at": "2026-07-09T10:00:00+00:00",
                }
            },
        }

        with (
            patch("src.control_app.load_creator_rows", return_value=rows),
            patch("src.control_app.load_state", return_value=state),
            patch("src.control_app.directory_size", return_value=0),
            patch("src.control_app.load_features", return_value=[]),
            patch("src.control_app.get_loop_state", return_value={"enabled": False, "payload": DEFAULT_LOOP_PAYLOAD}),
            patch("src.control_app.tiktok_public_status", return_value={"configured": True, "connected": True}),
            patch.dict("os.environ", {"TIKTOK_ACCESS_TOKEN": "token"}, clear=False),
        ):
            dashboard = summarize_dashboard()

        self.assertEqual(dashboard["stats"]["tiktok_published"], 1)
        self.assertEqual(dashboard["stats"]["tiktok_views"], 42)
        self.assertTrue(dashboard["stats"]["tiktok_configured"])
        self.assertTrue(dashboard["stats"]["tiktok_connected"])
        self.assertEqual(dashboard["videos"][0]["tiktok_publish_status"], "published")


if __name__ == "__main__":
    unittest.main()
