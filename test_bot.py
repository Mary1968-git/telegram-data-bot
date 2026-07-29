#!/usr/bin/env python3
"""
Local acceptance test for your deployed TDS bot — ticks most of the guide's
§8 checklist in one command. It does NOT need Telegram: it (1) checks your
host's /health and /run.jsonl are public, and (2) optionally sends a real
Telegram message *as your bot to yourself is impossible*, so for the true
end-to-end test you still DM the bot from your own account. This script
verifies the HOST side + reply SHAPE using the same JSON rules the grader uses.

Usage:
    python3 test_bot.py https://your-host.onrender.com
    # then follow the printed instructions to do the one manual Telegram check
"""
import sys, json, subprocess, urllib.request, urllib.error

def check(label, ok, detail=""):
    mark = "✅" if ok else "❌"
    print(f"{mark} {label}" + (f"  — {detail}" if detail else ""))
    return ok

def http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": "bot-tester"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read().decode("utf-8", "replace")

def main():
    if len(sys.argv) < 2:
        print("usage: python3 test_bot.py https://your-host"); sys.exit(1)
    host = sys.argv[1].rstrip("/")
    all_ok = True

    # 1. /health public and returns ok
    try:
        st, body = http_get(host + "/health")
        j = json.loads(body)
        all_ok &= check("/health reachable & JSON ok:true", st == 200 and j.get("ok") is True, body.strip()[:80])
    except Exception as e:
        all_ok &= check("/health reachable", False, str(e))

    # 2. /run.jsonl publicly wget-able + valid JSONL (each line parses)
    try:
        st, body = http_get(host + "/run.jsonl")
        lines = [l for l in body.splitlines() if l.strip()]
        bad = 0
        for l in lines:
            try: json.loads(l)
            except Exception: bad += 1
        all_ok &= check("/run.jsonl public & wget-able", st == 200)
        all_ok &= check("/run.jsonl is valid JSONL (one JSON/line)", bad == 0,
                        f"{len(lines)} lines, {bad} malformed")
    except Exception as e:
        all_ok &= check("/run.jsonl public", False, str(e))

    # 3. real wget check (guide uses wget)
    try:
        rc = subprocess.run(["wget", "-q", "-O", "/dev/null", host + "/run.jsonl"],
                            timeout=30).returncode
        all_ok &= check("wget run.jsonl succeeds", rc == 0)
    except FileNotFoundError:
        print("ℹ️  wget not installed locally — the urllib check above already covered it.")
    except Exception as e:
        all_ok &= check("wget run.jsonl", False, str(e))

    print("\n--- Manual Telegram check (the real grader path) ---")
    print("From YOUR OWN Telegram account, DM your bot exactly:")
    print('  Which state has the highest maternal mortality rate based on MOSPI data? '
          'Reply with ONLY a JSON object like {"state": "<state name>"}')
    print("Expect ONE message back that is pure JSON, e.g.:")
    print('  {"answer": {"state": "Assam"}, "log_url": "%s/run.jsonl"}' % host)
    print("Then paste the reply below to validate its shape (or Ctrl-C to skip):")
    try:
        reply = input("bot reply> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n(skipped manual paste)")
        reply = ""
    if reply:
        try:
            obj = json.loads(reply)
            check("reply is exactly one JSON object", isinstance(obj, dict))
            check("has 'answer' key", "answer" in obj)
            check("has 'log_url' key (public URL)", isinstance(obj.get("log_url"), str) and obj["log_url"].startswith("http"))
            check("no prose around JSON (parsed cleanly)", True)
            print("   parsed answer:", json.dumps(obj.get("answer")))
        except json.JSONDecodeError as e:
            check("reply parses as one JSON object", False, f"format_error: {e}")

    print("\nSummary:", "ALL HOST CHECKS PASSED ✅" if all_ok else "SOME CHECKS FAILED ❌")
    print("Still verify manually: multi-turn (send setup then data), and reply < 300s on a hard question.")

if __name__ == "__main__":
    main()
