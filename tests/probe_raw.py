import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()


def call(name, url, headers, body):
    t0 = time.time()
    try:
        r = httpx.post(url, headers=headers, json=body, timeout=60)
        print(f"--- {name}: {r.status_code} ({time.time() - t0:.1f}s)")
        print(r.text[:500].replace("\n", " "))
    except Exception as e:
        print(f"--- {name}: LOI {type(e).__name__} sau {time.time() - t0:.1f}s")


gkey = os.getenv("GEMINI_API_KEY", "")
for m in ["gemini-3.8-flash", "gemma-4-31b-it"]:
    call(
        "gemini/" + m,
        f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
        {"x-goog-api-key": gkey},
        {"contents": [{"parts": [{"text": "Reply with exactly one word: hello"}]}]},
    )

okey = os.getenv("OPENROUTER_API_KEY", "")
for m in ["nvidia/nemotron-3-super-120b-a12b:free", "google/gemma-4-31b-it:free"]:
    call(
        "openrouter/" + m,
        "https://openrouter.ai/api/v1/chat/completions",
        {"Authorization": f"Bearer {okey}"},
        {
            "model": m,
            "messages": [{"role": "user", "content": "Reply with exactly one word: hello"}],
            "max_tokens": 50,
        },
    )