from contextlib import contextmanager

from llm import client
from llm.anonymize import anonymize
from llm.client import LLMResult
from llm.errors import LLMChainExhausted, LLMConfigError, LLMError, LLMRequestError
from llm.fake import FakeLLM
from llm.usage import set_usage_log_path

_backend = None  # None: dung client that; use_fake() thay tam bang FakeLLM


def ask_model(messages, role, json_schema=None, task_id=None, use_cache=True):
    backend = _backend if _backend is not None else client
    return backend.ask_model(messages, role, json_schema=json_schema, task_id=task_id, use_cache=use_cache)


def ask_one_model(messages, role, model_key, json_schema=None, use_cache=False, task_id=None):
    backend = _backend if _backend is not None else client
    return backend.ask_one_model(
        messages, role, model_key, json_schema=json_schema, use_cache=use_cache, task_id=task_id
    )


@contextmanager
def use_fake(fake):
    """Thay backend cua ask_model bang fake trong khoi with, roi khoi phuc."""
    global _backend
    previous = _backend
    _backend = fake
    try:
        yield fake
    finally:
        _backend = previous


__all__ = [
    "ask_model",
    "ask_one_model",
    "use_fake",
    "set_usage_log_path",
    "anonymize",
    "FakeLLM",
    "LLMResult",
    "LLMError",
    "LLMConfigError",
    "LLMRequestError",
    "LLMChainExhausted",
]
