import os

import httpx
from dotenv import load_dotenv

load_dotenv()
key = os.getenv("MISTRAL_API_KEY", "")
models = [
    "codestral-2508",
    "mistral-small-latest",
    "mistral-medium-latest",
    "ministral-8b-2512",
    "ministral-3b-2512",
    "ministral-14b-2512",
    "mistral-large-2512",
]
for m in models:
    r = httpx.post(
        "https://api.mistral.ai/v1/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": m, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
        timeout=60,
    )
    limits = {k: v for k, v in r.headers.items() if "ratelimit" in k.lower()}
    print(m, r.status_code, limits)