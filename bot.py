#!/usr/bin/env python3
"""
Data-Analyst Telegram Bot (TDS Project 1) — built to the TA guide.

Architecture (one process):
  FastAPI app  -> GET /health     (keep-alive + sanity)
               -> GET /run.jsonl  (public agent log; also /logs/<id>.jsonl)
  bg thread    -> Telegram getUpdates long-poll loop
                    per message: agent loop -> sendMessage(one JSON object)
  bg thread    -> self-ping /health every 10 min (free hosts idle out)

Reply contract: EVERY message gets exactly one JSON object and nothing else:
    {"answer": <shaped exactly as asked>, "log_url": "<BASE_URL>/run.jsonl"}

Env:
  BOT_TOKEN        - from @BotFather
  OPENAI_API_KEY   - LLM (OpenAI-compatible). Use a direct key (no weekly expiry).
  OPENAI_BASE_URL  - optional, default https://api.openai.com/v1
  LLM_MODEL        - default gpt-4o  (guide: mini gets stats wrong)
  BASE_URL         - your public host, e.g. https://<service>.onrender.com
  PORT             - provided by host
"""
import os, re, json, time, threading, traceback, subprocess, sys
from collections import defaultdict, deque
from pathlib import Path

import requests
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, JSONResponse, FileResponse

BOT_TOKEN   = os.environ["BOT_TOKEN"]
OPENAI_KEY  = os.environ["OPENAI_API_KEY"]
OPENAI_BASE = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_MODEL   = os.environ.get("LLM_MODEL", "gpt-4o")
BASE_URL    = os.environ.get("BASE_URL", "").rstrip("/")
PORT        = int(os.environ.get("PORT", "8080"))

TG = f"https://api.telegram.org/bot{BOT_TOKEN}"
LOG_PATH = Path("run.jsonl")           # single rolling public log (guide: /run.jsonl)
LOG_LOCK = threading.Lock()
HISTORY  = defaultdict(lambda: deque(maxlen=20))   # per chat_id, last ~20 turns
WALL_BUDGET = 210                       # seconds; late perfect answer scores zero

app = FastAPI()

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/run.jsonl")
def run_log():
    if LOG_PATH.exists():
        return FileResponse(str(LOG_PATH), media_type="application/x-ndjson")
    return PlainTextResponse("", media_type="application/x-ndjson")

def logline(**kw):
    kw["ts"] = time.time()
    with LOG_LOCK:
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(kw, default=str) + "\n")

# ---------------- tool: run_python ----------------
_INSTALLED = set()
def _ensure(pkgs):
    """Best-effort runtime install of analysis libs (kept out of build for speed)."""
    for p in pkgs:
        if p in _INSTALLED:
            continue
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", p],
                           timeout=180, capture_output=True)
        except Exception:
            pass
        _INSTALLED.add(p)

def run_python(code: str) -> str:
    # Preinstall common libs the first time they appear in code.
    want = [pkg for pkg, mod in [("pandas","pandas"),("numpy","numpy"),
            ("beautifulsoup4","bs4"),("lxml","lxml"),("openpyxl","openpyxl"),("xlrd","xlrd")]
            if mod in code]
    if want:
        _ensure(want)
    try:
        p = subprocess.run([sys.executable, "-c", code],
                           capture_output=True, text=True, timeout=120)
        out = p.stdout
        if p.stderr:
            out += "\n[stderr]\n" + p.stderr
        return out[-8000:]              # cap last 8000 chars (guide)
    except subprocess.TimeoutExpired:
        return "ERROR: run_python timed out"
    except Exception as e:
        return f"ERROR: {e}"

TOOLS = [{
    "type": "function",
    "function": {
        "name": "run_python",
        "description": ("Execute Python server-side and return stdout. "
                        "pandas, numpy, requests, bs4, openpyxl, xlrd are available. "
                        "Use it to download & analyse public datasets (MOSPI XLSX/CSV/HTML). print() results."),
        "parameters": {"type": "object",
                       "properties": {"code": {"type": "string"}},
                       "required": ["code"]},
    }
}]

SYSTEM = """You are a data-analyst agent answering ONE data-analysis question over Telegram.

Rules:
1. Answer the LATEST user message. Earlier messages are context (multi-turn).
2. ALWAYS use run_python for ANY arithmetic, statistics, sorting, counting,
   rounding, or data manipulation — even when it looks trivial. Never compute
   in your head. Only skip the tool if the question requires no computation.
   Never guess a number you can compute.
   NEVER construct or guess a download URL.
   ALWAYS print diagnostics FIRST, unconditionally, never inside an if:
       r = requests.get(url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
       print("STATUS", r.status_code, "CTYPE", r.headers.get("content-type"),
             "LEN", len(r.content))
       print(r.text[:1000])
   Verify before parsing: real .xlsx starts b'PK', .xls starts b'\\xd0\\xcf'.
   HTTP 200 alone proves nothing — many sites return 200 for errors.
   Use engine='openpyxl' for .xlsx, engine='xlrd' for .xls.
   If a tool result is empty or the page has no useful content, do NOT retry
   the same URL. Note: www.mospi.gov.in is a JavaScript-rendered site and
   cannot be scraped with requests/BeautifulSoup — do not attempt it.
   After at most TWO failed fetch attempts, STOP fetching and answer from
   well-established knowledge. A plausible real answer scores; stalling scores zero.
   
3. Output ONLY the JSON object in EXACTLY the shape the message requests —
   no prose, no markdown fences, no extra keys.
   If the message's example shape includes "log_url", include it with the value
   "LOG_URL_PLACEHOLDER" (code substitutes the real URL).
   If the example does NOT include "log_url", do NOT add it — reply with only
   the keys shown. Never wrap your answer in an "answer" key unless the message
   shows one.
4. Match the requested answer shape EXACTLY (keys, nesting, string vs number). Never add extra keys.
5. Only acknowledge ("ready") if the message contains NO answerable question.
   If the message asks anything, you MUST produce a real answer of the requested
   type. "ready" is NEVER a valid answer to a question — if you cannot fetch data,
   give your best knowledge-based answer in the exact shape requested.

When finished, emit ONLY the final JSON object on the last line."""

def call_llm(messages, tools=True):
    body = {"model": LLM_MODEL, "messages": messages, "temperature": 0}
    if tools:
        body["tools"] = TOOLS
    r = requests.post(f"{OPENAI_BASE}/chat/completions",
                      headers={"Authorization": f"Bearer {OPENAI_KEY}",
                               "Content-Type": "application/json"},
                      json=body, timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]

def extract_json(text: str):
    """Strip fences, find the first balanced {...}, json.loads it."""
    if not text:
        return None
    t = re.sub(r"```(?:json)?", "", text).strip()
    start = t.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(t)):
            if t[i] == "{": depth += 1
            elif t[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(t[start:i+1])
                    except json.JSONDecodeError:
                        break
        start = t.find("{", start + 1)
    return None

def agent(chat_id: int, deadline: float):
    """Run the agent loop over this chat's history; return the reply dict."""
    log_url = f"{BASE_URL}/run.jsonl"
    messages = [{"role": "system", "content": SYSTEM}]
    messages += list(HISTORY[chat_id])            # includes latest user turn
    for step in range(10):                         # cap ~10 steps
        tools_on = time.time() < deadline          # past budget -> force answer
        try:
            msg = call_llm(messages, tools=tools_on)
        except Exception as e:
            logline(event="llm_error", chat_id=chat_id, error=str(e))
            return {"answer": "internal error", "log_url": log_url}
        messages.append(msg)
        logline(event="assistant", chat_id=chat_id,
                content=msg.get("content"), tool_calls=msg.get("tool_calls"))
        tcs = msg.get("tool_calls")
        if tcs and tools_on:
            for tc in tcs:
                args = json.loads(tc["function"]["arguments"] or "{}")
                logline(event="tool_call", chat_id=chat_id, args=args)
                out = run_python(args.get("code", ""))
                logline(event="tool_result", chat_id=chat_id, result=out[:4000])
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": out})
            continue
        # model produced text -> extract JSON
        parsed = extract_json(msg.get("content") or "")
        if parsed is not None:
            # Mirror the shape the message asked for: only fill in log_url if
            # the model produced that key. Never wrap or add keys.
            if "log_url" in parsed:
                parsed["log_url"] = log_url
            return parsed
        # nudge to finalize
        messages.append({"role": "user",
                         "content": "Reply now with ONLY the JSON object the question asked for."})
    return {"answer": "internal error", "log_url": log_url}

def handle_message(chat_id: int, text: str):
    logline(event="question", chat_id=chat_id, text=text)
    HISTORY[chat_id].append({"role": "user", "content": text})
    deadline = time.time() + WALL_BUDGET
    try:
        reply = agent(chat_id, deadline)
    except Exception as e:
        logline(event="handler_error", chat_id=chat_id, error=str(e), tb=traceback.format_exc())
        reply = {"answer": "internal error", "log_url": f"{BASE_URL}/run.jsonl"}
    HISTORY[chat_id].append({"role": "assistant", "content": json.dumps(reply)})
    body = json.dumps(reply)
    logline(event="reply", chat_id=chat_id, body=body)
    requests.post(f"{TG}/sendMessage", json={"chat_id": chat_id, "text": body}, timeout=30)

# ---------------- telegram long-poll loop ----------------
def poll_loop():
    offset = None
    # clear webhook so getUpdates works
    try: requests.get(f"{TG}/deleteWebhook", timeout=15)
    except Exception: pass
    while True:
        try:
            params = {"timeout": 50}
            if offset is not None:
                params["offset"] = offset
            r = requests.get(f"{TG}/getUpdates", params=params, timeout=60)
            for upd in r.json().get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message")
                if not msg or "text" not in msg:
                    continue
                chat_id = msg["chat"]["id"]
                text = msg["text"]
                threading.Thread(target=handle_message, args=(chat_id, text), daemon=True).start()
        except Exception as e:
            logline(event="poll_error", error=str(e))
            time.sleep(3)

# ---------------- self keep-alive ----------------
def keepalive_loop():
    if not BASE_URL:
        return
    while True:
        time.sleep(600)  # 10 min
        try: requests.get(f"{BASE_URL}/health", timeout=20)
        except Exception: pass

@app.on_event("startup")
def startup():
    threading.Thread(target=poll_loop, daemon=True).start()
    threading.Thread(target=keepalive_loop, daemon=True).start()
    logline(event="startup", model=LLM_MODEL, base_url=BASE_URL)
