"""Kiem tra JSON, cache, giu model theo task_id, usage log, an danh, FakeLLM.
Khong goi mang, khong dung khoa that.

Chay: python -m pytest tests/test_llm_features.py -v
"""

import json
import logging
import sqlite3
from types import SimpleNamespace

import pandas as pd
import pytest
from pydantic import BaseModel

import llm
from llm import (
    FakeLLM,
    LLMChainExhausted,
    LLMRequestError,
    anonymize,
    client,
    set_usage_log_path,
    usage,
    use_fake,
)

litellm = client.litellm  # chi lay lop exception

FAKE_KEYS = {
    "GEMINI_API_KEY": "fake-gemini-SECRET-111",
    "MISTRAL_API_KEY": "fake-mistral-SECRET-222",
    "GROQ_API_KEY": "fake-groq-SECRET-444",
}

CONFIG = """
settings:
  enforce_validation: false
  timeout_s: 60
  num_retries: 1
  retry_after_s: 5
  cooldown_s: 60
  cooldown_max_s: 300
  cooldown_not_found_s: 3600
  cooldown_whole_provider: []
  allowed_fails: 2
  json_retries: 2
  cache_path: {cache}
  usage_log_path: {usage}

models:
  a1: {{provider: gemini, litellm_model: gemini/a1, api_key_env: GEMINI_API_KEY, max_tokens: 100}}
  a2: {{provider: gemini, litellm_model: gemini/a2, api_key_env: GEMINI_API_KEY, max_tokens: 100}}
  m1: {{provider: mistral, litellm_model: mistral/m1, api_key_env: MISTRAL_API_KEY, max_tokens: 100}}
  g1: {{provider: groq, litellm_model: groq/g1, api_key_env: GROQ_API_KEY, max_tokens: 100}}

roles:
  r1:
    chain:
      - {{model: a1, validated: false}}
      - {{model: m1, validated: false}}
      - {{model: g1, validated: false}}
"""

T0 = 1_000_000.0
ERRORS = {"429", "500", "401"}
SECRET_PROMPT = "Nội dung BÍ MẬT của người dùng"


class Item(BaseModel):
    name: str
    qty: int


GOOD_JSON = '{"name": "táo", "qty": 2}'


def msgs(text=SECRET_PROMPT):
    return [{"role": "user", "content": text}]


def ok_response(text):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=11, completion_tokens=5),
    )


class ScriptedCompletion:
    """Thay client._completion. plan: litellm_model -> danh sach buoc; buoc cuoi lap lai.
    Buoc la ma loi ('429', '500', '401') hoac van ban tra ve. Mac dinh tra 'ok'."""

    def __init__(self):
        self.plan = {}
        self.calls = []  # (model, messages)

    def __call__(self, model, api_key, messages, timeout, max_tokens):
        self.calls.append((model, messages))
        steps = self.plan.get(model, ["ok"])
        n = sum(1 for m, _ in self.calls if m == model) - 1
        step = steps[min(n, len(steps) - 1)]
        if step == "429":
            raise litellm.RateLimitError(f"limit key={api_key}", llm_provider="x", model=model)
        if step == "500":
            raise litellm.InternalServerError("boom", llm_provider="x", model=model)
        if step == "401":
            raise litellm.AuthenticationError(f"bad key {api_key}", llm_provider="x", model=model)
        if step == "nousage":  # nha cung cap khong tra so token
            resp = ok_response("ok")
            resp.usage = None
            return resp
        return ok_response(step)

    def models(self):
        return [m for m, _ in self.calls]


@pytest.fixture
def env(tmp_path, monkeypatch):
    cache_path = tmp_path / "cache.sqlite"
    usage_path = tmp_path / "usage.jsonl"
    cfg = tmp_path / "llm.yaml"
    cfg.write_text(
        CONFIG.format(cache=cache_path.as_posix(), usage=usage_path.as_posix()), encoding="utf-8"
    )
    monkeypatch.setenv("LLM_CONFIG_PATH", str(cfg))
    for name, value in FAKE_KEYS.items():
        monkeypatch.setenv(name, value)

    monkeypatch.setattr(client, "_state", None)
    for name in ("_cooldown_until", "_rate_limit_streak", "_fail_counts", "_task_models"):
        monkeypatch.setattr(client, name, {})
    monkeypatch.setattr(usage, "_path_override", None)

    clock = SimpleNamespace(t=T0)
    monkeypatch.setattr(client, "_now", lambda: clock.t)
    monkeypatch.setattr(client, "_sleep", lambda s: setattr(clock, "t", clock.t + s))
    fake = ScriptedCompletion()
    monkeypatch.setattr(client, "_completion", fake)
    return SimpleNamespace(llm=fake, clock=clock, cache_path=cache_path, usage_path=usage_path)


def usage_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ---------- (a)-(c) JSON ----------

def test_a_valid_json_gives_typed_data(env):
    env.llm.plan["gemini/a1"] = [f"```json\n{GOOD_JSON}\n```"]
    r = client.ask_model(msgs(), "r1", json_schema=Item)
    assert isinstance(r.data, Item)
    assert r.data == Item(name="táo", qty=2)
    assert (r.model_key, r.json_retries, r.fallback_count) == ("a1", 0, 0)
    sent = env.llm.calls[0][1]
    assert sent[0] == msgs()[0]
    assert sent[-1]["role"] == "user" and '"qty"' in sent[-1]["content"]  # chi dan kem schema


def test_no_schema_data_is_none(env):
    r = client.ask_model(msgs(), "r1")
    assert r.data is None and r.json_retries == 0


def test_b_invalid_then_valid_retries_same_model(env):
    env.llm.plan["gemini/a1"] = ["khong phai json", GOOD_JSON]
    r = client.ask_model(msgs(), "r1", json_schema=Item)
    assert r.model_key == "a1"
    assert r.json_retries == 1
    assert r.data.qty == 2
    assert env.llm.models() == ["gemini/a1", "gemini/a1"]
    retry_msgs = env.llm.calls[1][1]
    assert retry_msgs[-2] == {"role": "assistant", "content": "khong phai json"}
    assert "not valid JSON" in retry_msgs[-1]["content"]


def test_b_wrong_type_error_is_reported_to_model(env):
    env.llm.plan["gemini/a1"] = ['{"name": "x", "qty": "nhiều"}', GOOD_JSON]
    r = client.ask_model(msgs(), "r1", json_schema=Item)
    assert r.json_retries == 1
    assert "qty" in env.llm.calls[1][1][-1]["content"]


def test_c_always_invalid_moves_on_without_cooldown(env):
    env.llm.plan["gemini/a1"] = ["sai mãi"]
    env.llm.plan["mistral/m1"] = [GOOD_JSON]
    r = client.ask_model(msgs(), "r1", json_schema=Item)
    assert env.llm.models() == ["gemini/a1"] * 3 + ["mistral/m1"]  # 1 + json_retries=2
    assert r.model_key == "m1"
    assert r.fallback_reasons == [{"model_key": "a1", "reason": "json_invalid", "status_code": None}]
    assert "a1" not in client._cooldown_until
    assert "a1" not in client._fail_counts


def test_c_whole_chain_invalid_raises_exhausted(env):
    for m in ("gemini/a1", "mistral/m1", "groq/g1"):
        env.llm.plan[m] = ["sai"]
    with pytest.raises(LLMChainExhausted) as ei:
        client.ask_model(msgs(), "r1", json_schema=Item)
    assert [a["reason"] for a in ei.value.attempts] == ["json_invalid"] * 3


# ---------- (d)-(f) Cache ----------

def test_d_cache_hit_skips_completion(env):
    env.llm.plan["gemini/a1"] = [GOOD_JSON]
    first = client.ask_model(msgs(), "r1", json_schema=Item)
    n = len(env.llm.calls)
    second = client.ask_model(msgs(), "r1", json_schema=Item)
    assert len(env.llm.calls) == n
    assert second.cached is True and first.cached is False
    assert (second.text, second.model_key, second.provider) == (first.text, "a1", "gemini")
    assert (second.prompt_tokens, second.completion_tokens) == (11, 5)
    assert second.data == first.data and isinstance(second.data, Item)
    assert second.fallback_count == 0


def test_d_cache_key_depends_on_role_messages_schema(env):
    env.llm.plan["gemini/a1"] = [GOOD_JSON]
    client.ask_model(msgs("một"), "r1")
    client.ask_model(msgs("hai"), "r1")  # messages khac
    client.ask_model(msgs("một"), "r1", json_schema=Item)  # schema khac
    assert len(env.llm.calls) == 3
    client.ask_model(msgs("một"), "r1", json_schema=Item)
    assert len(env.llm.calls) == 3


def test_e_cache_key_ignores_answering_model(env):
    client.ask_model(msgs(), "r1", use_cache=False)  # a1 tra loi
    env.llm.plan["gemini/a1"] = ["429"]
    client.ask_model(msgs(), "r1", use_cache=False)  # a1 429 -> m1 tra loi
    hashes = [line["prompt_hash"] for line in usage_lines(env.usage_path)]
    models = [line["model_key"] for line in usage_lines(env.usage_path)]
    assert models == ["a1", "m1"]
    assert hashes[0] == hashes[1]
    assert not env.cache_path.exists() or _rows(env.cache_path) == 0  # use_cache=False khong ghi


def test_e_use_cache_false_calls_again(env):
    client.ask_model(msgs(), "r1")
    n = len(env.llm.calls)
    r = client.ask_model(msgs(), "r1", use_cache=False)
    assert len(env.llm.calls) == n + 1
    assert r.cached is False
    assert client.ask_model(msgs(), "r1").cached is True


def _rows(path):
    con = sqlite3.connect(path)
    try:
        return con.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0]
    finally:
        con.close()


def test_f_errors_are_not_cached(env):
    for m in ("gemini/a1", "mistral/m1", "groq/g1"):
        env.llm.plan[m] = ["429"]
    with pytest.raises(LLMChainExhausted):
        client.ask_model(msgs(), "r1")

    env.llm.plan["gemini/a1"] = ["401"]
    env.clock.t += 10_000
    with pytest.raises(LLMRequestError):
        client.ask_model(msgs(), "r1")
    assert _rows(env.cache_path) == 0

    env.llm.plan["gemini/a1"] = ["ok"]
    env.clock.t += 10_000
    r = client.ask_model(msgs(), "r1")
    assert r.cached is False
    assert _rows(env.cache_path) == 1


# ---------- (g)-(h) task_id ----------

def test_g_task_pins_model_and_tries_it_first(env):
    env.llm.plan["gemini/a1"] = ["429", "ok"]
    r = client.ask_model(msgs("lần 1"), "r1", task_id="t1")
    assert r.model_key == "m1" and r.task_id == "t1"
    assert r.model_switched is False  # chua gan truoc do
    assert client._task_models["t1"] == "m1"

    env.clock.t += 1000  # a1 het cooldown
    env.llm.calls.clear()
    r = client.ask_model(msgs("lần 2"), "r1", task_id="t1")
    assert env.llm.models() == ["mistral/m1"]  # m1 o giua chuoi nhung duoc thu truoc
    assert r.model_key == "m1" and r.model_switched is False


def test_g_cache_hit_binds_task(env):
    client.ask_model(msgs(), "r1")
    r = client.ask_model(msgs(), "r1", task_id="t9")
    assert r.cached is True
    assert client._task_models["t9"] == "a1"


def test_g_cache_hit_from_other_model_is_not_a_switch(env):
    client.ask_model(msgs(), "r1")  # a1 tra loi, vao cache
    client._task_models["t1"] = "m1"
    r = client.ask_model(msgs(), "r1", task_id="t1")
    assert (r.cached, r.model_key, r.model_switched) == (True, "a1", False)
    assert client._task_models["t1"] == "m1"  # khong ghi de model dang gan


def test_set_task_model_restores_binding_for_new_process(env):
    client.set_task_model("t1", "m1")
    env.llm.plan["mistral/m1"] = ["429"]
    r = client.ask_model(msgs(), "r1", task_id="t1")
    assert (r.model_key, r.model_switched, r.previous_model) == ("a1", True, "m1")
    client.set_task_model("t1", None)
    assert "t1" not in client._task_models


def test_h_pinned_model_429_switches_and_rebinds(env):
    client._task_models["t1"] = "m1"
    env.llm.plan["mistral/m1"] = ["429"]
    r = client.ask_model(msgs(), "r1", task_id="t1")
    # thu m1 truoc, sau do cac model con lai THEO THU TU BAN DAU: a1 roi g1
    assert env.llm.models() == ["mistral/m1", "gemini/a1"]
    assert r.model_key == "a1"
    assert r.model_switched is True
    assert r.fallback_reasons == [{"model_key": "m1", "reason": "rate_limit", "status_code": 429}]
    assert client._task_models["t1"] == "a1"


def test_h_pinned_model_in_cooldown_uses_normal_order(env):
    client._task_models["t1"] = "m1"
    client._cooldown_until["m1"] = T0 + 100
    r = client.ask_model(msgs(), "r1", task_id="t1")
    assert env.llm.models() == ["gemini/a1"]
    assert r.model_switched is True and client._task_models["t1"] == "a1"


def test_no_task_id_does_nothing(env):
    client.ask_model(msgs(), "r1")
    assert client._task_models == {}


# ---------- (i) usage log ----------

def test_i_usage_log_fields_and_no_content(env):
    env.llm.plan["gemini/a1"] = [GOOD_JSON]
    client.ask_model(msgs(), "r1", json_schema=Item, task_id="t1")  # ok
    client.ask_model(msgs(), "r1", json_schema=Item, task_id="t1")  # cache_hit
    env.llm.plan["gemini/a1"] = ["401"]
    with pytest.raises(LLMRequestError):
        client.ask_model(msgs("khác"), "r1")  # request_error
    env.llm.plan["gemini/a1"] = ["429"]
    env.llm.plan["mistral/m1"] = ["429"]
    env.llm.plan["groq/g1"] = ["429"]
    with pytest.raises(LLMChainExhausted):
        client.ask_model(msgs("khác nữa"), "r1")  # chain_exhausted
    with pytest.raises(llm.LLMConfigError):
        client.ask_model(msgs(), "khong_co")  # error

    lines = usage_lines(env.usage_path)
    assert [line["status"] for line in lines] == [
        "ok", "cache_hit", "request_error", "chain_exhausted", "error",
    ]
    for line in lines:
        assert list(line) == usage.FIELDS
        assert line["event"] == "llm_call"

    ok, hit, req, exh, err = lines
    assert (ok["provider"], ok["model_key"], ok["prompt_tokens"], ok["cached"]) == ("gemini", "a1", 11, False)
    assert (hit["cached"], hit["prompt_hash"]) == (True, ok["prompt_hash"])
    assert len(ok["prompt_hash"]) == 64
    assert (req["error_type"], req["status_code"], req["model_key"]) == ("LLMRequestError", 401, "a1")
    assert exh["fallback_count"] == 3 and exh["error_type"] == "LLMChainExhausted"
    assert err["error_type"] == "LLMConfigError"

    raw = env.usage_path.read_text(encoding="utf-8")
    for forbidden in [SECRET_PROMPT, "táo", "khác", *FAKE_KEYS.values()]:
        assert forbidden not in raw


def test_i_set_usage_log_path_redirects(env, tmp_path):
    other = tmp_path / "bai" / "run_log.jsonl"
    set_usage_log_path(other)
    client.ask_model(msgs(), "r1")
    assert len(usage_lines(other)) == 1
    assert not env.usage_path.exists()


def test_i_usage_write_failure_does_not_break_call(env, tmp_path, caplog):
    set_usage_log_path(tmp_path)  # la thu muc -> mo file loi
    with caplog.at_level(logging.WARNING, logger="llm"):
        r = client.ask_model(msgs(), "r1")
    assert r.model_key == "a1"
    assert any("usage" in rec.getMessage() for rec in caplog.records)


# ---------- Token va network_calls cong don trong luot ----------

def test_tokens_sum_over_json_retries(env):
    env.llm.plan["gemini/a1"] = ["khong phai json", GOOD_JSON]
    r = client.ask_model(msgs(), "r1", json_schema=Item)
    assert (r.prompt_tokens, r.completion_tokens, r.network_calls) == (22, 10, 2)
    line = usage_lines(env.usage_path)[0]
    assert (line["prompt_tokens"], line["completion_tokens"], line["network_calls"]) == (22, 10, 2)
    assert line["cached_prompt_tokens"] is None and line["cached_completion_tokens"] is None


def test_tokens_include_failed_json_model(env):
    env.llm.plan["gemini/a1"] = ["sai"]  # 3 lan, moi lan 11/5
    env.llm.plan["mistral/m1"] = [GOOD_JSON]
    r = client.ask_model(msgs(), "r1", json_schema=Item)
    assert r.model_key == "m1"
    assert (r.prompt_tokens, r.completion_tokens, r.network_calls) == (44, 20, 4)


def test_network_calls_429_then_success(env):
    env.llm.plan["gemini/a1"] = ["429"]  # loi, khong co token
    r = client.ask_model(msgs(), "r1")
    assert r.model_key == "m1"
    assert (r.prompt_tokens, r.completion_tokens, r.network_calls) == (11, 5, 2)
    line = usage_lines(env.usage_path)[0]
    assert (line["prompt_tokens"], line["completion_tokens"], line["network_calls"]) == (11, 5, 2)


def test_network_calls_count_transient_retries(env):
    env.llm.plan["gemini/a1"] = ["500"]  # 1 + num_retries=1
    r = client.ask_model(msgs(), "r1")
    assert r.model_key == "m1" and r.network_calls == 3


def test_missing_usage_counts_as_zero(env):
    env.llm.plan["gemini/a1"] = ["nousage"]
    r = client.ask_model(msgs(), "r1")
    assert (r.prompt_tokens, r.completion_tokens, r.network_calls) == (0, 0, 1)


def test_cache_hit_logs_zero_tokens_and_keeps_original(env):
    env.llm.plan["gemini/a1"] = ["khong phai json", GOOD_JSON]
    first = client.ask_model(msgs(), "r1", json_schema=Item)
    hit = client.ask_model(msgs(), "r1", json_schema=Item)
    assert hit.cached is True
    assert (hit.prompt_tokens, hit.completion_tokens) == (first.prompt_tokens, first.completion_tokens) == (22, 10)
    assert hit.network_calls == 0

    line = usage_lines(env.usage_path)[1]
    assert line["status"] == "cache_hit"
    assert (line["prompt_tokens"], line["completion_tokens"], line["network_calls"]) == (0, 0, 0)
    assert (line["cached_prompt_tokens"], line["cached_completion_tokens"]) == (22, 10)


def test_error_rows_keep_tokens_and_network_calls(env):
    env.llm.plan["gemini/a1"] = ["sai"]  # 3 lan JSON sai: 33/15
    env.llm.plan["mistral/m1"] = ["401"]
    with pytest.raises(LLMRequestError):
        client.ask_model(msgs(), "r1", json_schema=Item)

    for m in ("gemini/a1", "mistral/m1", "groq/g1"):
        env.llm.plan[m] = ["429"]
    with pytest.raises(LLMChainExhausted):
        client.ask_model(msgs("khác"), "r1")

    req, exh = usage_lines(env.usage_path)
    assert req["status"] == "request_error"
    assert (req["prompt_tokens"], req["completion_tokens"], req["network_calls"]) == (33, 15, 4)
    assert exh["status"] == "chain_exhausted"
    assert (exh["prompt_tokens"], exh["completion_tokens"], exh["network_calls"]) == (0, 0, 3)


def test_config_error_row_has_zero_network_calls(env):
    with pytest.raises(llm.LLMConfigError):
        client.ask_model(msgs(), "khong_co")
    line = usage_lines(env.usage_path)[0]
    assert (line["prompt_tokens"], line["completion_tokens"], line["network_calls"]) == (0, 0, 0)


# ---------- ask_one_model ----------

def test_one_model_429_does_not_call_next(env):
    env.llm.plan["gemini/a1"] = ["429"]
    with pytest.raises(LLMChainExhausted) as ei:
        client.ask_one_model(msgs(), "r1", "a1")
    assert env.llm.models() == ["gemini/a1"]  # KHONG goi m1
    assert ei.value.attempts == [{"model_key": "a1", "reason": "rate_limit", "status_code": 429}]
    assert ei.value.next_available_at == T0 + 60
    assert ei.value.llm_usage == {"prompt_tokens": 0, "completion_tokens": 0, "network_calls": 1}


def test_one_model_not_in_chain(env):
    with pytest.raises(llm.LLMConfigError):
        client.ask_one_model(msgs(), "r1", "a2")
    with pytest.raises(llm.LLMConfigError):
        client.ask_one_model(msgs(), "khong_co", "a1")
    assert env.llm.calls == []


def test_one_model_calls_only_that_model_with_json_retry(env):
    env.llm.plan["mistral/m1"] = ["sai", GOOD_JSON]
    r = client.ask_one_model(msgs(), "r1", "m1", json_schema=Item)
    assert env.llm.models() == ["mistral/m1", "mistral/m1"]
    assert (r.model_key, r.json_retries, r.fallback_count, r.network_calls) == ("m1", 1, 0, 2)
    assert (r.prompt_tokens, r.completion_tokens) == (22, 10)
    assert r.data == Item(name="táo", qty=2) and r.cached is False
    line = usage_lines(env.usage_path)[0]
    assert (line["status"], line["model_key"], line["network_calls"]) == ("ok", "m1", 2)


def test_one_model_json_invalid_raises_with_usage(env):
    env.llm.plan["gemini/a1"] = ["sai"]
    with pytest.raises(LLMChainExhausted) as ei:
        client.ask_one_model(msgs(), "r1", "a1", json_schema=Item)
    assert ei.value.attempts == [{"model_key": "a1", "reason": "json_invalid", "status_code": None}]
    assert ei.value.llm_usage == {"prompt_tokens": 33, "completion_tokens": 15, "network_calls": 3}
    assert env.llm.models() == ["gemini/a1"] * 3


def test_one_model_request_error(env):
    env.llm.plan["groq/g1"] = ["401"]
    with pytest.raises(LLMRequestError) as ei:
        client.ask_one_model(msgs(), "r1", "g1")
    assert (ei.value.model_key, ei.value.status_code) == ("g1", 401)


def test_one_model_does_not_cooldown_other_models(env):
    client._get_state().settings["cooldown_whole_provider"] = ["gemini"]
    env.llm.plan["gemini/a1"] = ["429"]
    with pytest.raises(LLMChainExhausted):
        client.ask_one_model(msgs(), "r1", "a1")
    assert "a1" in client._cooldown_until
    assert "a2" not in client._cooldown_until  # cung hang nhung khong bi dong

    with pytest.raises(LLMChainExhausted) as ei:  # dang cooldown -> khong goi
        client.ask_one_model(msgs(), "r1", "a1")
    assert ei.value.attempts[0]["reason"] == "cooldown"
    assert env.llm.models() == ["gemini/a1"]


def test_one_model_cache_is_separate_from_ask_model(env):
    client.ask_model(msgs(), "r1")  # a1 tra loi, ghi cache khoa chung
    r = client.ask_one_model(msgs(), "r1", "m1", use_cache=True)
    assert r.cached is False and r.model_key == "m1"
    r = client.ask_one_model(msgs(), "r1", "m1", use_cache=True)
    assert r.cached is True and r.model_key == "m1"
    assert env.llm.models() == ["gemini/a1", "mistral/m1"]


def test_one_model_via_fake_backend(fake_usage):
    with use_fake(FakeLLM([{"name": "x", "qty": 1}])) as fake:
        r = llm.ask_one_model(msgs(), "analysis", "groq_gpt_oss_120b", json_schema=Item)
    assert (r.model_key, r.provider, r.data.qty) == ("groq_gpt_oss_120b", "fake", 1)
    assert fake.calls[0]["model_key"] == "groq_gpt_oss_120b"


# ---------- (j) an danh ----------

def test_j_anonymize_drop_and_mask():
    df = pd.DataFrame({
        "ten": ["An", "Bình", "An", None],
        "sdt": ["090", "091", "092", "093"],
        "diem": [7, 8, 9, 10],
    })
    original = df.copy()
    out = anonymize(df, drop=["sdt"], mask=["ten"])
    assert list(out.columns) == ["ten", "diem"]
    assert out["ten"].iloc[:3].tolist() == ["ten_0001", "ten_0002", "ten_0001"]
    assert out["ten"].iloc[3] is None or pd.isna(out["ten"].iloc[3])
    assert out["diem"].tolist() == [7, 8, 9, 10]
    pd.testing.assert_frame_equal(df, original)  # df goc khong doi


def test_j_anonymize_unknown_column():
    df = pd.DataFrame({"a": [1]})
    with pytest.raises(ValueError, match="khong_co"):
        anonymize(df, mask=["khong_co"])
    with pytest.raises(ValueError, match="cot_x"):
        anonymize(df, drop=["cot_x"])


def test_j_anonymize_nan_kept():
    df = pd.DataFrame({"x": [1.5, float("nan"), 1.5]})
    out = anonymize(df, mask=["x"])
    assert out["x"].iloc[0] == out["x"].iloc[2] == "x_0001"
    assert pd.isna(out["x"].iloc[1])


# ---------- (k)-(l) FakeLLM ----------

@pytest.fixture
def fake_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "_path_override", None)
    path = tmp_path / "fake_usage.jsonl"
    set_usage_log_path(path)
    return path


def test_k_fake_follows_script_and_restores_backend(fake_usage):
    err = LLMRequestError("bị từ chối", model_key="x", provider="y", status_code=401)
    fake = FakeLLM(["xin chào", {"name": "lê", "qty": 3}, err])
    assert llm._backend is None
    with use_fake(fake) as f:
        assert f is fake
        r = llm.ask_model(msgs(), "r1")
        assert (r.text, r.provider, r.model_key) == ("xin chào", "fake", "fake")
        r = llm.ask_model(msgs(), "r1", json_schema=Item, task_id="t1")
        assert r.data == Item(name="lê", qty=3) and r.task_id == "t1"
        with pytest.raises(LLMRequestError):
            llm.ask_model(msgs(), "r1")
        with pytest.raises(AssertionError, match="hết"):
            llm.ask_model(msgs(), "r1")
    assert llm._backend is None
    assert len(fake.calls) == 4
    assert fake.calls[1]["json_schema"] is Item

    lines = usage_lines(fake_usage)
    assert [line["status"] for line in lines] == ["ok", "ok", "request_error"]
    assert all(line["extra"]["provider"] == "fake" for line in lines)


def test_k_fake_restores_backend_on_exception(fake_usage):
    with pytest.raises(RuntimeError):
        with use_fake(FakeLLM([])):
            raise RuntimeError("x")
    assert llm._backend is None


def test_k_fake_script_per_role(fake_usage):
    fake = FakeLLM({"analysis": ["phân tích"], "report": ["báo cáo"]})
    assert fake.ask_model(msgs(), "report").text == "báo cáo"
    assert fake.ask_model(msgs(), "analysis").text == "phân tích"
    with pytest.raises(AssertionError, match="report"):
        fake.ask_model(msgs(), "report")


def test_k_fake_validates_schema(fake_usage):
    fake = FakeLLM(['{"name": "x"}'])
    with pytest.raises(LLMChainExhausted) as ei:
        fake.ask_model(msgs(), "r1", json_schema=Item)
    assert ei.value.attempts[0]["reason"] == "json_invalid"


def test_k_fake_by_schema_repeats_regardless_of_order(fake_usage):
    fake = FakeLLM(["theo thứ tự"], by_schema={Item: {"name": "x", "qty": 1}})
    assert fake.ask_model(msgs(), "r1", json_schema=Item).data == Item(name="x", qty=1)
    assert fake.ask_model(msgs(), "r1").text == "theo thứ tự"  # khong tieu kich ban
    assert fake.ask_model(msgs(), "r1", json_schema=Item).data == Item(name="x", qty=1)


def test_k_fake_by_schema_other_schema_is_out_of_script_not_exhausted(fake_usage):
    class Other(BaseModel):
        x: int

    with pytest.raises(AssertionError, match="hết"):
        FakeLLM(by_schema={Item: {"name": "x", "qty": 1}}).ask_model(msgs(), "r1", json_schema=Other)


def test_chain_exhausted_json_invalid_only():
    bad = {"model_key": "a", "reason": "json_invalid", "status_code": None}
    limit = {"model_key": "b", "reason": "rate_limit", "status_code": 429}
    assert LLMChainExhausted("x", attempts=[bad, bad]).json_invalid_only
    assert not LLMChainExhausted("x", attempts=[bad, limit]).json_invalid_only
    assert not LLMChainExhausted("x").json_invalid_only


def test_l_fake_chain_exhausted(fake_usage):
    exc = LLMChainExhausted(
        "hết chuỗi", attempts=[{"model_key": "a1", "reason": "rate_limit", "status_code": 429}],
        next_available_at=T0,
    )
    with use_fake(FakeLLM([exc])):
        with pytest.raises(LLMChainExhausted) as ei:
            llm.ask_model(msgs(), "r1")
    assert ei.value is exc and ei.value.next_available_at == T0
    assert usage_lines(fake_usage)[0]["status"] == "chain_exhausted"
