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

from pydantic import ValidationError

from llm import cache, structured, usage
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
REASON_JSON_INVALID = "json_invalid"


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
    data: object = None  # doi tuong pydantic da validate, None neu khong co json_schema
    json_retries: int = 0
    cached: bool = False
    task_id: object = None
    model_switched: bool = False
    network_calls: int = 0  # so lan goi litellm.completion that su trong luot, ke ca lan loi


@dataclass
class _Tally:
    """Cong don trong mot luot ask_model: moi lan goi mang, ke ca thu lai JSON va model that bai."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    network_calls: int = 0

    def add_usage(self, response):
        tokens = getattr(response, "usage", None)
        self.prompt_tokens += getattr(tokens, "prompt_tokens", None) or 0
        self.completion_tokens += getattr(tokens, "completion_tokens", None) or 0


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
_fail_counts = {}  # model_key -> so luot loi tam thoi lien tiep
_task_models = {}  # task_id -> model_key da gan


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


def _on_rate_limit(state, model_key, whole_provider=True):
    settings = state.settings
    streak = _rate_limit_streak.get(model_key, 0) + 1
    _rate_limit_streak[model_key] = streak
    seconds = min(settings["cooldown_s"] * 2 ** (streak - 1), settings["cooldown_max_s"])
    now = _now()
    _set_cooldown(model_key, seconds, now)

    provider = state.models[model_key]["provider"]
    if whole_provider and provider in settings["cooldown_whole_provider"]:
        for other, model in state.models.items():
            if model["provider"] == provider:
                _set_cooldown(other, seconds, now)


def _on_success(model_key):
    _rate_limit_streak.pop(model_key, None)
    _fail_counts.pop(model_key, None)


def _format_time(ts):
    return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")


# ---------- Vong lap ----------

def _try_model(state, model_key, messages, tally=None, whole_provider=True):
    """Goi mot model, gom ca thu lai. Tra ve (response, None) neu thanh cong,
    (None, {model_key, reason, status_code}) neu can chuyen model ke."""
    settings = state.settings
    model = state.models[model_key]
    retries = settings["num_retries"]
    tally = tally if tally is not None else _Tally()

    for attempt in range(retries + 1):
        tally.network_calls += 1
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
                _on_rate_limit(state, model_key, whole_provider)
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

        tally.add_usage(response)
        _on_success(model_key)
        return response, None


def _try_model_json(state, model_key, messages, json_schema, tally, whole_provider=True):
    """Goi mot model, kiem tra JSON neu co schema. Tra ve (response, data, failure, json_retries)."""
    if json_schema is None:
        response, failure = _try_model(state, model_key, messages, tally, whole_provider)
        return response, None, failure, 0

    max_retries = state.settings.get("json_retries", 0)
    msgs = list(messages) + [structured.instruction_message(json_schema)]
    for attempt in range(max_retries + 1):
        response, failure = _try_model(state, model_key, msgs, tally, whole_provider)
        if failure is not None:
            return None, None, failure, attempt
        text = response.choices[0].message.content or ""
        try:
            return response, structured.parse(text, json_schema), None, attempt
        except ValidationError as e:
            summary = structured.summarize_error(e)
        if attempt == max_retries:
            break
        if text:  # mot so hang tu choi tin nhan assistant rong
            msgs = msgs + [{"role": "assistant", "content": text}]
        msgs = msgs + [structured.retry_message(summary)]
    return None, None, {"model_key": model_key, "reason": REASON_JSON_INVALID, "status_code": None}, max_retries


def _order(chain, start_model, pinned):
    """Thu tu thu model: chain tu start_model; model da gan theo task_id (neu con dung duoc) len dau."""
    base = chain[chain.index(start_model):]
    if pinned in base and not _in_cooldown(pinned, _now()):
        return [pinned] + [k for k in base if k != pinned]
    return base


def _bind_task(task_id, model_key):
    """Gan task_id vao model tra loi. Tra ve model_switched."""
    if task_id is None:
        return False
    pinned = _task_models.get(task_id)
    _task_models[task_id] = model_key
    return pinned is not None and pinned != model_key


def _from_cache(state, role, prompt_hash, json_schema, task_id):
    t0 = time.perf_counter()
    hit = cache.get(state.settings["cache_path"], prompt_hash)
    if hit is None:
        return None
    data = None
    if json_schema is not None:
        try:
            data = structured.parse(hit["text"], json_schema)
        except ValidationError:
            log.warning("Bản ghi cache %s không còn khớp schema, bỏ qua.", prompt_hash[:12])
            return None
    return LLMResult(
        text=hit["text"],
        role=role,
        model_key=hit["model_key"],
        provider=hit["provider"],
        litellm_model=hit["litellm_model"],
        prompt_tokens=hit["prompt_tokens"],
        completion_tokens=hit["completion_tokens"],
        finish_reason=hit["finish_reason"],
        fallback_count=0,
        fallback_reasons=[],
        latency_s=round(time.perf_counter() - t0, 3),
        data=data,
        json_retries=0,
        cached=True,
        task_id=task_id,
        model_switched=_bind_task(task_id, hit["model_key"]),
    )


def _to_cache(state, prompt_hash, result):
    cache.put(state.settings["cache_path"], prompt_hash, {
        "text": result.text,
        "model_key": result.model_key,
        "provider": result.provider,
        "litellm_model": result.litellm_model,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "finish_reason": result.finish_reason,
        "cached_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    })


def _make_result(state, role, model_key, response, data, reasons, json_retries, task_id, tally, t0):
    model = state.models[model_key]
    choice = response.choices[0]
    return LLMResult(
        text=choice.message.content or "",
        role=role,
        model_key=model_key,
        provider=model["provider"],
        litellm_model=model["litellm_model"],
        prompt_tokens=tally.prompt_tokens,  # tong moi lan goi mang trong luot
        completion_tokens=tally.completion_tokens,
        finish_reason=choice.finish_reason,
        fallback_count=len(reasons),
        fallback_reasons=reasons,
        latency_s=round(time.perf_counter() - t0, 3),
        data=data,
        json_retries=json_retries,
        cached=False,
        task_id=task_id,
        model_switched=_bind_task(task_id, model_key),
        network_calls=tally.network_calls,
    )


def _run_chain(state, messages, role, chain, start_model, json_schema, task_id, tally):
    reasons = []
    json_retries = 0
    t0 = time.perf_counter()
    pinned = _task_models.get(task_id) if task_id is not None else None
    for model_key in _order(chain, start_model, pinned):
        if _in_cooldown(model_key, _now()):
            reasons.append({"model_key": model_key, "reason": REASON_COOLDOWN, "status_code": None})
            continue

        try:
            response, data, failure, used = _try_model_json(state, model_key, messages, json_schema, tally)
        except LLMError as e:
            e.fallback_reasons = list(reasons)  # de ghi usage
            raise
        json_retries += used
        if failure is not None:
            reasons.append(failure)
            continue

        return _make_result(state, role, model_key, response, data, reasons, json_retries, task_id, tally, t0)

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


def _log_ok(state, result, prompt_hash):
    if result.cached:
        # khong ton token o luot nay; token goc ghi rieng
        tokens = {
            "prompt_tokens": 0, "completion_tokens": 0, "network_calls": 0,
            "cached_prompt_tokens": result.prompt_tokens, "cached_completion_tokens": result.completion_tokens,
        }
    else:
        tokens = {
            "prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
            "network_calls": result.network_calls,
        }
    usage.write_usage(usage.make_record(
        role=result.role, task_id=result.task_id, status="cache_hit" if result.cached else "ok",
        provider=result.provider, model_key=result.model_key, litellm_model=result.litellm_model,
        **tokens,
        fallback_count=result.fallback_count, fallback_reasons=result.fallback_reasons,
        model_switched=result.model_switched, cached=result.cached, json_retries=result.json_retries,
        latency_s=result.latency_s, prompt_hash=prompt_hash,
    ), state.settings.get("usage_log_path"))


def _log_error(state, e, role, task_id, prompt_hash, t0, tally):
    if isinstance(e, LLMRequestError):
        status = "request_error"
    elif isinstance(e, LLMChainExhausted):
        status = "chain_exhausted"
    else:
        status = "error"
    reasons = e.attempts if isinstance(e, LLMChainExhausted) else getattr(e, "fallback_reasons", [])
    model_key = getattr(e, "model_key", None)
    model = state.models.get(model_key, {}) if state is not None and model_key else {}
    usage.write_usage(usage.make_record(
        role=role, task_id=task_id, status=status,
        provider=getattr(e, "provider", None), model_key=model_key, litellm_model=model.get("litellm_model"),
        prompt_tokens=tally.prompt_tokens, completion_tokens=tally.completion_tokens,
        network_calls=tally.network_calls,
        fallback_count=len(reasons), fallback_reasons=reasons, model_switched=False, cached=False,
        latency_s=round(time.perf_counter() - t0, 3), error_type=type(e).__name__,
        status_code=getattr(e, "status_code", None), prompt_hash=prompt_hash,
    ), state.settings.get("usage_log_path") if state is not None else None)


def _chain_of(state, role):
    if role not in state.chains:
        raise LLMConfigError(f"Role '{role}' không có trong cấu hình. Có: {sorted(state.chains)}.")
    chain = state.chains[role]
    if not chain:
        raise LLMConfigError(
            f"Role '{role}' không còn model nào (enforce_validation=true và chưa có mục validated)."
        )
    return chain


def _attach_usage(e, tally):
    """Gan token va so lan goi mang da dung vao exception, de noi goi (vd. bo kiem dinh) doc duoc."""
    try:
        e.llm_usage = {
            "prompt_tokens": tally.prompt_tokens,
            "completion_tokens": tally.completion_tokens,
            "network_calls": tally.network_calls,
        }
    except Exception:  # noqa: BLE001  (exception khong cho gan thuoc tinh)
        pass


def _ask(messages, role, start_model=None, json_schema=None, task_id=None, use_cache=True):
    """Nhu ask_model, nhung cho phep chon model bat dau (start_model) trong chain."""
    t0 = time.perf_counter()
    state = prompt_hash = None
    tally = _Tally()
    try:
        state = _get_state()
        structured.check_schema(json_schema)
        chain = _chain_of(state, role)
        if start_model is None:
            start_model = chain[0]
        elif start_model not in chain:
            raise LLMConfigError(f"Model '{start_model}' không nằm trong chain của role '{role}': {chain}.")

        prompt_hash = cache.cache_key(role, messages, structured.schema_json(json_schema))
        use_cache = use_cache and state.settings.get("cache_path") is not None
        result = _from_cache(state, role, prompt_hash, json_schema, task_id) if use_cache else None
        if result is None:
            result = _run_chain(state, messages, role, chain, start_model, json_schema, task_id, tally)
            if use_cache:
                _to_cache(state, prompt_hash, result)
    except Exception as e:
        _attach_usage(e, tally)
        _log_error(state, e, role, task_id, prompt_hash, t0, tally)
        raise
    _log_ok(state, result, prompt_hash)
    return result


def _run_one(state, messages, role, model_key, json_schema, task_id, tally):
    """Goi dung mot model, khong chuyen model ke. Model khong dung duoc -> LLMChainExhausted (1 attempt)."""
    t0 = time.perf_counter()
    failure = None
    if _in_cooldown(model_key, _now()):
        failure = {"model_key": model_key, "reason": REASON_COOLDOWN, "status_code": None}
    else:
        # whole_provider=False: khong dong cooldown cac model khac cung hang
        response, data, failure, used = _try_model_json(
            state, model_key, messages, json_schema, tally, whole_provider=False
        )
        if failure is None:
            return _make_result(state, role, model_key, response, data, [], used, task_id, tally, t0)

    next_at = _cooldown_until[model_key] if _in_cooldown(model_key, _now()) else None
    when = _format_time(next_at) if next_at is not None else "không cooldown"
    raise LLMChainExhausted(
        f"Model '{model_key}' (role '{role}') không trả lời được: {failure['reason']} "
        f"(status={failure['status_code']}). Hết cooldown: {when}",
        attempts=[failure],
        next_available_at=next_at,
    )


def ask_one_model(messages, role, model_key, json_schema=None, use_cache=False, task_id=None):
    """Goi DUNG model_key (phai nam trong chain cua role), khong chuyen model ke.

    Dung cho kiem dinh model. Kiem tra/thu lai JSON, dem token, ghi usage nhu ask_model.
    Cache (neu bat) dung khoa rieng co ten model, khong lan voi cache cua ask_model.
    """
    t0 = time.perf_counter()
    state = prompt_hash = None
    tally = _Tally()
    try:
        state = _get_state()
        structured.check_schema(json_schema)
        chain = _chain_of(state, role)
        if model_key not in chain:
            raise LLMConfigError(f"Model '{model_key}' không nằm trong chain của role '{role}': {chain}.")

        prompt_hash = cache.cache_key(role, messages, structured.schema_json(json_schema), model_key=model_key)
        use_cache = use_cache and state.settings.get("cache_path") is not None
        result = _from_cache(state, role, prompt_hash, json_schema, task_id) if use_cache else None
        if result is None:
            result = _run_one(state, messages, role, model_key, json_schema, task_id, tally)
            if use_cache:
                _to_cache(state, prompt_hash, result)
    except Exception as e:
        _attach_usage(e, tally)
        _log_error(state, e, role, task_id, prompt_hash, t0, tally)
        raise
    _log_ok(state, result, prompt_hash)
    return result


def ask_model(messages, role, json_schema=None, task_id=None, use_cache=True):
    """Gui messages toi chuoi model cua role.

    json_schema: lop pydantic BaseModel; cau tra loi phai la JSON dung schema, ket qua o LLMResult.data.
    task_id: giu model theo task (thu model da gan truoc). use_cache=False: bo qua cache ca doc lan ghi.
    """
    return _ask(messages, role, start_model=None, json_schema=json_schema, task_id=task_id, use_cache=use_cache)
