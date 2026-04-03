# Changelog

This file records project changes with enough detail to answer:

- Reason: why the change was made
- Logic: how the change was implemented

## Entry Format

### YYYY-MM-DD - Short Title
- Commit: `commit_sha`
- Reason:
  Short explanation of the problem, risk, or motivation.
- Logic:
  Concrete explanation of what code or behavior changed.

## 2026-04-04 - Standardize Published Time Display To KST
- Commit: `unreleased`
- Reason:
  Published timestamps were shown in raw YouTube UTC form in summaries, transcripts, Telegram messages, and failure alerts, which made the output inconsistent with the user's Korea-based workflow.
- Logic:
  Added a shared `DigestApp` formatter that converts user-facing `published_at` values from YouTube's ISO timestamp into `YYYY-MM-DD HH:MM:SS KST`, then routed summary files, transcript files, Telegram summary messages, and per-video failure alerts through that helper while keeping the raw UTC timestamp untouched for sorting and first-run window logic.

## 2026-04-04 - Strengthen Prompt Example Language Rule
- Commit: `unreleased`
- Reason:
  The tracked prompt example needed a stricter transcript-language instruction so the default template more clearly tells users and the model to keep summaries in the same language as the source transcript.
- Logic:
  Replaced the language instruction in `prompt.example.md` with the exact stronger wording requested by the user while preserving the rest of the structured summary template.

## 2026-04-04 - Refine Prompt Example Template
- Commit: `unreleased`
- Reason:
  The tracked example prompt needed to better reflect the desired structure, tone, and evidence-preservation rules for summaries, while still remaining an example users can copy into their local `prompt.md`.
- Logic:
  Replaced the contents of `prompt.example.md` with a more explicit template covering transcript-language output, preservation of numbers and comparisons, structured detailed-summary rules, and the additional title-driven conclusion task.

## 2026-04-04 - Implement Summary Language Mode
- Commit: `unreleased`
- Reason:
  `SUMMARY_LANGUAGE` existed in local configuration but had no effect, which made output-language debugging confusing.
- Logic:
  Added `SUMMARY_LANGUAGE_MODE` with `transcript` as the default and `fixed` as an explicit forced-language mode, injected that behavior into prompt rendering, and documented the setting in `.env.example` and `README.md`.

## 2026-04-04 - Add Per-Video Failure Telegram Alerts
- Commit: `unreleased`
- Reason:
  Per-video transcript and summary failures were only visible in local logs, which made it easy to miss broken videos when the daemon was running unattended.
- Logic:
  Added a reusable per-video failure notification helper and used it for transcript-fetch failures and summary-generation failures in both normal runs and test runs. The alerts include the stage, title, channel, URL, and failure reason, while still avoiding recursive alerting for Telegram delivery failures themselves.

## 2026-04-04 - Split Prompt Template Into Example And Local File
- Commit: `unreleased`
- Reason:
  The tracked `prompt.md` file looked like a real runtime prompt, which made it too easy to commit personal prompt instructions back to Git instead of treating them as local configuration.
- Logic:
  Switched the repository convention to track `prompt.example.md` as the checked-in example, ignore local `prompt.md` in `.gitignore`, updated the runtime error message to instruct users to create `prompt.md` from the example, and updated the README setup steps to make that workflow explicit.

## 2026-04-04 - Fix Review Issues And Harden Notifications
- Commit: `dcd414f`
- Reason:
  A follow-up code review identified several legitimate issues: `requests` was only relied on transitively, daemon mode could swallow `SystemExit`, processed video counts were misleading, channel ID parsing was too loose, notification/error handling could expose edge-case risks, and dotenv parsing could strip unmatched quotes incorrectly.
- Logic:
  Added `requests` to `requirements.txt`, made daemon mode re-raise `SystemExit`, counted only successfully summarized videos in the processed summary, tightened plain channel ID parsing to a real YouTube channel ID pattern, escaped additional characters before passing notification text to AppleScript, redacted the Telegram bot token from logged error messages, preserved unmatched quotes in `.env` parsing, replaced the transcript truncation magic number with `MAX_TRANSCRIPT_CHARS`, removed the dead `subscribed_channel_ids()` path, and changed YouTube API retry handling to catch `googleapiclient.errors.HttpError` directly. Added tests for notification escaping, token redaction, dotenv parsing, and invalid channel ID parsing.

## 2026-04-04 - Make README Paths Portable
- Commit: `b8eab9c`
- Reason:
  The README used machine-specific absolute paths under `/Users/samuelnam/...`, which would confuse anyone running the project on a different machine or under a different username.
- Logic:
  Replaced those absolute path examples with portable relative paths like `./credentials.json` and `./data/...`, and changed command examples to use a generic `cd /path/to/youtube-video-digest`.

## 2026-04-04 - Add Daemon Heartbeat Logging
- Commit: `4ac03da`
- Reason:
  It was hard to tell whether daemon mode was still alive, sleeping normally, or silently stopped after a run.
- Logic:
  Added loop-level heartbeat logging in `daemon()` to print the local start time, finish time, sleep duration, and next scheduled run time for each cycle. Added a small timestamp-formatting test.

## 2026-04-04 - Harden Transcript Fetching Behavior
- Commit: `fe7707c`
- Reason:
  Transcript fetching needed to behave less like a bot, reduce repeated fetches for the same videos, and pause more gracefully when YouTube signals blocking or rate limiting.
- Logic:
  Injected a custom `requests.Session` into `youtube-transcript-api` with a browser-like user agent and optional cookie header, added randomized delay windows before transcript requests, reused cached transcript files instead of re-fetching when available, and persisted a pause-until timestamp in state when transcript requests appear rate-limited or blocked. Added config knobs in `.env.example` and documentation in `README.md`.

## 2026-04-04 - Externalize And Save Summary Prompts
- Commit: `3124283`
- Reason:
  The summary prompt was embedded directly in code, which made it harder to edit, inspect, and reproduce the exact prompt used for a given generated summary.
- Logic:
  Moved the prompt template into `prompt.md`, loaded it from disk in `GeminiSummarizer`, and saved the fully rendered prompt for each summary to `data/prompts/` alongside summaries and transcripts. Added tests for prompt rendering and updated README documentation.

## 2026-04-04 - Track Failed Transcript Retries Separately
- Commit: `b37ed5c`
- Reason:
  Videos with transcript-fetch failures were being treated as fully processed, which prevented later retries even when the failure might have been temporary.
- Logic:
  Added `failed_videos` state with retry count, last attempt time, and last error; introduced retry limits and cooldown behavior; retried eligible failures later instead of marking them seen immediately; and documented/configured the behavior via `.env.example` and README. Added dedicated state tests.

## 2026-04-04 - Fix Transcript API Compatibility
- Commit: `56e2038`
- Reason:
  The installed `youtube-transcript-api` version used the newer instance-based API (`fetch()` and `list()`), while the app still called the older class-style methods, causing false "no transcript" behavior.
- Logic:
  Updated `TranscriptFetcher` to instantiate `YouTubeTranscriptApi()`, use `fetch()` and `list()`, normalize the newer return objects into the internal dict format, and add regression tests for the current API shape and fallback behavior.

## 2026-04-03 - Add Watched Channel Allowlist And Retry Handling
- Commit: `d5574b4`
- Reason:
  Watching every subscribed channel was too noisy, and transient YouTube API backend cancellations were causing runs to fail prematurely.
- Logic:
  Added `watched_channels.txt` as an explicit allowlist, resolved watched channels from supported YouTube URL forms and handles, switched run/test flows to use that watchlist instead of subscriptions, updated the README, and added retry/backoff handling around YouTube Data API requests to survive transient backend errors.
