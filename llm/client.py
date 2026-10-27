"""Goi LLM theo chuoi fallback cua tung role. Day la noi DUY NHAT trong du an import litellm.

Khong dung litellm.Router: vong lap ben duoi tu duyet chain, tu thu lai va tu quan ly cooldown.
Cooldown luu trong bo nho cua process, khoa la model_key, dung chung giua cac role.
"""

import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv

from llm.errors import LLMChainExhausted, LLMConfigError, LLMError, LLMRequestError

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Dung bang gia model co san trong goi, khong tai tu mang khi import.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm  # noqa: E402

litellm.suppress_debug_info = True
litellm.set_verbose = False
for _name in ("LiteLLM", "LiteLLM Router", "LiteLLM Proxy", "httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.CRITICAL)

log = logging.getLogger("llm")

REQUIRED_SETTINGS = [
    "enforce_validation",
    "timeout_s",
    "num_retries",
    "retry_after_s",
    "cooldown_s",
    "cooldown_max_s",
    "cooldown_not_found_s",
    "cooldown_whole_provider",
    "allowed_fails",
]
REQUIRED_MODEL_FIELDS = ["provider", "litellm_model", "api_key_env", "max_tokens"]

# Ly do ghi vao fallback_reasons / LLMChainExhausted.attempts
REASON_COOLDOWN = "cooldown"
REASON_RATE_LIMIT = "rate_limit"
REASON_TRANSIENT = "transient"  # 5xx, timeout, loi mang
REASON_NOT_FOUND = "not_found"


@dataclass(frozen=True)
class LLMResult:
    text: str
    role: str
    model_key: str
    provider: str
    litellm_model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    finish_reason: str | None
    fallback_count: int
    fallback_reasons: list = field(default_factory=list)
    latency_s: float = 0.0


@dataclass(frozen=True)
class _State:
    settings: dict
    models: dict  # model_key -> cau hinh model trong yaml
    chains: dict  # role -> [model_key, ...] da loc theo enforce_validation
    keys: dict  # api_key_env -> gia tri khoa
    secrets: tuple  # gia tri khoa, chi dung de che trong thong bao loi


_state = None

# Trang thai luc chay, khoa la model_key (dung chung giua cac role).
_cooldown_until = {}  # model_key -> thoi diem (epoch giay) het cooldown
_rate_limit_streak = {}  # model_key -> so lan 429 lien tiep
_fail_counts = {}  # model_key -> so lan loi tam thoi lien tiep


def _now():
    return time.time()


def _sleep(seconds):
    time.sleep(seconds)


def _completion(model, api_key, messages, timeout, max_tokens):
    """Mot lan goi duy nhat toi nha cung cap. num_retries=0: litellm khong tu thu lai."""
    return litellm.completion(
        model=model,
        api_key=api_key,
        messages=messages,
        timeout=timeout,
        max_tokens=max_tokens,
        num_retries=0,
    )


# ---------- Cau hinh ----------

def _config_path():
    raw = (os.getenv("LLM_CONFIG_PATH") or "").strip()
    if not raw:
        raise LLMConfigError("Thiếu biến 'LLM_CONFIG_PATH' trong file .env.")
    path = Path(raw)
    return path if path.is_absolute() else ROOT / path


def _load_config(path):
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except FileNotFoundError:
        raise LLMConfigError(f"Không tìm thấy file cấu hình LLM: {path}") from None
    except yaml.YAMLError as e:
        raise LLMConfigError(f"File {path} không phải YAML hợp lệ: {e}") from None

    if not isinstance(cfg, dict):
        raise LLMConfigError(f"{path}: nội dung gốc phải là một mapping.")
    for section in ("settings", "models", "roles"):
        if not isinstance(cfg.get(section), dict):
            raise LLMConfigError(f"{path}: thiếu mục '{section}' hoặc sai kiểu.")

    missing = [k for k in REQUIRED_SETTINGS if k not in cfg["settings"]]
    if missing:
        raise LLMConfigError(f"{path}: settings thiếu {missing}.")
    if not isinstance(cfg["settings"]["cooldown_whole_provider"], list):
        raise LLMConfigError(f"{path}: settings.cooldown_whole_provider phải là danh sách.")

    for key, model in cfg["models"].items():
        missing = [k for k in REQUIRED_MODEL_FIELDS if not isinstance(model, dict) or k not in model]
        if missing:
            raise LLMConfigError(f"{path}: model '{key}' thiếu {missing}.")

    for role, role_cfg in cfg["roles"].items():
        chain = role_cfg.get("chain") if isinstance(role_cfg, dict) else None
        if not isinstance(chain, list) or not chain:
            raise LLMConfigError(f"{path}: role '{role}' thiếu chain.")
        for item in chain:
            if not isinstance(item, dict) or item.get("model") not in cfg["models"]:
                raise LLMConfigError(f"{path}: role '{role}' có mục chain không hợp lệ: {item}.")
    return cfg


def _active_chains(cfg):
    enforce = bool(cfg["settings"]["enforce_validation"])
    chains = {}
    for role, role_cfg in cfg["roles"].items():
        keys = [i["model"] for i in role_cfg["chain"] if not enforce or i.get("validated") is True]
        if len(keys) != len(set(keys)):
            raise LLMConfigError(f"Role '{role}' có model lặp lại trong chain.")
        chains[role] = keys
    return chains


def _build_state(cfg):
    models = cfg["models"]
    chains = _active_chains(cfg)

    keys = {}
    for model_key in sorted({k for chain in chains.values() for k in chain}):
        env = models[model_key]["api_key_env"]
        value = (os.getenv(env) or "").strip()
        if not value:
            raise LLMConfigError(
                f"Thiếu khóa API: biến '{env}' (cho model '{model_key}') chưa có trong .env."
            )
        keys[env] = value

    return _State(
        settings=cfg["settings"],
        models=models,
        chains=chains,
        keys=keys,
        secrets=tuple(keys.values()),
    )


def _get_state():
    global _state
    if _state is None:
        _state = _build_state(_load_config(_config_path()))
    return _state


# ---------- Phan loai loi va cooldown ----------

def _redact(text, secrets):
    for s in secrets:
        text = text.replace(s, "***")
    return text


def _classify(e):
    """Tra ve 'rate_limit', 'request', 'not_found', 'transient' hoac None (loi la)."""
    if isinstance(e, litellm.RateLimitError):
        return "rate_limit"
    # BadRequestError gom ca ContextWindowExceeded, ContentPolicyViolation, UnsupportedParams...
    if isinstance(e, (litellm.AuthenticationError, litellm.PermissionDeniedError, litellm.BadRequestError)):
        return "request"
    if isinstance(e, litellm.NotFoundError):
        return "not_found"
    if isinstance(e, (
        litellm.Timeout,
        litellm.APIConnectionError,
        litellm.InternalServerError,
        litellm.ServiceUnavailableError,
        litellm.BadGatewayError,
    )):
        return "transient"
    # APIError chung cua litellm: chi dua vao status_code
    status = getattr(e, "status_code", None)
    if isinstance(e, litellm.APIError) and isinstance(status, int):
        if status == 429:
            return "rate_limit"
        if status in (400, 401, 403):
            return "request"
        if status == 404:
            return "not_found"
        if status >= 500:
            return "transient"
    return None


def _in_cooldown(model_key, now):
    return _cooldown_until.get(model_key, 0) > now


def _set_cooldown(model_key, seconds, now):
    _cooldown_until[model_key] = max(_cooldown_until.get(model_key, 0), now + seconds)


def _on_rate_limit(state, model_key):
    settings = state.settings
    streak = _rate_limit_streak.get(model_key, 0) + 1
    _rate_limit_streak[model_key] = streak
    seconds = min(settings["cooldown_s"] * 2 ** (streak - 1), settings["cooldown_max_s"])
    now = _now()
    _set_cooldown(model_key, seconds, now)

    provider = state.models[model_key]["provider"]
    if provider in settings["cooldown_whole_provider"]:
        for other, model in state.models.items():
            if model["provider"] == provider:
                _set_cooldown(other, seconds, now)


def _on_success(model_key):
    _rate_limit_streak.pop(model_key, None)
    _fail_counts.pop(model_key, None)


def _format_time(ts):
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


# ---------- Vong lap ----------

def _try_model(state, model_key, messages):
    """Goi mot model, gom ca thu lai. Tra ve (response, None) neu thanh cong,
    (None, {model_key, reason, status_code}) neu can chuyen model ke."""
    settings = state.settings
    model = state.models[model_key]
    retries = settings["num_retries"]

    for attempt in range(retries + 1):
        try:
            response = _completion(
                model=model["litellm_model"],
                api_key=state.keys[model["api_key_env"]],
                messages=messages,
                timeout=settings["timeout_s"],
                max_tokens=model["max_tokens"],
            )
        except Exception as e:
            kind = _classify(e)
            status = getattr(e, "status_code", None)
            msg = _redact(f"{type(e).__name__}: {e}", state.secrets)

            if kind == "request":
                raise LLMRequestError(
                    f"Nhà cung cấp từ chối yêu cầu (model='{model_key}', provider='{model['provider']}', "
                    f"status={status}): {msg}",
                    model_key=model_key, provider=model["provider"], status_code=status,
                ) from None
            if kind is None:
                raise LLMError(
                    f"Lỗi không phân loại được từ model '{model_key}' "
                    f"(lớp {type(e).__module__}.{type(e).__name__}, status={status}): {msg}"
                ) from None

            if kind == "rate_limit":
                _on_rate_limit(state, model_key)
                return None, {"model_key": model_key, "reason": REASON_RATE_LIMIT, "status_code": status}

            if kind == "not_found":
                seconds = settings["cooldown_not_found_s"]
                _set_cooldown(model_key, seconds, _now())
                log.warning(
                    "Model '%s' (%s) trả 404, có thể đã bị gỡ. Tạm loại %s giây.",
                    model_key, model["litellm_model"], seconds,
                )
                return None, {"model_key": model_key, "reason": REASON_NOT_FOUND, "status_code": status}

            # transient: thu lai; het num_retries moi tinh MOT lan that bai cho luot nay
            if attempt < retries:
                _sleep(settings["retry_after_s"])
                continue
            _fail_counts[model_key] = _fail_counts.get(model_key, 0) + 1
            if _fail_counts[model_key] >= settings["allowed_fails"]:
                _set_cooldown(model_key, settings["cooldown_s"], _now())
                _fail_counts[model_key] = 0
            return None, {"model_key": model_key, "reason": REASON_TRANSIENT, "status_code": status}

        _on_success(model_key)
        return response, None


def _ask(messages, role, start_model=None, json_schema=None, task_id=None):
    """Nhu ask_model, nhung cho phep chon model bat dau (start_model) trong chain."""
    state = _get_state()
    if role not in state.chains:
        raise LLMConfigError(f"Role '{role}' không có trong cấu hình. Có: {sorted(state.chains)}.")
    chain = state.chains[role]
    if not chain:
        raise LLMConfigError(
            f"Role '{role}' không còn model nào (enforce_validation=true và chưa có mục validated)."
        )
    if start_model is None:
        start_model = chain[0]
    elif start_model not in chain:
        raise LLMConfigError(f"Model '{start_model}' không nằm trong chain của role '{role}': {chain}.")

    reasons = []
    t0 = time.perf_counter()
    for model_key in chain[chain.index(start_model):]:
        if _in_cooldown(model_key, _now()):
            reasons.append({"model_key": model_key, "reason": REASON_COOLDOWN, "status_code": None})
            continue

        response, failure = _try_model(state, model_key, messages)
        if failure is not None:
            reasons.append(failure)
            continue

        model = state.models[model_key]
        choice = response.choices[0]
        usage = getattr(response, "usage", None)
        return LLMResult(
            text=choice.message.content or "",
            role=role,
            model_key=model_key,
            provider=model["provider"],
            litellm_model=model["litellm_model"],
            prompt_tokens=getattr(usage, "prompt_tokens", None),
            completion_tokens=getattr(usage, "completion_tokens", None),
            finish_reason=choice.finish_reason,
            fallback_count=len(reasons),
            fallback_reasons=reasons,
            latency_s=round(time.perf_counter() - t0, 3),
        )

    now = _now()
    pending = [_cooldown_until[k] for k in chain if _in_cooldown(k, now)]
    next_at = min(pending) if pending else None
    lines = "\n".join(f"  - {r['model_key']}: {r['reason']} (status={r['status_code']})" for r in reasons)
    when = _format_time(next_at) if next_at is not None else "không có model nào đang cooldown"
    raise LLMChainExhausted(
        f"Hết chuỗi model của role '{role}' (bắt đầu từ '{start_model}'):\n{lines}\n"
        f"Sớm nhất có model hết cooldown: {when}",
        attempts=reasons,
        next_available_at=next_at,
    )


def ask_model(messages, role, json_schema=None, task_id=None):
    """Gui messages toi chuoi model cua role. json_schema, task_id: de danh cho buoc sau."""
    return _ask(messages, role, start_model=None, json_schema=json_schema, task_id=task_id)
