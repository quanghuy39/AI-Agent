"""Kiem tra profiling/quality.py. Du lieu tu tao trong code, khong goi mang (R2 duoc thay bang monkeypatch).

Chay: python -m pytest tests/test_profiling_quality.py -v
"""

import io
import json

import pandas as pd
import pytest

from ledger import Ledger
from profiling import quality
from profiling.quality import profile_table
from storage import r2


def csv(text):
    """Doc giong du lieu that: moi cot dang chuoi, null mac dinh cua pandas."""
    return pd.read_csv(io.StringIO(text.strip() + "\n"), dtype=str)


def col(df, name, **kw):
    return profile_table(df, "T", **kw)["columns"][name]


# ---------- khoa va trung lap ----------

def test_duplicate_keys_and_exact_duplicates():
    df = csv("""
customer_id,name
C1,An
C1,An
C2,Bình
C2,Chi
C3,Dũng
""")
    p = profile_table(df, "CUSTOMER", key_cols=["customer_id"])
    assert (p["n_rows"], p["n_cols"], p["key_guessed"]) == (5, 2, False)
    assert p["duplicate_key_count"] == 2      # C1, C2
    assert p["duplicate_key_rows"] == 2       # moi khoa thua 1 dong
    assert p["exact_duplicate_rows"] == 1     # C1,An lap y het
    assert p["key_conflict_count"] == 1       # C2 cung khoa khac noi dung


def test_same_key_different_content_only():
    df = csv("""
order_id,amount
O1,10
O1,12
O2,5
""")
    p = profile_table(df, "ORDER", key_cols="order_id")
    assert (p["duplicate_key_count"], p["exact_duplicate_rows"], p["key_conflict_count"]) == (1, 0, 1)


def test_composite_key_and_null_key():
    df = csv("""
order_id,line,qty
O1,1,2
O1,2,1
O1,2,1
,1,3
""")
    p = profile_table(df, "ITEM", key_cols=["order_id", "line"])
    assert (p["duplicate_key_count"], p["duplicate_key_rows"], p["key_conflict_count"]) == (1, 1, 0)
    assert p["null_key_rows"] == 1


def test_guess_key_when_none():
    df = csv("""
name,order_id,customer_id
a,O1,C1
b,O2,C1
""")
    p = profile_table(df, "ORDER")
    assert p["key_cols"] == ["order_id"] and p["key_guessed"] is True
    assert p["duplicate_key_count"] == 0

    p = profile_table(csv("a,b\n1,2"), "X")
    assert p["key_cols"] is None and p["key_guessed"] is False and p["duplicate_key_count"] is None


def test_unknown_key_column():
    with pytest.raises(ValueError, match="khong_co"):
        profile_table(csv("a\n1"), "X", key_cols=["khong_co"])


# ---------- null va kieu ----------

def test_nulls():
    df = csv("""
id,amount
1,10
2,
3,NA
4,7
""")
    c = col(df, "amount")
    assert (c["null_count"], c["null_ratio"]) == (2, 0.5)
    assert c["inferred_type"] == "integer" and (c["min"], c["max"]) == (7, 10)


def test_integer_stored_as_float():
    c = col(csv("qty\n4.0\n5.0\n3"), "qty")
    assert c["inferred_type"] == "integer"
    assert "integer_stored_as_float" in c["notes"]
    assert (c["min"], c["max"]) == (3, 5)


def test_float_and_string():
    assert col(csv("p\n1.5\n2\n3.25"), "p")["inferred_type"] == "float"
    c = col(csv("s\nabc\n12\nxyz"), "s")
    assert c["inferred_type"] == "string" and c["min"] is None and c["max"] is None


def test_quotes_and_spaces_stripped_for_inference():
    c = col(pd.DataFrame({"x": [" 12 ", "'13'", '"14"']}, dtype=str), "x")
    assert c["inferred_type"] == "integer" and (c["min"], c["max"]) == (12, 14)


def test_whitespace_count_and_raw_unique():
    c = col(csv('cust\n"CUST001 "\nCUST001\nCUST002'), "cust")
    assert c["inferred_type"] == "string"
    assert c["whitespace_count"] == 1
    assert c["n_unique"] == 3  # "CUST001 " khac "CUST001"
    assert {"value": "CUST001 ", "count": 1} in c["top_values"]


def test_eight_digit_non_dates_are_integers():
    c = col(csv("code\n99999999\n12345678\n87654321"), "code")
    assert c["inferred_type"] == "integer"


# ---------- ngay ----------

def test_dd_mm_yyyy():
    c = col(csv("d\n15-04-2025\n01-01-2024\n31-12-2025"), "d")
    assert c["inferred_type"] == "date" and c["date_format"] == "%d-%m-%Y"
    assert (c["min"], c["max"]) == ("2024-01-01", "2025-12-31")
    assert c["date_format_ratios"] == {"%d-%m-%Y": 1.0}
    assert c["flags"] == [] and c["date_invalid_count"] == 0


def test_ddmmyyyy_keeps_leading_zero():
    df = csv("d\n05042025\n15042025\n01012024")
    assert df["d"].tolist()[0] == "05042025"  # doc dang chuoi giu so 0 dau
    c = col(df, "d")
    assert c["inferred_type"] == "date" and c["date_format"] == "%d%m%Y"
    assert (c["min"], c["max"]) == ("2024-01-01", "2025-04-15")


def test_yyyymmdd():
    c = col(csv("d\n20250415\n20240101"), "d")
    assert c["inferred_type"] == "date" and c["date_format"] == "%Y%m%d"


def test_ambiguous_day_month():
    c = col(csv("d\n01/02/2025\n03/04/2025\n05/06/2025"), "d")
    assert c["inferred_type"] == "date"
    assert "ambiguous_day_month" in c["flags"]
    assert c["date_format"] is None and c["min"] is None and c["max"] is None
    assert c["date_format_ratios"] == {"%d/%m/%Y": 1.0, "%m/%d/%Y": 1.0}


def test_day_first_not_ambiguous_when_day_over_12():
    c = col(csv("d\n01/02/2025\n25/04/2025\n13/06/2025"), "d")
    assert c["date_format"] == "%d/%m/%Y" and c["flags"] == []


def test_mixed_date_formats():
    c = col(csv("d\n2025-04-15\n2025-04-16\n16-04-2025\n17-04-2025"), "d")
    assert c["inferred_type"] == "date"
    assert "mixed_date_formats" in c["flags"]
    assert c["date_format"] is None and c["min"] is None
    assert c["date_format_ratios"] == {"%Y-%m-%d": 0.5, "%d-%m-%Y": 0.5}


def test_date_invalid_values():
    values = [f"{d:02d}-03-2025" for d in range(1, 29)] * 2 + ["abc", "31-02-2025"]  # 56 dung + 2 rac
    c = col(pd.DataFrame({"d": values}, dtype=str), "d")
    assert c["inferred_type"] == "date" and c["date_format"] == "%d-%m-%Y"
    assert c["date_invalid_count"] == 2
    assert c["date_invalid_examples"] == ["abc", "31-02-2025"]


def test_string_column_has_no_date_fields():
    c = col(csv("s\nabc\nxyz"), "s")
    assert "date_invalid_count" not in c and "date_invalid_examples" not in c


# ---------- thong ke so ----------

def test_numeric_stats_zero_negative_p99():
    import numpy as np
    values = ["-3", "0", "0"] + ["99"] * 97
    c = col(pd.DataFrame({"x": values}, dtype=str), "x")
    assert c["inferred_type"] == "integer"
    assert (c["zero_count"], c["negative_count"]) == (2, 1)
    assert c["p99"] == 99.0 and c["median"] == 99.0
    full = np.array([float(v) for v in values])
    assert c["p01"] == pytest.approx(np.percentile(full, 1))
    assert (c["min"], c["max"]) == (-3, 99)


def test_numeric_stats_float_and_weights():
    c = col(pd.DataFrame({"x": ["1.5", "2.5", "2.5", "-0.5", "0.0"]}, dtype=str), "x")
    assert c["inferred_type"] == "float"
    assert c["median"] == 1.5 and (c["zero_count"], c["negative_count"]) == (1, 1)


def test_string_column_has_no_numeric_stats():
    c = col(csv("s\nabc\nxyz"), "s")
    assert "median" not in c and "p99" not in c


# ---------- cau truc va bien the hoa thuong ----------

def test_pattern_top_finds_minority_lowercase_id():
    values = [f"CUST{i:06d}" for i in range(1, 20)] + ["cust000020"]
    c = col(pd.DataFrame({"customer_id": values}, dtype=str), "customer_id")
    assert c["pattern_top"] == [
        {"pattern": "AAAA999999", "count": 19, "ratio": 0.95},
        {"pattern": "aaaa999999", "count": 1, "ratio": 0.05},
    ]


def test_pattern_on_raw_values_and_unicode():
    c = col(pd.DataFrame({"x": ["Đà Nẵng", "Hà Nội", "AB-12 "]}, dtype=str), "x")
    pats = {p["pattern"] for p in c["pattern_top"]}
    assert "Aa Aaaa" in pats and "Aa Aaa" in pats and "AA-99 " in pats  # giu khoang trang goc


def test_too_many_patterns():
    values = ["A" * i for i in range(1, 60)]
    c = col(pd.DataFrame({"x": values}, dtype=str), "x")
    assert c["pattern_top"] is None
    assert "too_many_patterns" in c["notes"]
    assert c["pattern_n_distinct"] == 59
    assert c["pattern_dominant"] is None and c["pattern_dominant_ratio"] is None
    assert c["pattern_other_count"] is None


def test_pattern_dominant_counts_beyond_top():
    values = [f"CUST{i:06d}" for i in range(96)] + ["cust000001", "cust000002", "Cust000003", "x_1"]
    c = col(pd.DataFrame({"customer_id": values}, dtype=str), "customer_id")
    assert c["pattern_n_distinct"] == 4 and len(c["pattern_top"]) == 3
    assert "a_9" not in {p["pattern"] for p in c["pattern_top"]}
    assert c["pattern_dominant"] == "AAAA999999" and c["pattern_dominant_ratio"] == 0.96
    assert c["pattern_other_count"] == 4  # gom ca cau truc thu 4 (a_9) nam ngoai pattern_top


def test_case_variant_groups():
    c = col(pd.DataFrame({"city": ["Delhi", "delhi", "Mumbai", "Pune", "Delhi"]}, dtype=str), "city")
    assert c["case_variant_groups"] == 1
    assert c["case_variant_examples"] == [["Delhi", "delhi"]]


def test_case_variant_includes_strip_and_limits_examples():
    values = ["A", "a", "B ", "b", "C", "c", "D", "d", "E"]
    c = col(pd.DataFrame({"x": values}, dtype=str), "x")
    assert c["case_variant_groups"] == 4
    assert len(c["case_variant_examples"]) == 3
    assert ["B ", "b"] in c["case_variant_examples"]


def test_no_samples_hides_case_examples_but_keeps_counts():
    df = pd.DataFrame({"city": ["Delhi", "delhi", "Pune"]}, dtype=str)
    c = profile_table(df, "T", include_samples=False)["columns"]["city"]
    assert c["case_variant_groups"] == 1 and "case_variant_examples" not in c
    assert c["pattern_top"] is not None


# ---------- top_values va mau ----------

def test_top_values_small_and_large():
    small = col(csv("ch\nweb\nweb\nstore\nphone\nweb\nstore"), "ch")
    assert small["top_values"] == [
        {"value": "web", "count": 3}, {"value": "store", "count": 2}, {"value": "phone", "count": 1},
    ]
    values = [f"v{i:02d}" for i in range(25)] + ["v00"] * 5 + ["v01"] * 4 + ["v02"] * 3 + ["v03"] * 2
    big = col(pd.DataFrame({"x": values}, dtype=str), "x")
    assert big["n_unique"] == 25
    assert [t["value"] for t in big["top_values"]] == ["v00", "v01", "v02", "v03", "v04"]
    assert big["top_values"][0] == {"value": "v00", "count": 6}


def test_include_samples_false_hides_values():
    df = csv("order_id,ch\nO1,web\nO2,store")
    p = profile_table(df, "T", include_samples=False)
    assert "sample_records" not in p
    assert all("top_values" not in c for c in p["columns"].values())


def test_sample_records_stable():
    df = pd.DataFrame({"id": [f"R{i}" for i in range(100)], "v": [str(i) for i in range(100)]})
    a = profile_table(df, "T", sample_n=5, seed=42)["sample_records"]
    b = profile_table(df, "T", sample_n=5, seed=42)["sample_records"]
    assert a == b and len(a) == 5
    assert profile_table(df.head(3), "T", sample_n=5)["sample_records"].__len__() == 3


def test_report_is_json_serializable():
    df = csv("order_id,d,amount\nO1,15-04-2025,4.0\nO2,,5")
    json.dumps(profile_table(df, "T"), ensure_ascii=False)


# ---------- read_table(dtype) ----------

def test_read_table_dtype_keeps_old_behavior(tmp_path, monkeypatch):
    path = tmp_path / "t.csv"
    path.write_text("code,qty\n05042025,4.0\n00123,\n", encoding="utf-8")
    monkeypatch.setattr(r2, "download_with_cache", lambda key, grader=False: path)

    old = r2.read_table("input/x/t.csv")
    assert old["code"].tolist() == [5042025, 123]  # hanh vi cu: pandas tu doan kieu

    new = r2.read_table("input/x/t.csv", dtype=str)
    assert new["code"].tolist() == ["05042025", "00123"]
    assert new["qty"].iloc[0] == "4.0" and pd.isna(new["qty"].iloc[1])


def test_read_table_parquet_dtype_str(tmp_path, monkeypatch):
    path = tmp_path / "t.parquet"
    pd.DataFrame({"a": [1.0, None], "b": ["x", "y"]}).to_parquet(path)
    monkeypatch.setattr(r2, "download_with_cache", lambda key, grader=False: path)
    df = r2.read_table("input/x/t.parquet", dtype=str)
    assert df["a"].iloc[0] == "1.0" and pd.isna(df["a"].iloc[1])  # null van la null, nhu duong CSV
    assert r2.read_table("input/x/t.parquet")["a"].iloc[0] == 1.0  # mac dinh nhu cu


# ---------- bo du lieu la ----------

def _read_bytes(tmp_path, monkeypatch, data, name="t.csv"):
    path = tmp_path / name
    path.write_bytes(data)
    monkeypatch.setattr(r2, "download_with_cache", lambda key, grader=False: path)
    return r2.read_table(f"input/x/{name}", dtype=str)


def test_unusual_dataset_semicolon_bom_vietnamese(tmp_path, monkeypatch):
    rows = [f"KH{i:03d};Nguyễn Văn {chr(65 + i)};2025-04-{i + 1:02d} 13:45:{i:02d};{i + 1}.250.000"
            for i in range(10)]
    data = ("MaKH;Ten khach;NgayTao;SoTien\n" + "\n".join(rows) + "\n").encode("utf-8-sig")
    df = _read_bytes(tmp_path, monkeypatch, data)
    assert list(df.columns) == ["MaKH", "Ten khach", "NgayTao", "SoTien"]  # BOM khong dinh vao ten cot
    assert df.attrs["csv_format"] == {"delimiter": ";", "encoding": "utf-8-sig", "flags": []}
    assert df["SoTien"].iloc[0] == "1.250.000"

    p = profile_table(df, "KHACH")
    assert p["n_cols"] == 4 and p["key_cols"] == ["MaKH"] and p["key_guessed"] is True
    assert p["key_candidates"] == [{"column": "MaKH", "unique_ratio": 1.0, "hint": "ma"}]  # SoTien khong con la ung vien
    c = p["columns"]["NgayTao"]
    assert c["inferred_type"] == "datetime" and c["date_format"] == "%Y-%m-%d %H:%M:%S"
    assert (c["min"], c["max"]) == ("2025-04-01T13:45:00", "2025-04-10T13:45:09")
    assert p["columns"]["Ten khach"]["inferred_type"] == "string"
    json.dumps(p, ensure_ascii=False)


def test_cp1258_fallback(tmp_path, monkeypatch):
    data = "Tên;Nơi\nĐà;Hà\nAn;Cà Mau\n".encode("cp1258")  # cp1258 khong co chu dung san nhu "ố"
    df = _read_bytes(tmp_path, monkeypatch, data)
    assert df.attrs["csv_format"]["encoding"] == "cp1258"
    assert df["Tên"].tolist() == ["Đà", "An"]


@pytest.mark.parametrize("sep", ["\t", "|", ","])
def test_other_delimiters(tmp_path, monkeypatch, sep):
    data = sep.join(["id", "name", "amount"]) + "\n" + sep.join(["1", "a", "10"]) + "\n" + sep.join(["2", "b", "20"]) + "\n"
    df = _read_bytes(tmp_path, monkeypatch, data.encode("utf-8"))
    assert df.shape == (2, 3) and df.attrs["csv_format"]["delimiter"] == sep


def test_suspect_wrong_delimiter(tmp_path, monkeypatch):
    import csv as csv_mod

    def fail(self, sample, delimiters=None):
        raise csv_mod.Error("không đoán được")

    monkeypatch.setattr(r2.csv.Sniffer, "sniff", fail)  # roi ve "," voi file dung ";"
    df = _read_bytes(tmp_path, monkeypatch, b"a;b\n1;2\n3;4\n")
    assert df.shape[1] == 1
    assert df.attrs["csv_format"]["flags"] == ["suspect_wrong_delimiter"]


def test_paid_amount_and_valid_are_not_keys():
    df = pd.DataFrame({"paid_amount": [str(i) for i in range(10)], "valid": [f"v{i}" for i in range(10)]})
    p = profile_table(df, "T")
    assert p["key_cols"] is None and p["key_candidates"] == []
    assert "no_key_found" in p["notes"]


def test_no_key_found_still_counts_exact_duplicates():
    df = csv("name,city\nAn,HN\nAn,HN\nBinh,HCM")
    p = profile_table(df, "T")
    assert p["key_cols"] is None and p["key_guessed"] is False
    assert p["notes"] == ["no_key_found"]
    assert p["duplicate_key_count"] is None and p["exact_duplicate_rows"] == 1


def test_key_candidate_needs_unique_ratio():
    df = pd.DataFrame({
        "customer_id": ["C1", "C1", "C2", "C2"] * 3,
        "product_code": [f"P{i}" for i in range(12)],
        "OrderID": [f"O{i}" for i in range(11)] + ["O0"],
    })
    p = profile_table(df, "T")
    # customer_id (ty le 0.17) bi loai; "id" uu tien hon "code" du OrderID chi co ty le 11/12
    assert p["key_cols"] == ["OrderID"]
    assert [c["column"] for c in p["key_candidates"]] == ["OrderID", "product_code"]


def test_key_hint_priority_id_over_code():
    df = pd.DataFrame({"code": ["x", "y", "z"], "id": ["1", "2", "3"]})  # cung ty le 1.0, code dung truoc
    p = profile_table(df, "T")
    assert p["key_cols"] == ["id"]
    assert [(c["column"], c["hint"]) for c in p["key_candidates"]] == [("id", "id"), ("code", "code")]


def test_key_same_hint_higher_ratio_then_first_column():
    df = pd.DataFrame({
        "ma_a": [f"a{i}" for i in range(9)] + ["a0"],   # 0.9
        "ma_b": [f"b{i}" for i in range(10)],           # 1.0
        "ma_c": [f"c{i}" for i in range(10)],           # 1.0, dung sau ma_b
    })
    assert profile_table(df, "T")["key_cols"] == ["ma_b"]


def test_float_column_not_key_candidate():
    df = pd.DataFrame({"score_id": ["1.5", "2.25", "3.75"], "name": ["a", "b", "c"]})
    p = profile_table(df, "T")
    assert p["columns"]["score_id"]["inferred_type"] == "float"
    assert p["key_cols"] is None and p["key_candidates"] == [] and "no_key_found" in p["notes"]
    # cot so nguyen van duoc
    assert profile_table(pd.DataFrame({"row_id": ["1", "2", "3"]}), "T")["key_cols"] == ["row_id"]


def test_sotien_not_key_candidate():
    df = pd.DataFrame({"SoTien": ["1.250.000", "2.250.000", "3.250.000"], "SoLuong": ["1", "2", "3"]})
    p = profile_table(df, "T")
    assert p["key_candidates"] == [] and p["key_cols"] is None


def test_key_tie_picks_first_column():
    df = pd.DataFrame({"ma_kh": ["a", "b", "c"], "code": ["x", "y", "z"]})
    assert profile_table(df, "T")["key_cols"] == ["ma_kh"]


@pytest.mark.parametrize("values,fmt,kind", [
    (["2025-04-15T13:45:00", "2025-04-16T08:00:00"], "%Y-%m-%dT%H:%M:%S", "datetime"),
    (["2025-04-15 13:45", "2025-04-16 08:00"], "%Y-%m-%d %H:%M", "datetime"),
    (["15/04/2025 13:45:00", "16/04/2025 08:00:00"], "%d/%m/%Y %H:%M:%S", "datetime"),
    (["15.04.2025", "16.04.2025"], "%d.%m.%Y", "date"),
])
def test_datetime_formats(values, fmt, kind):
    c = col(pd.DataFrame({"t": values}, dtype=str), "t")
    assert (c["inferred_type"], c["date_format"]) == (kind, fmt)


def test_date_like_unknown_format():
    c = col(pd.DataFrame({"t": ["15 Apr 2025", "Apr 16, 2025", "17 April 2025"]}, dtype=str), "t")
    assert c["inferred_type"] == "string"
    assert "date_like_unknown_format" in c["flags"]
    assert "date_like_unknown_format" not in col(csv("s\nabc\nxyz"), "s")["flags"]


# ---------- profile_problem ----------

@pytest.fixture
def fake_r2(tmp_path, monkeypatch):
    files = {
        # 11 dong, 1 dong lap y het -> ty le duy nhat 10/11 >= KEY_MIN_UNIQUE_RATIO
        "input/bai9/ORDER.csv": "order_id,order_date,amount\nO1,15-04-2025,4.0\nO1,15-04-2025,4.0\nO2,16-04-2025,\n"
                                + "".join(f"O{i},1{i - 3}-04-2025,{i}.0\n" for i in range(3, 11)),
        "input/bai9/CUSTOMER.csv": 'customer_id,name\nC1,"An "\nC2,Bình\n',
        "input/bai9/README.md": "# đề bài",
        "input/bai9/answers/key.csv": "x\n1\n",
    }
    for key, text in files.items():
        p = tmp_path / "cache" / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    read_keys = []

    def download(key, grader=False):
        read_keys.append(key)
        return tmp_path / "cache" / key

    monkeypatch.setattr(r2, "get_r2_settings", lambda: {"R2_INPUT_PREFIX": "input/"})
    monkeypatch.setattr(r2, "list_objects", lambda prefix, grader=False: [
        {"key": k, "etag": f"etag-{k.rsplit('/', 1)[-1]}"} for k in files if k.startswith(prefix)
    ])
    monkeypatch.setattr(r2, "download_with_cache", download)
    monkeypatch.setattr(r2, "get_etag", lambda key, cache_dir=None: f"cached-{key.rsplit('/', 1)[-1]}")
    monkeypatch.setattr(quality, "OUTPUT_DIR", tmp_path / "output")
    return read_keys


def test_profile_problem_writes_data_profile(fake_r2, tmp_path):
    led = Ledger(tmp_path / "output" / "bai9")
    led.set_data_profile({"ghi_chu_khac": "giữ nguyên"})

    report = quality.profile_problem("bai9")
    assert set(report["tables"]) == {"ORDER", "CUSTOMER"}
    assert {f["table"]: f["etag"] for f in report["files"]} == {
        "ORDER": "cached-ORDER.csv", "CUSTOMER": "cached-CUSTOMER.csv"}
    assert all((f["delimiter"], f["encoding"]) == (",", "utf-8-sig") for f in report["files"])
    assert {s["key"] for s in report["skipped"]} == {"input/bai9/README.md", "input/bai9/answers/key.csv"}
    assert all("answers" not in k for k in fake_r2)  # khong tai file trong answers/
    assert report["problem_id"] == "bai9" and report["generated_at"]

    order = report["tables"]["ORDER"]
    assert order["key_cols"] == ["order_id"] and order["exact_duplicate_rows"] == 1
    assert order["columns"]["amount"]["notes"] == ["integer_stored_as_float"]

    saved = Ledger(tmp_path / "output" / "bai9").get_data_profile()
    assert saved["ghi_chu_khac"] == "giữ nguyên"
    assert saved["data_quality_report"]["tables"]["CUSTOMER"]["columns"]["name"]["whitespace_count"] == 1


def test_profile_problem_no_samples(fake_r2):
    report = quality.profile_problem("bai9", include_samples=False)
    for t in report["tables"].values():
        assert "sample_records" not in t
        assert all("top_values" not in c for c in t["columns"].values())


@pytest.mark.parametrize("bad", ["answers", "../answers", "bai9/answers", "", "a\\b"])
def test_profile_problem_rejects_bad_ids(bad, fake_r2):
    with pytest.raises(ValueError):
        quality.profile_problem(bad)


def test_format_summary_extra_lines():
    base = {"null_count": 0, "null_ratio": 0.0, "notes": [], "flags": []}
    rich = {
        "table": "KHACH", "n_rows": 100, "n_cols": 5, "key_cols": ["customer_id"], "key_guessed": True,
        "duplicate_key_count": 0, "duplicate_key_rows": 0, "key_conflict_count": 0, "null_key_rows": 0,
        "exact_duplicate_rows": 0, "notes": [], "flags": [],
        "columns": {
            "customer_id": {**base, "inferred_type": "string", "whitespace_count": 2, "case_variant_groups": 1,
                            "case_variant_examples": [["Delhi", "delhi"]],
                            "pattern_top": [{"pattern": "AAAA999999", "count": 95, "ratio": 0.95},
                                            {"pattern": "aaaa999999", "count": 3, "ratio": 0.03},
                                            {"pattern": "Aaaa999999", "count": 1, "ratio": 0.01}],
                            "pattern_dominant": "AAAA999999", "pattern_dominant_ratio": 0.95,
                            "pattern_other_count": 5, "pattern_n_distinct": 4},
            "payment_type": {**base, "inferred_type": "string", "whitespace_count": 0, "case_variant_groups": 0,
                             "pattern_top": [{"pattern": "aaaa", "count": 60, "ratio": 0.6},
                                             {"pattern": "aaaaaa", "count": 40, "ratio": 0.4}],
                             "pattern_dominant": "aaaa", "pattern_dominant_ratio": 0.6,
                             "pattern_other_count": 40, "pattern_n_distinct": 2},
            "qty": {**base, "inferred_type": "integer", "min": -2, "max": 99, "p01": -1.5, "p99": 99.0,
                    "zero_count": 4, "negative_count": 1},
            "price": {**base, "inferred_type": "float", "min": 0.5, "max": 10.25, "p01": 0.5, "p99": 10.0,
                      "zero_count": 0, "negative_count": 0},
            "created": {**base, "inferred_type": "datetime", "date_format": "%Y-%m-%d %H:%M:%S",
                        "date_invalid_count": 2, "min": "2023-02-26T08:00:00", "max": "2026-02-21T17:30:00"},
            "ship_date": {**base, "inferred_type": "date", "date_format": None},
            "note": {**base, "inferred_type": "string", "whitespace_count": 0, "case_variant_groups": 0,
                     "pattern_top": None, "pattern_dominant": None, "pattern_dominant_ratio": None,
                     "pattern_other_count": None, "pattern_n_distinct": 80},
        },
    }
    plain = {**rich, "table": "TRONG", "columns": {"name": {**base, "inferred_type": "string",
                                                             "whitespace_count": 0, "case_variant_groups": 0,
                                                             "pattern_top": [{"pattern": "Aaa", "count": 5, "ratio": 1.0}],
                                                             "pattern_dominant": "Aaa", "pattern_dominant_ratio": 1.0,
                                                             "pattern_other_count": 0, "pattern_n_distinct": 1}}}
    report = {"problem_id": "bai9", "generated_at": "2026-10-29T10:00:00+07:00",
              "tables": {"KHACH": rich, "TRONG": plain}, "skipped": [], "errors": []}

    text = quality.format_summary(report)
    rich_part, plain_part = text.split("== TRONG")
    assert "Cột số: qty (min -2, max 99, p01 -1.5, p99 99, zero_count 4, negative_count 1); " \
           "price (min 0.5, max 10.25, p01 0.5, p99 10)" in rich_part
    assert "Cấu trúc thiểu số: customer_id: 5 giá trị khác cấu trúc chính AAAA999999 " \
           "(4 cấu trúc; top: aaaa999999 (3), Aaaa999999 (1), ...)" in rich_part
    assert "payment_type" not in rich_part.split("Cấu trúc thiểu số")[1].splitlines()[0]  # cot da dang
    assert "Khoảng trắng thừa: customer_id 2" in rich_part
    assert "Biến thể hoa/thường: customer_id 1 nhóm" in rich_part
    assert "Delhi" not in text  # khong in vi du (gia tri that)
    assert "Cột ngày: created %Y-%m-%d %H:%M:%S, từ 2023-02-26T08:00:00 đến 2026-02-21T17:30:00, " \
           "ship_date không xác định\n" in rich_part  # khong co min/max -> chi in dinh dang
    assert "Ngày không đọc được: created 2" in rich_part
    for label in ("Cột số", "Cấu trúc thiểu số", "Khoảng trắng thừa", "Biến thể hoa/thường", "Ngày không đọc được"):
        assert label not in plain_part  # khong co du lieu -> khong in dong


def test_cli_prints_summary(fake_r2, capsys):
    assert quality.main(["bai9", "--no-samples"]) == 0
    out = capsys.readouterr().out
    assert "ORDER" in out and "order_date %d-%m-%Y" in out and "Dòng trùng y hệt: 1" in out
    assert "amount: integer_stored_as_float" in out
