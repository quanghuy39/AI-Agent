"""Kiem tra profiling/data_summary.py, pii_like trong profile_table va the (SENSITIVE) cua dictionary.
Du lieu tu tao trong code, khong goi mang (R2 duoc thay bang monkeypatch).

Chay: python -m pytest tests/test_data_summary.py -v
"""

import copy

import pandas as pd
import pytest

import config
from ledger import Ledger
from profiling import data_summary as ds
from profiling import dictionary as dd
from profiling import quality
from profiling.quality import profile_table
from storage import r2

SECRET = "SECRET_VALUE"


def col(values, name="c", **kw):
    return profile_table(pd.DataFrame({name: values}, dtype=str), "T", **kw)["columns"][name]


# ---------- dictionary (SENSITIVE) ----------

def test_parser_sensitive_tag():
    md = ("## T\n| Cột | Kiểu | Định nghĩa |\n|---|---|---|\n| id | string (PK, SENSITIVE) | |\n"
          "| phone | text (sensitive) | |\n| ref | int (FK) | |\n| note | string | |\n")
    cols = dd.parse_dictionary_md(md)[0]["T"]["columns"]
    assert [(c["type"], c["key"], c["sensitive"]) for c in cols] == [
        ("string", "PK", True), ("text", None, True), ("int", "FK", False), ("string", None, False)]
    tables, _ = dd.parse_dictionary_csv(pd.DataFrame({"table": ["T"], "column": ["a"], "type": ["varchar(9) (SENSITIVE)"]}))
    assert tables["T"]["columns"][0] == {"name": "a", "type": "varchar(9)", "key": None, "sensitive": True,
                                         "definition": "", "valid_range": None}


# ---------- pii_like ----------

def test_pii_email():
    c = col(["a@b.com", "x.y@mail.vn", "khong", None])
    assert c["pii_like"]["email_ratio"] == pytest.approx(2 / 3, abs=1e-4)


@pytest.mark.parametrize("value", ["0912345678", "+84 912 345 678", "091-234-5678", "(028) 3823 4567"])
def test_phone_detected(value):
    assert col([value] * 3, "contact")["pii_like"]["phone_ratio"] == 1.0


def test_money_with_ten_digits_is_not_phone():
    c = col(["1500000000", "2500000000", "1234567890"], "amount")
    assert c["inferred_type"] == "integer" and c["pii_like"]["phone_ratio"] == 0.0
    assert col(["1500000000.0", "2500000000.0"], "amount")["pii_like"]["phone_ratio"] == 0.0


def test_key_column_with_ten_digits_is_not_phone():
    values = [f"09{i:08d}" for i in range(10)]
    assert col(values, "customer_id")["pii_like"]["phone_ratio"] is None  # ten cot co goi y khoa
    assert col(values, "x", key_cols=["x"])["pii_like"]["phone_ratio"] is None
    assert col(values, "x")["pii_like"]["phone_ratio"] == 1.0


def test_pii_only_string_and_integer():
    assert "pii_like" not in col(["1.5", "2.5"])
    assert "pii_like" not in col(["2025-01-01", "2025-01-02"])


# ---------- dung report ----------

def make_report(tmp_path, monkeypatch, files, problem_id="b1", include_samples=True, **kw):
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
    return quality.profile_problem(problem_id, include_samples=include_samples, **kw)


CUSTOMER = ("customer_id,full_name,email,phone,city,birth\n"
            + "".join(f"C{i:03d},Name {i},u{i}@mail.com,09{i:08d},{'Hanoi' if i % 2 else 'Hue'},"
                      f"2000-01-{i:02d}\n" for i in range(1, 11))
            + f"C011,{SECRET},{SECRET}@x.com,0911111111,{SECRET},2000-01-11\n")
ORDERS = ("order_id,customer_id,amount,city_name\n"
          + "".join(f"O{i:03d},C{(i % 11) + 1:03d},{i * 1000},{'Hanoi' if i % 3 else 'Da Nang'}\n"
                    for i in range(1, 21)))
DICT = """
## CUSTOMER
| Cột | Kiểu | Định nghĩa |
|---|---|---|
| customer_id | string (PK) | Mã khách |
| full_name | string (SENSITIVE) | Họ tên |
| email | string | Email |
| phone | string | Điện thoại |
| city | string | Thành phố |
| birth | date | Ngày sinh |

## ORDERS
| Cột | Kiểu | Định nghĩa |
|---|---|---|
| order_id | string (PK) | Mã đơn |
| customer_id | string (FK) | Khách |
| amount | int | Giá trị đơn |
| city_name | string | Nơi giao |
| total | float | Derived: tổng |
"""
FILES = {"input/b1/CUSTOMER.csv": CUSTOMER, "input/b1/ORDERS.csv": ORDERS, "input/b1/data_dictionary.md": DICT}


@pytest.fixture
def report(tmp_path, monkeypatch):
    return make_report(tmp_path, monkeypatch, FILES)


def masked_map(summary):
    return {(m["table"], m["column"]): m["reason"] for m in summary["masked_columns"]}


def test_masking_sources_and_secret_never_in_text(report):
    s = ds.build_data_summary(report, sensitive=["CUSTOMER.city"])
    m = masked_map(s)
    assert m[("CUSTOMER", "full_name")] == "dictionary"
    assert m[("CUSTOMER", "city")] == "argument"
    assert m[("CUSTOMER", "email")] == "auto_email"
    assert m[("CUSTOMER", "phone")] == "auto_phone"
    assert ("CUSTOMER", "customer_id") not in m
    assert sum("nghi ngờ nhạy cảm" in w for w in s["warnings"]) == 2
    assert SECRET.lower() not in s["text"].lower()
    assert "- full_name [masked]: string" in s["text"] and "- city [masked]: string" in s["text"]


def test_masked_column_keeps_structure_drops_values(report):
    s = ds.build_data_summary(report, sensitive=["CUSTOMER.city"])
    line = next(l for l in s["text"].splitlines() if l.startswith("- city [masked]"))
    assert "nulls 0" in line and "unique 3" in line and "pattern" in line and "top" not in line
    assert "city_000" in s["text"]  # ban ghi mau dung ma an danh cua llm.anonymize


def test_unmasked_column_shows_stats_and_top(report):
    text = ds.build_data_summary(report)["text"]
    assert "- amount: integer; nulls 0; unique 20; min 1000, max 20000" in text
    assert "- birth: date %Y-%m-%d; nulls 0; unique 11; range 2000-01-01..2000-01-11" in text
    assert "- order_id [key]: string" in text
    assert "missing in data: total (derived)" in text
    assert 'def "Giá trị đơn"' in text
    assert "## ORDERS (rows 20; key order_id [dictionary])" in text


def test_shared_value_only_warns(report):
    s = ds.build_data_summary(report, sensitive=["CUSTOMER.city"])  # "Hanoi" cung la gia tri cua ORDERS.city_name
    assert "Hanoi" in s["text"]
    assert any("CUSTOMER.city cũng xuất hiện ở cột không bị che ORDERS.city_name" in w for w in s["warnings"])
    assert all("Hanoi" not in w for w in s["warnings"])


def test_leak_via_definition_raises(report):
    leaky = copy.deepcopy(report)
    leaky["dictionary"]["tables"]["ORDERS"]["columns"][3]["definition"] = f"Nơi giao, vd {SECRET}"
    with pytest.raises(ds.SensitiveLeakError) as e:
        ds.build_data_summary(leaky, sensitive=["CUSTOMER.city"])
    assert SECRET not in str(e.value)


def test_never_includes_examples(report):
    rep = copy.deepcopy(report)
    cust = rep["tables"]["CUSTOMER"]["columns"]
    assert "date_invalid_examples" in cust["birth"] and "case_variant_examples" in cust["city"]
    cust["birth"]["date_invalid_examples"] = ["INVALID_MARKER"]
    cust["city"]["case_variant_examples"] = [["CASE_MARKER", "case_marker"]]
    check = next(c for c in rep["relationship_checks"] if c["status"] != "rejected")
    check["result"]["details"]["examples"] = {"left_unmatched": ["EXAMPLE_MARKER"], "right_duplicate": ["DUP_MARKER"]}
    text = ds.build_data_summary(rep)["text"]
    for marker in ("INVALID_MARKER", "CASE_MARKER", "EXAMPLE_MARKER", "DUP_MARKER"):
        assert marker.lower() not in text.lower()


def test_bad_sensitive_name(report):
    with pytest.raises(ValueError, match="không có cột"):
        ds.build_data_summary(report, sensitive=["CUSTOMER.nope"])
    with pytest.raises(ValueError, match="không có bảng"):
        ds.build_data_summary(report, sensitive=["NOPE.x"])


def test_deterministic_and_no_samples(report):
    a = ds.build_data_summary(report)
    assert a == ds.build_data_summary(report)
    off = ds.build_data_summary(report, include_samples=False)
    assert "sample (" not in off["text"] and "top " not in off["text"]
    assert "sample (3):" in a["text"]
    assert a["est_tokens"] == -(-a["chars"] // 4) and a["level"] == 0 and not a["truncated"]


def test_flags_rebuilt_in_english(report):
    rep = copy.deepcopy(report)
    rep["tables"]["ORDERS"]["quality_flags"].append(
        {"code": "null_ratio_high", "severity": "flag", "table": "ORDERS", "column": "amount",
         "message": "ORDERS.amount: 3 null (15.0%)", "value": 0.15, "threshold": 0.05})
    text = ds.build_data_summary(rep)["text"]
    assert "## Flags (1)\n- null_ratio_high ORDERS.amount: value 0.15, threshold 0.05" in text
    assert "## Notes (" in text and "- dict_missing_column ORDERS.total (derived)\n" in text
    for line in text.split("## Flags")[1].splitlines():
        assert "null (" not in line and "không" not in line  # khong dung message tieng Viet
    assert "## Relationships" in text and "ORDERS.customer_id -> CUSTOMER.customer_id: all matched" in text


def test_pattern_only_when_clear_key_or_masked(tmp_path, monkeypatch):
    cats = ["Cash", "UPI", "Credit Card", "E-Wallet"]
    rows = [f"R{i:03d},{cats[i % 4]},{'AB%04d' % i if i else 'ab0000'},{'X%d' % i if i % 2 else 'Y-%d' % i},"
            f"{cats[i % 4]}" for i in range(100)]
    files = {"input/b5/T.csv": "row_id,payment,sku,ref_code,secret_cat\n" + "\n".join(rows) + "\n"}
    rep = make_report(tmp_path, monkeypatch, files, problem_id="b5")
    text = ds.build_data_summary(rep, sensitive=["T.secret_cat"])["text"]
    line = {l.split(":")[0].split(" ")[1]: l for l in text.splitlines() if l.startswith("- ")}
    assert "pattern" not in line["payment"]                    # cot phan loai nhieu cau truc
    assert "pattern AA9999 (99.0%, other 1" in line["sku"]     # cau truc chinh 99%
    assert "pattern" in line["ref_code"]                       # cot khoa/noi bang (ten co "code")
    assert "pattern" in line["secret_cat"]                     # cot bi che


def test_join_flag_lines_show_direction(tmp_path, monkeypatch):
    parent = "parent_id,name\n" + "".join(f"P{i:02d},N{i}\n" for i in range(1, 11)) + "P10,dup\n"
    child = "child_id,parent_id\nc1,P01\nc2,P01\nc3,p02\nc4,P99\nc5,P10\n"
    rep = make_report(tmp_path, monkeypatch, {"input/b6/P.csv": parent, "input/b6/C.csv": child}, problem_id="b6")
    text = ds.build_data_summary(rep)["text"]
    assert "- join_unmatched C.parent_id: 2 rows (40.0%) with no match in P.parent_id, " \
           "1 recoverable by normalizing keys" in text
    assert "- join_right_duplicate_keys P.parent_id: 1 duplicate keys, join from C.parent_id adds 1 rows" in text
    assert "- join_right_unmatched P.parent_id: 8 rows not referenced by C.parent_id" in text


# ---------- lan theo quan he ----------

def test_mask_spreads_to_related_column(report):
    s = ds.build_data_summary(report, sensitive=["CUSTOMER.customer_id"])
    m = masked_map(s)
    assert m[("CUSTOMER", "customer_id")] == "argument"
    assert m[("ORDERS", "customer_id")] == "relationship"
    assert "- customer_id [masked]: string" in s["text"].split("## ORDERS")[1]
    order_ids = {r["customer_id"] for r in report["tables"]["ORDERS"]["sample_records"]}
    assert order_ids and all(v not in s["text"] for v in order_ids)


def _sample_rows(text, table):
    """Cac dong cua bang mau trong phan ## table: list dict cot -> o."""
    part = text.split(f"## {table} ")[1].split("\n## ")[0]
    lines = [l for l in part.split("sample (")[1].splitlines()[1:] if l.startswith("| ")]
    head = [c.strip() for c in lines[0].strip("| ").split(" | ")]
    return [dict(zip(head, (c.strip() for c in l.strip("| ").split(" | ")))) for l in lines[1:]]


def test_related_masked_columns_share_codes(tmp_path, monkeypatch):
    # Moi khach co 2 don -> mau ORDERS chac chan co khach trung voi mau CUSTOMER
    customer = "customer_id,name\n" + "".join(f"C{i:03d},N{i}\n" for i in range(1, 4))
    orders = "order_id,customer_id\n" + "".join(f"O{i:03d},C{(i % 3) + 1:03d}\n" for i in range(1, 7))
    rep = make_report(tmp_path, monkeypatch, {"input/b4/CUSTOMER.csv": customer, "input/b4/ORDERS.csv": orders},
                      problem_id="b4")
    s = ds.build_data_summary(rep, sensitive=["CUSTOMER.customer_id"], sample_n=5)
    raw = {t: rep["tables"][t]["sample_records"] for t in ("CUSTOMER", "ORDERS")}
    shown = {t: _sample_rows(s["text"], t) for t in raw}
    code_of = {}
    for t in raw:
        for r, row in zip(raw[t], shown[t]):
            assert row["customer_id"].startswith("customer_id_")
            code_of.setdefault(r["customer_id"], set()).add(row["customer_id"])
    assert all(len(codes) == 1 for codes in code_of.values())        # cung gia tri -> cung ma, ca hai bang
    assert len({next(iter(c)) for c in code_of.values()}) == len(code_of)  # khac gia tri -> khac ma
    shared = set(r["customer_id"] for r in raw["CUSTOMER"]) & set(r["customer_id"] for r in raw["ORDERS"])
    assert shared  # du lieu test that su co gia tri chung
    assert s == ds.build_data_summary(rep, sensitive=["CUSTOMER.customer_id"], sample_n=5)


def synthetic(rels):
    tables = {n: {"columns": {"a": {}}, "key_cols": None} for n in ("A", "B", "C")}
    return {"tables": tables, "relationship_checks": [
        {"child": c, "child_col": "a", "parent": p, "parent_col": "a", "status": st} for c, p, st in rels]}


def test_rejected_relationship_does_not_spread():
    masked, _ = ds.resolve_masked_columns(synthetic([("A", "B", "rejected"), ("C", "B", "flag")]),
                                          sensitive=["B.a"])
    assert masked == {("B", "a"): ["argument"], ("C", "a"): ["relationship"]}


def test_mask_spreads_transitively():
    masked, _ = ds.resolve_masked_columns(synthetic([("A", "B", "ok"), ("B", "C", "ok")]), sensitive=["C.a"])
    assert masked == {("A", "a"): ["relationship"], ("B", "a"): ["relationship"], ("C", "a"): ["argument"]}


# ---------- ngan sach ----------

def big_report(tmp_path, monkeypatch):
    cols = [f"col_{i:02d}" for i in range(30)]
    rows = "".join(",".join([f"K{r}"] + [f"value {r % 7} {c}" for c in cols]) + "\n" for r in range(40))
    rows += rows.splitlines()[0] + "\n"  # 1 dong lap -> co duplicate_key, exact_duplicate_rows
    files = {f"input/b3/TABLE_{t}.csv": "id," + ",".join(cols) + "\n" + rows for t in range(4)}
    return make_report(tmp_path, monkeypatch, files, problem_id="b3")


def test_budget_levels_increase(tmp_path, monkeypatch):
    rep = big_report(tmp_path, monkeypatch)
    full = ds.build_data_summary(rep, max_chars=10 ** 7)
    assert full["level"] == 0
    levels = []
    for budget in (full["chars"] - 1, full["chars"] // 2, full["chars"] // 4, full["chars"] // 8):
        s = ds.build_data_summary(rep, max_chars=budget)
        levels.append(s["level"])
        assert s["chars"] <= budget or s["truncated"]
    assert levels == sorted(levels) and levels[0] >= 1 and levels[-1] > levels[0]


def test_level5_keeps_core_and_truncates(tmp_path, monkeypatch):
    rep = big_report(tmp_path, monkeypatch)
    s = ds.build_data_summary(rep, max_chars=50)
    assert s["truncated"] and s["level"] == 5 and any("Vượt ngân sách" in w for w in s["warnings"])
    text = s["text"]
    for t in range(4):
        assert f"## TABLE_{t} (rows 41; key id [guessed])" in text
    assert "- col_29: string" in text and "- id [key]: string" in text
    assert "## Flags (8)" in text and "- duplicate_key TABLE_3: value 1, threshold 0" in text
    assert "## Notes" not in text and "sample (" not in text


# ---------- CLI ----------

def test_cli_writes_ai_summary_and_keeps_other_keys(tmp_path, monkeypatch, capsys):
    make_report(tmp_path, monkeypatch, FILES)
    led = Ledger(tmp_path / "output" / "b1")
    profile = led.get_data_profile()
    profile["ghi_chu_khac"] = "giữ"
    led.set_data_profile(profile)

    assert ds.main(["b1", "--sensitive", "CUSTOMER.city", "--max-chars", "4000"]) == 0
    out = capsys.readouterr().out
    assert "# Data summary: b1" in out and "CUSTOMER.city (argument)" in out and "level" in out
    saved = led.get_data_profile()
    assert saved["ghi_chu_khac"] == "giữ" and quality.REPORT_KEY in saved
    assert saved["ai_summary"]["settings"]["max_chars"] == 4000
    with pytest.raises(SystemExit):
        ds.main(["b1", "--sensitive", "CUSTOMER.nope"])


DICT_KEY = "input/b1/data_dictionary.md"


def change_etag(monkeypatch):
    monkeypatch.setattr(r2, "list_objects", lambda prefix, grader=False: [
        {"key": k, "etag": "moi" if k == DICT_KEY else "e"} for k in FILES if k.startswith(prefix)])


def test_cli_stops_when_dictionary_changed_then_refresh_fixes(tmp_path, monkeypatch, capsys):
    assert make_report(tmp_path, monkeypatch, FILES)["dictionary"]["etag"] == "e"
    assert ds.main(["b1"]) == 0
    change_etag(monkeypatch)
    assert ds.main(["b1"]) == config.STALE_PROFILE_EXIT_CODE == 3
    err = capsys.readouterr().err
    assert "python -m profiling.data_summary b1 --refresh" in err and "--set/--range" in err
    assert ds.main(["b1", "--refresh", "--range", "ORDERS.amount=0:", "--set", "NULL_RATIO_FLAG=0.01"]) == 0
    report = Ledger(tmp_path / "output" / "b1").get_data_profile()[quality.REPORT_KEY]
    assert report["dictionary"]["etag"] == "moi"
    assert [(c["table"], c["source"]) for c in report["range_checks"]] == [("ORDERS", "argument")]
    assert ds.main(["b1"]) == 0


def test_cli_stops_on_old_profile_without_etag(tmp_path, monkeypatch):
    make_report(tmp_path, monkeypatch, FILES)
    led = Ledger(tmp_path / "output" / "b1")
    profile = led.get_data_profile()
    del profile[quality.REPORT_KEY]["dictionary"]["etag"]
    led.set_data_profile(profile)
    assert ds.main(["b1"]) == config.STALE_PROFILE_EXIT_CODE


def test_cli_task_without_dictionary_is_not_stale(tmp_path, monkeypatch):
    files = {k: v for k, v in FILES.items() if k != DICT_KEY}
    assert make_report(tmp_path, monkeypatch, files)["dictionary"]["etag"] is None
    assert ds.main(["b1"]) == 0


@pytest.mark.parametrize("extra", [["--set", "NULL_RATIO_FLAG=0.01"], ["--range", "ORDERS.amount=0:"]])
def test_cli_set_and_range_need_refresh(tmp_path, monkeypatch, extra):
    make_report(tmp_path, monkeypatch, FILES)
    with pytest.raises(SystemExit):
        ds.main(["b1", *extra])


def test_cli_runs_profile_when_missing(tmp_path, monkeypatch, capsys):
    make_report(tmp_path, monkeypatch, FILES)
    led = Ledger(tmp_path / "output" / "b1")
    led.set_data_profile({})
    assert ds.main(["b1", "--no-samples"]) == 0
    saved = led.get_data_profile()
    assert quality.REPORT_KEY in saved and "sample (" not in saved["ai_summary"]["text"]
