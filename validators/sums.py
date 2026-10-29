"""Kiem tong cac phan bang tong chung."""

import math
import numbers

import pandas as pd

import config
from validators.result import labelled, make_result


def _is_number(v):
    return isinstance(v, numbers.Real) and not isinstance(v, bool)


def sum_consistency(parts, total, rel_tol=None, abs_tol=None, label=None):
    """Tong parts (bo qua null, co rieng neu co null) so voi total bang math.isclose."""
    s = parts if isinstance(parts, pd.Series) else pd.Series(list(parts), dtype=object)
    if not _is_number(total) or pd.isna(total):
        raise ValueError("total phải là một số (không null)")
    if pd.api.types.is_bool_dtype(s):
        raise ValueError("parts phải là số, nhận được bool")
    nulls = s.isna()
    values = s[~nulls]
    if not pd.api.types.is_numeric_dtype(values):
        bad = sum(1 for v in values if not _is_number(v))
        if bad:
            raise ValueError(f"parts có {bad} phần tử không phải số")
    rel_tol = config.SUM_REL_TOL if rel_tol is None else rel_tol
    abs_tol = config.SUM_ABS_TOL if abs_tol is None else abs_tol

    sum_parts = math.fsum(float(v) for v in values)
    diff = sum_parts - float(total)
    rel_diff = abs(diff) / abs(total) if total else None
    close = math.isclose(sum_parts, float(total), rel_tol=rel_tol, abs_tol=abs_tol)
    n_null = int(nulls.sum())

    problems = []
    if not close:
        rel = f" ({rel_diff:.3g} tương đối)" if rel_diff is not None else ""
        problems.append(f"tổng {len(values)} phần lệch tổng chung {diff:.6g}{rel}")
    if n_null:
        problems.append(f"các phần có {n_null} giá trị null, tổng bỏ qua null")
    reason = "; ".join(problems) if problems else f"tổng {len(values)} phần khớp tổng chung"
    name, reason = labelled(label, "sum_consistency", reason)
    return make_result(name, not problems, reason, {
        "sum_parts": sum_parts, "total": float(total), "diff": diff, "rel_diff": rel_diff,
        "null_parts": n_null, "rel_tol": rel_tol, "abs_tol": abs_tol,
    })
