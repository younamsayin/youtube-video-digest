import unittest

from main import TranscriptFetcher


class FakeTranscript:
    def __init__(self, items):
        self.items = items

    def fetch(self):
        return self.items


class TranscriptFetcherTests(unittest.TestCase):
    def test_preferred_languages_include_base_code_and_fallbacks(self):
        fetcher = TranscriptFetcher.__new__(TranscriptFetcher)

        languages = fetcher._preferred_languages(["pt-BR", "ko"])

        self.assertEqual(languages, ["pt-BR", "pt", "ko", "en"])

    def test_fetch_falls_back_to_any_available_transcript(self):
        fetcher = TranscriptFetcher.__new__(TranscriptFetcher)

        class FakeApi:
            @staticmethod
            def get_transcript(video_id, languages):
                raise RuntimeError("preferred transcript unavailable")

            @staticmethod
            def list_transcripts(video_id):
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


if __name__ == "__main__":
    unittest.main()
