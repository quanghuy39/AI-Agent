import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


class ConfigError(Exception):
    """Loi cau hinh: thieu hoac sai bien trong .env."""


def get_env(name, required=True, default=None):
    value = os.getenv(name, default)
    if required and (value is None or value.strip() == ""):
        raise ConfigError(
            f"Thiếu biến '{name}' trong file .env. "
            f"Hãy mở .env và điền giá trị (xem mẫu trong .env.example)."
        )
    return value


R2_VARS = [
    "R2_ENDPOINT",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
    "R2_INPUT_PREFIX",
    "R2_OUTPUT_PREFIX",
    "R2_WRITE_ACCESS_KEY_ID",
    "R2_WRITE_SECRET_ACCESS_KEY",
]

LLM_KEY_VARS = [
    "GROQ_API_KEY",
    "GEMINI_API_KEY",
    "OPENROUTER_API_KEY",
    "MISTRAL_API_KEY",
]
OTHER_VARS = ["DEFAULT_ENGINE", "CAUTION_LEVEL", "LLM_CONFIG_PATH"]


def get_r2_settings():
    """Tra ve dict cau hinh R2; bao loi ro neu thieu bien."""
    return {name: get_env(name) for name in R2_VARS}


def get_llm_key(name):
    """Lay khoa API cua mot hang LLM (dung o Ngay 2)."""
    return get_env(name)


if __name__ == "__main__":
    # Chay: python config.py -> chi in OK/THIEU, khong in gia tri bi mat
    for name in R2_VARS + LLM_KEY_VARS + OTHER_VARS:
        ok = bool((os.getenv(name) or "").strip())
        print(f"{name}: {'OK' if ok else 'THIẾU'}")