#!/usr/bin/env python3
import argparse
import html
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional


SCOPES = ["https://www.googleapis.com/auth/youtube.readonly"]
FIRST_RUN_LOOKBACK_DAYS = 7
FAILED_VIDEO_RETRY_LIMIT = 3
FAILED_VIDEO_RETRY_COOLDOWN_HOURS = 24
MAX_TRANSCRIPT_CHARS = 18000
TRANSCRIPT_REQUEST_DELAY_MIN_SECONDS = 2.0
TRANSCRIPT_REQUEST_DELAY_MAX_SECONDS = 6.0
TRANSCRIPT_RATE_LIMIT_PAUSE_MIN_MINUTES = 30
TRANSCRIPT_RATE_LIMIT_PAUSE_MAX_MINUTES = 60
DEFAULT_TRANSCRIPT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
CHANNEL_ID_PATTERN = re.compile(r"^UC[a-zA-Z0-9_-]{22}$")


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
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
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
    summary_language_mode: str
    summary_language: str
    telegram_bot_token: str
    telegram_chat_id: str
    check_interval_seconds: int
    max_videos_per_channel: int
    summary_dir: Path
    transcript_dir: Path
    prompt_dir: Path
    state_path: Path
    token_path: Path
    credentials_path: Path
    watched_channels_path: Path
    prompt_template_path: Path
    failed_video_retry_limit: int
    failed_video_retry_cooldown_hours: int
    transcript_request_delay_min_seconds: float
    transcript_request_delay_max_seconds: float
    transcript_rate_limit_pause_min_minutes: int
    transcript_rate_limit_pause_max_minutes: int
    transcript_user_agent: str
    transcript_cookie_header: str


class StateStore:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
        else:
            self.data = {
                "seen_video_ids": [],
                "failed_videos": {},
                "first_run_completed": False,
                "last_checked_at": None,
                "transcript_fetch_pause_until": None,
            }
        self.data.setdefault("seen_video_ids", [])
        self.data.setdefault("failed_videos", {})
        self.data.setdefault("first_run_completed", False)
        self.data.setdefault("last_checked_at", None)
        self.data.setdefault("transcript_fetch_pause_until", None)

    def has_seen(self, video_id: str) -> bool:
        return video_id in self.data["seen_video_ids"]

    def mark_seen(self, video_id: str) -> None:
        if not self.has_seen(video_id):
            self.data["seen_video_ids"].append(video_id)
        self.clear_failed(video_id)

    def mark_many_seen(self, video_ids: List[str]) -> None:
        for video_id in video_ids:
            self.mark_seen(video_id)

    def failed_entry(self, video_id: str) -> Optional[Dict[str, object]]:
        return self.data["failed_videos"].get(video_id)

    def should_retry_failed_video(
        self,
        video_id: str,
        retry_limit: int,
        cooldown_hours: int,
    ) -> bool:
        entry = self.failed_entry(video_id)
        if not entry:
            return True

        retry_count = int(entry.get("retry_count", 0))
        if retry_count >= retry_limit:
            return False

        last_attempt_at = entry.get("last_attempt_at")
        if not last_attempt_at:
            return True

        try:
            last_attempt_dt = datetime.fromisoformat(str(last_attempt_at))
        except ValueError:
            return True
        cooldown = timedelta(hours=cooldown_hours)
        return datetime.now(timezone.utc) - last_attempt_dt >= cooldown

    def mark_failed(self, video_id: str, reason: str) -> None:
        existing = self.failed_entry(video_id) or {}
        retry_count = int(existing.get("retry_count", 0)) + 1
        self.data["failed_videos"][video_id] = {
            "retry_count": retry_count,
            "last_attempt_at": datetime.now(timezone.utc).isoformat(),
            "last_error": reason,
        }

    def clear_failed(self, video_id: str) -> None:
        self.data["failed_videos"].pop(video_id, None)

    def transcript_fetch_pause_until(self) -> Optional[datetime]:
        value = self.data.get("transcript_fetch_pause_until")
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except ValueError:
            return None

    def is_transcript_fetch_paused(self) -> bool:
        pause_until = self.transcript_fetch_pause_until()
        return pause_until is not None and datetime.now(timezone.utc) < pause_until

    def set_transcript_fetch_pause(self, pause_until: datetime) -> None:
        self.data["transcript_fetch_pause_until"] = pause_until.isoformat()

    def clear_transcript_fetch_pause(self) -> None:
        self.data["transcript_fetch_pause_until"] = None

    def set_first_run_completed(self) -> None:
        self.data["first_run_completed"] = True

    def touch_last_checked(self) -> None:
        self.data["last_checked_at"] = datetime.now(timezone.utc).isoformat()

    def save(self) -> None:
        self.path.write_text(json.dumps(self.data, indent=2))


class YouTubeWatcher:
    RETRYABLE_STATUS_CODES = {499, 500, 502, 503, 504}

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

    def _execute_request(self, request, label: str):
        google_errors = require_package(
            "googleapiclient.errors", "google-api-python-client"
        )
        max_attempts = 5
        for attempt in range(1, max_attempts + 1):
            try:
                return request.execute()
            except google_errors.HttpError as exc:
                status_code = getattr(exc.resp, "status", None)
                details = str(exc)
                is_retryable = (
                    status_code in self.RETRYABLE_STATUS_CODES
                    or "backendError" in details
                    or "The operation was cancelled." in details
                )
                if not is_retryable or attempt == max_attempts:
                    raise

                delay_seconds = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.5)
                print(
                    "{0} failed with a retryable error (attempt {1}/{2}): {3}. "
                    "Retrying in {4:.1f}s...".format(
                        label, attempt, max_attempts, details, delay_seconds
                    )
                )
                time.sleep(delay_seconds)

    def recent_uploads(self) -> List[Dict[str, str]]:
        channel_ids = self.configured_channel_ids()
        return self.recent_uploads_for_channel_ids(channel_ids)

    def configured_channel_ids(self) -> List[str]:
        configured_channels = self._load_configured_channels()
        if not configured_channels:
            raise SystemExit(
                "No channels are configured in {0}.\n"
                "Add one YouTube channel URL per line, for example:\n"
                "  https://www.youtube.com/@GoogleDevelopers".format(
                    self.config.watched_channels_path
                )
            )

        youtube = self._service()
        channel_ids: List[str] = []
        print(
            "Resolving {0} configured channel(s) from {1}...".format(
                len(configured_channels), self.config.watched_channels_path.name
            )
        )
        for raw_channel in configured_channels:
            channel = self.resolve_channel_reference(youtube, raw_channel)
            channel_ids.append(channel["channel_id"])
            print(
                "Watching channel: {0} ({1})".format(
                    channel["channel_title"], channel["channel_id"]
                )
            )
        return channel_ids

    def _load_configured_channels(self) -> List[str]:
        if not self.config.watched_channels_path.exists():
            return []

        channels: List[str] = []
        for raw_line in self.config.watched_channels_path.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            channels.append(line)
        return channels

    def resolve_channel_reference(self, youtube, reference: str) -> Dict[str, str]:
        parsed_reference = self._parse_channel_reference(reference)
        channel = self._lookup_channel(youtube, parsed_reference)
        if channel is None:
            raise SystemExit(
                "Could not resolve channel reference: {0}\n"
                "Use one of these formats in {1}:\n"
                "  https://www.youtube.com/@handle\n"
                "  https://www.youtube.com/channel/UC...\n"
                "  https://www.youtube.com/user/legacyName".format(
                    reference, self.config.watched_channels_path
                )
            )
        return channel

    def _lookup_channel(self, youtube, parsed_reference: Dict[str, str]) -> Optional[Dict[str, str]]:
        lookup_methods = []
        if parsed_reference["kind"] == "channel_id":
            lookup_methods.append(
                (
                    "id",
                    youtube.channels().list(
                        part="id,snippet", id=parsed_reference["value"], maxResults=1
                    ),
                )
            )
        elif parsed_reference["kind"] == "handle":
            lookup_methods.append(
                (
                    "forHandle",
                    youtube.channels().list(
                        part="id,snippet",
                        forHandle=parsed_reference["value"],
                        maxResults=1,
                    ),
                )
            )
        elif parsed_reference["kind"] == "username":
            lookup_methods.append(
                (
                    "forUsername",
                    youtube.channels().list(
                        part="id,snippet",
                        forUsername=parsed_reference["value"],
                        maxResults=1,
                    ),
                )
            )

        for parameter_name, request in lookup_methods:
            response = self._execute_request(
                request,
                "Resolving channel with {0}={1}".format(
                    parameter_name, parsed_reference["value"]
                ),
            )
            items = response.get("items", [])
            if items:
                item = items[0]
                return {
                    "channel_id": item.get("id", ""),
                    "channel_title": item.get("snippet", {}).get("title", "Unknown channel"),
                }
        return None

    def _parse_channel_reference(self, reference: str) -> Dict[str, str]:
        candidate = reference.strip()
        if not candidate:
            raise SystemExit("Encountered an empty channel reference in the watchlist.")

        if candidate.startswith("@"):
            return {"kind": "handle", "value": candidate}

        if CHANNEL_ID_PATTERN.fullmatch(candidate):
            return {"kind": "channel_id", "value": candidate}

        parsed = urllib.parse.urlparse(candidate)
        if not parsed.scheme and not parsed.netloc:
            raise SystemExit(
                "Unsupported channel reference: {0}\n"
                "Use a full YouTube channel URL, @handle, or channel ID.".format(
                    reference
                )
            )

        host = parsed.netloc.lower()
        if host not in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
            raise SystemExit(
                "Unsupported channel URL host: {0}\n"
                "Use youtube.com channel URLs in {1}.".format(
                    parsed.netloc or candidate, self.config.watched_channels_path
                )
            )

        path = parsed.path.rstrip("/")
        handle_match = re.match(r"^/@([^/]+)$", path)
        if handle_match:
            return {"kind": "handle", "value": "@" + handle_match.group(1)}

        channel_match = re.match(r"^/channel/([^/]+)$", path)
        if channel_match:
            return {"kind": "channel_id", "value": channel_match.group(1)}

        username_match = re.match(r"^/user/([^/]+)$", path)
        if username_match:
            return {"kind": "username", "value": username_match.group(1)}

        raise SystemExit(
            "Unsupported channel URL format: {0}\n"
            "Use an @handle URL, /channel/ URL, or /user/ URL in {1}.".format(
                reference, self.config.watched_channels_path
            )
        )

    def recent_uploads_for_channel_ids(self, channel_ids: List[str]) -> List[Dict[str, str]]:
        youtube = self._service()
        uploads_playlists: Dict[str, Dict[str, str]] = {}

        print("Looking up upload playlists for watched channels...")

        for index in range(0, len(channel_ids), 50):
            batch = channel_ids[index : index + 50]
            request = (
                youtube.channels()
                .list(part="contentDetails,snippet", id=",".join(batch), maxResults=50)
            )
            response = self._execute_request(
                request,
                "Looking up upload playlists for channels {0}-{1}".format(
                    index + 1, index + len(batch)
                ),
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
            request = (
                youtube.playlistItems()
                .list(
                    part="snippet,contentDetails",
                    playlistId=playlist_id,
                    maxResults=self.config.max_videos_per_channel,
                )
            )
            response = self._execute_request(
                request,
                "Scanning uploads for channel {0}/{1}".format(position, total_playlists),
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
            request = (
                youtube.videos()
                .list(part="snippet", id=",".join(batch), maxResults=50)
            )
            response = self._execute_request(
                request,
                "Looking up video languages {0}-{1}".format(
                    index + 1, index + len(batch)
                ),
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
        channel_ids = self.configured_channel_ids()
        if not channel_ids:
            raise SystemExit("No channels were configured for this account.")

        random_channel_id = random.choice(channel_ids)
        print("Selected a random watched channel for test mode.")
        videos = self.recent_uploads_for_channel_ids([random_channel_id])
        if not videos:
            raise SystemExit("The selected channel does not have recent uploads to summarize.")
        return videos[0]


class TranscriptFetcher:
    def __init__(self, config: Config):
        transcript_module = require_package(
            "youtube_transcript_api", "youtube-transcript-api"
        )
        requests_module = require_package("requests", "requests")
        self.http_client = requests_module.Session()
        self.http_client.headers.update(
            {
                "User-Agent": config.transcript_user_agent,
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        if config.transcript_cookie_header:
            self.http_client.headers.update(
                {"Cookie": config.transcript_cookie_header.strip()}
            )
        self.api = transcript_module.YouTubeTranscriptApi(http_client=self.http_client)
        self.last_error: Optional[str] = None
        self.pause_until: Optional[datetime] = None
        self.delay_min_seconds = min(
            config.transcript_request_delay_min_seconds,
            config.transcript_request_delay_max_seconds,
        )
        self.delay_max_seconds = max(
            config.transcript_request_delay_min_seconds,
            config.transcript_request_delay_max_seconds,
        )
        self.pause_min_minutes = min(
            config.transcript_rate_limit_pause_min_minutes,
            config.transcript_rate_limit_pause_max_minutes,
        )
        self.pause_max_minutes = max(
            config.transcript_rate_limit_pause_min_minutes,
            config.transcript_rate_limit_pause_max_minutes,
        )

    def fetch(
        self, video_id: str, preferred_languages: Optional[List[str]] = None
    ) -> Optional[Dict[str, str]]:
        self.last_error = None
        self.pause_until = None
        languages = self._preferred_languages(preferred_languages)
        self._sleep_before_request()
        try:
            transcript = self.api.fetch(video_id, languages=languages)
        except Exception as exc:
            transcript = self._fetch_any_transcript(video_id, exc)
            if transcript is None:
                return None

        transcript_items = self._normalize_transcript_items(transcript)
        parts = []
        for item in transcript_items:
            text = item.get("text", "").strip()
            if text:
                parts.append(text)

        if not parts:
            self.last_error = "Transcript fetch returned no text snippets."
            return None
        return {
            "text": " ".join(parts),
            "language_code": self._transcript_language_code(transcript, transcript_items),
        }

    def _preferred_languages(
        self, preferred_languages: Optional[List[str]] = None
    ) -> List[str]:
        languages: List[str] = []
        for language in preferred_languages or []:
            normalized = (language or "").strip()
            if normalized and normalized not in languages:
                languages.append(normalized)
                base_language = normalized.split("-", 1)[0]
                if base_language and base_language not in languages:
                    languages.append(base_language)

        for fallback_language in ["en", "ko"]:
            if fallback_language not in languages:
                languages.append(fallback_language)
        return languages

    def _fetch_any_transcript(self, video_id: str, original_error: Exception):
        self._sleep_before_request()
        try:
            transcript_list = self.api.list(video_id)
        except Exception as fallback_error:
            self._maybe_pause_on_rate_limit(original_error, fallback_error)
            self.last_error = (
                "Preferred-language fetch failed: {0}. "
                "Fallback transcript listing also failed: {1}.".format(
                    self._format_exception(original_error),
                    self._format_exception(fallback_error),
                )
            )
            return None

        fetch_errors: List[str] = []
        for transcript in transcript_list:
            self._sleep_before_request()
            try:
                fetched = transcript.fetch()
            except Exception as exc:
                self._maybe_pause_on_rate_limit(exc)
                fetch_errors.append(self._format_exception(exc))
                continue
            if fetched:
                return fetched

        details = " ".join(fetch_errors[:3]).strip()
        self.last_error = (
            "Preferred-language fetch failed: {0}. "
            "Fallback transcript listing succeeded, but no transcript could be fetched.{1}".format(
                self._format_exception(original_error),
                " Errors: {0}".format(details) if details else "",
            )
        )
        return None

    def _normalize_transcript_items(self, transcript) -> List[Dict[str, str]]:
        if hasattr(transcript, "to_raw_data"):
            return transcript.to_raw_data()

        normalized_items: List[Dict[str, str]] = []
        for item in transcript:
            if isinstance(item, dict):
                normalized_items.append(item)
                continue
            normalized_items.append(
                {
                    "text": getattr(item, "text", ""),
                    "start": getattr(item, "start", 0),
                    "duration": getattr(item, "duration", 0),
                }
            )
        return normalized_items

    def _transcript_language_code(self, transcript, transcript_items: List[Dict[str, str]]) -> str:
        language_code = getattr(transcript, "language_code", "")
        if language_code:
            return language_code
        if transcript_items:
            return transcript_items[0].get("language_code", "")
        return ""

    def _format_exception(self, exc: Exception) -> str:
        message = str(exc).strip()
        if not message:
            return exc.__class__.__name__
        return "{0}: {1}".format(exc.__class__.__name__, message)

    def _sleep_before_request(self) -> None:
        delay_seconds = random.uniform(self.delay_min_seconds, self.delay_max_seconds)
        if delay_seconds <= 0:
            return
        time.sleep(delay_seconds)

    def _maybe_pause_on_rate_limit(self, *exceptions: Exception) -> None:
        for exc in exceptions:
            if exc is None:
                continue
            message = str(exc)
            class_name = exc.__class__.__name__
            if (
                "429" in message
                or class_name in {"RequestBlocked", "IpBlocked"}
                or "too many requests" in message.lower()
                or "blocked" in message.lower()
            ):
                pause_minutes = random.randint(
                    self.pause_min_minutes, self.pause_max_minutes
                )
                self.pause_until = datetime.now(timezone.utc) + timedelta(
                    minutes=pause_minutes
                )
                return


class GeminiSummarizer:
    def __init__(self, config: Config):
        if not config.gemini_api_key:
            raise SystemExit(
                "GEMINI_API_KEY is missing. Add it to your shell environment or {0}".format(
                    Path(__file__).resolve().parent / ".env"
                )
            )
        self.config = config
        genai_module = require_package("google.genai", "google-genai")
        self.client = genai_module.Client(api_key=config.gemini_api_key)
        self.model = config.gemini_model
        self.prompt_template_path = config.prompt_template_path
        self.prompt_template = self._load_prompt_template()

    def summarize(
        self, video: Dict[str, str], transcript_data: Optional[Dict[str, str]]
    ) -> Dict[str, str]:
        prompt = self.render_prompt(video, transcript_data)
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        return {
            "prompt": prompt,
            "summary": self._extract_text_response(response),
        }

    def render_prompt(
        self, video: Dict[str, str], transcript_data: Optional[Dict[str, str]]
    ) -> str:
        # Keep prompt size bounded so long transcripts do not overwhelm the model.
        transcript_block = (
            transcript_data["text"][:MAX_TRANSCRIPT_CHARS]
            if transcript_data
            else "No transcript was available. Summarize from title and description only."
        )
        preferred_language = (
            video.get("original_language")
            or (transcript_data or {}).get("language_code", "")
            or "unknown"
        )

        prompt_body = self.prompt_template.format(
            title=video["title"],
            channel=video["channel_title"],
            url=video["url"],
            preferred_language=preferred_language,
            description=video["description"] or "(empty)",
            transcript=transcript_block,
        )
        return "{0}\n\n{1}".format(
            self._summary_language_instruction(preferred_language), prompt_body
        )

    def _load_prompt_template(self) -> str:
        if not self.prompt_template_path.exists():
            example_path = self.prompt_template_path.with_name("prompt.example.md")
            raise SystemExit(
                "Missing prompt template at {0}.\n"
                "Create a local prompt file by copying {1} to {0} and editing it for your needs.".format(
                    self.prompt_template_path, example_path
                )
            )
        return self.prompt_template_path.read_text()

    def _extract_text_response(self, response) -> str:
        text_parts: List[str] = []
        for candidate in getattr(response, "candidates", []) or []:
            content = getattr(candidate, "content", None)
            for part in getattr(content, "parts", []) or []:
                text = getattr(part, "text", None)
                if text:
                    text_parts.append(text)

        if text_parts:
            return "".join(text_parts).strip()
        return (getattr(response, "text", "") or "").strip()

    def _summary_language_instruction(self, transcript_language: str) -> str:
        mode = (self.config.summary_language_mode or "transcript").strip().lower()
        if mode not in {"transcript", "fixed"}:
            raise SystemExit(
                "Invalid SUMMARY_LANGUAGE_MODE '{0}'. Use 'transcript' or 'fixed'.".format(
                    self.config.summary_language_mode
                )
            )

        if mode == "fixed":
            target_language = (self.config.summary_language or "").strip()
            if not target_language:
                raise SystemExit(
                    "SUMMARY_LANGUAGE_MODE=fixed requires SUMMARY_LANGUAGE to be set."
                )
            return (
                "[Output Language]\n"
                "Write the entire summary in {0}. Do not switch to another language."
            ).format(target_language)

        return (
            "[Output Language]\n"
            "Write the entire summary in the same language as the transcript. "
            "Transcript language code: {0}. Do not translate the summary into another language."
        ).format(transcript_language or "unknown")


class NotificationClient:
    def __init__(self, config: Config):
        self.telegram_bot_token = config.telegram_bot_token
        self.telegram_chat_id = config.telegram_chat_id

    def send(self, title: str, body: str, full_message: Optional[str] = None) -> None:
        if sys.platform == "darwin":
            safe_title = self._escape_osascript_string(title)
            safe_body = self._escape_osascript_string(body)
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

        telegram_message = full_message or "*{0}*\n\n{1}".format(title, body)
        self._send_telegram(telegram_message)

    def _send_telegram(self, message: str) -> None:
        if not self.telegram_bot_token or not self.telegram_chat_id:
            print("Telegram delivery skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is not set.")
            return

        chunks = self._chunk_telegram_message(self._markdown_to_telegram_html(message))
        print(
            "Sending Telegram message to chat {0} in {1} chunk(s)...".format(
                self.telegram_chat_id, len(chunks)
            )
        )

        for index, chunk in enumerate(chunks, start=1):
            payload = urllib.parse.urlencode(
                {
                    "chat_id": self.telegram_chat_id,
                    "text": chunk,
                    "disable_web_page_preview": "true",
                    "parse_mode": "HTML",
                }
            ).encode("utf-8")
            request = urllib.request.Request(
                "https://api.telegram.org/bot{0}/sendMessage".format(
                    self.telegram_bot_token
                ),
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request, timeout=20) as response:
                    response.read()
                print(
                    "Telegram chunk {0}/{1} sent successfully.".format(
                        index, len(chunks)
                    )
                )
            except Exception as exc:
                print(
                    "Telegram notification failed: {0}".format(
                        self._redact_telegram_error(str(exc))
                    ),
                    file=sys.stderr,
                )
                return

        print("Telegram delivery complete.")

    def _chunk_telegram_message(self, message: str) -> List[str]:
        max_length = 4000
        if len(message) <= max_length:
            return [message]

        chunks: List[str] = []
        remaining = message
        while len(remaining) > max_length:
            split_at = remaining.rfind("\n", 0, max_length)
            if split_at == -1 or split_at < max_length // 2:
                split_at = max_length
            chunks.append(remaining[:split_at].strip())
            remaining = remaining[split_at:].strip()
        if remaining:
            chunks.append(remaining)
        return chunks

    def _markdown_to_telegram_html(self, message: str) -> str:
        lines = message.splitlines()
        converted_lines: List[str] = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("### "):
                converted_lines.append("<b>{0}</b>".format(self._format_inline_markdown(stripped[4:])))
                continue
            if stripped.startswith("## "):
                converted_lines.append("<b>{0}</b>".format(self._format_inline_markdown(stripped[3:])))
                continue
            if stripped.startswith("# "):
                converted_lines.append("<b>{0}</b>".format(self._format_inline_markdown(stripped[2:])))
                continue
            if stripped.startswith("- "):
                converted_lines.append("• {0}".format(self._format_inline_markdown(stripped[2:])))
                continue
            converted_lines.append(self._format_inline_markdown(line))

        return "\n".join(converted_lines)

    def _format_inline_markdown(self, text: str) -> str:
        placeholders: List[str] = []

        def replace_bold(match) -> str:
            placeholders.append("<b>{0}</b>".format(html.escape(match.group(1))))
            return "<<<BOLD_{0}>>>".format(len(placeholders) - 1)

        protected = re.sub(r"\*\*(.+?)\*\*", replace_bold, text)
        escaped = html.escape(protected)
        for index, replacement in enumerate(placeholders):
            escaped = escaped.replace(
                html.escape("<<<BOLD_{0}>>>".format(index)), replacement
            )
        return escaped

    def _escape_osascript_string(self, value: str) -> str:
        return (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("`", "\\`")
        )

    def _redact_telegram_error(self, message: str) -> str:
        if not self.telegram_bot_token:
            return message
        return message.replace(self.telegram_bot_token, "[REDACTED_TELEGRAM_BOT_TOKEN]")


class DigestApp:
    def __init__(self, config: Config):
        self.config = config
        self.state = StateStore(config.state_path)
        self.youtube = YouTubeWatcher(config)
        self.transcripts = TranscriptFetcher(config)
        self.summarizer = GeminiSummarizer(config)
        self.notifier = NotificationClient(config)
        self.config.summary_dir.mkdir(parents=True, exist_ok=True)
        self.config.transcript_dir.mkdir(parents=True, exist_ok=True)
        self.config.prompt_dir.mkdir(parents=True, exist_ok=True)

    def _summary_path(self, video_id: str) -> Path:
        return self.config.summary_dir / "{0}.md".format(video_id)

    def _test_summary_path(self, video_id: str) -> Path:
        return self.config.summary_dir / "{0}-test.md".format(video_id)

    def _transcript_path(self, video_id: str, test_mode: bool = False) -> Path:
        suffix = "-test" if test_mode else ""
        return self.config.transcript_dir / "{0}{1}.txt".format(video_id, suffix)

    def _prompt_path(self, video_id: str, test_mode: bool = False) -> Path:
        suffix = "-test" if test_mode else ""
        return self.config.prompt_dir / "{0}{1}.md".format(video_id, suffix)

    def _write_prompt(self, video: Dict[str, str], prompt: str, test_mode: bool = False) -> Path:
        output_path = self._prompt_path(video["video_id"], test_mode=test_mode)
        output_path.write_text(prompt)
        return output_path

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

    def _write_transcript(
        self,
        video: Dict[str, str],
        transcript_data: Optional[Dict[str, str]],
        test_mode: bool = False,
    ) -> Path:
        output_path = self._transcript_path(video["video_id"], test_mode=test_mode)
        if transcript_data and transcript_data.get("text"):
            content = (
                "Title: {title}\n"
                "Channel: {channel}\n"
                "Published: {published_at}\n"
                "Original language: {language}\n"
                "Transcript language: {transcript_language}\n"
                "URL: {url}\n\n"
                "{transcript}\n"
            ).format(
                title=video["title"],
                channel=video["channel_title"],
                published_at=video["published_at"],
                language=video.get("original_language", "unknown") or "unknown",
                transcript_language=transcript_data.get("language_code", "unknown")
                or "unknown",
                url=video["url"],
                transcript=transcript_data["text"],
            )
        else:
            content = (
                "Title: {title}\n"
                "Channel: {channel}\n"
                "Published: {published_at}\n"
                "Original language: {language}\n"
                "URL: {url}\n\n"
                "Transcript unavailable.\n"
            ).format(
                title=video["title"],
                channel=video["channel_title"],
                published_at=video["published_at"],
                language=video.get("original_language", "unknown") or "unknown",
                url=video["url"],
            )
        output_path.write_text(content)
        return output_path

    def _read_cached_transcript(
        self, video_id: str, test_mode: bool = False
    ) -> Optional[Dict[str, str]]:
        path = self._transcript_path(video_id, test_mode=test_mode)
        if not path.exists():
            return None

        text = path.read_text()
        if "Transcript unavailable." in text:
            return None

        sections = text.split("\n\n", 1)
        if len(sections) != 2:
            return None

        header, transcript_body = sections
        transcript_body = transcript_body.strip()
        if not transcript_body:
            return None

        transcript_language = "unknown"
        for line in header.splitlines():
            if line.startswith("Transcript language:"):
                transcript_language = line.split(":", 1)[1].strip() or "unknown"
                break

        return {
            "text": transcript_body,
            "language_code": transcript_language,
        }

    def _telegram_message(self, video: Dict[str, str], summary: str, test_mode: bool = False) -> str:
        return (
            "# {title}\n\n"
            "- Channel: {channel}\n"
            "- Published: {published_at}\n"
            "- Original language: {language}\n"
            "- URL: {url}\n\n"
            "{summary}"
        ).format(
            title=video["title"],
            channel=video["channel_title"],
            published_at=video["published_at"],
            language=video.get("original_language", "unknown") or "unknown",
            url=video["url"],
            summary=summary,
        )

    def _video_failure_message(self, video: Dict[str, str], stage: str, reason: str) -> str:
        return (
            "# YouTube video processing failed\n\n"
            "- Stage: {stage}\n"
            "- Title: {title}\n"
            "- Channel: {channel}\n"
            "- Published: {published_at}\n"
            "- URL: {url}\n\n"
            "Reason:\n"
            "{reason}"
        ).format(
            stage=stage,
            title=video["title"],
            channel=video["channel_title"],
            published_at=video["published_at"],
            url=video["url"],
            reason=reason,
        )

    def _notify_video_failure(self, video: Dict[str, str], stage: str, reason: str) -> None:
        self.notifier.send(
            "YouTube video failed",
            "{0}: {1}".format(stage, video["title"]),
            full_message=self._video_failure_message(video, stage, reason),
        )

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

    def _eligible_videos(self, videos: List[Dict[str, str]]) -> List[Dict[str, str]]:
        eligible: List[Dict[str, str]] = []
        for video in videos:
            video_id = video["video_id"]
            if self.state.has_seen(video_id):
                continue
            if not self.state.should_retry_failed_video(
                video_id,
                self.config.failed_video_retry_limit,
                self.config.failed_video_retry_cooldown_hours,
            ):
                failed_entry = self.state.failed_entry(video_id) or {}
                print(
                    "Skipping retry for video after repeated transcript failures: {0}\n"
                    "Last error: {1}".format(
                        video["title"],
                        failed_entry.get("last_error", "Unknown transcript error."),
                    )
                )
                continue
            eligible.append(video)
        return eligible

    def _handle_transcript_pause(self) -> bool:
        if self.transcripts.pause_until is None:
            return False

        self.state.set_transcript_fetch_pause(self.transcripts.pause_until)
        self.state.save()
        print(
            "Pausing transcript fetches until {0} after a rate-limit/blocking signal.".format(
                self.transcripts.pause_until.isoformat()
            )
        )
        return True

    def _format_local_timestamp(self, dt: datetime) -> str:
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")

    def check_once(self, include_existing: bool = False) -> None:
        print("Checking for new videos...")
        videos = self.youtube.recent_uploads()
        unseen = self._eligible_videos(videos)
        processed_count = 0

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

        if self.state.is_transcript_fetch_paused():
            pause_until = self.state.transcript_fetch_pause_until()
            print(
                "Transcript fetches are paused until {0}. Skipping this run.".format(
                    pause_until.isoformat() if pause_until else "an unknown time"
                )
            )
            return
        self.state.clear_transcript_fetch_pause()

        for video in unseen:
            print("Summarizing: {0} ({1})".format(video["title"], video["url"]))
            transcript_data = self._read_cached_transcript(video["video_id"])
            if transcript_data:
                print(
                    "Using cached transcript for video: {0}".format(video["title"])
                )
            else:
                transcript_data = self.transcripts.fetch(
                    video["video_id"], [video.get("original_language", "")]
                )
                if self._handle_transcript_pause():
                    break
            if not transcript_data or not transcript_data.get("text"):
                failure_reason = self.transcripts.last_error or "Unknown transcript error."
                print(
                    "Skipping video because transcript fetch failed: {0}\n"
                    "Reason: {1}".format(video["title"], failure_reason)
                )
                self._notify_video_failure(video, "Transcript fetch", failure_reason)
                self.state.mark_failed(video["video_id"], failure_reason)
                self.state.save()
                continue
            transcript_path = self._write_transcript(video, transcript_data)
            print("Transcript saved to {0}".format(transcript_path))
            try:
                summary_result = self.summarizer.summarize(video, transcript_data)
            except Exception as exc:
                failure_reason = "Summary generation failed: {0}".format(exc)
                print(
                    "Skipping video because summary generation failed: {0}\n"
                    "Reason: {1}".format(video["title"], failure_reason)
                )
                self._notify_video_failure(video, "Summary generation", failure_reason)
                self.state.mark_failed(video["video_id"], failure_reason)
                self.state.save()
                continue
            prompt_path = self._write_prompt(video, summary_result["prompt"])
            print("Prompt saved to {0}".format(prompt_path))
            summary = summary_result["summary"]
            output_path = self._write_summary(video, summary)
            self.state.mark_seen(video["video_id"])
            self.state.save()
            processed_count += 1
            self.notifier.send(
                "YouTube summary ready",
                "{0} - saved to {1}".format(video["title"], output_path.name),
                full_message=self._telegram_message(video, summary),
            )

        self.state.set_first_run_completed()
        self.state.touch_last_checked()
        self.state.save()
        print("Processed {0} new videos.".format(processed_count))

    def test_run(self) -> None:
        print("Running test mode with one random watched channel...")
        video = self.youtube.random_channel_recent_video()
        print(
            "Summarizing test video: {0} from {1}".format(
                video["title"], video["channel_title"]
            )
        )
        if self.state.is_transcript_fetch_paused():
            pause_until = self.state.transcript_fetch_pause_until()
            print(
                "Transcript fetches are paused until {0}. Skipping test run.".format(
                    pause_until.isoformat() if pause_until else "an unknown time"
                )
            )
            return

        transcript_data = self._read_cached_transcript(video["video_id"], test_mode=True)
        if transcript_data:
            print("Using cached test transcript for video.")
        else:
            self.state.clear_transcript_fetch_pause()
            transcript_data = self.transcripts.fetch(
                video["video_id"], [video.get("original_language", "")]
            )
            if self._handle_transcript_pause():
                return
        if not transcript_data or not transcript_data.get("text"):
            failure_reason = self.transcripts.last_error or "Unknown transcript error."
            print(
                "Skipping test run because transcript fetch failed for this video.\n"
                "Reason: {0}".format(failure_reason)
            )
            self._notify_video_failure(video, "Transcript fetch", failure_reason)
            return
        transcript_path = self._write_transcript(video, transcript_data, test_mode=True)
        print("Test transcript saved to {0}".format(transcript_path))
        try:
            summary_result = self.summarizer.summarize(video, transcript_data)
        except Exception as exc:
            failure_reason = "Summary generation failed: {0}".format(exc)
            print(
                "Skipping test run because summary generation failed for this video.\n"
                "Reason: {0}".format(failure_reason)
            )
            self._notify_video_failure(video, "Summary generation", failure_reason)
            return
        prompt_path = self._write_prompt(video, summary_result["prompt"], test_mode=True)
        print("Test prompt saved to {0}".format(prompt_path))
        summary = summary_result["summary"]
        output_path = self._write_summary(video, summary, test_mode=True)
        self.notifier.send(
            "YouTube test summary ready",
            "{0} - saved to {1}".format(video["title"], output_path.name),
            full_message=self._telegram_message(video, summary, test_mode=True),
        )
        print("Test summary saved to {0}".format(output_path))

    def daemon(self) -> None:
        while True:
            started_at = datetime.now(timezone.utc)
            print(
                "[Daemon] Check started at {0}".format(
                    self._format_local_timestamp(started_at)
                )
            )
            try:
                self.check_once()
            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except Exception as exc:
                print("Run failed: {0}".format(exc), file=sys.stderr)
                self.notifier.send("YouTube digest failed", str(exc))
            finished_at = datetime.now(timezone.utc)
            next_run_at = finished_at + timedelta(
                seconds=self.config.check_interval_seconds
            )
            print(
                "[Daemon] Check finished at {0}".format(
                    self._format_local_timestamp(finished_at)
                )
            )
            print(
                "[Daemon] Sleeping for {0} seconds. Next check at {1}".format(
                    self.config.check_interval_seconds,
                    self._format_local_timestamp(next_run_at),
                )
            )
            time.sleep(self.config.check_interval_seconds)


def build_config(project_dir: Path) -> Config:
    load_dotenv(project_dir)
    data_dir = project_dir / "data"
    return Config(
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        summary_language_mode=os.getenv("SUMMARY_LANGUAGE_MODE", "transcript"),
        summary_language=os.getenv("SUMMARY_LANGUAGE", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        check_interval_seconds=int(os.getenv("CHECK_INTERVAL_SECONDS", "3600")),
        max_videos_per_channel=int(os.getenv("MAX_VIDEOS_PER_CHANNEL", "3")),
        summary_dir=data_dir / "summaries",
        transcript_dir=data_dir / "transcripts",
        prompt_dir=data_dir / "prompts",
        state_path=data_dir / "state.json",
        token_path=data_dir / "google_token.json",
        credentials_path=project_dir / "credentials.json",
        watched_channels_path=project_dir / "watched_channels.txt",
        prompt_template_path=project_dir / "prompt.md",
        failed_video_retry_limit=int(
            os.getenv("FAILED_VIDEO_RETRY_LIMIT", str(FAILED_VIDEO_RETRY_LIMIT))
        ),
        failed_video_retry_cooldown_hours=int(
            os.getenv(
                "FAILED_VIDEO_RETRY_COOLDOWN_HOURS",
                str(FAILED_VIDEO_RETRY_COOLDOWN_HOURS),
            )
        ),
        transcript_request_delay_min_seconds=float(
            os.getenv(
                "TRANSCRIPT_REQUEST_DELAY_MIN_SECONDS",
                str(TRANSCRIPT_REQUEST_DELAY_MIN_SECONDS),
            )
        ),
        transcript_request_delay_max_seconds=float(
            os.getenv(
                "TRANSCRIPT_REQUEST_DELAY_MAX_SECONDS",
                str(TRANSCRIPT_REQUEST_DELAY_MAX_SECONDS),
            )
        ),
        transcript_rate_limit_pause_min_minutes=int(
            os.getenv(
                "TRANSCRIPT_RATE_LIMIT_PAUSE_MIN_MINUTES",
                str(TRANSCRIPT_RATE_LIMIT_PAUSE_MIN_MINUTES),
            )
        ),
        transcript_rate_limit_pause_max_minutes=int(
            os.getenv(
                "TRANSCRIPT_RATE_LIMIT_PAUSE_MAX_MINUTES",
                str(TRANSCRIPT_RATE_LIMIT_PAUSE_MAX_MINUTES),
            )
        ),
        transcript_user_agent=os.getenv(
            "TRANSCRIPT_USER_AGENT", DEFAULT_TRANSCRIPT_USER_AGENT
        ),
        transcript_cookie_header=os.getenv("TRANSCRIPT_COOKIE_HEADER", ""),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Watch selected YouTube channels, summarize new videos, and notify when ready."
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
        help="Pick one random watched channel and summarize one recent video without updating seen state.",
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
