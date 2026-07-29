# Data-Analyst Telegram Bot (TDS Project 1)

LLM agent that answers data-analysis questions over Telegram and replies with
exactly one JSON object: `{"answer": ..., "log_url": ".../run.jsonl"}`.

## Architecture (one process)
- FastAPI: `GET /health` (keep-alive), `GET /run.jsonl` (public agent log)
- Background thread: Telegram getUpdates long-poll → per message → agent loop → sendMessage
- Background thread: self-ping `/health` every 10 min (free hosts idle out)

## Agent
- OpenAI-compatible chat API with function-calling; one tool `run_python(code)`
  runs server-side (pandas/numpy/requests/bs4/openpyxl) to fetch & analyse public
  datasets (MOSPI XLSX/CSV/HTML). Loop caps at ~10 steps; ~210 s wall budget.
- Replies to EVERY message (multi-turn); setup messages get a JSON ack.
- JSON extraction: strip fences, first balanced `{...}`, wrap if no `answer` key,
  always overwrite `log_url` with the real URL.

## Env
`BOT_TOKEN`, `OPENAI_API_KEY`, `LLM_MODEL` (default gpt-4o), `BASE_URL`, `PORT`.

## Deploy (Render free tier)
- Build: `pip install -r requirements.txt`
- Start: `uvicorn bot:app --host 0.0.0.0 --port $PORT`
- Env: `BOT_TOKEN`, `OPENAI_API_KEY`, `LLM_MODEL=gpt-4o`, `BASE_URL=https://<service>.onrender.com`
- Verify: `curl https://<host>/health` → `{"ok":true}`; `wget https://<host>/run.jsonl`
