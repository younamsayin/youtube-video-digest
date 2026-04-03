import unittest

from main import TranscriptFetcher


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


if __name__ == "__main__":
    unittest.main()
