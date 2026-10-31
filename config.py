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
# Doi chieu data dictionary: dinh nghia chua mot trong cac tu nay (khong phan biet hoa thuong)
# -> cot thieu trong du lieu duoc ghi chu derived_hint (cot phai tu tinh).
DERIVED_HINTS = ["derived", "not present", "calculated", "computed"]
# Co theo nguong: cot co ty le null >= nguong nay la "flag", duoi nguong (nhung > 0) la "info".
NULL_RATIO_FLAG = 0.05
# So dong thay doi qua ty le nay giua hai lan doc (dung o muc validators).
ROW_COUNT_CHANGE_FLAG = 0.10

# ---------- Validators (Ngay 4 muc 4) ----------
# join_check: co khi ty le dong ben trai khong khop (khong tinh khoa null) > nguong nay.
JOIN_UNMATCHED_FLAG_RATIO = 0.0
# sum_consistency: sai so cho phep (math.isclose).
SUM_REL_TOL = 1e-6
SUM_ABS_TOL = 1e-9
# range_check: co khi ty le gia tri ngoai khoang > nguong nay.
RANGE_OUT_FLAG_RATIO = 0.0
# Do quan he: cot con co it hon ty le nay gia tri xuat hien ben cha -> loai (ten trung nhung khong giao nhau).
RELATIONSHIP_MIN_OVERLAP = 0.05

# ---------- Tom tat du lieu cho AI (Ngay 4 muc 5) ----------
SUMMARY_MAX_CHARS = 8000            # ngan sach ky tu cua ban tom tat
SUMMARY_SAMPLE_N = 3                # so ban ghi mau moi bang
SUMMARY_CELL_MAX_CHARS = 30         # moi o (ban ghi mau, top values) cat con chung nay ky tu
SUMMARY_DEFINITION_MAX_CHARS = 120  # dinh nghia dictionary cat con chung nay ky tu
# Cot string/integer co ty le gia tri giong email/so dien thoai >= nguong nay -> tu dong che (kem canh bao).
SENSITIVE_AUTO_RATIO = 0.30

# ---------- Runner: chay code do AI sinh (Ngay 5) ----------
RUNNER_TIMEOUT = 120                 # giay
RUNNER_MAX_OUTPUT_CHARS = 20_000     # stdout/stderr dai hon thi giu dau + cuoi
RUNNER_META_MAX_BYTES = 200_000      # file meta (AGENT_META_PATH) lon hon thi bo, bao meta_error

# ---------- Vong lap phan tich (Ngay 5) ----------
# Ho so cua --fake nam o output/_fake/<bai>/, tach khoi ho so that. Thu muc nay KHONG duoc dong bo len R2
# (ham sync lam o muc 5 phai bo qua no).
FAKE_OUTPUT_SUBDIR = "_fake"
CAUTION_DEFAULT = "chuan"            # muc than trong duy nhat dang ho tro
SYNC_MANIFEST_DIR = "sync_manifest"  # trong cache/: manifest dong bo ho so len R2, moi bai mot file
ANALYSIS_MAX_RETRIES = 3             # so lan goi lai AI cho moi cau hoi sau lan dau
ANALYSIS_UNDERSTAND_MAX_RETRIES = 3  # so lan goi lai AI dien giai de sau lan dau, khi nguoi dung gop y
ANALYSIS_PLAN_MAX_RETRIES = 3        # so lan lap lai ke hoach sau lan dau, khi nguoi dung gop y
ANALYSIS_FEEDBACK_STDERR_CHARS = 1500  # error_feedback chi giu chung nay ky tu cuoi cua stderr
ANALYSIS_MASK_MIN_CHARS = 4          # gia tri cot che ngan hon thi khong thay (tranh thay nham)
# Bao ve do dai prompt buoc giai: token ~ ky tu / PROMPT_CHARS_PER_TOKEN + PROMPT_SCHEMA_TOKENS (chi dan schema
# ask_model gan them), so voi max_input_tokens nho nhat cua chuoi analysis, chua bien PROMPT_MARGIN.
PROMPT_CHARS_PER_TOKEN = 3.4
PROMPT_SCHEMA_TOKENS = 700
PROMPT_MARGIN = 0.10
ANALYSIS_FEEDBACK_SHORT_CHARS = 600  # khi phai cat: error_feedback chi giu chung nay ky tu cuoi
ANALYSIS_DECISION_SHORT_CHARS = 100  # khi phai cat: moi decision chi giu text ngan chung nay ky tu
# context_builder: tran cua cac ban rut gon va bac cat cuoi (data summary)
CONTEXT_SUMMARY_MAX_CHARS = (6000, 4500, 3000, 2000)  # bac cat data summary, gioi han ky tu giam dan
CONTEXT_CORRECTIONS_MAX_CHARS = 1500  # tran user_corrections dua vao prompt, giu chinh sua moi nhat
CONTEXT_CORRECTIONS_SHORT_CHARS = 400  # nhu tren, khi phai cat
CONTEXT_PREVIOUS_MAX_RESULTS = 8     # khi phai cat: so ket qua moi nhat (cong ket qua cau hien tai phu thuoc)
CONTEXT_PREVIOUS_MAX_FILES = 20      # khi phai cat: so file moi nhat trong work/
CONTEXT_DECISIONS_MAX = 15           # khi phai cat: so decision giu lai
# Tong hop (Ngay 7)
REPORT_SYNTH_MAX_RETRIES = 2         # so lan gui lai AI kem danh sach loi, sau lan dau
REPORT_FEEDBACK_MAX_ERRORS = 20      # so loi toi da trong moi lan gui lai
REPORT_TABLE_MAX_COLUMNS = 40        # so ten cot toi da cua moi bang ket qua dua vao prompt
REPORT_TABLE_SHOW_COLUMNS = 6        # so cot toi da AI chon hien thi cho mot bang
REPORT_SUBDIR = "report"             # trong output/<bai>/: bieu do, Word, ho so, slides_outline
REPORT_FONT = "Times New Roman"
# Word (Ngay 7)
REPORT_BODY_PT = 12
REPORT_TABLE_PT = 11
REPORT_LABEL_PT = 9                  # nhan kiem chung, ghi chu duoi bang
REPORT_MARGIN_CM = 2.5
REPORT_PICTURE_CM = 16               # chieu rong anh = kho A4 tru hai le
REPORT_HEADER_FILL = "D9E2F3"        # mau nen dong tieu de bang
REPORT_TABLE_MAX_ROWS = 15
REPORT_SUMMARY_MAX_CHARS = 1500      # khoang nua trang
# Cot so co tu nay trong ten la ma, khong phai so do: giu nguyen, khong ngan nghin (2024 khong thanh 2.024)
REPORT_PLAIN_NUMBER_HINTS = ("year", "nam", "id", "code", "ma", "key")
REPORT_CODE_COLUMN_HINTS = ("id", "code", "ma", "key")  # cot ma; di cung cot ten thi bieu do dung cot ten
# Cot thoi gian: khong xep lai theo gia tri, thu tu trong file da co nghia
REPORT_TIME_COLUMN_HINTS = ("date", "time", "year", "month", "quarter", "week", "period", "ngay", "thang", "nam", "quy", "tuan", "ky")
# Bieu do (Ngay 7)
REPORT_CHART_FONT_FALLBACK = "DejaVu Sans"  # khi may khong co REPORT_FONT (vd. container); co du dau tieng Viet
REPORT_CHART_DPI = 200
REPORT_CHART_SIZE = (10, 5.625)      # inch, ty le 16:9
REPORT_CHART_MAX_Y = 4               # so cot so toi da cua mot bieu do
REPORT_AUTO_CHART_MAX_ROWS = 20      # bieu do du phong chi ve bang khong qua so dong nay
REPORT_CHART_MAX_GROUPS = 20         # nhieu hon so nhom nay thi chi ve REPORT_CHART_TOP_GROUPS nhom lon nhat
REPORT_CHART_TOP_GROUPS = 15
REPORT_CHART_LABEL_MAX_BARS = 15     # bar: nhieu cot hon thi bo nhan gia tri; line: so nhan toi da tren truc x
REPORT_CHART_VERTICAL_MAX_GROUPS = 10   # nhieu nhom hon thi ve cot ngang
REPORT_CHART_VERTICAL_MAX_CHARS = 12    # nhan nhom dai hon thi ve cot ngang
# Bang mau theo thu tu co dinh, 8 mau; dai thu 9 tro di gop vao "Khác"
REPORT_CHART_PALETTE = ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948")
REPORT_MAX_DECIMALS = 2              # so chu so thap phan toi da khi dien so co |x| >= 1 vao van ban
REPORT_SIG_DIGITS = 3                # so chu so co nghia khi dien so co 0 < |x| < 1 (ty le nho khong bi lam tron mat)
REPORT_PERCENT_FIELD_HINTS = ("pct", "percent")  # ten truong chua mot tu nay moi duoc viet % phia sau
# Cot che mac dinh theo bai (cong voi (SENSITIVE) trong dictionary va --sensitive); chi ap dung khi cot co that.
ANALYSIS_DEFAULT_SENSITIVE = {"bai1": ["CUSTOMER.customer_id"]}
# Module goc (phan truoc dau cham dau tien) bi cam import trong code AI.
RUNNER_FORBIDDEN_IMPORTS = ["boto3", "botocore", "storage", "llm", "litellm", "requests", "httpx", "urllib",
                            "socket", "subprocess"]
# Regex ap len moi hang chuoi trong code (ast): duong dan tro toi answers, cache, thu muc cha, .env.
# Theo thanh phan duong dan nen "lru_cache", bien "cached" hay "Loading..." khong bi chan.
RUNNER_FORBIDDEN_PATTERNS = [
    r"(?i)(?:^|[\\/])answers(?:[\\/]|$)",
    r"(?i)(?:^|[\\/])cache[\\/]",
    r"(?:^|[\\/])\.\.(?:[\\/]|$)",
    r"(?i)(?:^|[\\/])\.env(?:$|[\\/.])",
]


def get_r2_settings():
    """Tra ve dict cau hinh R2; bao loi ro neu thieu bien."""
    return {name: get_env(name) for name in R2_VARS}


def report_author():
    """REPORT_AUTHOR trong .env, tuy chon (khong co trong danh sach kiem tra o duoi): khong co thi bia khong in dong nay."""
    return (os.getenv("REPORT_AUTHOR") or "").strip()


def get_llm_key(name):
    """Lay khoa API cua mot hang LLM (dung o Ngay 2)."""
    return get_env(name)


if __name__ == "__main__":
    # Chay: python config.py -> chi in OK/THIEU, khong in gia tri bi mat
    for name in R2_VARS + LLM_KEY_VARS + OTHER_VARS:
        ok = bool((os.getenv(name) or "").strip())
        print(f"{name}: {'OK' if ok else 'THIẾU'}")