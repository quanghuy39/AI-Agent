"""Dang ket qua chung cua cac validator.

reason (tieng Viet, ngan) CHI chua ten cot, so dem, ty le; KHONG chua gia tri du lieu that.
Gia tri that (vd. khoa khong khop) chi nam trong details["examples"], toi da max_examples (0 = tat).
"""

PASS, FLAG = "pass", "flag"
DEFAULT_MAX_EXAMPLES = 5


def make_result(name, ok, reason, details=None):
    return {"name": name, "status": PASS if ok else FLAG, "reason": reason, "details": details or {}}


def to_ledger_check(result):
    """Dang Check cua so ket qua (ledger.schema.Check): chi name, status, reason."""
    return {"name": result["name"], "status": result["status"], "reason": result["reason"]}


def py(value):
    """Gia tri numpy/pandas -> kieu Python (de ghi JSON)."""
    if isinstance(value, tuple):
        return [py(v) for v in value]
    return value.item() if hasattr(value, "item") else value


def take_examples(values, max_examples):
    """Lay toi da max_examples phan tu dau (da la gia tri khac nhau). max_examples=0 -> []."""
    if not max_examples:
        return []
    return [py(v) for v in list(values)[:max_examples]]


def labelled(label, name, reason):
    """(ten ket qua, reason) co tien to label neu co."""
    return (f"{name}:{label}" if label else name), (f"{label}: {reason}" if label else reason)


def pct(x):
    return f"{x:.1%}"
