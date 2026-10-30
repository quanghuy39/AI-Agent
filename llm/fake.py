"""FakeLLM: thay cho backend that trong test. Khong goi mang."""

import json
import time
from dataclasses import dataclass

from pydantic import ValidationError

from llm import structured, usage
from llm.client import LLMResult
from llm.errors import LLMChainExhausted, LLMRequestError

FAKE = "fake"


@dataclass
class FromModel:
    """Phan tu kich ban tra loi nhu thu model_key dap: gia lap doi model. cached=True: gia lap trung cache."""
    model_key: str
    item: object
    cached: bool = False


class FakeLLM:
    """Tra loi theo kich ban.

    script: danh sach phan tu (chuoi van ban, dict -> json.dumps, hoac exception de nem ra),
            hoac dict role -> danh sach nhu tren.
    usage_log_path: file usage mac dinh khi chua goi set_usage_log_path. None: lay theo llm.yaml.
    by_schema: {schema pydantic: phan tu nhu tren}. Luot goi co schema nay luon nhan phan tu do, khong theo thu tu
               va khong het (dung cho kich ban chay tiep duoc tu pha bat ky).
    Phan tu boc FromModel: model_switched tinh nhu client that (so voi model da gan theo task_id).
    """

    def __init__(self, script=(), usage_log_path=None, by_schema=None):
        if isinstance(script, dict):
            self._script = {role: list(items) for role, items in script.items()}
        else:
            self._script = {None: list(script)}
        self._usage_log_path = usage_log_path
        self._by_schema = dict(by_schema or {})
        self._task_models = {}
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

    def set_task_model(self, task_id, model_key):
        if model_key is None:
            self._task_models.pop(task_id, None)
        else:
            self._task_models[task_id] = model_key

    def _bind(self, task_id, model_key, cached):
        """(model_switched, model truoc do), cung quy tac voi client: trung cache khong tinh, khong ghi de."""
        previous = self._task_models.get(task_id)
        if task_id is None:
            return False, None
        if cached:
            self._task_models.setdefault(task_id, model_key)
            return False, previous
        self._task_models[task_id] = model_key
        return previous is not None and previous != model_key, previous

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
        item = self._by_schema[json_schema] if json_schema in self._by_schema else self._next(role)
        cached = False
        if isinstance(item, FromModel):
            model_key, cached, item = item.model_key, item.cached, item.item
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

        switched, previous = self._bind(task_id, model_key, cached)
        result = LLMResult(
            text=text, role=role, model_key=model_key, provider=FAKE, litellm_model=FAKE,
            prompt_tokens=0, completion_tokens=0, finish_reason="stop",
            fallback_count=0, fallback_reasons=[], latency_s=round(time.perf_counter() - t0, 3),
            data=data, json_retries=0, cached=cached, task_id=task_id, model_switched=switched,
            previous_model=previous,
        )
        self._log(**common, status="ok", prompt_tokens=0, completion_tokens=0, latency_s=result.latency_s)
        return result
