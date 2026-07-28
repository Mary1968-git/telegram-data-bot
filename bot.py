#!/usr/bin/env python3
"""
Data-Analyst Telegram Bot (TDS Project 1).

Receives a plain-text data-analysis question via Telegram DM, runs an LLM
agent loop (with web-fetch + python-exec tools) to solve it, and replies with
exactly one JSON object:
    {"answer": <shaped as asked>, "log_url": "https://<host>/logs/<run>.jsonl"}

The agent's full trace is written as JSONL and served publicly so the grader
can wget it.

Env vars required:
    TELEGRAM_BOT_TOKEN   - from @BotFather
    OPENAI_API_KEY (or ANTHROPIC_API_KEY) - LLM backend
    PUBLIC_BASE_URL      - e.g. https://your-app.onrender.com  (for log_url)
    LLM_MODEL            - optional, default gpt-4o-mini or similar
"""
import os, json, re, io, time, uuid, traceback, contextlib, subprocess, sys
from pathlib import Path

import requests
from flask import Flask, send_from_directory
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes

LOG_DIR = Path(os.environ.get("LOG_DIR", "logs"))
LOG_DIR.mkdir(exist_ok=True)
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4o-mini")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY")

# ----- tiny web server to serve the JSONL logs publicly -----
web = Flask(__name__)
@web.route("/logs/<path:fn>")
def serve_log(fn):
    return send_from_directory(str(LOG_DIR), fn, mimetype="application/x-ndjson")
@web.route("/")
def home():
    return "data-analyst bot up"

# ----- run logger: one JSON object per line -----
class RunLog:
    def __init__(self):
        self.run_id = uuid.uuid4().hex[:12]
        self.path = LOG_DIR / f"{self.run_id}.jsonl"
        self.f = open(self.path, "w")
    def add(self, **kw):
        kw["ts"] = time.time()
        self.f.write(json.dumps(kw, default=str) + "\n"); self.f.flush()
    def url(self):
        return f"{PUBLIC_BASE_URL}/logs/{self.run_id}.jsonl"
    def close(self):
        self.f.close()

# ----- tools the agent can call -----
def tool_fetch(url):
    r = requests.get(url, timeout=45, headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    return r.text[:200000]

def tool_python(code):
    """Run python in a subprocess; return stdout (captures printed results)."""
    try:
        p = subprocess.run([sys.executable, "-c", code], capture_output=True,
                           text=True, timeout=90)
        return (p.stdout + ("\nSTDERR:\n"+p.stderr if p.stderr else ""))[:20000]
    except subprocess.TimeoutExpired:
        return "ERROR: python execution timed out"

TOOLS = [
    {"type":"function","function":{
        "name":"fetch_url","description":"HTTP GET a public URL, returns text (HTML/JSON/CSV).",
        "parameters":{"type":"object","properties":{"url":{"type":"string"}},"required":["url"]}}},
    {"type":"function","function":{
        "name":"run_python","description":"Execute Python code and return its stdout. Use for computation, parsing, pandas, etc. print() the result.",
        "parameters":{"type":"object","properties":{"code":{"type":"string"}},"required":["code"]}}},
]

SYSTEM = """You are a data-analyst agent. You receive ONE data-analysis question.
Work out the correct answer using the fetch_url and run_python tools when needed
(datasets are public: MOSPI and similar). Think step by step.

When done, output ONLY the final answer value shaped EXACTLY as the question asks,
as a JSON value, prefixed with the literal token FINAL_ANSWER: on its own.
Example: FINAL_ANSWER: {"state": "Assam"}
Do not add prose after FINAL_ANSWER. Match the requested keys/shape and types exactly."""

def call_llm(messages, log):
    hdr={"Authorization":f"Bearer {OPENAI_KEY}","Content-Type":"application/json"}
    body={"model":LLM_MODEL,"messages":messages,"tools":TOOLS,"temperature":0}
    r=requests.post("https://api.openai.com/v1/chat/completions",headers=hdr,json=body,timeout=120)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]

def solve(question, log):
    log.add(event="question", text=question)
    messages=[{"role":"system","content":SYSTEM},{"role":"user","content":question}]
    for step in range(12):
        msg=call_llm(messages, log)
        messages.append(msg)
        log.add(event="assistant", content=msg.get("content"), tool_calls=msg.get("tool_calls"))
        tcs=msg.get("tool_calls")
        if tcs:
            for tc in tcs:
                name=tc["function"]["name"]; args=json.loads(tc["function"]["arguments"])
                log.add(event="tool_call", name=name, args=args)
                try:
                    out = tool_fetch(args["url"]) if name=="fetch_url" else tool_python(args["code"])
                except Exception as e:
                    out=f"ERROR: {e}"
                log.add(event="tool_result", name=name, result=out[:5000])
                messages.append({"role":"tool","tool_call_id":tc["id"],"content":out})
            continue
        content=msg.get("content") or ""
        m=re.search(r"FINAL_ANSWER:\s*(.+)", content, re.DOTALL)
        if m:
            raw=m.group(1).strip()
            try: return json.loads(raw)
            except json.JSONDecodeError: return raw
        # nudge to finalize
        messages.append({"role":"user","content":"Output FINAL_ANSWER: <json> now."})
    return None

# ----- telegram handler -----
async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text or ""
    log = RunLog()
    try:
        answer = solve(text, log)
        reply = {"answer": answer, "log_url": log.url()}
    except Exception as e:
        log.add(event="error", error=str(e), tb=traceback.format_exc())
        reply = {"answer": None, "log_url": log.url()}
    finally:
        log.close()
    await update.message.reply_text(json.dumps(reply))

def main():
    import threading
    port=int(os.environ.get("PORT","8080"))
    threading.Thread(target=lambda: web.run(host="0.0.0.0",port=port),daemon=True).start()
    app=Application.builder().token(os.environ["TELEGRAM_BOT_TOKEN"]).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))
    app.run_polling()

if __name__=="__main__":
    main()
