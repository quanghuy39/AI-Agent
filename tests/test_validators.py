"""Kiem tra goi validators/. Du lieu tu tao trong code, khong goi mang.

Chay: python -m pytest tests/test_validators.py -v
"""

import numpy as np
import pandas as pd
import pytest

from ledger import Ledger
from validators import (join_check, make_result, normalize_keys, range_check, row_count_check, sum_consistency,
                        to_ledger_check)

SECRET = "SECRET_VALUE"


# ---------- result ----------

def test_make_result_and_to_ledger_check(tmp_path):
    ok = make_result("a", True, "đạt", {"x": 1})
    bad = make_result("b", False, "lệch")
    assert ok == {"name": "a", "status": "pass", "reason": "đạt", "details": {"x": 1}}
    assert bad["status"] == "flag" and bad["details"] == {}
    checks = [to_ledger_check(ok), to_ledger_check(join_check(pd.DataFrame({"k": ["1"]}),
                                                                pd.DataFrame({"k": ["2"]}), "k", "k"))]
    assert set(checks[1]) == {"name", "status", "reason"}
    entry = Ledger(tmp_path).add_result("q", "m", {"n": 1}, checks)  # Check(extra="forbid") nhan duoc
    assert [c.status.value for c in entry.checks] == ["pass", "flag"]


# ---------- row_count_check ----------

@pytest.mark.parametrize("expect, before, after, ok", [
    ("same", 10, 10, True), ("same", 10, 9, False),
    ("not_more", 10, 10, True), ("not_more", 10, 11, False),
    ("not_fewer", 10, 10, True), ("not_fewer", 10, 9, False),
])
def test_row_count_expect(expect, before, after, ok):
    assert (row_count_check(before, after, expect=expect)["status"] == "pass") is ok


def test_row_count_free_empty_and_change_threshold():
    r = row_count_check(10, 0)
    assert r["status"] == "flag" and "rỗng" in r["reason"]
    assert row_count_check(100, 105)["status"] == "pass"
    r = row_count_check(100, 120)
    assert r["status"] == "flag" and "chưa khai báo expected_ratio" in r["reason"]
    assert row_count_check(100, 120, change_threshold=0.5)["status"] == "pass"
    assert row_count_check(0, 5)["status"] == "pass"  # before = 0: khong chia cho 0


def test_row_count_expected_ratio():
    assert row_count_check(100, 300, expected_ratio=(2, 4))["status"] == "pass"  # khong dung change_threshold
    assert row_count_check(100, 500, expected_ratio=(2, 4))["status"] == "flag"
    assert row_count_check(100, 90, expect="not_more", expected_ratio=(0.95, 1))["status"] == "flag"
    assert row_count_check(0, 0, expected_ratio=(1, 1))["status"] == "pass"
    r = row_count_check(0, 3, expected_ratio=(1, 2))
    assert r["status"] == "flag" and r["details"]["ratio"] is None


def test_row_count_accepts_len_and_label():
    r = row_count_check(pd.DataFrame({"a": range(4)}), [1, 2, 3, 4], expect="same", label="clean")
    assert r["details"] == {"before": 4, "after": 4, "ratio": 1.0, "expect": "same"}
    assert r["name"] == "row_count_check:clean" and r["reason"].startswith("clean: ")


@pytest.mark.parametrize("kw", [{"expect": "more"}, {"expected_ratio": (2, 1)}, {"expected_ratio": (1,)}])
def test_row_count_bad_args(kw):
    with pytest.raises(ValueError):
        row_count_check(1, 1, **kw)
    with pytest.raises(ValueError):
        row_count_check(object(), 1)


# ---------- join_check ----------

def test_join_clean_pass():
    left = pd.DataFrame({"k": ["A", "B", "B"]})
    right = pd.DataFrame({"k": ["A", "B", "C"]})
    r = join_check(left, right, "k", "k")
    d = r["details"]
    assert r["status"] == "pass"
    assert (d["expected_rows"], d["left_unmatched_raw"], d["right_unmatched"], d["extra_rows"]) == (3, 0, 1, 0)


def test_join_right_duplicates_flag():
    left = pd.DataFrame({"k": ["A", "B"]})
    right = pd.DataFrame({"k": ["A", "A", "A", "B"]})
    r = join_check(left, right, "k", "k")
    d = r["details"]
    assert r["status"] == "flag" and d["flags"] == ["right_duplicate_keys"]
    assert (d["right_duplicate_keys"], d["extra_rows"], d["expected_rows"]) == (1, 2, 4)
    assert "tăng 2 dòng" in r["reason"]
    assert join_check(left, right, "k", "k", expect_right_unique=False)["status"] == "pass"


def test_join_unmatched_threshold_and_nulls():
    left = pd.DataFrame({"k": ["A", "X", None, None]})
    right = pd.DataFrame({"k": ["A", None]})
    r = join_check(left, right, "k", "k")
    d = r["details"]
    assert (d["left_null_keys"], d["right_null_keys"], d["left_unmatched_raw"]) == (2, 1, 1)
    assert d["unmatched_ratio"] == 0.5 and d["flags"] == ["unmatched"]  # null khong tinh vao khong khop
    assert join_check(left, right, "k", "k", unmatched_threshold=0.5)["status"] == "pass"
    nulls_only = join_check(pd.DataFrame({"k": [None, "A"]}), right, "k", "k")
    assert nulls_only["status"] == "pass"  # khoa null khong bi co


def test_join_recoverable_case_and_spaces():
    left = pd.DataFrame({"k": ["a1", " A2 ", "A3", "zz"]})
    right = pd.DataFrame({"k": ["A1", "A2", "A3"]})
    d = join_check(left, right, "k", "k")["details"]
    assert (d["left_unmatched_raw"], d["left_unmatched_normalized"], d["recoverable"]) == (3, 1, 2)


def test_join_recoverable_int_stored_as_float():
    left = pd.DataFrame({"k": ["7.0", "007", "1.50"]})
    right = pd.DataFrame({"k": ["7", "1.5"]})
    d = join_check(left, right, "k", "k")["details"]
    assert d["left_unmatched_raw"] == 3 and d["recoverable"] == 1  # chi "7.0" -> "7"; "007" va "1.50" giu nguyen
    out = normalize_keys(pd.Series(["123.0", "1.50", "A1.0", " 007 ", None])).tolist()
    assert out[:4] == ["123", "1.50", "A1.0", "007"] and pd.isna(out[4])


def test_join_key_type_mismatch():
    left = pd.DataFrame({"k": [1, 2, 3]})
    right = pd.DataFrame({"k": ["1", "2", "3"]})
    r = join_check(left, right, "k", "k")
    d = r["details"]
    assert r["status"] == "flag" and "key_type_mismatch" in d["flags"]
    assert d["left_unmatched_raw"] == 0  # van phan tich bang chuoi
    assert d["key_types"] == [{"left": "numeric", "right": "string"}]


def test_join_composite_keys():
    left = pd.DataFrame({"a": ["1", "1", "2"], "b": ["x", "y", "x"]})
    right = pd.DataFrame({"a2": ["1", "1", "2"], "b2": ["x", "x", "z"]})
    d = join_check(left, right, ["a", "b"], ["a2", "b2"], how="inner")["details"]
    assert d["expected_rows"] == len(pd.merge(left, right, left_on=["a", "b"], right_on=["a2", "b2"]))
    assert (d["right_duplicate_keys"], d["left_unmatched_raw"]) == (1, 2)


@pytest.mark.parametrize("kw", [{"how": "outer"}, {"left_on": ["k", "k"]}, {"left_on": "nope"}])
def test_join_bad_args(kw):
    args = {"left": pd.DataFrame({"k": ["1"]}), "right": pd.DataFrame({"k": ["1"]}),
            "left_on": "k", "right_on": "k", **kw}
    with pytest.raises(ValueError):
        join_check(**args)


def test_join_examples_and_reason_without_values():
    left = pd.DataFrame({"k": [SECRET, f"{SECRET}2", "A", "A"]})
    right = pd.DataFrame({"k": ["A", "A", f"{SECRET}3"]})
    r = join_check(left, right, "k", "k", label="T.k -> P.k")
    assert SECRET not in r["reason"]
    assert r["details"]["examples"]["left_unmatched"] == [SECRET, f"{SECRET}2"]
    assert r["details"]["examples"]["right_duplicate"] == ["A"]
    assert join_check(left, right, "k", "k", max_examples=1)["details"]["examples"]["left_unmatched"] == [SECRET]
    off = join_check(left, right, "k", "k", max_examples=0)["details"]["examples"]
    assert off == {"left_unmatched": [], "right_duplicate": []}


def _random_table(rng, n, n_keys, prefix):
    cols = {}
    for i in range(n_keys):
        cols[f"{prefix}{i}"] = [None if rng.random() < 0.15 else f"K{rng.integers(0, 6)}" for _ in range(n)]
    cols[f"{prefix}_v"] = np.arange(n)
    return pd.DataFrame(cols)


@pytest.mark.parametrize("how", ["left", "inner"])
def test_property_expected_rows_equals_merge(how):
    """Khoa trung, khoa null, khoa khong khop, khoa don va ghep. Ghep that tren khoa GOC (bo khoa null ben phai,
    vi pd.merge mac dinh ghep null voi null con join_check thi khong)."""
    rng = np.random.default_rng(20251029)
    for _ in range(200):
        n_keys = int(rng.integers(1, 3))
        left = _random_table(rng, int(rng.integers(0, 25)), n_keys, "l")
        right = _random_table(rng, int(rng.integers(0, 25)), n_keys, "r")
        lo, ro = [f"l{i}" for i in range(n_keys)], [f"r{i}" for i in range(n_keys)]
        merged = pd.merge(left, right.dropna(subset=ro), left_on=lo, right_on=ro, how=how)
        assert join_check(left, right, lo, ro, how=how)["details"]["expected_rows"] == len(merged)


def test_join_performance_large():
    n = 356_000
    rng = np.random.default_rng(1)
    left = pd.DataFrame({"k": pd.Series(rng.integers(0, 50_000, n)).map(lambda i: f"C{i:06d}")})
    right = pd.DataFrame({"k": [f"C{i:06d}" for i in range(50_000)]})
    import time
    t0 = time.perf_counter()
    assert join_check(left, right, "k", "k")["status"] == "pass"
    assert time.perf_counter() - t0 < 5


# ---------- sum_consistency ----------

def test_sum_consistency_pass_and_flag():
    r = sum_consistency([0.1, 0.2, 0.3], 0.6)
    assert r["status"] == "pass" and r["details"]["diff"] == pytest.approx(0)
    r = sum_consistency(pd.Series([10, 20]), 31)
    assert r["status"] == "flag" and r["details"]["diff"] == -1 and r["details"]["rel_diff"] == pytest.approx(1 / 31)
    assert sum_consistency([10, 20], 31, abs_tol=1)["status"] == "pass"
    assert sum_consistency([100, 1], 100, rel_tol=0.02)["status"] == "pass"
    assert sum_consistency([], 0)["status"] == "pass"


def test_sum_consistency_nulls_flag_separately():
    r = sum_consistency([1, None, 2, float("nan")], 3)
    assert r["status"] == "flag" and "2 giá trị null" in r["reason"] and "lệch" not in r["reason"]
    assert r["details"]["null_parts"] == 2


@pytest.mark.parametrize("parts, total", [(["1", 2], 3), ([True, 1], 2), ([1, 2], "3"), ([1], float("nan"))])
def test_sum_consistency_bad_values(parts, total):
    with pytest.raises(ValueError):
        sum_consistency(parts, total)


# ---------- range_check ----------

def test_range_check_pass_and_out_of_range():
    assert range_check(pd.Series(["1", "3", "5"]), 1, 5)["status"] == "pass"
    r = range_check(pd.Series(["0", "3", "6", "6", None]), 1, 5)
    d = r["details"]
    assert r["status"] == "flag" and (d["below"], d["above"], d["null"], d["n"]) == (1, 2, 1, 5)
    assert (d["found_min"], d["found_max"]) == (0, 6)
    assert d["examples"]["out_of_range"] == [{"value": "6", "count": 2}, {"value": "0", "count": 1}]


def test_range_check_one_sided_inclusive_and_ratio():
    s = pd.Series([1, 2, 3, 4])
    assert range_check(s, min_value=1)["status"] == "pass"
    assert range_check(s, min_value=1, inclusive=False)["details"]["below"] == 1
    assert range_check(s, max_value=3)["status"] == "flag"
    assert range_check(s, max_value=3, max_out_ratio=0.25)["status"] == "pass"


def test_range_check_nulls_and_non_numeric():
    s = pd.Series(["1", None, SECRET])
    r = range_check(s, 0, 10)
    assert r["status"] == "flag" and r["details"]["non_numeric"] == 1 and SECRET not in r["reason"]
    assert r["details"]["examples"]["non_numeric"] == [{"value": SECRET, "count": 1}]
    assert range_check(pd.Series(["1", None]), 0, 10)["status"] == "pass"
    assert range_check(pd.Series(["1", None]), 0, 10, allow_null=False)["status"] == "flag"


def test_range_check_examples_limit_and_no_values_in_reason():
    s = pd.Series([f"{i}" for i in range(98760, 98790)] + [SECRET])
    r = range_check(s, 0, 10, max_examples=50)
    assert len(r["details"]["examples"]["out_of_range"]) == 10  # toi da 10 gia tri khac nhau
    assert range_check(s, 0, 10, max_examples=0)["details"]["examples"]["out_of_range"] == []
    assert "9876" not in r["reason"] and SECRET not in r["reason"]


@pytest.mark.parametrize("kw", [{}, {"min_value": 5, "max_value": 1}])
def test_range_check_bad_args(kw):
    with pytest.raises(ValueError):
        range_check(pd.Series([1]), **kw)
