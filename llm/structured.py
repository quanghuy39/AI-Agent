"""Yeu cau va kiem tra JSON theo lop pydantic. Khong dung tool calling hay response_format."""

import json
import re

from pydantic import BaseModel, ValidationError

_FENCE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL | re.IGNORECASE)
MAX_ERRORS_SHOWN = 5


def check_schema(json_schema):
    if json_schema is not None and not (isinstance(json_schema, type) and issubclass(json_schema, BaseModel)):
        raise TypeError(f"json_schema phải là một lớp pydantic BaseModel hoặc None, nhận {json_schema!r}.")


def schema_json(json_schema):
    """JSON Schema dang chuoi on dinh, dung cho chi dan va khoa cache. None neu khong co schema."""
    if json_schema is None:
        return None
    return json.dumps(json_schema.model_json_schema(), sort_keys=True, ensure_ascii=False)


def instruction_message(json_schema):
    return {
        "role": "user",
        "content": (
            "Respond with ONLY one JSON object that conforms to the JSON Schema below. "
            "No explanation, no extra text, no markdown.\n"
            f"JSON Schema:\n{schema_json(json_schema)}"
        ),
    }


def retry_message(error_summary):
    return {
        "role": "user",
        "content": (
            f"Your previous reply was not valid JSON for the schema: {error_summary}\n"
            "Reply again with ONLY the corrected JSON object."
        ),
    }


def strip_fence(text):
    m = _FENCE.match(text or "")
    return m.group(1) if m else (text or "")


def parse(text, json_schema):
    """Tra ve doi tuong da validate. Nem ValidationError neu sai."""
    return json_schema.model_validate_json(strip_fence(text).strip())


def summarize_error(err):
    """Thong bao loi validate ngan gon, khong chep lai noi dung cau tra loi."""
    if not isinstance(err, ValidationError):
        return f"{type(err).__name__}"
    items = []
    for e in err.errors()[:MAX_ERRORS_SHOWN]:
        loc = ".".join(str(p) for p in e.get("loc", ())) or "(root)"
        items.append(f"{loc}: {e.get('msg')}")
    more = err.error_count() - len(items)
    if more > 0:
        items.append(f"... và {more} lỗi khác")
    return "; ".join(items)
