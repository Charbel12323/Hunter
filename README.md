# Hunter

Polls a watchlist of job sources (ATS job boards and curated GitHub repos), detects postings never seen before, filters them, and pushes each new match to Telegram.
Runs on a GitHub Actions cron schedule; every run is stateless and persists its dedup state to `seen_jobs.json`.

## How it works

```
sources.yaml -> FETCH -> NORMALIZE -> DEDUP -> FILTER -> NOTIFY (Telegram)
                (adapters)  (Job)   (seen_jobs.json) (predicates)
```

Each run fetches everything currently posted, subtracts everything already in `seen_jobs.json`, and notifies only the remainder.

## Setup

### 1. Create a Telegram bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts (pick a name and a unique username ending in `bot`).
3. BotFather replies with an HTTP API token that looks like `123456789:AAE...xyz`.
   This is your `TELEGRAM_BOT_TOKEN`. Treat it like a password.

### 2. Get your chat ID

1. Send any message (for example `hi`) to your new bot so a chat exists.
2. Open `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser.
3. Find `"chat":{"id":...}` in the JSON response.
   That number is your `TELEGRAM_CHAT_ID` (for a group chat it is negative).

### 3. Set the GitHub Secrets

In the repo: Settings -> Secrets and variables -> Actions -> New repository secret.

| Secret | Value |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | the token from BotFather |
| `TELEGRAM_CHAT_ID` | the chat id from `getUpdates` |

Secrets are injected into runs as environment variables.
Never put them in `sources.yaml` or any other tracked file.

### 4. Add sources

Edit `sources.yaml`.
Adding a company on a supported ATS is one entry, for example:

```yaml
sources:
  - type: ashby
    company: wealthsimple
```

## Running locally

```bash
pip install -r requirements.txt
python -m scraper.main --dry-run
```

`--dry-run` prints would-be notifications to stdout instead of sending to Telegram; everything before the notify stage behaves exactly like a real run.

## Development

```bash
ruff check .   # lint
pytest         # tests run fully offline (HTTP is mocked)
```
