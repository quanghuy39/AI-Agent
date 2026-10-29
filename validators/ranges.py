"""Kiem gia tri nam trong khoang [min_value, max_value]."""

import pandas as pd

import config
from validators.result import DEFAULT_MAX_EXAMPLES, labelled, make_result, pct, py

MAX_DISTINCT_EXAMPLES = 10


def _value_examples(values, k):
    if not k:
        return []
    return [{"value": py(v), "count": int(n)} for v, n in values.value_counts().head(k).items()]


def range_check(values, min_value=None, max_value=None, allow_null=True, inclusive=True,
                max_out_ratio=None, max_examples=DEFAULT_MAX_EXAMPLES, label=None):
    """values doi sang so bang pd.to_numeric(errors="coerce"); gia tri khac null khong doi duoc = non_numeric.
    Co khi ty le ngoai khoang > max_out_ratio (mac dinh RANGE_OUT_FLAG_RATIO), co null ma allow_null=False,
    hoac co non_numeric."""
    if min_value is None and max_value is None:
        raise ValueError("range_check cần ít nhất một cận (min_value hoặc max_value)")
    if min_value is not None and max_value is not None and min_value > max_value:
        raise ValueError(f"min_value ({min_value}) lớn hơn max_value ({max_value})")
    s = values if isinstance(values, pd.Series) else pd.Series(list(values), dtype=object)
    s = s.reset_index(drop=True)
    limit = config.RANGE_OUT_FLAG_RATIO if max_out_ratio is None else max_out_ratio
    nums = pd.to_numeric(s, errors="coerce")
    null = s.isna()
    numeric = nums.notna()
    non_numeric = ~null & ~numeric

    below = pd.Series(False, index=s.index)
    above = pd.Series(False, index=s.index)
    if min_value is not None:
        below = numeric & ((nums < min_value) if inclusive else (nums <= min_value))
    if max_value is not None:
        above = numeric & ((nums > max_value) if inclusive else (nums >= max_value))
    out = below | above
    n_numeric = int(numeric.sum())
    out_ratio = int(out.sum()) / n_numeric if n_numeric else 0.0

    problems = []
    if out_ratio > limit:
        problems.append(f"{int(out.sum())}/{n_numeric} giá trị ngoài khoảng ({pct(out_ratio)}): "
                        f"{int(below.sum())} dưới, {int(above.sum())} trên")
    if not allow_null and null.any():
        problems.append(f"{int(null.sum())} giá trị null (không cho phép null)")
    if non_numeric.any():
        problems.append(f"{int(non_numeric.sum())} giá trị không phải số")
    lo = "-∞" if min_value is None else f"{min_value:g}"
    hi = "+∞" if max_value is None else f"{max_value:g}"
    brackets = "[]" if inclusive else "()"
    reason = "; ".join(problems) if problems else \
        f"{n_numeric} giá trị trong khoảng {brackets[0]}{lo}, {hi}{brackets[1]}"
    name, reason = labelled(label, "range_check", reason)
    k = min(max_examples, MAX_DISTINCT_EXAMPLES)
    return make_result(name, not problems, reason, {
        "n": int(len(s)), "below": int(below.sum()), "above": int(above.sum()), "null": int(null.sum()),
        "non_numeric": int(non_numeric.sum()), "out_ratio": round(out_ratio, 6), "max_out_ratio": limit,
        "found_min": py(nums.min()) if n_numeric else None, "found_max": py(nums.max()) if n_numeric else None,
        "min_value": min_value, "max_value": max_value, "inclusive": inclusive,
        "examples": {"out_of_range": _value_examples(s[out], k), "non_numeric": _value_examples(s[non_numeric], k)},
    })
