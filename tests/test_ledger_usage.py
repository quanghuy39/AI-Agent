"""Noi luot goi AI vao run_log.jsonl cua bai. Dung tmp_path, khong mang.

Chay: python -m pytest tests/test_ledger_usage.py -v
"""

import json

import pytest

from ledger import Ledger, LogEvent
from llm import usage
from llm.usage import make_record, write_usage


@pytest.fixture
def led(tmp_path):
    led = Ledger(tmp_path / "bai1")
    led.attach_usage_log()
    yield led
    led.detach_usage_log()  # luon tra ve mac dinh, ke ca khi test loi


def llm_record(**kw):
    fields = dict(role="analysis", provider="groq", model_key="x", prompt_tokens=10, completion_tokens=5,
                  fallback_count=1, fallback_reasons=["429"], model_switched=True, status="ok")
    fields.update(kw)
    return make_record(**fields)


def run_log_lines(led):
    path = led.root / "run_log.jsonl"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def test_llm_call_written_as_log_event(led):
    write_usage(llm_record())
    lines = run_log_lines(led)
    assert len(lines) == 1

    raw = json.loads(lines[0])
    assert set(raw) == set(LogEvent.model_fields)
    assert (raw["step"], raw["action"], raw["inputs"], raw["code_file"], raw["checks"], raw["status"]) == (
        "llm", "llm_call", {}, None, [], "ok")
    assert set(raw["extra"]) == set(usage.FIELDS) - {"ts", "event", "status"}

    events = led.read_log()
    assert len(events) == 1
    e = events[0]
    assert isinstance(e, LogEvent)
    assert e.extra["provider"] == "groq"
    assert e.extra["model_switched"] is True
    assert e.extra["fallback_reasons"] == ["429"]
    assert (e.extra["prompt_tokens"], e.extra["completion_tokens"], e.extra["role"]) == (10, 5, "analysis")


def test_status_and_ts_come_from_record(led):
    write_usage(llm_record(status="chain_exhausted", error_type="LLMChainExhausted"))
    write_usage(llm_record(status=None))
    first, second = led.read_log()
    assert first.status == "chain_exhausted" and first.extra["error_type"] == "LLMChainExhausted"
    assert second.status == "ok"
    rec = llm_record()
    write_usage(rec)
    assert led.read_log()[-1].ts == rec["ts"]


def test_mixed_log_events_and_llm_calls(led):
    led.log_event("profile", "load_data", inputs={"file": "orders.csv"})
    write_usage(llm_record(model_key="a"))
    led.log_event("analysis", "run_code", code_file="code/r1.py", checks=[{"name": "runs", "status": "pass"}])
    write_usage(llm_record(model_key="b", status="request_error", status_code=401))

    assert len(run_log_lines(led)) == 4
    events = led.read_log()
    assert [(e.step, e.action) for e in events] == [
        ("profile", "load_data"), ("llm", "llm_call"), ("analysis", "run_code"), ("llm", "llm_call"),
    ]
    assert [e.extra.get("model_key") for e in events if e.action == "llm_call"] == ["a", "b"]
    assert events[3].status == "request_error" and events[3].extra["status_code"] == 401


def test_no_prompt_or_answer_in_run_log(led):
    write_usage(llm_record(prompt_hash="ab" * 32))
    raw = json.loads(run_log_lines(led)[0])
    assert "messages" not in json.dumps(raw) and "text" not in raw["extra"]
    assert raw["extra"]["prompt_hash"] == "ab" * 32


def test_detach_stops_writing_to_run_log(led, tmp_path):
    write_usage(llm_record())
    led.detach_usage_log()
    default = tmp_path / "default_usage.jsonl"
    write_usage(llm_record(), default_path=default)
    assert len(run_log_lines(led)) == 1  # dong moi khong vao run_log.jsonl

    flat = json.loads(default.read_text(encoding="utf-8").splitlines()[0])
    assert list(flat) == usage.FIELDS  # mac dinh van la ban ghi phang
    assert flat["provider"] == "groq"


def test_detach_without_default_writes_nothing(led):
    led.detach_usage_log()
    write_usage(llm_record())  # khong co default_path -> khong ghi dau ca
    assert run_log_lines(led) == []
