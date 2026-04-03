# YouTube Video Digest

This program:

1. Reads the YouTube channels you subscribe to.
2. Finds newly uploaded videos.
3. Summarizes each new video with Gemini.
4. Sends a local notification when the summary is ready.

It is built for macOS and uses:

- Google OAuth to access your own YouTube subscriptions
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

`/Users/samuelnam/Desktop/code/youtube-video-digest/credentials.json`

### 2. Install Python dependencies

```bash
cd /Users/samuelnam/Desktop/code/youtube-video-digest
pip3 install -r requirements.txt
```

### 3. Configure environment variables

Copy `.env.example` to `.env` and add your Gemini API key.

```bash
cd /Users/samuelnam/Desktop/code/youtube-video-digest
cp .env.example .env
```

Important variables:

- `GEMINI_API_KEY`: required
- `GEMINI_MODEL`: defaults to `gemini-2.5-flash`
- `TELEGRAM_BOT_TOKEN`: optional, enables Telegram delivery
- `TELEGRAM_CHAT_ID`: optional, the target Telegram user/chat/channel id
- `CHECK_INTERVAL_SECONDS`: defaults to `3600`
- `MAX_VIDEOS_PER_CHANNEL`: how many recent uploads to inspect per subscribed channel

### 4. Authorize YouTube access

```bash
cd /Users/samuelnam/Desktop/code/youtube-video-digest
python3 main.py authorize
```

This opens a browser and stores a refreshable token at:

`/Users/samuelnam/Desktop/code/youtube-video-digest/data/google_token.json`

## Usage

Run one check:

```bash
cd /Users/samuelnam/Desktop/code/youtube-video-digest
python3 main.py run-once
```

Run one check and summarize the current backlog too:

```bash
cd /Users/samuelnam/Desktop/code/youtube-video-digest
python3 main.py run-once --include-existing
```

Run a test summary on one random subscribed channel:

```bash
cd /Users/samuelnam/Desktop/code/youtube-video-digest
python3 main.py test-run
```

Run forever and check every hour:

```bash
cd /Users/samuelnam/Desktop/code/youtube-video-digest
python3 main.py daemon
```

## First-run behavior

On the first normal run, the program marks the current feed as already seen and only summarizes videos uploaded after that point. This avoids a flood of old summaries.

If you use `--include-existing` on the very first run, the app only considers videos uploaded in the last 7 days and skips older backlog items.

If you do want the existing backlog, use:

```bash
python3 main.py run-once --include-existing
```

## Output

Summaries are written to:

`/Users/samuelnam/Desktop/code/youtube-video-digest/data/summaries/`

Each summary is saved as a markdown file named with the YouTube video ID.

Test summaries are saved with a `-test.md` suffix and do not update the seen-video state.

Fetched transcripts are also written to:

`/Users/samuelnam/Desktop/code/youtube-video-digest/data/transcripts/`

Each transcript is saved as a text file named with the YouTube video ID. Test transcripts use a `-test.txt` suffix.

## Notes

- Transcript retrieval depends on whether subtitles are available for the video.
- The app tries to summarize in the video's original language using YouTube metadata first, then falls back to inferring from the transcript, title, and description.
- If no transcript is available, the app skips transcript saving, summary generation, and Telegram delivery for that video.
- Desktop notifications currently use macOS Notification Center.
- If both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` are set, completed summaries are also sent to Telegram.
