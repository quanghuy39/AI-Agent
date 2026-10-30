class LLMError(Exception):
    """Lop cha cho moi loi cua goi llm/."""


class LLMConfigError(LLMError):
    """Loi cau hinh: thieu khoa API, yaml sai, role khong ton tai."""


class LLMRequestError(LLMError):
    """Nha cung cap tu choi yeu cau (401, 403, 400). Khong phai loi tam thoi, khong chuyen model."""

    def __init__(self, message, model_key=None, provider=None, status_code=None):
        super().__init__(message)
        self.model_key = model_key
        self.provider = provider
        self.status_code = status_code


class LLMChainExhausted(LLMError):
    """Da thu het chuoi model cua role ma khong model nao tra loi.

    attempts: [{model_key, reason, status_code}, ...] theo thu tu da duyet.
    next_available_at: thoi diem (epoch giay) som nhat co model trong chuoi het cooldown, None neu khong co.
    """

    def __init__(self, message, attempts=None, next_available_at=None):
        super().__init__(message)
        self.attempts = attempts or []
        self.next_available_at = next_available_at

    @property
    def json_invalid_only(self):
        """True: moi model da thu deu tra loi sai JSON/schema, khong phai loi han muc hay ket noi."""
        return bool(self.attempts) and all(a.get("reason") == "json_invalid" for a in self.attempts)
