"""Kiem tra vong lap fallback cua llm/client.py. Khong goi mang, khong dung khoa that.

Chay: python -m pytest tests/test_ask_model_fallback.py -v
"""

import logging
from types import SimpleNamespace

import httpx
import pytest

from llm import LLMChainExhausted, LLMConfigError, LLMError, LLMRequestError, client

litellm = client.litellm  # chi lay lop exception, khong import litellm truc tiep

FAKE_KEYS = {
    "GEMINI_API_KEY": "fake-gemini-SECRET-111",
    "MISTRAL_API_KEY": "fake-mistral-SECRET-222",
    "OPENROUTER_API_KEY": "fake-openrouter-SECRET-333",
    "GROQ_API_KEY": "fake-groq-SECRET-444",
}

CONFIG = """
settings:
  enforce_validation: false
  timeout_s: 60
  num_retries: 2
  retry_after_s: 5
  cooldown_s: 60
  cooldown_max_s: 300
  cooldown_not_found_s: 3600
  cooldown_whole_provider: [openrouter]
  allowed_fails: 2

models:
  a1: {provider: gemini, litellm_model: gemini/a1, api_key_env: GEMINI_API_KEY, max_tokens: 100}
  m1: {provider: mistral, litellm_model: mistral/m1, api_key_env: MISTRAL_API_KEY, max_tokens: 100}
  o1: {provider: openrouter, litellm_model: openrouter/o1, api_key_env: OPENROUTER_API_KEY, max_tokens: 100}
  o2: {provider: openrouter, litellm_model: openrouter/o2, api_key_env: OPENROUTER_API_KEY, max_tokens: 100}
  g1: {provider: groq, litellm_model: groq/g1, api_key_env: GROQ_API_KEY, max_tokens: 100}

roles:
  r1:
    chain:
      - {model: a1, validated: false}
      - {model: m1, validated: false}
      - {model: o1, validated: false}
      - {model: g1, validated: false}
  r2:
    chain:
      - {model: m1, validated: false}
      - {model: o2, validated: false}
      - {model: g1, validated: false}
"""

MESSAGES = [{"role": "user", "content": "xin chào"}]
T0 = 1_000_000.0


def ok_response(text="ok"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=7, completion_tokens=3),
    )


def _resp(status):
    return httpx.Response(status, request=httpx.Request("POST", "https://example.invalid"))


def make_error(kind, model, api_key=""):
    if kind == "429":
        return litellm.RateLimitError(f"rate limited key={api_key}", llm_provider="x", model=model)
    if kind == "500":
        return litellm.InternalServerError("boom", llm_provider="x", model=model)
    if kind == "timeout":
        return litellm.Timeout("timeout", model=model, llm_provider="x")
    if kind == "401":
        return litellm.AuthenticationError(f"bad key {api_key}", llm_provider="x", model=model)
    if kind == "400":
        return litellm.BadRequestError("bad request", model=model, llm_provider="x")
    if kind == "context":
        return litellm.ContextWindowExceededError("too long", model=model, llm_provider="x")
    if kind == "403":
        return litellm.PermissionDeniedError("forbidden", llm_provider="x", model=model, response=_resp(403))
    if kind == "404":
        return litellm.NotFoundError("no such model", model=model, llm_provider="x")
    raise AssertionError(kind)


class FakeLLM:
    """Thay cho client._completion. plan: litellm_model -> danh sach hanh vi theo thu tu goi.
    Hanh vi cuoi cung duoc lap lai khi het danh sach. Mac dinh: 'ok'."""

    def __init__(self):
        self.plan = {}
        self.calls = []

    def __call__(self, model, api_key, messages, timeout, max_tokens):
        self.calls.append(model)
        steps = self.plan.get(model, ["ok"])
        n = self.calls.count(model) - 1
        step = steps[min(n, len(steps) - 1)]
        if step == "ok":
            return ok_response(f"answer from {model}")
        if isinstance(step, Exception):
            raise step
        raise make_error(step, model, api_key)


class Clock:
    def __init__(self):
        self.t = T0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = tmp_path / "llm.yaml"
    cfg.write_text(CONFIG, encoding="utf-8")
    monkeypatch.setenv("LLM_CONFIG_PATH", str(cfg))
    for name, value in FAKE_KEYS.items():
        monkeypatch.setenv(name, value)

    monkeypatch.setattr(client, "_state", None)
    monkeypatch.setattr(client, "_cooldown_until", {})
    monkeypatch.setattr(client, "_rate_limit_streak", {})
    monkeypatch.setattr(client, "_fail_counts", {})

    fake, clock = FakeLLM(), Clock()
    monkeypatch.setattr(client, "_completion", fake)
    monkeypatch.setattr(client, "_now", clock.now)
    monkeypatch.setattr(client, "_sleep", clock.sleep)
    return SimpleNamespace(llm=fake, clock=clock)


# (1)
def test_first_model_answers(env):
    r = client.ask_model(MESSAGES, "r1")
    assert r.model_key == "a1"
    assert r.provider == "gemini"
    assert r.litellm_model == "gemini/a1"
    assert r.text == "answer from gemini/a1"
    assert (r.prompt_tokens, r.completion_tokens, r.finish_reason) == (7, 3, "stop")
    assert r.fallback_count == 0 and r.fallback_reasons == []
    assert env.llm.calls == ["gemini/a1"]


# (2)
def test_429_moves_on_and_cools_down(env):
    env.llm.plan["gemini/a1"] = ["429"]
    r = client.ask_model(MESSAGES, "r1")
    assert r.model_key == "m1"
    assert r.fallback_count == 1
    assert r.fallback_reasons == [{"model_key": "a1", "reason": "rate_limit", "status_code": 429}]
    assert env.llm.calls == ["gemini/a1", "mistral/m1"]  # khong thu lai a1
    assert client._cooldown_until["a1"] == T0 + 60

    env.clock.t += 10
    r = client.ask_model(MESSAGES, "r1")
    assert r.model_key == "m1"
    assert r.fallback_reasons == [{"model_key": "a1", "reason": "cooldown", "status_code": None}]
    assert env.llm.calls == ["gemini/a1", "mistral/m1", "mistral/m1"]


# (3)
@pytest.mark.parametrize("kind", ["500", "timeout"])
def test_transient_retries_num_retries_then_moves_on(env, kind):
    env.llm.plan["gemini/a1"] = [kind]
    r = client.ask_model(MESSAGES, "r1")
    assert env.llm.calls == ["gemini/a1"] * 3 + ["mistral/m1"]  # 1 lan dau + num_retries=2
    assert env.clock.sleeps == [5, 5]
    assert r.model_key == "m1"
    assert r.fallback_reasons[0]["reason"] == "transient"
    # 3 lan goi loi trong mot luot chi tinh la 1 lan that bai, chua dat allowed_fails=2
    assert client._fail_counts["a1"] == 1
    assert "a1" not in client._cooldown_until


@pytest.mark.parametrize("kind", ["500", "timeout"])
def test_two_consecutive_transient_rounds_cause_cooldown(env, kind):
    env.llm.plan["gemini/a1"] = [kind]
    client.ask_model(MESSAGES, "r1")
    assert "a1" not in client._cooldown_until

    r = client.ask_model(MESSAGES, "r1")
    assert env.llm.calls.count("gemini/a1") == 6  # luot hai van thu lai du num_retries
    assert r.model_key == "m1"
    assert client._cooldown_until["a1"] == T0 + 20 + 60  # 4 lan cho 5 giay
    assert client._fail_counts["a1"] == 0

    r = client.ask_model(MESSAGES, "r1")
    assert r.fallback_reasons[0] == {"model_key": "a1", "reason": "cooldown", "status_code": None}
    assert env.llm.calls.count("gemini/a1") == 6


def test_success_between_transient_rounds_resets_count(env):
    # luot 1: 3 lan loi; luot 2: thanh cong; luot 3: 3 lan loi
    env.llm.plan["gemini/a1"] = ["500"] * 3 + ["ok", "500"]
    client.ask_model(MESSAGES, "r1")
    assert client._fail_counts["a1"] == 1

    r = client.ask_model(MESSAGES, "r1")
    assert r.model_key == "a1"
    assert "a1" not in client._fail_counts

    client.ask_model(MESSAGES, "r1")
    assert client._fail_counts["a1"] == 1
    assert "a1" not in client._cooldown_until


def test_transient_then_success_resets_fail_count(env):
    env.llm.plan["gemini/a1"] = ["500", "ok"]
    r = client.ask_model(MESSAGES, "r1")
    assert r.model_key == "a1" and r.fallback_count == 0
    assert env.llm.calls == ["gemini/a1", "gemini/a1"]
    assert "a1" not in client._fail_counts
    assert "a1" not in client._cooldown_until


# (4) (5) (6)
@pytest.mark.parametrize("kind,status", [("401", 401), ("400", 400), ("context", 400), ("403", 403)])
def test_request_errors_stop_immediately(env, kind, status):
    env.llm.plan["gemini/a1"] = [kind]
    with pytest.raises(LLMRequestError) as ei:
        client.ask_model(MESSAGES, "r1")
    e = ei.value
    assert (e.model_key, e.provider, e.status_code) == ("a1", "gemini", status)
    assert env.llm.calls == ["gemini/a1"]  # model sau KHONG bi goi, khong thu lai
    assert client._cooldown_until == {}
    assert FAKE_KEYS["GEMINI_API_KEY"] not in str(e)
    assert e.__suppress_context__


# (7)
def test_404_moves_on_with_long_cooldown(env, caplog):
    env.llm.plan["gemini/a1"] = ["404"]
    with caplog.at_level(logging.WARNING, logger="llm"):
        r = client.ask_model(MESSAGES, "r1")
    assert r.model_key == "m1"
    assert r.fallback_reasons == [{"model_key": "a1", "reason": "not_found", "status_code": 404}]
    assert env.llm.calls == ["gemini/a1", "mistral/m1"]
    assert client._cooldown_until["a1"] == T0 + 3600
    assert any("a1" in rec.getMessage() and "404" in rec.getMessage() for rec in caplog.records)


# (8)
def test_whole_chain_429_raises_exhausted_without_keys(env):
    for model in ["gemini/a1", "mistral/m1", "openrouter/o1", "groq/g1"]:
        env.llm.plan[model] = ["429"]
    with pytest.raises(LLMChainExhausted) as ei:
        client.ask_model(MESSAGES, "r1")
    e = ei.value
    assert [a["model_key"] for a in e.attempts] == ["a1", "m1", "o1", "g1"]
    assert all(a["reason"] == "rate_limit" and a["status_code"] == 429 for a in e.attempts)
    assert e.next_available_at == T0 + 60
    text = str(e)
    for key in FAKE_KEYS.values():
        assert key not in text


def test_exhausted_by_cooldown_only(env):
    for model in ["mistral/m1", "openrouter/o2", "groq/g1"]:
        env.llm.plan[model] = ["429"]
    with pytest.raises(LLMChainExhausted):
        client.ask_model(MESSAGES, "r2")
    calls_before = len(env.llm.calls)

    env.clock.t += 30
    with pytest.raises(LLMChainExhausted) as ei:
        client.ask_model(MESSAGES, "r2")
    assert len(env.llm.calls) == calls_before  # khong goi model nao
    assert [a["reason"] for a in ei.value.attempts] == ["cooldown"] * 3
    assert ei.value.next_available_at == T0 + 60


# (9)
def test_openrouter_429_cools_down_whole_provider(env):
    env.llm.plan["openrouter/o1"] = ["429"]
    r = client._ask(MESSAGES, "r1", start_model="o1")
    assert r.model_key == "g1"
    assert client._cooldown_until["o1"] == T0 + 60
    assert client._cooldown_until["o2"] == T0 + 60
    assert "g1" not in client._cooldown_until

    r = client._ask(MESSAGES, "r2", start_model="o2")
    assert r.model_key == "g1"
    assert r.fallback_reasons == [{"model_key": "o2", "reason": "cooldown", "status_code": None}]
    assert "openrouter/o2" not in env.llm.calls


def test_non_whole_provider_429_cools_down_only_that_model(env, monkeypatch):
    env.llm.plan["openrouter/o1"] = ["429"]
    client._get_state().settings["cooldown_whole_provider"] = []
    client._ask(MESSAGES, "r1", start_model="o1")
    assert "o1" in client._cooldown_until
    assert "o2" not in client._cooldown_until


# (10)
def test_cooldown_shared_between_roles(env):
    env.llm.plan["mistral/m1"] = ["429"]
    r = client._ask(MESSAGES, "r1", start_model="m1")
    assert r.model_key == "o1"

    r = client.ask_model(MESSAGES, "r2")
    assert r.model_key == "o2"
    assert r.fallback_reasons == [{"model_key": "m1", "reason": "cooldown", "status_code": None}]
    assert env.llm.calls.count("mistral/m1") == 1


# (11)
def test_rate_limit_cooldown_doubles_and_caps_then_resets(env):
    env.llm.plan["gemini/a1"] = ["429", "429", "429", "429", "ok", "429"]
    expected = [60, 120, 240, 300]  # cooldown_max_s = 300
    for seconds in expected:
        start = env.clock.t
        client.ask_model(MESSAGES, "r1")
        assert client._cooldown_until["a1"] == start + seconds
        env.clock.t = client._cooldown_until["a1"] + 1  # het cooldown

    r = client.ask_model(MESSAGES, "r1")  # thanh cong -> dat lai
    assert r.model_key == "a1"
    assert "a1" not in client._rate_limit_streak

    start = env.clock.t
    client.ask_model(MESSAGES, "r1")
    assert client._cooldown_until["a1"] == start + 60


# (12)
def test_start_model_skips_earlier_models(env):
    r = client._ask(MESSAGES, "r1", start_model="o1")
    assert r.model_key == "o1"
    assert r.fallback_count == 0
    assert env.llm.calls == ["openrouter/o1"]


def test_start_model_not_in_chain(env):
    with pytest.raises(LLMConfigError):
        client._ask(MESSAGES, "r2", start_model="a1")


# (13)
def test_unknown_role(env):
    with pytest.raises(LLMConfigError):
        client.ask_model(MESSAGES, "khong_co")


def test_unknown_error_raises_llm_error_with_class_name(env):
    env.llm.plan["gemini/a1"] = [ValueError("weird")]
    with pytest.raises(LLMError) as ei:
        client.ask_model(MESSAGES, "r1")
    assert type(ei.value) is LLMError
    assert "ValueError" in str(ei.value)
    assert env.llm.calls == ["gemini/a1"]


def test_missing_key_names_variable(env, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY")
    with pytest.raises(LLMConfigError) as ei:
        client.ask_model(MESSAGES, "r1")
    assert "GROQ_API_KEY" in str(ei.value)


def test_empty_text_and_length_are_not_errors(env, monkeypatch):
    resp = ok_response("")
    resp.choices[0].finish_reason = "length"
    monkeypatch.setattr(client, "_completion", lambda **kw: resp)
    r = client.ask_model(MESSAGES, "r1")
    assert (r.text, r.finish_reason, r.model_key) == ("", "length", "a1")
