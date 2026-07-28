# Data-Analyst Telegram Bot (TDS Project 1)

An LLM agent that answers data-analysis questions sent over Telegram and
replies with a single JSON object `{"answer": ..., "log_url": ...}`.

## How it works
- `bot.py` runs long-polling against the Telegram Bot API.
- On each message it runs an LLM agent loop (OpenAI chat-completions with
  tool-calling): tools are `fetch_url` (HTTP GET) and `run_python` (execute
  Python for parsing/pandas/computation).
- The agent emits `FINAL_ANSWER: <json>`; the bot wraps it as
  `{"answer": <that>, "log_url": <public JSONL trace>}` and replies.
- The full run trace is written as JSONL under `logs/<run>.jsonl` and served
  publicly by an embedded Flask server at `/logs/<run>.jsonl`.

## Environment
- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `OPENAI_API_KEY` — LLM backend
- `PUBLIC_BASE_URL` — your deployed base URL (for building log_url)
- `LLM_MODEL` — optional (default gpt-4o-mini)
- `PORT` — provided by host

## Deploy (Render / Railway / Fly / any always-on host)
- Build: `pip install -r requirements.txt`
- Start: `python bot.py`
- Set the env vars above. Ensure the service stays reachable during grading.
