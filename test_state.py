import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from main import Config, DigestApp, StateStore


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

    def test_format_local_timestamp_includes_clock_time(self):
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config = Config(
                gemini_api_key="",
                gemini_model="gemini-2.5-flash",
                summary_language_mode="transcript",
                summary_language="",
                enable_macos_notifications=True,
                telegram_bot_token="",
                telegram_chat_id="",
                check_interval_seconds=3600,
                max_videos_per_channel=3,
                summary_dir=tmp_path / "summaries",
                transcript_dir=tmp_path / "transcripts",
                prompt_dir=tmp_path / "prompts",
                state_path=tmp_path / "state.json",
                token_path=tmp_path / "google_token.json",
                credentials_path=tmp_path / "credentials.json",
                watched_channels_path=tmp_path / "watched_channels.txt",
                prompt_template_path=tmp_path / "prompt.md",
                failed_video_retry_limit=3,
                failed_video_retry_cooldown_hours=24,
                transcript_request_delay_min_seconds=0,
                transcript_request_delay_max_seconds=0,
                transcript_rate_limit_pause_min_minutes=30,
                transcript_rate_limit_pause_max_minutes=60,
                transcript_user_agent="test-agent",
                transcript_cookie_header="",
            )
            app = DigestApp.__new__(DigestApp)
            app.config = config

            formatted = app._format_local_timestamp(
                datetime(2026, 4, 4, 0, 0, 0, tzinfo=timezone.utc)
            )

        self.assertRegex(formatted, r"2026-04-04 \d{2}:\d{2}:\d{2}")

    def test_load_dotenv_preserves_unmatched_quotes(self):
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            env_path = tmp_path / ".env"
            env_path.write_text("TEST_VALUE=abc'\nQUOTED=\"value\"\n")

            original_test_value = os.environ.get("TEST_VALUE")
            original_quoted = os.environ.get("QUOTED")
            try:
                os.environ.pop("TEST_VALUE", None)
                os.environ.pop("QUOTED", None)
                from main import load_dotenv

                load_dotenv(tmp_path)

                self.assertEqual(os.environ["TEST_VALUE"], "abc'")
                self.assertEqual(os.environ["QUOTED"], "value")
            finally:
                if original_test_value is None:
                    os.environ.pop("TEST_VALUE", None)
                else:
                    os.environ["TEST_VALUE"] = original_test_value
                if original_quoted is None:
                    os.environ.pop("QUOTED", None)
                else:
                    os.environ["QUOTED"] = original_quoted


if __name__ == "__main__":
    unittest.main()
