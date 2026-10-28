"""FakeLLM: thay cho backend that trong test. Khong goi mang."""

import json
import time

from pydantic import ValidationError

from llm import structured, usage
from llm.client import LLMResult
from llm.errors import LLMChainExhausted, LLMRequestError

FAKE = "fake"


class FakeLLM:
    """Tra loi theo kich ban.

    script: danh sach phan tu (chuoi van ban, dict -> json.dumps, hoac exception de nem ra),
            hoac dict role -> danh sach nhu tren.
    usage_log_path: file usage mac dinh khi chua goi set_usage_log_path. None: lay theo llm.yaml.
    """

    def __init__(self, script, usage_log_path=None):
        if isinstance(script, dict):
            self._script = {role: list(items) for role, items in script.items()}
        else:
            self._script = {None: list(script)}
        self._usage_log_path = usage_log_path
        self.calls = []

    def _next(self, role):
        key = role if None not in self._script else None
        items = self._script.get(key)
        if not items:
            where = f"role '{role}'" if key is not None else "kịch bản"
            raise AssertionError(f"FakeLLM: hết {where} sau {len(self.calls)} lần gọi.")
        return items.pop(0)

    def _default_usage_path(self):
        if self._usage_log_path is not None:
            return self._usage_log_path
        try:
            from llm import client
            return client._load_config(client._config_path())["settings"].get("usage_log_path")
        except Exception:  # noqa: BLE001
            return None

    def _log(self, model_key=FAKE, **fields):
        usage.write_usage(usage.make_record(
            provider=FAKE, model_key=model_key, litellm_model=FAKE,
            fallback_count=0, fallback_reasons=[], model_switched=False, cached=False, json_retries=0,
            network_calls=0, **fields,
        ), self._default_usage_path())

    def ask_model(self, messages, role, json_schema=None, task_id=None, use_cache=True):
        return self._respond(messages, role, json_schema, task_id, use_cache, model_key=None)

    def ask_one_model(self, messages, role, model_key, json_schema=None, use_cache=False, task_id=None):
        """Nhu ask_model; LLMResult.model_key la model_key duoc chi dinh, provider van la 'fake'."""
        return self._respond(messages, role, json_schema, task_id, use_cache, model_key=model_key)

    def _respond(self, messages, role, json_schema, task_id, use_cache, model_key):
        t0 = time.perf_counter()
        structured.check_schema(json_schema)
        self.calls.append({
            "messages": messages, "role": role, "json_schema": json_schema,
            "task_id": task_id, "use_cache": use_cache, "model_key": model_key,
        })
        item = self._next(role)
        model_key = model_key or FAKE
        common = {"role": role, "task_id": task_id, "model_key": model_key}

        if isinstance(item, BaseException):
            if isinstance(item, LLMRequestError):
                status = "request_error"
            elif isinstance(item, LLMChainExhausted):
                status = "chain_exhausted"
            else:
                status = "error"
            self._log(**common, status=status, error_type=type(item).__name__,
                      status_code=getattr(item, "status_code", None),
                      latency_s=round(time.perf_counter() - t0, 3))
            raise item

        text = json.dumps(item, ensure_ascii=False) if isinstance(item, dict) else str(item)
        data = None
        if json_schema is not None:
            try:
                data = structured.parse(text, json_schema)
            except ValidationError as e:
                attempts = [{"model_key": model_key, "reason": "json_invalid", "status_code": None}]
                exc = LLMChainExhausted(
                    f"FakeLLM: câu trả lời không đúng schema {json_schema.__name__}: "
                    f"{structured.summarize_error(e)}",
                    attempts=attempts,
                )
                self._log(**common, status="chain_exhausted", error_type=type(exc).__name__,
                          latency_s=round(time.perf_counter() - t0, 3))
                raise exc from None

        result = LLMResult(
            text=text, role=role, model_key=model_key, provider=FAKE, litellm_model=FAKE,
            prompt_tokens=0, completion_tokens=0, finish_reason="stop",
            fallback_count=0, fallback_reasons=[], latency_s=round(time.perf_counter() - t0, 3),
            data=data, json_retries=0, cached=False, task_id=task_id, model_switched=False,
        )
        self._log(**common, status="ok", prompt_tokens=0, completion_tokens=0, latency_s=result.latency_s)
        return result
