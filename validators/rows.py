"""Kiem so dong truoc/sau mot buoc xu ly."""

import config
from validators.result import labelled, make_result, pct

EXPECTS = ("same", "not_more", "not_fewer", "free")


def _count(x):
    if isinstance(x, bool):
        raise ValueError("before/after phải là số nguyên hoặc đối tượng có len()")
    if isinstance(x, int):
        return x
    if hasattr(x, "__len__"):
        return len(x)
    if hasattr(x, "item") and float(x.item()).is_integer():  # so nguyen numpy
        return int(x.item())
    raise ValueError("before/after phải là số nguyên hoặc đối tượng có len()")


def row_count_check(before, after, expect="free", expected_ratio=None, change_threshold=None, label=None):
    """expect: same | not_more | not_fewer | free. expected_ratio=(lo, hi): kiem them after/before.
    free khong co expected_ratio: co khi |after-before|/before > change_threshold (mac dinh ROW_COUNT_CHANGE_FLAG)."""
    if expect not in EXPECTS:
        raise ValueError(f"expect phải là một trong {EXPECTS}, nhận được {expect!r}")
    if expected_ratio is not None:
        if len(expected_ratio) != 2 or expected_ratio[0] > expected_ratio[1]:
            raise ValueError(f"expected_ratio phải là (lo, hi) với lo <= hi, nhận được {expected_ratio!r}")
    b, a = _count(before), _count(after)
    if b < 0 or a < 0:
        raise ValueError("số dòng không được âm")
    threshold = config.ROW_COUNT_CHANGE_FLAG if change_threshold is None else change_threshold
    ratio = a / b if b else None
    problems = []

    if expect == "same" and a != b:
        problems.append(f"số dòng đổi từ {b} thành {a} (cần giữ nguyên)")
    elif expect == "not_more" and a > b:
        problems.append(f"số dòng tăng từ {b} lên {a} (không được tăng)")
    elif expect == "not_fewer" and a < b:
        problems.append(f"số dòng giảm từ {b} xuống {a} (không được giảm)")
    elif expect == "free":
        if b > 0 and a == 0:
            problems.append(f"kết quả rỗng (trước có {b} dòng)")
        elif expected_ratio is None and b > 0 and abs(a - b) / b > threshold:
            problems.append(f"số dòng đổi {pct(abs(a - b) / b)} ({b} -> {a}), vượt ngưỡng {pct(threshold)}; "
                            f"chưa khai báo expected_ratio")

    if expected_ratio is not None:
        lo, hi = expected_ratio
        if ratio is None:
            if a != 0:
                problems.append(f"không tính được tỷ lệ (trước 0 dòng, sau {a} dòng)")
        elif not lo <= ratio <= hi:
            problems.append(f"tỷ lệ sau/trước {ratio:.4g} nằm ngoài [{lo:g}, {hi:g}]")

    reason = "; ".join(problems) if problems else f"số dòng {b} -> {a}, đạt"
    name, reason = labelled(label, "row_count_check", reason)
    return make_result(name, not problems, reason,
                       {"before": b, "after": a, "ratio": ratio, "expect": expect})
