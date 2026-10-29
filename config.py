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

# ---------- Profiling (Ngay 4) ----------
# Mot kieu duoc nhan khi ty le gia tri khong rong khop kieu do >= nguong nay.
TYPE_INFER_MIN_RATIO = 0.95
# Tu 2 dinh dang ngay tro len, moi cai chiem >= ty le nay -> co mixed_date_formats.
DATE_MIXED_MIN_RATIO = 0.05
# Thu tu co y nghia: hoa giua cac dinh dang dat nguong thi dinh dang dung truoc duoc ghi truoc.
# Dinh dang co %H -> kieu "datetime", chi co ngay -> "date".
DATE_FORMATS = [
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%d%m%Y", "%Y%m%d",
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d.%m.%Y",
]
# top_values: liet ke het neu so gia tri khac nhau <= TOP_VALUES_MAX_UNIQUE, nguoc lai lay TOP_VALUES_N.
TOP_VALUES_MAX_UNIQUE = 20
TOP_VALUES_N = 5
# Doan khoa khi khong truyen key_cols: ten cot chua mot TU trong danh sach (khong phan biet hoa thuong,
# tach tu theo _ - khoang trang va camelCase) va ty le gia tri duy nhat >= KEY_MIN_UNIQUE_RATIO.
# Thu tu trong danh sach la thu tu uu tien khi chon giua cac ung vien.
KEY_NAME_HINTS = ["id", "ma", "code", "key"]
KEY_MIN_UNIQUE_RATIO = 0.9
# Tom tat profiling: cot co cau truc chinh chiem >= 1 - ty le nay va con gia tri lech cau truc
# duoc in o dong "cau truc thieu so" (cot von da dang thi khong in).
MINORITY_PATTERN_RATIO = 0.05


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