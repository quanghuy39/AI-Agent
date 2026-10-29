"""Kiem tra profiling/dictionary.py va phan doi chieu dictionary trong profile_problem.
Du lieu tu tao trong code, khong goi mang (R2 duoc thay bang monkeypatch).

Chay: python -m pytest tests/test_profiling_dictionary.py -v
"""

import io

import pandas as pd
import pytest

from profiling import dictionary as dd
from profiling import quality
from profiling.quality import profile_table
from storage import r2


def csv(text):
    return pd.read_csv(io.StringIO(text.strip() + "\n"), dtype=str)


def dict_table(*rows):
    """rows: (name, raw_type, definition)."""
    tables, _ = dd.parse_dictionary_csv(pd.DataFrame(
        [{"table": "T", "column": n, "type": t, "definition": d} for n, t, d in rows]))
    return tables["T"]


def check(df, *rows, key_source=None):
    t = profile_table(df, "T")
    t["key_source"] = key_source
    return dd.compare_table_with_dictionary(t, dict_table(*rows), "T")


MD = """
# Data dictionary

## Support Ticket

| Cột | Kiểu | Định nghĩa |
|-----|------|-----------|
| ticket_id | INT (PK) | Mã ticket |
| opened | Date | Ngày mở, dd-mm-yyyy |
| broken row | only two |

## Products

| Column | Type | Definition |
| :--- | :--- | ---: |
| product_code | varchar(20) (PK, FK) | Mã sản phẩm |
|  | int | thiếu tên cột |
| price | decimal |  |
"""


# ---------- tim file ----------

def test_find_dictionary_priority_and_warning():
    keys = ["in/b/my_dictionary.csv", "in/b/DATA_DICTIONARY.CSV", "in/b/data_dictionary.md", "in/b/ORDER.csv"]
    f = dd.find_dictionary(keys)
    assert (f["key"], f["format"], f["status"]) == ("in/b/data_dictionary.md", "md", "ok")
    assert len(f["warnings"]) == 1 and "in/b/DATA_DICTIONARY.CSV" in f["warnings"][0]
    assert set(f["dictionary_keys"]) == set(keys) - {"in/b/ORDER.csv"}


def test_find_dictionary_none_and_unsupported():
    assert dd.find_dictionary(["in/b/ORDER.csv", "in/b/README.md"])["status"] == "none"
    f = dd.find_dictionary(["in/b/ORDER.csv", "in/b/Data_Dictionary.xlsx"])
    assert (f["status"], f["key"]) == ("unsupported", "in/b/Data_Dictionary.xlsx")
    assert dd.find_dictionary(["in/b/answers/data_dictionary.md"])["status"] == "none"


# ---------- parse ----------

def test_parse_markdown():
    tables, warnings = dd.parse_dictionary_md(MD)
    assert list(tables) == ["Support Ticket", "Products"]
    cols = tables["Support Ticket"]["columns"]
    assert cols[0] == {"name": "ticket_id", "type": "int", "key": "PK", "definition": "Mã ticket"}
    assert cols[1]["type"] == "date" and cols[1]["key"] is None
    assert [c["name"] for c in cols] == ["ticket_id", "opened"]
    prod = tables["Products"]["columns"]
    assert prod[0]["type"] == "varchar(20)" and prod[0]["key"] == "PK"
    assert [c["name"] for c in prod] == ["product_code", "price"]
    assert len(warnings) == 2  # dong thieu o, dong thieu ten cot
    assert any("2 ô" in w for w in warnings) and any("thiếu tên cột" in w for w in warnings)


def test_parse_markdown_bad_header_and_table_outside_heading():
    text = "| a | b |\n|---|---|\n| x | y |\n\n## T\n\n| Tên | Ghi chú |\n|---|---|\n| a | b |\n"
    tables, warnings = dd.parse_dictionary_md(text)
    assert tables == {} and len(warnings) == 2


def test_parse_csv_vietnamese_headers():
    df = pd.DataFrame({" Bảng ": ["ORDER", "ORDER", None, "ORDER"], "COT": ["order_id", "amount", "x", "order_id"],
                       "Kiểu": ["string (pk)", "Float", "int", "int"], "Dinh Nghia": ["Mã", None, "", "lặp"]})
    tables, warnings = dd.parse_dictionary_csv(df)
    cols = tables["ORDER"]["columns"]
    assert cols == [{"name": "order_id", "type": "string", "key": "PK", "definition": "Mã"},
                    {"name": "amount", "type": "float", "key": None, "definition": ""}]
    assert len(warnings) == 2  # dong thieu ten bang, cot lap


def test_parse_csv_missing_required_column():
    with pytest.raises(ValueError, match="thiếu cột bắt buộc"):
        dd.parse_dictionary_csv(pd.DataFrame({"table": ["T"], "column": ["a"]}))


def test_match_tables():
    m = dd.match_tables(["Support Ticket", "Products", "Region"], ["SUPPORT_TICKET", "PRODUCT", "ORDER"])
    assert m["matched"] == [("Support Ticket", "SUPPORT_TICKET"), ("Products", "PRODUCT")]
    assert m["tables_without_file"] == ["Region"] and m["files_without_dictionary"] == ["ORDER"]


# ---------- doi chieu cot ----------

def test_missing_and_undocumented():
    df = csv("id,amount,extra\n1,2,x\n2,3,y")
    r = check(df, ("id", "int (PK)", "Mã"), ("amount", "float", ""), ("Total", "float", "Calculated: amount * qty"),
              ("region", "string", "Vùng"))
    assert r["missing_in_data"] == [{"column": "Total", "type": "float", "derived_hint": True},
                                    {"column": "region", "type": "string", "derived_hint": False}]
    assert r["undocumented_in_dictionary"] == ["extra"]


def test_column_names_match_ignoring_case_and_spaces():
    r = check(csv("OrderID\n1\n2"), (" orderid ", "int", ""))
    assert r["missing_in_data"] == [] and r["undocumented_in_dictionary"] == []


@pytest.mark.parametrize("values, dict_type, flagged", [
    (["1.0", "2.0"], "int", False),             # integer_stored_as_float van la integer
    (["a", "b"], "int", True),
    (["1.5", "2.5"], "integer", True),
    (["1", "2"], "string", False),
    (["2025-01-01", "2025-01-02"], "text", False),
    (["1", "2"], "float", False),
    (["a", "b"], "decimal(10,2)", True),
    (["2025-01-01", "2025-02-01"], "int", True),  # int nhung du lieu suy ra date
    (["1", "2"], "date", True),                   # date nhung du lieu suy ra integer
    (["x", "y"], "date", True),
    (["2025-01-01 10:00:00", "2025-01-02 11:00:00"], "date", False),
    (["2025-01-01", "2025-01-02"], "timestamp", False),
    (["1.5", "2"], "datetime", True),
])
def test_type_rules(values, dict_type, flagged):
    r = check(pd.DataFrame({"c": values}, dtype=str), ("c", dict_type, ""))
    assert bool(r["type_mismatches"]) is flagged
    if flagged:
        assert r["type_mismatches"][0]["dictionary_type"] == dict_type


def test_unknown_type_and_all_null_not_flagged():
    df = pd.DataFrame({"g": ["a", "b"], "n": [None, None]}, dtype=str)
    r = check(df, ("g", "geometry", ""), ("n", "int", ""))
    assert r["type_mismatches"] == []
    assert r["notes"] == ["g: unknown_dictionary_type (geometry)"]


def test_date_format_mismatch():
    df = pd.DataFrame({"d": ["2025-04-15", "2025-04-16"], "e": ["15/04/2025", "16/04/2025"]}, dtype=str)
    r = check(df, ("d", "date", "Ngày đặt, DD-MM-YYYY"), ("e", "date", "dd/mm/yyyy"))
    assert r["date_format_mismatches"] == [{"column": "d", "dictionary_format": "%d-%m-%Y",
                                            "data_format": "%Y-%m-%d"}]


def test_date_format_without_text_format_is_skipped():
    r = check(pd.DataFrame({"d": ["2025-04-15"]}, dtype=str), ("d", "date", "Ngày đặt hàng"))
    assert r["date_format_mismatches"] == []
    assert dd.text_date_format("yyyy.mm.dd") == "%Y.%m.%d" and dd.text_date_format("mm/dd/yyyy") == "%m/%d/%Y"
    assert dd.text_date_format("dd-mm/yyyy") is None


# ---------- khoa ----------

def test_dictionary_key_cols():
    t = dict_table(("A", "int (PK)", ""), ("b", "int (PK)", ""), ("c", "int (FK)", ""))
    assert dd.dictionary_key_cols(t, ["a", "b", "c"]) == (["a", "b"], [])
    assert dd.dictionary_key_cols(t, ["a", "c"]) == (None, ["b"])


# ---------- profile_problem ----------

# 11 dong, O1 lap -> ty le duy nhat 10/11 du nguong doan khoa khi khong dung PK cua dictionary
ORDER_CSV = "order_id,order_date,amount\nO1,10-04-2025,1\n" + "".join(
    f"O{i},{10 + i}-04-2025,{i}\n" for i in range(1, 11))
LINE_CSV = "order_id,line_no,qty\nO1,1,2\nO1,2,1\nO2,1,5\nO2,1,6\n"
DICT_MD = """
## Orders
| Cột | Kiểu | Định nghĩa |
|---|---|---|
| order_id | string (PK) | Mã đơn |
| order_date | date | yyyy-mm-dd |
| amount | int | |
| total | float | derived from lines |

## Order Line
| Cột | Kiểu | Định nghĩa |
|---|---|---|
| order_id | string (PK) | |
| line_no | int (PK) | |
| qty | int | |

## Customer
| Cột | Kiểu | Định nghĩa |
|---|---|---|
| cust_code | string (PK) | |
"""


def make_r2(tmp_path, monkeypatch, files):
    for key, data in files.items():
        p = tmp_path / "cache" / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    read_keys = []

    def download(key, grader=False):
        read_keys.append(key)
        return tmp_path / "cache" / key

    monkeypatch.setattr(r2, "get_r2_settings", lambda: {"R2_INPUT_PREFIX": "input/"})
    monkeypatch.setattr(r2, "list_objects", lambda prefix, grader=False: [
        {"key": k, "etag": "e"} for k in files if k.startswith(prefix)])
    monkeypatch.setattr(r2, "download_with_cache", download)
    monkeypatch.setattr(r2, "get_etag", lambda key, cache_dir=None: None)
    monkeypatch.setattr(quality, "OUTPUT_DIR", tmp_path / "output")
    return read_keys


def test_profile_problem_with_markdown_dictionary(tmp_path, monkeypatch):
    make_r2(tmp_path, monkeypatch, {
        "input/b1/data_dictionary.md": DICT_MD, "input/b1/ORDER.csv": ORDER_CSV,
        "input/b1/ORDER_LINE.csv": LINE_CSV, "input/b1/EXTRA.csv": "x_id\n1\n2\n",
    })
    report = quality.profile_problem("b1", include_samples=False)
    d = report["dictionary"]
    assert (d["file"], d["format"], d["status"]) == ("input/b1/data_dictionary.md", "md", "ok")
    assert d["tables_without_file"] == ["Customer"]          # chi la thong tin
    assert d["files_without_dictionary"] == ["EXTRA"]        # la co
    assert set(d["tables"]) == {"Orders", "Order Line", "Customer"}
    assert report["skipped"] == [] and set(report["tables"]) == {"ORDER", "ORDER_LINE", "EXTRA"}
    assert "dictionary: not_in_dictionary" in report["tables"]["EXTRA"]["flags"]
    assert "dictionary_check" not in report["tables"]["EXTRA"]
    assert report["tables"]["EXTRA"]["key_source"] == "guessed"

    order = report["tables"]["ORDER"]
    assert (order["key_cols"], order["key_guessed"], order["key_source"]) == (["order_id"], False, "dictionary")
    assert order["duplicate_key_count"] == 1
    c = order["dictionary_check"]
    assert c["dictionary_table"] == "Orders" and c["missing_in_data"][0]["derived_hint"] is True
    assert c["date_format_mismatches"][0]["column"] == "order_date"

    line = report["tables"]["ORDER_LINE"]
    assert line["key_cols"] == ["order_id", "line_no"] and line["key_source"] == "dictionary"
    assert line["duplicate_key_count"] == 1 and line["key_conflict_count"] == 1  # (O2, 1) lap

    text = quality.format_summary(report)
    assert "Dictionary: input/b1/data_dictionary.md (md, 3 bảng)" in text
    assert "Bảng trong dictionary không có file: Customer" in text
    assert "File không có trong dictionary: EXTRA" in text
    assert "Thiếu trong dữ liệu: total (dictionary nói là cột phải tính)" in text
    assert "Định dạng ngày lệch: order_date (dictionary %Y-%m-%d, dữ liệu %d-%m-%Y)" in text
    assert "Khóa lấy từ: dictionary (order_id, line_no)" in text


def test_pk_missing_in_data_falls_back_to_guess(tmp_path, monkeypatch):
    md = "## ORDER\n| Cột | Kiểu | Định nghĩa |\n|---|---|---|\n| order_no | string (PK) | |\n| amount | int | |\n"
    make_r2(tmp_path, monkeypatch, {"input/b2/data_dictionary.md": md, "input/b2/ORDER.csv": ORDER_CSV})
    report = quality.profile_problem("b2", include_samples=False)
    order = report["tables"]["ORDER"]
    assert order["dictionary_check"]["flags"] == ["pk_missing_in_data"]
    assert order["dictionary_check"]["pk_missing"] == ["order_no"]
    assert "dictionary: pk_missing_in_data" in order["flags"]
    assert (order["key_cols"], order["key_guessed"], order["key_source"]) == (["order_id"], True, "guessed")
    assert "Khóa lấy từ: đoán; PK trong dictionary thiếu trong dữ liệu: order_no" in quality.format_summary(report)


def test_no_dictionary(tmp_path, monkeypatch):
    make_r2(tmp_path, monkeypatch, {"input/b3/ORDER.csv": ORDER_CSV})
    report = quality.profile_problem("b3", include_samples=False)
    d = report["dictionary"]
    assert d["status"] == "none" and d["file"] is None and d["tables"] == {}
    assert d["files_without_dictionary"] == []  # khong co dictionary -> khong gan co
    order = report["tables"]["ORDER"]
    assert order["key_cols"] == ["order_id"] and order["key_source"] == "guessed"
    assert "dictionary_check" not in order and order["flags"] == []
    assert "Dictionary: không có" in quality.format_summary(report)


def test_unsupported_dictionary_not_profiled(tmp_path, monkeypatch):
    read = make_r2(tmp_path, monkeypatch, {"input/b4/data_dictionary.xlsx": b"PK\x03\x04",
                                           "input/b4/ORDER.csv": ORDER_CSV})
    report = quality.profile_problem("b4", include_samples=False)
    d = report["dictionary"]
    assert (d["status"], d["file"]) == ("unsupported", "input/b4/data_dictionary.xlsx")
    assert report["skipped"] == [] and set(report["tables"]) == {"ORDER"}
    assert "input/b4/data_dictionary.xlsx" not in read
    assert "không hỗ trợ định dạng file input/b4/data_dictionary.xlsx" in quality.format_summary(report)


def test_csv_dictionary_not_profiled_as_table(tmp_path, monkeypatch):
    dict_csv = "Bang,Cot,Kieu,Dinh nghia\nORDER,order_id,string (PK),Mã đơn\nORDER,amount,int,\n"
    make_r2(tmp_path, monkeypatch, {"input/b5/Data_Dictionary.csv": dict_csv, "input/b5/ORDER.csv": ORDER_CSV})
    report = quality.profile_problem("b5", include_samples=False)
    assert report["dictionary"]["format"] == "csv" and report["dictionary"]["status"] == "ok"
    assert set(report["tables"]) == {"ORDER"} and all("Dictionary" not in f["key"] for f in report["files"])
    order = report["tables"]["ORDER"]
    assert order["key_source"] == "dictionary"
    assert order["dictionary_check"]["undocumented_in_dictionary"] == ["order_date"]


def test_broken_csv_dictionary_does_not_stop(tmp_path, monkeypatch):
    make_r2(tmp_path, monkeypatch, {"input/b6/data_dictionary.csv": "foo,bar\n1,2\n", "input/b6/ORDER.csv": ORDER_CSV})
    report = quality.profile_problem("b6", include_samples=False)
    d = report["dictionary"]
    assert d["status"] == "error" and "thiếu cột bắt buộc" in d["parse_warnings"][0]
    assert set(report["tables"]) == {"ORDER"} and report["tables"]["ORDER"]["key_source"] == "guessed"
