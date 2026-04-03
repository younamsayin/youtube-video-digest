import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from main import Config, GeminiSummarizer, TranscriptFetcher


class FakeFetchedTranscript:
    def __init__(self, items, language_code=""):
        self._items = items
        self.language_code = language_code

    def to_raw_data(self):
        return self._items


class FakeTranscript:
    def __init__(self, items):
        self.items = items

    def fetch(self):
        return FakeFetchedTranscript(self.items, language_code="es")


class TranscriptFetcherTests(unittest.TestCase):
    def test_preferred_languages_include_base_code_and_fallbacks(self):
        fetcher = TranscriptFetcher.__new__(TranscriptFetcher)

        languages = fetcher._preferred_languages(["pt-BR", "ko"])

        self.assertEqual(languages, ["pt-BR", "pt", "ko", "en"])

    def test_fetch_falls_back_to_any_available_transcript(self):
        fetcher = TranscriptFetcher.__new__(TranscriptFetcher)

        class FakeApi:
            @staticmethod
            def fetch(video_id, languages):
                raise RuntimeError("preferred transcript unavailable")

            @staticmethod
            def list(video_id):
                return iter(
                    [
                        FakeTranscript(
                            [
                                {"text": " Hola ", "language_code": "es"},
                                {"text": "mundo", "language_code": "es"},
                            ]
                        )
                    ]
                )

        fetcher.api = FakeApi()

        transcript = fetcher.fetch("video-123", ["es-MX"])

        self.assertEqual(transcript, {"text": "Hola mundo", "language_code": "es"})

    def test_fetch_supports_current_instance_api_return_shape(self):
        fetcher = TranscriptFetcher.__new__(TranscriptFetcher)
        fetcher.last_error = None

        class FakeApi:
            @staticmethod
            def fetch(video_id, languages):
                return FakeFetchedTranscript(
                    [
                        {"text": " Bonjour ", "language_code": "fr"},
                        {"text": "le monde", "language_code": "fr"},
                    ],
                    language_code="fr",
                )

        fetcher.api = FakeApi()

        transcript = fetcher.fetch("video-456", ["fr"])

        self.assertEqual(transcript, {"text": "Bonjour le monde", "language_code": "fr"})

    def test_fetch_stores_failure_reason_when_both_paths_fail(self):
        fetcher = TranscriptFetcher.__new__(TranscriptFetcher)
        fetcher.last_error = None

        class FakeApi:
            @staticmethod
            def fetch(video_id, languages):
                raise RuntimeError("primary path failed")

            @staticmethod
            def list(video_id):
                raise ValueError("listing failed")

        fetcher.api = FakeApi()

        transcript = fetcher.fetch("video-789", ["en"])

        self.assertIsNone(transcript)
        self.assertIn("primary path failed", fetcher.last_error)
        self.assertIn("listing failed", fetcher.last_error)


class GeminiSummarizerPromptTests(unittest.TestCase):
    def test_render_prompt_uses_external_template(self):
        with TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            prompt_template_path = tmp_path / "prompt.md"
            prompt_template_path.write_text(
                "Title: {title}\nLanguage: {preferred_language}\nTranscript:\n{transcript}\n"
            )
            config = Config(
                gemini_api_key="fake-key",
                gemini_model="gemini-test",
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
                prompt_template_path=prompt_template_path,
                failed_video_retry_limit=3,
                failed_video_retry_cooldown_hours=24,
            )
            summarizer = GeminiSummarizer.__new__(GeminiSummarizer)
            summarizer.prompt_template_path = config.prompt_template_path
            summarizer.prompt_template = summarizer._load_prompt_template()

            prompt = summarizer.render_prompt(
                {
                    "title": "Video title",
                    "channel_title": "Channel title",
                    "url": "https://example.com/watch?v=123",
                    "original_language": "ko",
                    "description": "A sample description",
                },
                {"text": "Transcript body", "language_code": "ko"},
            )

        self.assertIn("Title: Video title", prompt)
        self.assertIn("Language: ko", prompt)
        self.assertIn("Transcript body", prompt)


if __name__ == "__main__":
    unittest.main()
