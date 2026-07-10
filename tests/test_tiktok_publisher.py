import unittest

from src.tiktok_publisher import (
    MIN_CHUNK_SIZE,
    choose_publish_mode,
    make_chunk_plan,
    mime_type_for_video,
    validate_creator_can_publish,
)
from pathlib import Path


class TikTokPublisherTests(unittest.TestCase):
    def test_make_chunk_plan_for_small_video(self):
        plan = make_chunk_plan(MIN_CHUNK_SIZE - 1)
        self.assertEqual(plan.total_chunk_count, 1)
        self.assertEqual(plan.chunk_size, MIN_CHUNK_SIZE - 1)

    def test_make_chunk_plan_for_large_video(self):
        plan = make_chunk_plan(50_000_123, preferred_chunk_size=10_000_000)
        self.assertEqual(plan.total_chunk_count, 5)
        self.assertEqual(plan.chunk_size, 10_000_000)

    def test_make_chunk_plan_uses_tiktok_floor_count_for_partial_final_chunk(self):
        plan = make_chunk_plan(45_037_057, preferred_chunk_size=16_777_216)
        self.assertEqual(plan.total_chunk_count, 2)
        self.assertEqual(plan.chunk_size, 16_777_216)

    def test_mime_type_for_video(self):
        self.assertEqual(mime_type_for_video(Path("clip.mp4")), "video/mp4")
        self.assertEqual(mime_type_for_video(Path("clip.mov")), "video/quicktime")
        self.assertEqual(mime_type_for_video(Path("clip.webm")), "video/webm")

    def test_validate_creator_can_publish_rejects_bad_privacy(self):
        with self.assertRaises(RuntimeError):
            validate_creator_can_publish(
                {"privacy_level_options": ["SELF_ONLY"]},
                "PUBLIC_TO_EVERYONE",
            )

    def test_choose_publish_mode_prefers_direct_when_publish_scope_exists(self):
        self.assertEqual(choose_publish_mode("auto", "user.info.basic,video.publish,video.upload"), "direct")

    def test_choose_publish_mode_falls_back_to_upload_scope(self):
        self.assertEqual(choose_publish_mode("auto", "user.info.basic,video.upload"), "upload")


if __name__ == "__main__":
    unittest.main()
