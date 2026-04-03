# YouTube Video Digest

This program:

1. Reads the YouTube channels you list in `watched_channels.txt`.
2. Finds newly uploaded videos.
3. Summarizes each new video with Gemini.
4. Sends a local notification when the summary is ready.

It is built for macOS and uses:

- Google OAuth to access YouTube Data API
- Gemini for summarization
- `osascript` for desktop notifications
- Telegram Bot API for chat delivery

## Setup

### 1. Create a Google OAuth client

In Google Cloud:

- Enable the **YouTube Data API v3**
- Create an **OAuth client ID**
- Choose **Desktop app**
- Download the client JSON
- Save it as `credentials.json` in this folder

Expected path:

`./credentials.json`

### 2. Install Python dependencies

```bash
cd /path/to/youtube-video-digest
pip3 install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and add your Gemini API key.

```bash
cd /path/to/youtube-video-digest
cp .env.example .env
```

Important variables:

- `GEMINI_API_KEY`: required
- `GEMINI_MODEL`: defaults to `gemini-2.5-flash`
- `TELEGRAM_BOT_TOKEN`: optional, enables Telegram delivery
- `TELEGRAM_CHAT_ID`: optional, the target Telegram user/chat/channel id
- `CHECK_INTERVAL_SECONDS`: defaults to `3600`
- `MAX_VIDEOS_PER_CHANNEL`: how many recent uploads to inspect per watched channel
- `FAILED_VIDEO_RETRY_LIMIT`: defaults to `3`
- `FAILED_VIDEO_RETRY_COOLDOWN_HOURS`: defaults to `24`
- `TRANSCRIPT_REQUEST_DELAY_MIN_SECONDS`: defaults to `2`
- `TRANSCRIPT_REQUEST_DELAY_MAX_SECONDS`: defaults to `6`
- `TRANSCRIPT_RATE_LIMIT_PAUSE_MIN_MINUTES`: defaults to `30`
- `TRANSCRIPT_RATE_LIMIT_PAUSE_MAX_MINUTES`: defaults to `60`
- `TRANSCRIPT_USER_AGENT`: browser-like user agent used for transcript requests
- `TRANSCRIPT_COOKIE_HEADER`: optional raw `Cookie` header value to attach to transcript requests

### 4. Choose which channels to watch

Edit `watched_channels.txt` and add one YouTube channel per line.

Supported formats:

- `https://www.youtube.com/@handle`
- `https://www.youtube.com/channel/UC...`
- `https://www.youtube.com/user/legacyUsername`

Example:

```text
https://www.youtube.com/@GoogleDevelopers
https://www.youtube.com/@OpenAI
```

### 5. Authorize YouTube access

```bash
cd /path/to/youtube-video-digest
python3 main.py authorize
```

This opens a browser and stores a refreshable token at:

`./data/google_token.json`

## Usage

Run one check:

```bash
cd /path/to/youtube-video-digest
python3 main.py run-once
```

Run one check and summarize the current backlog too:

```bash
cd /path/to/youtube-video-digest
python3 main.py run-once --include-existing
```

Run a test summary on one random watched channel:

```bash
cd /path/to/youtube-video-digest
python3 main.py test-run
```

Run forever and check every hour:

```bash
cd /path/to/youtube-video-digest
python3 main.py daemon
```

## First-run behavior

On the first normal run, the program marks the current watched-channel feed as already seen and only summarizes videos uploaded after that point. This avoids a flood of old summaries.

If you use `--include-existing` on the very first run, the app only considers videos uploaded in the last 7 days and skips older backlog items.

If you do want the existing backlog, use:

```bash
python3 main.py run-once --include-existing
```

## Output

Summaries are written to:

`./data/summaries/`

Each summary is saved as a markdown file named with the YouTube video ID.

Test summaries are saved with a `-test.md` suffix and do not update the seen-video state.

Fetched transcripts are also written to:

`./data/transcripts/`

Each transcript is saved as a text file named with the YouTube video ID. Test transcripts use a `-test.txt` suffix.

Rendered Gemini prompts are written to:

`./data/prompts/`

Each prompt is saved as a markdown file named with the YouTube video ID. Test prompts use a `-test.md` suffix.

## Notes

- Transcript retrieval depends on whether subtitles are available for the video.
- The prompt template is stored in `prompt.md`, and the fully rendered prompt used for each summary is saved to `data/prompts/`.
- The app tries to summarize in the video's original language using YouTube metadata first, then falls back to inferring from the transcript, title, and description.
- If transcript fetching fails, the app tracks the video separately from successful summaries and retries it later based on the configured retry limit and cooldown.
- Transcript fetching is intentionally serialized, uses randomized request delays, reuses cached transcript files, and pauses future transcript requests for 30-60 minutes when YouTube starts signaling blocking or rate limiting.
- Desktop notifications currently use macOS Notification Center.
- If both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set, completed summaries are also sent to Telegram.
