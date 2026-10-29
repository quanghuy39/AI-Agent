"""Kiem tra profiling/flags.py va phan co theo nguong trong profile_problem.
Du lieu tu tao trong code, khong goi mang (R2 duoc thay bang monkeypatch).

Chay: python -m pytest tests/test_profiling_flags.py -v
"""

import json

import pandas as pd
import pytest

from ledger import Ledger
from profiling import dictionary as dd
from profiling import flags as fl
from profiling import quality
from profiling.quality import profile_table
from storage import r2


def table(data, name="T", key_cols=None, dict_rows=None):
    """Profile mot bang; dict_rows (column, type, definition) -> them dictionary_check nhu profile_problem."""
    t = profile_table(pd.DataFrame(data, dtype=str), name, key_cols=key_cols)
    t["key_source"] = "dictionary" if key_cols else None
    if dict_rows is not None:
        tables, _ = dd.parse_dictionary_csv(pd.DataFrame(
            [{"table": name, "column": c, "type": ty, "definition": de} for c, ty, de in dict_rows]))
        t["dictionary_check"] = dd.compare_table_with_dictionary(t, tables[name], name)
        t["_dict_tables"] = tables
    return t


def report(*tables, dictionary=None, files=None):
    dict_tables = {}
    for t in tables:
        dict_tables.update(t.pop("_dict_tables", {}))
    if dictionary is None and dict_tables:
        dictionary = {"status": "ok", "tables": dict_tables, "tables_without_file": [],
                      "files_without_dictionary": []}
    return {"tables": {t["table"]: t for t in tables}, "dictionary": dictionary, "files": files or []}


def found(flags, code, column=None):
    """[(severity)] cua cac co co code (va column neu truyen)."""
    return [f["severity"] for f in flags if f["code"] == code and (column is None or f["column"] == column)]


IDS = [f"CUST{i:06d}" for i in range(1, 20)]  # 19 gia tri cung cau truc AAAA999999


# ---------- khoa va trung lap ----------

def test_duplicate_key_exact_duplicate_and_conflict():
    t = table({"id": ["1", "1", "2", "2", "3"], "v": ["a", "a", "b", "c", "d"]}, key_cols=["id"])
    flags = fl.build_flags(report(t))
    assert found(flags, "duplicate_key") == ["flag"] and found(flags, "exact_duplicate_rows") == ["flag"]
    assert found(flags, "key_conflict") == ["flag"]
    dup = next(f for f in flags if f["code"] == "duplicate_key")
    assert (dup["value"], dup["threshold"], dup["column"]) == (2, 0, None)


def test_no_duplicates_no_flags():
    flags = fl.build_flags(report(table({"id": ["1", "2", "3"], "v": ["a", "b", "c"]}, key_cols=["id"])))
    assert not found(flags, "duplicate_key") and not found(flags, "exact_duplicate_rows")


def test_null_key_rows():
    flags = fl.build_flags(report(table({"id": ["1", None, "3"], "v": ["a", "b", "c"]}, key_cols=["id"])))
    assert found(flags, "null_key_rows") == ["flag"]


# ---------- null ----------

def test_null_ratio_flag_info_and_all_null():
    data = {"few": [None] + ["x"] * 99, "many": [None] * 10 + ["x"] * 90, "empty": [None] * 100}
    flags = fl.build_flags(report(table(data)))
    assert found(flags, "nulls_present", "few") == ["info"]
    assert found(flags, "null_ratio_high", "many") == ["flag"]
    assert found(flags, "all_null", "empty") == ["flag"]
    assert not found(flags, "null_ratio_high", "empty") and not found(flags, "nulls_present", "empty")


def test_null_threshold_override():
    t = table({"few": [None] + ["x"] * 99})
    flags = fl.build_flags(report(t), thresholds={"NULL_RATIO_FLAG": 0.01})
    f = next(f for f in flags if f["column"] == "few")
    assert (f["code"], f["severity"], f["threshold"]) == ("null_ratio_high", "flag", 0.01)


def test_unknown_threshold_key():
    with pytest.raises(ValueError, match="NULL_RATIO"):
        fl.build_flags(report(table({"a": ["1"]})), thresholds={"NULL_RATIO": 0.1})
    with pytest.raises(ValueError, match="TYPE_INFER_MIN_RATIO"):
        fl.resolve_thresholds({"TYPE_INFER_MIN_RATIO": 0.9})
    assert fl.resolve_thresholds()["NULL_RATIO_FLAG"] == 0.05


# ---------- cot khoa so voi cot thuong ----------

def test_pattern_deviation_key_column_is_flag_regular_is_info():
    values = IDS + ["cust000020"]  # cau truc chinh 95%
    flags = fl.build_flags(report(table({"customer_id": values, "note": values})))
    assert found(flags, "pattern_deviation", "customer_id") == ["flag"]
    assert found(flags, "pattern_deviation", "note") == ["info"]
    f = next(f for f in flags if f["code"] == "pattern_deviation" and f["column"] == "customer_id")
    assert "AAAA999999" in f["message"] and f["value"] == 1


def test_pattern_deviation_needs_clear_dominant_pattern():
    values = [f"O{i}" for i in range(1, 11)]  # A9 (9) va A99 (1): cau truc chinh chi 90%
    t = table({"order_id": values})
    assert not found(fl.build_flags(report(t)), "pattern_deviation")
    flags = fl.build_flags(report(t), thresholds={"MINORITY_PATTERN_RATIO": 0.2})
    assert found(flags, "pattern_deviation", "order_id") == ["flag"]


def test_key_column_from_key_cols_and_dictionary():
    values = IDS + ["cust000020"]
    t = table({"buyer": values, "seller": values, "other": values}, key_cols=["seller"],
              dict_rows=[("buyer", "string (FK)", ""), ("seller", "string", ""), ("other", "string", "")])
    flags = fl.build_flags(report(t))
    assert found(flags, "pattern_deviation", "buyer") == ["flag"]   # FK trong dictionary
    assert found(flags, "pattern_deviation", "seller") == ["flag"]  # nam trong key_cols
    assert found(flags, "pattern_deviation", "other") == ["info"]


def test_key_column_hint_override():
    values = IDS + ["cust000020"]
    flags = fl.build_flags(report(table({"khach": values})), thresholds={"KEY_NAME_HINTS": ["khach"]})
    assert found(flags, "pattern_deviation", "khach") == ["flag"]


def test_whitespace_key_flag_regular_info():
    values = ["A1 ", "A2", "A3"]
    flags = fl.build_flags(report(table({"product_code": values, "label": values})))
    assert found(flags, "whitespace", "product_code") == ["flag"]
    assert found(flags, "whitespace", "label") == ["info"]


# ---------- gia tri ----------

def test_case_variants_and_constant_column():
    flags = fl.build_flags(report(table({"city": ["Delhi", "delhi", "Pune"], "k": ["x", "x", "x"]})))
    assert found(flags, "case_variants", "city") == ["info"]
    assert found(flags, "constant_column", "k") == ["info"]
    assert not found(fl.build_flags(report(table({"k": ["x"]}))), "constant_column")  # 1 dong: khong co


# ---------- ngay ----------

def test_date_invalid():
    values = [f"2025-01-{d:02d}" for d in range(1, 20)] + ["2025-13-45"]
    flags = fl.build_flags(report(table({"d": values})))
    assert found(flags, "date_invalid", "d") == ["flag"]


def test_mixed_and_ambiguous_dates():
    mixed = [f"2025-01-{d:02d}" for d in range(10, 20)] + [f"{d}-01-2025" for d in range(10, 20)]
    ambiguous = [f"{d:02d}/{m:02d}/2025" for d in range(1, 6) for m in range(1, 5)]
    flags = fl.build_flags(report(table({"mixed": mixed, "amb": ambiguous})))
    assert found(flags, "mixed_date_formats", "mixed") == ["flag"]
    assert found(flags, "ambiguous_day_month", "amb") == ["flag"]


def test_date_like_unknown_format():
    flags = fl.build_flags(report(table({"d": ["15 Apr 2025", "16 Apr 2025", "1 May 2025"]})))
    assert found(flags, "date_like_unknown_format", "d") == ["flag"]


# ---------- dictionary ----------

def test_dictionary_flags():
    t = table({"id": ["1", "2"], "amount": ["a", "b"], "d": ["2025-01-01", "2025-01-02"], "extra": ["x", "y"]},
              dict_rows=[("order_no", "int (PK)", ""), ("amount", "float", ""), ("d", "date", "dd-mm-yyyy"),
                         ("total", "float", "Calculated from lines"), ("id", "int", "")])
    flags = fl.build_flags(report(t))
    assert found(flags, "dict_missing_column", "order_no") == ["flag"]
    assert found(flags, "dict_missing_column", "total") == ["info"]   # derived_hint
    assert found(flags, "dict_type_mismatch", "amount") == ["flag"]
    assert found(flags, "dict_date_format_mismatch", "d") == ["flag"]
    assert found(flags, "dict_pk_missing") == ["flag"]
    assert found(flags, "dict_undocumented_column", "extra") == ["info"]


def test_dictionary_file_level_flags():
    t = table({"a": ["1", "2"]})
    d = {"status": "ok", "tables": {}, "tables_without_file": ["Region"], "files_without_dictionary": ["T"]}
    flags = fl.build_flags(report(t, dictionary=d))
    assert found(flags, "not_in_dictionary") == ["flag"]
    region = next(f for f in flags if f["code"] == "dict_table_without_file")
    assert (region["severity"], region["table"]) == ("info", "Region")
    for status, code in (("error", "dictionary_error"), ("unsupported", "dictionary_unsupported")):
        d = {"status": status, "file": "input/b/data_dictionary.xlsx", "tables": {}, "parse_warnings": []}
        assert found(fl.build_flags(report(t, dictionary=d)), code) == ["flag"]


# ---------- bang ----------

def test_empty_table_and_wrong_delimiter():
    empty = profile_table(pd.DataFrame({"a": [], "b": []}, dtype=str), "EMPTY")
    one = table({"a;b;c": ["1;2;3", "4;5;6"]}, name="ONE")
    files = [{"table": "ONE", "flags": ["suspect_wrong_delimiter"]}, {"table": "EMPTY", "flags": []}]
    flags = fl.build_flags(report(empty, one, files=files))
    assert [f["code"] for f in flags if f["table"] == "EMPTY"] == ["empty_table"]  # khong co all_null
    assert found(flags, "suspect_wrong_delimiter") == ["flag"]


# ---------- khong lo gia tri that ----------

def test_messages_never_contain_real_values():
    secret = "SECRET_VALUE"
    data = {
        "customer_id": IDS + [secret],                    # lech cau truc
        "name": ["Ann"] * 18 + [secret, secret.lower()],  # bien the hoa thuong
        "label": [f" {secret}"] + ["x"] * 19,             # khoang trang thua
        "d": [f"2025-01-{i:02d}" for i in range(1, 20)] + [secret],
        "same": [secret] * 20,                            # 1 gia tri
        "gap": [secret] + [None] * 19,                    # nhieu null
    }
    t = table(data, dict_rows=[("customer_id", "int (PK)", f"{secret} dd-mm-yyyy"), ("d", "date", "dd-mm-yyyy"),
                               ("lost", "int", secret)])
    assert secret in json.dumps(t, ensure_ascii=False)  # bao cao co gia tri that (top_values, mau...)
    flags = fl.build_flags(report(t))
    assert len(flags) >= 8
    for f in flags:
        assert secret.lower() not in f["message"].lower()
        assert secret.lower() not in str(f["value"]).lower() and secret.lower() not in str(f["threshold"]).lower()


def test_summarize_flags():
    flags = [{"severity": "flag", "table": "A"}, {"severity": "info", "table": "A"},
             {"severity": "info", "table": "Region"}]
    assert fl.summarize_flags(flags, ["A", "B"]) == {
        "flag": 1, "info": 2, "by_table": {"A": {"flag": 1, "info": 1}, "B": {"flag": 0, "info": 0}}}


# ---------- profile_problem ----------

ORDER_CSV = "order_id,amount,note\n" + "".join(f"O{i},{i},\n" for i in range(1, 11)) + "O1,1,x\n"


@pytest.fixture
def fake_r2(tmp_path, monkeypatch):
    files = {"input/b1/ORDER.csv": ORDER_CSV}
    for key, text in files.items():
        p = tmp_path / "cache" / key
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    monkeypatch.setattr(r2, "get_r2_settings", lambda: {"R2_INPUT_PREFIX": "input/"})
    monkeypatch.setattr(r2, "list_objects", lambda prefix, grader=False: [
        {"key": k, "etag": "e"} for k in files if k.startswith(prefix)])
    monkeypatch.setattr(r2, "download_with_cache", lambda key, grader=False: tmp_path / "cache" / key)
    monkeypatch.setattr(r2, "get_etag", lambda key, cache_dir=None: None)
    monkeypatch.setattr(quality, "OUTPUT_DIR", tmp_path / "output")
    return tmp_path


def test_profile_problem_without_dictionary_adds_flags_and_logs_once(fake_r2):
    report_ = quality.profile_problem("b1", include_samples=False)
    assert report_["dictionary"]["status"] == "none"
    order = report_["tables"]["ORDER"]
    codes = {f["code"] for f in order["quality_flags"]}
    assert {"duplicate_key", "null_ratio_high"} <= codes
    assert order["flags"] == []  # khoa "flags" cu giu nguyen
    s = report_["quality_flags_summary"]
    assert s["flag"] == sum(f["severity"] == "flag" for f in order["quality_flags"])
    assert s["by_table"]["ORDER"] == {"flag": s["flag"], "info": s["info"]}

    led = Ledger(fake_r2 / "output" / "b1")
    saved = led.get_data_profile()[quality.REPORT_KEY]
    assert saved["quality_flags_summary"] == s
    events = [e for e in led.read_log() if e.action == "quality_flags"]
    assert len(events) == 1 and len(led.read_log()) == 1
    assert events[0].step == "profiling" and events[0].extra == s
    assert events[0].inputs["thresholds"]["NULL_RATIO_FLAG"] == 0.05
    assert led.open_items() == [] and led.decisions() == [] and led.findings() == []

    text = quality.format_summary(report_)
    assert "Cờ (cần xem): ORDER: 1 khóa trùng (order_id)" in text
    assert f"Tổng: {s['flag']} cờ, {s['info']} lưu ý" in text.splitlines()[-1]


def test_profile_problem_thresholds_and_bad_key(fake_r2):
    with pytest.raises(ValueError):
        quality.profile_problem("b1", thresholds={"BAD": 1})
    r = quality.profile_problem("b1", include_samples=False, thresholds={"NULL_RATIO_FLAG": 0.99})
    assert {f["code"] for f in r["tables"]["ORDER"]["quality_flags"]} >= {"nulls_present"}


def test_format_summary_no_flags():
    t = table({"a": ["1", "2"]})
    t["quality_flags"] = []
    text = quality.format_summary({"problem_id": "x", "generated_at": "t", "tables": {"T": t},
                                   "skipped": [], "errors": []})
    assert "  Cờ: không" in text and text.endswith("Tổng: 0 cờ, 0 lưu ý")


def test_cli_set_overrides(fake_r2, capsys):
    assert quality.main(["b1", "--no-samples", "--set", "NULL_RATIO_FLAG=1"]) == 0
    assert "Lưu ý (info): " in capsys.readouterr().out
    log = Ledger(fake_r2 / "output" / "b1").read_log()
    assert log[-1].inputs["thresholds"]["NULL_RATIO_FLAG"] == 1
    for bad in (["--set", "NOPE=1"], ["--set", "NULL_RATIO_FLAG=abc"], ["--set", "NULL_RATIO_FLAG"]):
        with pytest.raises(SystemExit):
            quality.main(["b1", *bad])
