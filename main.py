#!/usr/bin/env python3
import argparse
import json
import os
import random
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional


SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
FIRST_RUN_LOOKBACK_DAYS = 7


def load_dotenv(project_dir: Path) -> None:
    env_path = project_dir / ".env"
    if not env_path.exists():
        return

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def require_package(name: str, install_hint: str):
    try:
        return __import__(name, fromlist=["*"])
    except ImportError as exc:
        raise SystemExit(
            "Missing dependency '{0}'. Install dependencies first:\n"
            "  pip3 install -r requirements.txt\n"
            "Package hint: {1}".format(name, install_hint)
        ) from exc


@dataclass
class Config:
    gemini_api_key: str
    gemini_model: str
    check_interval_seconds: int
    max_videos_per_channel: int
    summary_dir: Path
    state_path: Path
    token_path: Path
    credentials_path: Path


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        else:
            self.data = {
                "seen_video_ids": [],
                "first_run_completed": False,
                "last_checked_at": None,
            }

    def has_seen(self, video_id: str) -> bool:
        return video_id in self.data["seen_video_ids"]

    def mark_seen(self, video_id: str) -> None:
        if not self.has_seen(video_id):
            self.data["seen_video_ids"].append(video_id)

    def mark_many_seen(self, video_ids: List[str]) -> None:
        for video_id in video_ids:
            self.mark_seen(video_id)

    def set_first_run_completed(self) -> None:
        self.data["first_run_completed"] = True

    def touch_last_checked(self) -> None:
        self.data["last_checked_at"] = datetime.now(timezone.utc).isoformat()

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2))


class YouTubeWatcher:
    def __init__(self, config: Config):
        self.config = config

    def _google_modules(self):
        google_oauth2 = require_package("google.oauth2.credentials", "google-auth")
        google_auth_flow = require_package(
            "google_auth_oauthlib.flow", "google-auth-oauthlib"
        )
        google_discovery = require_package(
            "googleapiclient.discovery", "google-api-python-client"
        )
        google_transport = require_package(
            "google.auth.transport.requests", "google-auth"
        )
        return google_oauth2, google_auth_flow, google_discovery, google_transport

    def authorize(self) -> None:
        _, google_auth_flow, _, _ = self._google_modules()
        if not self.config.credentials_path.exists():
            raise SystemExit(
                "Missing Google OAuth client file at {0}.\n"
                "Create a Desktop app OAuth client in Google Cloud and save the JSON there.".format(
                    self.config.credentials_path
                )
            )

        flow = google_auth_flow.InstalledAppFlow.from_client_secrets_file(
            str(self.config.credentials_path), SCOPES
        )
        creds = flow.run_local_server(port=0)
        self.config.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.token_path.write_text(creds.to_json())
        print("Authorization complete. Token saved to {0}".format(self.config.token_path))

    def _load_credentials(self):
        google_oauth2, _, _, google_transport = self._google_modules()
        if not self.config.token_path.exists():
            raise SystemExit(
                "Missing token at {0}. Run:\n  python3 main.py authorize".format(
                    self.config.token_path
                )
            )

        creds = google_oauth2.Credentials.from_authorized_user_file(
            str(self.config.token_path), SCOPES
        )
        if creds.expired and creds.refresh_token:
            creds.refresh(google_transport.Request())
            self.config.token_path.write_text(creds.to_json())
        return creds

    def _service(self):
        _, _, google_discovery, _ = self._google_modules()
        creds = self._load_credentials()
        return google_discovery.build("youtube", "v3", credentials=creds)

    def subscribed_channel_ids(self) -> List[str]:
        youtube = self._service()
        channel_ids: List[str] = []
        page_token = None
        page_count = 0

        print("Loading subscribed channels...")

        while True:
            page_count += 1
            response = (
                youtube.subscriptions()
                .list(
                    part="snippet",
                    mine=True,
                    maxResults=50,
                    pageToken=page_token,
                )
                .execute()
            )

            for item in response.get("items", []):
                resource = item.get("snippet", {}).get("resourceId", {})
                channel_id = resource.get("channelId")
                if channel_id:
                    channel_ids.append(channel_id)

            page_token = response.get("nextPageToken")
            print(
                "Fetched subscription page {0}. Total channels so far: {1}".format(
                    page_count, len(channel_ids)
                )
            )
            if not page_token:
                break

        print("Loaded {0} subscribed channels.".format(len(channel_ids)))
        return channel_ids

    def recent_uploads(self) -> List[Dict[str, str]]:
        youtube = self._service()
        channel_ids = self.subscribed_channel_ids()
        return self.recent_uploads_for_channel_ids(channel_ids)

    def recent_uploads_for_channel_ids(self, channel_ids: List[str]) -> List[Dict[str, str]]:
        youtube = self._service()
        uploads_playlists: Dict[str, Dict[str, str]] = {}

        print("Looking up upload playlists for subscribed channels...")

        for index in range(0, len(channel_ids), 50):
            batch = channel_ids[index : index + 50]
            response = (
                youtube.channels()
                .list(part="contentDetails,snippet", id=",".join(batch), maxResults=50)
                .execute()
            )
            for item in response.get("items", []):
                playlist_id = (
                    item.get("contentDetails", {})
                    .get("relatedPlaylists", {})
                    .get("uploads")
                )
                channel_title = item.get("snippet", {}).get("title", "Unknown channel")
                if playlist_id:
                    uploads_playlists[playlist_id] = {
                        "channel_id": item.get("id", ""),
                        "channel_title": channel_title,
                    }
            print(
                "Prepared upload playlists for {0}/{1} channels.".format(
                    min(index + len(batch), len(channel_ids)), len(channel_ids)
                )
            )

        videos: List[Dict[str, str]] = []
        playlist_items = list(uploads_playlists.items())
        total_playlists = len(playlist_items)
        print("Scanning recent uploads from {0} channels...".format(total_playlists))
        for position, (playlist_id, metadata) in enumerate(playlist_items, start=1):
            response = (
                youtube.playlistItems()
                .list(
                    part="snippet,contentDetails",
                    playlistId=playlist_id,
                    maxResults=self.config.max_videos_per_channel,
                )
                .execute()
            )
            for item in response.get("items", []):
                video_id = item.get("contentDetails", {}).get("videoId")
                snippet = item.get("snippet", {})
                if not video_id:
                    continue
                videos.append(
                    {
                        "video_id": video_id,
                        "title": snippet.get("title", "Untitled video"),
                        "published_at": snippet.get("publishedAt", ""),
                        "description": snippet.get("description", ""),
                        "channel_title": metadata["channel_title"],
                        "channel_id": metadata["channel_id"],
                        "url": "https://www.youtube.com/watch?v={0}".format(video_id),
                    }
                )
            if position == total_playlists or position % 25 == 0:
                print(
                    "Scanned uploads for {0}/{1} channels.".format(
                        position, total_playlists
                    )
                )

        video_language_by_id: Dict[str, str] = {}
        video_ids = [video["video_id"] for video in videos]
        print("Looking up video languages for {0} recent uploads...".format(len(video_ids)))
        for index in range(0, len(video_ids), 50):
            batch = video_ids[index : index + 50]
            if not batch:
                continue
            response = (
                youtube.videos()
                .list(part="snippet", id=",".join(batch), maxResults=50)
                .execute()
            )
            for item in response.get("items", []):
                snippet = item.get("snippet", {})
                language_code = snippet.get("defaultAudioLanguage") or snippet.get(
                    "defaultLanguage", ""
                )
                if language_code:
                    video_language_by_id[item.get("id", "")] = language_code
            print(
                "Checked languages for {0}/{1} videos.".format(
                    min(index + len(batch), len(video_ids)), len(video_ids)
                )
            )

        for video in videos:
            video["original_language"] = video_language_by_id.get(video["video_id"], "")

        videos.sort(key=lambda item: item.get("published_at", ""), reverse=True)
        return videos

    def random_channel_recent_video(self) -> Dict[str, str]:
        channel_ids = self.subscribed_channel_ids()
        if not channel_ids:
            raise SystemExit("No subscribed channels were found for this account.")

        random_channel_id = random.choice(channel_ids)
        print("Selected a random subscribed channel for test mode.")
        videos = self.recent_uploads_for_channel_ids([random_channel_id])
        if not videos:
            raise SystemExit("The selected channel does not have recent uploads to summarize.")
        return videos[0]


class TranscriptFetcher:
    def __init__(self):
        transcript_module = require_package(
            "youtube_transcript_api", "youtube-transcript-api"
        )
        self.api = transcript_module.YouTubeTranscriptApi

    def fetch(self, video_id: str) -> Optional[Dict[str, str]]:
        try:
            transcript = self.api.get_transcript(video_id, languages=["en", "ko"])
        except Exception:
            return None

        parts = []
        for item in transcript:
            text = item.get("text", "").strip()
            if text:
                parts.append(text)

        if not parts:
            return None
        return {
            "text": " ".join(parts),
            "language_code": transcript[0].get("language_code", ""),
        }


class GeminiSummarizer:
    def __init__(self, config: Config):
        if not config.gemini_api_key:
            raise SystemExit(
                "GEMINI_API_KEY is missing. Add it to your shell environment or {0}".format(
                    Path(__file__).resolve().parent / ".env"
                )
            )
        genai_module = require_package("google.genai", "google-genai")
        self.client = genai_module.Client(api_key=config.gemini_api_key)
        self.model = config.gemini_model

    def summarize(
        self, video: Dict[str, str], transcript_data: Optional[Dict[str, str]]
    ) -> str:
        transcript_block = (
            transcript_data["text"][:18000]
            if transcript_data
            else "No transcript was available. Summarize from title and description only."
        )
        preferred_language = (
            video.get("original_language")
            or (transcript_data or {}).get("language_code", "")
            or "unknown"
        )

        prompt = (
            "Summarize this YouTube video in the video's original language.\n"
            "Prefer the language indicated by the metadata below. "
            "If the metadata is missing or unclear, infer the language from the transcript, "
            "title, and description, then write the summary in that language.\n"
            "Return markdown with these sections:\n"
            "## TL;DR\n"
            "## Key Points\n"
            "## Action Items\n"
            "Keep it concise but useful.\n\n"
            "Video title: {title}\n"
            "Channel: {channel}\n"
            "URL: {url}\n"
            "Preferred original language code: {preferred_language}\n"
            "Description:\n{description}\n\n"
            "Transcript or fallback content:\n{transcript}\n"
        ).format(
            title=video["title"],
            channel=video["channel_title"],
            url=video["url"],
            preferred_language=preferred_language,
            description=video["description"] or "(empty)",
            transcript=transcript_block,
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        return (response.text or "").strip()


class NotificationClient:
    def send(self, title: str, body: str) -> None:
        if sys.platform == "darwin":
            safe_title = title.replace('"', '\\"')
            safe_body = body.replace('"', '\\"')
            subprocess.run(
                [
                    "osascript",
                    "-e",
                    'display notification "{0}" with title "{1}"'.format(
                        safe_body, safe_title
                    ),
                ],
                check=False,
            )
        else:
            print("[Notification] {0}: {1}".format(title, body))


class DigestApp:
    def __init__(self, config: Config):
        self.config = config
        self.state = StateStore(config.state_path)
        self.youtube = YouTubeWatcher(config)
        self.transcripts = TranscriptFetcher()
        self.summarizer = GeminiSummarizer(config)
        self.notifier = NotificationClient()
        self.config.summary_dir.mkdir(parents=True, exist_ok=True)

    def _summary_path(self, video_id: str) -> Path:
        return self.config.summary_dir / "{0}.md".format(video_id)

    def _test_summary_path(self, video_id: str) -> Path:
        return self.config.summary_dir / "{0}-test.md".format(video_id)

    def _write_summary(self, video: Dict[str, str], summary: str, test_mode: bool = False) -> Path:
        output_path = (
            self._test_summary_path(video["video_id"])
            if test_mode
            else self._summary_path(video["video_id"])
        )
        content = (
            "# {title}\n\n"
            "- Channel: {channel}\n"
            "- Published: {published_at}\n"
            "- Original language: {language}\n"
            "- URL: {url}\n\n"
            "{summary}\n"
        ).format(
            title=video["title"],
            channel=video["channel_title"],
            published_at=video["published_at"],
            language=video.get("original_language", "unknown"),
            url=video["url"],
            summary=summary,
        )
        output_path.write_text(content)
        return output_path

    def _is_within_first_run_window(self, video: Dict[str, str]) -> bool:
        published_at = video.get("published_at", "")
        if not published_at:
            return False
        try:
            published_dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        cutoff = datetime.now(timezone.utc) - timedelta(days=FIRST_RUN_LOOKBACK_DAYS)
        return published_dt >= cutoff

    def check_once(self, include_existing: bool = False) -> None:
        print("Checking for new videos...")
        videos = self.youtube.recent_uploads()
        unseen = [video for video in videos if not self.state.has_seen(video["video_id"])]

        if not self.state.data["first_run_completed"] and not include_existing:
            self.state.mark_many_seen([video["video_id"] for video in videos])
            self.state.set_first_run_completed()
            self.state.touch_last_checked()
            self.state.save()
            recent_count = sum(1 for video in videos if self._is_within_first_run_window(video))
            print(
                "First run complete. Marked {0} current videos as seen without summarizing. "
                "Only videos from the last {1} days would be considered on a first-run summary pass "
                "({2} recent videos found).".format(
                    len(videos), FIRST_RUN_LOOKBACK_DAYS, recent_count
                )
            )
            return

        if not self.state.data["first_run_completed"] and include_existing:
            recent_unseen = [video for video in unseen if self._is_within_first_run_window(video)]
            older_unseen = [video for video in unseen if not self._is_within_first_run_window(video)]
            if older_unseen:
                self.state.mark_many_seen([video["video_id"] for video in older_unseen])
                self.state.save()
            unseen = recent_unseen
            print(
                "First run with backlog enabled: considering only videos from the last {0} days "
                "({1} recent, {2} older videos skipped).".format(
                    FIRST_RUN_LOOKBACK_DAYS, len(recent_unseen), len(older_unseen)
                )
            )

        if not unseen:
            self.state.set_first_run_completed()
            self.state.touch_last_checked()
            self.state.save()
            print("No new videos found.")
            return

        for video in unseen:
            print("Summarizing: {0} ({1})".format(video["title"], video["url"]))
            transcript_data = self.transcripts.fetch(video["video_id"])
            summary = self.summarizer.summarize(video, transcript_data)
            output_path = self._write_summary(video, summary)
            self.state.mark_seen(video["video_id"])
            self.state.save()
            self.notifier.send(
                "YouTube summary ready",
                "{0} - saved to {1}".format(video["title"], output_path.name),
            )

        self.state.set_first_run_completed()
        self.state.touch_last_checked()
        self.state.save()
        print("Processed {0} new videos.".format(len(unseen)))

    def test_run(self) -> None:
        print("Running test mode with one random subscribed channel...")
        video = self.youtube.random_channel_recent_video()
        print(
            "Summarizing test video: {0} from {1}".format(
                video["title"], video["channel_title"]
            )
        )
        transcript_data = self.transcripts.fetch(video["video_id"])
        summary = self.summarizer.summarize(video, transcript_data)
        output_path = self._write_summary(video, summary, test_mode=True)
        self.notifier.send(
            "YouTube test summary ready",
            "{0} - saved to {1}".format(video["title"], output_path.name),
        )
        print("Test summary saved to {0}".format(output_path))

    def daemon(self) -> None:
        while True:
            try:
                self.check_once()
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print("Run failed: {0}".format(exc), file=sys.stderr)
                self.notifier.send("YouTube digest failed", str(exc))
            time.sleep(self.config.check_interval_seconds)


def build_config(project_dir: Path) -> Config:
    load_dotenv(project_dir)
    data_dir = project_dir / "data"
    return Config(
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        check_interval_seconds=int(os.getenv("CHECK_INTERVAL_SECONDS", "3600")),
        max_videos_per_channel=int(os.getenv("MAX_VIDEOS_PER_CHANNEL", "3")),
        summary_dir=data_dir / "summaries",
        state_path=data_dir / "state.json",
        token_path=data_dir / "google_token.json",
        credentials_path=project_dir / "credentials.json",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch subscribed YouTube channels, summarize new videos, and notify when ready."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("authorize", help="Run Google OAuth and store a refreshable token.")

    run_once = subparsers.add_parser("run-once", help="Check once for new videos.")
    run_once.add_argument(
        "--include-existing",
        action="store_true",
        help="On the first run, summarize the current backlog instead of marking it as seen.",
    )

    subparsers.add_parser(
        "test-run",
        help="Pick one random subscribed channel and summarize one recent video without updating seen state.",
    )

    subparsers.add_parser("daemon", help="Run forever and check every configured interval.")
    return parser.parse_args()


def main() -> None:
    project_dir = Path(__file__).resolve().parent
    config = build_config(project_dir)
    args = parse_args()

    if args.command == "authorize":
        YouTubeWatcher(config).authorize()
        return

    app = DigestApp(config)
    if args.command == "run-once":
        app.check_once(include_existing=args.include_existing)
    elif args.command == "test-run":
        app.test_run()
    elif args.command == "daemon":
        app.daemon()


if __name__ == "__main__":
    main()
