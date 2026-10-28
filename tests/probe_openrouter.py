import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()
key = os.getenv("OPENROUTER_API_KEY", "")
models = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "nvidia/nemotron-3.5-lightning:free",
    "thinkingmachines/inkling-small:free",
    "cohere/north-mini-code:free",
]
for m in models:
    t0 = time.time()
    try:
        r = httpx.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={
                "model": m,
                "messages": [{"role": "user", "content": "Reply with exactly one word: hello"}],
                "max_tokens": 50,
            },
            timeout=60,
        )
    except Exception as e:
        print(f"--- {m}: LOI {type(e).__name__} sau {time.time() - t0:.1f}s")
        continue
    try:
        j = r.json()
    except Exception:
        j = {}
    err = j.get("error") if isinstance(j, dict) else None
    meta = err.get("metadata") if isinstance(err, dict) else None
    prov = (j.get("provider") if isinstance(j, dict) else None) or (
        meta.get("provider_name") if isinstance(meta, dict) else None
    )
    print(f"--- {m}: {r.status_code} ({time.time() - t0:.1f}s) provider={prov}")
    print(r.text[:300].replace("\n", " "))