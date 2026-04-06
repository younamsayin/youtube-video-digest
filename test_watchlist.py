import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from main import Config, YouTubeWatcher


def build_config(tmp_path: Path) -> Config:
    return Config(
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


class WatchlistTests(unittest.TestCase):
    def test_load_configured_channels_skips_comments_and_blanks(self):
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            config = build_config(tmp_path)
            config.watched_channels_path.write_text(
                "\n# comment\nhttps://www.youtube.com/@Alpha\n\nhttps://www.youtube.com/channel/UC1234567890123456789012\n"
            )
            watcher = YouTubeWatcher(config)

            channels = watcher._load_configured_channels()

        self.assertEqual(
            channels,
            [
                "https://www.youtube.com/@Alpha",
                "https://www.youtube.com/channel/UC1234567890123456789012",
            ],
        )

    def test_parse_handle_url(self):
        watcher = YouTubeWatcher(build_config(Path(".")))

        parsed = watcher._parse_channel_reference("https://www.youtube.com/@OpenAI")

        self.assertEqual(parsed, {"kind": "handle", "value": "@OpenAI"})

    def test_parse_channel_id_url(self):
        watcher = YouTubeWatcher(build_config(Path(".")))

        parsed = watcher._parse_channel_reference(
            "https://www.youtube.com/channel/UC1234567890123456789012"
        )

        self.assertEqual(
            parsed, {"kind": "channel_id", "value": "UC1234567890123456789012"}
        )

    def test_parse_legacy_username_url(self):
        watcher = YouTubeWatcher(build_config(Path(".")))

        parsed = watcher._parse_channel_reference(
            "https://www.youtube.com/user/GoogleDevelopers"
        )

        self.assertEqual(parsed, {"kind": "username", "value": "GoogleDevelopers"})


if __name__ == "__main__":
    unittest.main()
