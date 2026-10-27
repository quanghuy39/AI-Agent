import os

import httpx
from dotenv import load_dotenv

load_dotenv()


def get(url, key_env, header):
    key = os.getenv(key_env)
    if not key:
        print(f"  Thiếu {key_env}")
        return None
    r = httpx.get(url, headers={header: key}, timeout=30)
    if r.status_code != 200:
        print(f"  Lỗi {r.status_code}: {r.text[:200]}")
        return None
    return r.json()


print("=== GROQ ===")
d = get("https://api.groq.com/openai/v1/models", "GROQ_API_KEY", "Authorization")
# Groq cần "Bearer " phía trước khóa
if d is None:
    key = os.getenv("GROQ_API_KEY", "")
    r = httpx.get(
        "https://api.groq.com/openai/v1/models",
        headers={"Authorization": f"Bearer {key}"},
        timeout=30,
    )
    d = r.json() if r.status_code == 200 else None
    if d is None:
        print(f"  Lỗi {r.status_code}")
if d:
    for m in sorted(x["id"] for x in d["data"]):
        print("  ", m)

print("=== GEMINI (chỉ dòng có 'flash') ===")
d = get(
    "https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
    "GEMINI_API_KEY",
    "x-goog-api-key",
)
if d:
    for m in sorted(x["name"] for x in d.get("models", [])):
        if "flash" in m.lower():
            print("  ", m)

print("=== OPENROUTER (chỉ model :free) ===")
r = httpx.get("https://openrouter.ai/api/v1/models", timeout=30)
if r.status_code == 200:
    for m in sorted(x["id"] for x in r.json()["data"]):
        if m.endswith(":free"):
            print("  ", m)
else:
    print(f"  Lỗi {r.status_code}")

print("=== MISTRAL ===")
key = os.getenv("MISTRAL_API_KEY", "")
r = httpx.get(
    "https://api.mistral.ai/v1/models",
    headers={"Authorization": f"Bearer {key}"},
    timeout=30,
)
if r.status_code == 200:
    for m in sorted(x["id"] for x in r.json()["data"]):
        print("  ", m)
else:
    print(f"  Lỗi {r.status_code}: {r.text[:200]}")