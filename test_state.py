import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from main import StateStore


class StateStoreTests(unittest.TestCase):
    def test_mark_failed_tracks_retry_count_and_error(self):
        with TemporaryDirectory() as tmp_dir:
            state = StateStore(Path(tmp_dir) / "state.json")

            state.mark_failed("video-1", "first failure")
            state.mark_failed("video-1", "second failure")

            entry = state.failed_entry("video-1")
            self.assertEqual(entry["retry_count"], 2)
            self.assertEqual(entry["last_error"], "second failure")

    def test_mark_seen_clears_failed_state(self):
        with TemporaryDirectory() as tmp_dir:
            state = StateStore(Path(tmp_dir) / "state.json")

            state.mark_failed("video-2", "temporary error")
            state.mark_seen("video-2")

            self.assertTrue(state.has_seen("video-2"))
            self.assertIsNone(state.failed_entry("video-2"))

    def test_should_retry_failed_video_respects_limit_and_cooldown(self):
        with TemporaryDirectory() as tmp_dir:
            state = StateStore(Path(tmp_dir) / "state.json")
            state.data["failed_videos"]["video-3"] = {
                "retry_count": 2,
                "last_attempt_at": (
                    datetime.now(timezone.utc) - timedelta(hours=25)
                ).isoformat(),
                "last_error": "captions unavailable",
            }

            self.assertTrue(state.should_retry_failed_video("video-3", 3, 24))
            self.assertFalse(state.should_retry_failed_video("video-3", 2, 24))

            state.data["failed_videos"]["video-3"]["last_attempt_at"] = datetime.now(
                timezone.utc
            ).isoformat()
            self.assertFalse(state.should_retry_failed_video("video-3", 3, 24))


if __name__ == "__main__":
    unittest.main()
