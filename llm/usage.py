"""Ghi moi luot ask_model thanh mot dong JSON Lines. KHONG ghi noi dung prompt, cau tra loi hay khoa.

Mac dinh (chua goi set_usage_log_path): ban ghi phang vao usage_log_path cua llm.yaml.
Da goi set_usage_log_path(path): moi luot la mot dong dung hinh dang LogEvent (ledger/schema.py) de nam
chung run_log.jsonl cua bai. Khong import ledger o day de tranh vong import; dict duoi tu dung dung khoa.
"""

import json
import logging
from datetime import datetime
from pathlib import Path

log = logging.getLogger("llm")

ROOT = Path(__file__).resolve().parent.parent

FIELDS = [
    "ts", "event", "role", "task_id", "status", "provider", "model_key", "litellm_model",
    "prompt_tokens", "completion_tokens", "network_calls", "cached_prompt_tokens", "cached_completion_tokens",
    "fallback_count", "fallback_reasons", "model_switched",
    "cached", "json_retries", "latency_s", "error_type", "status_code", "prompt_hash",
]

_path_override = None


def set_usage_log_path(path):
    """Chuyen file usage sang duong dan khac (vd. run_log.jsonl cua bai). None: quay ve mac dinh."""
    global _path_override
    _path_override = Path(path) if path is not None else None


def _resolve(default_path):
    path = _path_override if _path_override is not None else default_path
    if path is None:
        return None
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def make_record(**fields):
    unknown = set(fields) - set(FIELDS)
    if unknown:
        raise ValueError(f"Trường usage không hợp lệ: {sorted(unknown)}")
    record = {name: None for name in FIELDS}
    record["ts"] = datetime.now().astimezone().isoformat(timespec="seconds")
    record["event"] = "llm_call"
    record.update(fields)
    return record


def as_log_event(record):
    """Ban ghi usage -> dict dung khoa cua LogEvent: ts, step, action, inputs, code_file, checks, status, extra."""
    extra = {k: v for k, v in record.items() if k not in ("ts", "event", "status")}
    return {
        "ts": record.get("ts") or datetime.now().astimezone().isoformat(timespec="seconds"),
        "step": "llm",
        "action": "llm_call",
        "inputs": {},
        "code_file": None,
        "checks": [],
        "status": record.get("status") or "ok",
        "extra": extra,
    }


def write_usage(record, default_path=None):
    """Ghi mot dong. Loi ghi file chi canh bao, khong lam hong luot goi."""
    path = _resolve(default_path)
    if path is None:
        return
    if _path_override is not None:
        record = as_log_event(record)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:  # noqa: BLE001
        log.warning("Không ghi được usage log vào %s: %s: %s", path, type(e).__name__, e)
