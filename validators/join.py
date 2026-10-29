"""Kiem mot phep ghep bang TRUOC khi ghep that (khong merge).

Khoa null khong bao gio khop (giong SQL). Luu y: pd.merge mac dinh ghep null voi null, nen expected_rows
bang len(pd.merge(left, right.dropna(subset=right_on), ...)).
"""

import numpy as np
import pandas as pd

import config
from validators.result import DEFAULT_MAX_EXAMPLES, labelled, make_result, pct, py, take_examples

HOWS = ("left", "inner")
_INT_AS_FLOAT_RE = r"^(\d+)\.0+$"


def normalize_keys(series):
    """Chuan hoa khoa de phan tich: ep chuoi, strip, upper, bo duoi ".0" cua chuoi chi gom chu so
    ("123.0" -> "123"; "1.50", "A1.0" giu nguyen). Khong bo so 0 o dau ("007" van la "007"). Null giu null."""
    s = series.astype(object)
    if not s.notna().any():
        return s
    s = s.where(s.isna(), s.astype(str))
    return s.str.strip().str.upper().str.replace(_INT_AS_FLOAT_RE, r"\1", regex=True)


def _type_group(s):
    """numeric | datetime | string | None (toan null)."""
    if pd.api.types.is_bool_dtype(s):
        return "string"
    if pd.api.types.is_numeric_dtype(s):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "datetime"
    values = s.dropna()
    if values.empty:
        return None
    inferred = pd.api.types.infer_dtype(values, skipna=True)
    if inferred in ("integer", "floating", "mixed-integer-float", "decimal"):
        return "numeric"
    if inferred in ("datetime", "datetime64", "date"):
        return "datetime"
    return "string"


def _as_list(cols):
    return [cols] if isinstance(cols, str) else list(cols)


def _counts(frame):
    """So lan xuat hien moi khoa (frame da bo dong co khoa null)."""
    return frame.groupby(list(frame.columns), sort=False).size()


def _lookup(counts, frame):
    """So lan xuat hien trong counts cua khoa tung dong cua frame (0 neu khong co)."""
    if frame.empty:
        return np.zeros(0, dtype=np.int64)
    idx = pd.Index(frame.iloc[:, 0]) if frame.shape[1] == 1 else pd.MultiIndex.from_frame(frame)
    return counts.reindex(idx).fillna(0).to_numpy(dtype=np.int64)


def _unmatched(left, right):
    """(so lan khop cua tung dong trai, so dong phai khong co ben trai)."""
    c = _lookup(_counts(right), left)
    right_unmatched = int((_lookup(_counts(left), right) == 0).sum())
    return c, right_unmatched


def _distinct_rows(frame, mask, max_examples):
    rows = frame[mask].drop_duplicates().head(max_examples) if max_examples else frame.iloc[:0]
    return take_examples(rows.iloc[:, 0] if rows.shape[1] == 1 else map(tuple, rows.itertuples(index=False)),
                         max_examples)


def join_check(left, right, left_on, right_on, how="left", expect_right_unique=True,
               unmatched_threshold=None, max_examples=DEFAULT_MAX_EXAMPLES, label=None):
    """Du bao ket qua ghep left voi right theo khoa (don hoac ghep) va gan co neu dang ngo."""
    if how not in HOWS:
        raise ValueError(f"how phải là một trong {HOWS}, nhận được {how!r}")
    left_on, right_on = _as_list(left_on), _as_list(right_on)
    if not left_on or len(left_on) != len(right_on):
        raise ValueError(f"left_on và right_on phải cùng số cột (>0): {left_on} / {right_on}")
    for side, df, cols in (("trái", left, left_on), ("phải", right, right_on)):
        missing = [c for c in cols if c not in df.columns]
        if missing:
            raise ValueError(f"Bảng {side} không có cột khóa {missing}")
    threshold = config.JOIN_UNMATCHED_FLAG_RATIO if unmatched_threshold is None else unmatched_threshold

    lk = left[left_on].reset_index(drop=True)
    rk = right[right_on].reset_index(drop=True)
    lk.columns = rk.columns = [f"k{i}" for i in range(len(left_on))]
    left_null = lk.isna().any(axis=1).to_numpy()
    right_null = rk.isna().any(axis=1).to_numpy()

    types, mismatch = [], False
    raw_l, raw_r, norm_l, norm_r = {}, {}, {}, {}
    for col in lk.columns:
        gl, gr = _type_group(lk[col]), _type_group(rk[col])
        types.append({"left": gl, "right": gr})
        pair_mismatch = gl is not None and gr is not None and gl != gr
        mismatch |= pair_mismatch
        if pair_mismatch:  # van phan tich: ep ca hai ve chuoi
            raw_l[col], raw_r[col] = lk[col].where(lk[col].isna(), lk[col].astype(str)), \
                rk[col].where(rk[col].isna(), rk[col].astype(str))
        else:
            raw_l[col], raw_r[col] = lk[col], rk[col]
        is_text = pair_mismatch or "string" in (gl, gr)
        norm_l[col] = normalize_keys(raw_l[col]) if is_text else raw_l[col]
        norm_r[col] = normalize_keys(raw_r[col]) if is_text else raw_r[col]

    def frames(parts):
        return pd.DataFrame(parts)

    L, R = frames(raw_l)[~left_null], frames(raw_r)[~right_null]
    c, right_unmatched = _unmatched(L, R)
    c_norm = _lookup(_counts(frames(norm_r)[~right_null]), frames(norm_l)[~left_null])

    n_left, n_right = len(left), len(right)
    n_left_null, n_right_null = int(left_null.sum()), int(right_null.sum())
    matched = c > 0
    expected_rows = int(np.maximum(c, 1).sum()) + n_left_null if how == "left" else int(c.sum())
    extra_rows = int((c[matched] - 1).sum())
    unmatched_raw = int((~matched).sum())
    unmatched_norm = int((c_norm == 0).sum())
    recoverable = max(0, unmatched_raw - unmatched_norm)
    denom = n_left - n_left_null
    unmatched_ratio = unmatched_raw / denom if denom else 0.0
    right_counts = _counts(R)
    dup_keys = right_counts[right_counts > 1]

    flags, problems = [], []
    if expect_right_unique and len(dup_keys):
        flags.append("right_duplicate_keys")
        problems.append(f"bên phải có {len(dup_keys)} khóa trùng, kết quả tăng {extra_rows} dòng")
    if unmatched_ratio > threshold:
        flags.append("unmatched")
        problems.append(f"{unmatched_raw}/{denom} dòng bên trái không khớp ({pct(unmatched_ratio)}), "
                        f"{recoverable} dòng khớp được nếu chuẩn hóa khóa")
    if mismatch:
        flags.append("key_type_mismatch")
        problems.append("hai bên khác nhóm kiểu khóa (" + ", ".join(f"{t['left']}/{t['right']}" for t in types) + ")")
    info = [f"dự kiến {expected_rows} dòng (trái {n_left})"]
    if n_left_null or n_right_null:
        info.append(f"khóa null: trái {n_left_null}, phải {n_right_null}")
    if right_unmatched:
        info.append(f"{right_unmatched} dòng bên phải không có bên trái")
    reason = "; ".join(problems + info)
    name, reason = labelled(label, "join_check", reason)

    examples = {
        "left_unmatched": _distinct_rows(L, ~matched, max_examples),
        "right_duplicate": take_examples((py(k) for k in dup_keys.index), max_examples),
    }
    details = {
        "how": how, "left_on": left_on, "right_on": right_on, "n_left": n_left, "n_right": n_right,
        "expected_rows": expected_rows, "left_null_keys": n_left_null, "right_null_keys": n_right_null,
        "left_unmatched_raw": unmatched_raw, "left_unmatched_normalized": unmatched_norm,
        "recoverable": recoverable, "unmatched_ratio": round(unmatched_ratio, 6), "unmatched_threshold": threshold,
        "right_duplicate_keys": int(len(dup_keys)), "extra_rows": extra_rows, "right_unmatched": right_unmatched,
        "key_type_mismatch": mismatch, "key_types": types, "flags": flags, "examples": examples,
    }
    return make_result(name, not flags, reason, details)
