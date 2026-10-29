"""Kiem tra profiling/relationships.py va phan quan he/khoang gia tri trong profile_problem.
Du lieu tu tao trong code, khong goi mang (R2 duoc thay bang monkeypatch).

Chay: python -m pytest tests/test_profiling_relationships.py -v
"""

import pandas as pd
import pytest

from profiling import quality
from profiling.relationships import check_relationships, infer_relationships, overlap_ratio
from storage import r2

SECRET = "SECRET_VALUE"


def df(**cols):
    return pd.DataFrame(cols, dtype=str)


# ---------- do theo ten ----------

def test_infer_by_column_name_case_insensitive():
    tables = {"CUSTOMER": df(Customer_ID=["C1", "C2"]), "ORDER": df(order_id=["O1"], customer_id=[" C1"]),
              "LINE": df(order_id=["O1", "O1"], line_no=["1", "2"])}
    keys = {"CUSTOMER": ["Customer_ID"], "ORDER": ["order_id"], "LINE": ["order_id", "line_no"]}
    rels = infer_relationships(tables, keys)
    assert rels["relationships"] == [
        {"child": "ORDER", "child_col": "customer_id", "parent": "CUSTOMER", "parent_col": "Customer_ID"},
        {"child": "LINE", "child_col": "order_id", "parent": "ORDER", "parent_col": "order_id"},  # cot thuoc khoa ghep
    ]
    assert rels["notes"] == []


def test_composite_parent_key_skipped():
    tables = {"A": df(x=["1"], y=["2"]), "B": df(x=["1"])}
    assert infer_relationships(tables, {"A": ["x", "y"], "B": None})["relationships"] == []


def test_ambiguous_direction():
    tables = {"A": df(id=["1", "2"]), "B": df(ID=["1", "2"])}
    rels = infer_relationships(tables, {"A": ["id"], "B": ["ID"]})
    assert rels["relationships"] == []
    assert len(rels["notes"]) == 1 and rels["notes"][0]["code"] == "ambiguous_direction"


def test_rejected_when_overlap_low():
    tables = {"CAMPAIGN": df(channel_id=[f"MKT{i:03d}" for i in range(1, 11)]),
              "SALE": df(sale_id=[str(i) for i in range(10)], channel_id=[f"CH{i:03d}" for i in range(1, 11)])}
    out = check_relationships(tables, {"CAMPAIGN": ["channel_id"], "SALE": ["sale_id"]})
    c = out["checks"][0]
    assert c["status"] == "rejected" and c["result"]["overlap"] == 0.0
    assert check_relationships(tables, {"CAMPAIGN": ["channel_id"], "SALE": ["sale_id"]},
                               min_overlap=0)["checks"][0]["status"] == "flag"


def test_overlap_uses_same_normalization():
    assert overlap_ratio(pd.Series(["7.0", " a1 ", "007", None]), pd.Series(["7", "A1"])) == pytest.approx(2 / 3)


def test_check_ok_and_flag():
    tables = {"P": df(pid=["P1", "P2", "P3"]), "C": df(cid=["1", "2", "3"], pid=["P1", "p2", "P9"])}
    out = check_relationships(tables, {"P": ["pid"], "C": ["cid"]})
    c = out["checks"][0]
    d = c["result"]["details"]
    assert c["status"] == "flag" and (d["left_unmatched_raw"], d["recoverable"]) == (2, 1)
    out = check_relationships(tables, {"P": ["pid"], "C": ["cid"]}, unmatched_threshold=0.9)
    assert out["checks"][0]["status"] == "ok"


def test_fk_without_reference():
    tables = {"P": df(pid=["P1"]), "C": df(cid=["1"], pid=["P1"], region_code=["R1"])}
    out = check_relationships(tables, {"P": ["pid"], "C": ["cid"]}, fk_columns={"C": ["pid", "region_code"]})
    assert out["notes"] == [{"code": "fk_without_reference", "table": "C", "column": "region_code"}]


# ---------- profile_problem ----------

CUSTOMER = "customer_id,segment\n" + "".join(f"C{i:03d},A\n" for i in range(1, 11)) + "C010,B\n"
ORDERS = ("order_id,customer_id,channel_id,score\n"
          + "".join(f"O{i:03d},C{i:03d},CH{i:03d},{i % 5 + 1}\n" for i in range(1, 9))
          + f"O009,c001 ,CH009,9\nO010,{SECRET},CH010,0\nO011,C002,CH011,3\n")
CHANNEL = "channel_id\n" + "".join(f"MKT{i:03d}\n" for i in range(1, 11))
DICT_MD = """
## ORDERS
| Cột | Kiểu | Định nghĩa |
|---|---|---|
| order_id | string (PK) | |
| customer_id | string (FK) | |
| channel_id | string (FK) | |
| score | int | |
"""


@pytest.fixture
def fake_r2(tmp_path, monkeypatch):
    files = {"input/b1/CUSTOMER.csv": CUSTOMER, "input/b1/ORDERS.csv": ORDERS, "input/b1/CHANNEL.csv": CHANNEL,
             "input/b1/data_dictionary.md": DICT_MD}
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


def _flags(report):
    return report["quality_flags"] + [f for t in report["tables"].values() for f in t["quality_flags"]]


def test_profile_problem_relationships_and_flags(fake_r2):
    report = quality.profile_problem("b1")
    checks = {(c["child"], c["parent"]): c for c in report["relationship_checks"]}
    cust = checks[("ORDERS", "CUSTOMER")]
    assert cust["status"] == "flag"
    d = cust["result"]["details"]
    assert (d["left_unmatched_raw"], d["recoverable"], d["right_duplicate_keys"]) == (2, 1, 1)
    assert checks[("ORDERS", "CHANNEL")]["status"] == "rejected"
    assert report["relationship_notes"] == [{"code": "fk_without_reference", "table": "ORDERS",
                                             "column": "channel_id"}]

    codes = {(f["code"], f["severity"], f["table"]) for f in _flags(report)}
    assert ("join_unmatched", "flag", "ORDERS") in codes
    assert ("join_right_duplicate_keys", "flag", "CUSTOMER") in codes
    assert ("relationship_rejected", "info", "ORDERS") in codes
    assert ("fk_without_reference", "info", "ORDERS") in codes
    assert ("join_right_unmatched", "info", "CUSTOMER") in codes
    s = report["quality_flags_summary"]
    assert s["flag"] + s["info"] == len(_flags(report))

    text = quality.format_summary(report)
    assert "Quan hệ giữa các bảng:" in text
    assert "ORDERS.customer_id -> CUSTOMER.customer_id: 2 dòng không khớp (18.2%), 1 khớp được nếu chuẩn hóa; " \
           "1 khóa trùng bên CUSTOMER (+0 dòng)" in text  # C010 trung nhung ORDERS khong dung
    assert "ORDERS.channel_id -> CHANNEL.channel_id: bị loại (overlap 0.0%)" in text
    assert "ORDERS.channel_id: dictionary ghi FK nhưng không ghép được bảng nào" in text


def test_messages_have_no_real_values(fake_r2):
    report = quality.profile_problem("b1", ranges={"ORDERS.score": (1, 5)})
    assert SECRET in str(report["relationship_checks"])  # gia tri that chi nam trong details["examples"]
    for f in _flags(report):
        assert SECRET not in f["message"] and "c001" not in f["message"].lower()
    assert SECRET not in quality.format_summary(report)


def test_no_samples_disables_examples(fake_r2):
    report = quality.profile_problem("b1", include_samples=False)
    ex = next(c for c in report["relationship_checks"] if c["status"] != "rejected")["result"]["details"]["examples"]
    assert ex == {"left_unmatched": [], "right_duplicate": []}


def test_threshold_override_join_unmatched(fake_r2):
    report = quality.profile_problem("b1", thresholds={"JOIN_UNMATCHED_FLAG_RATIO": 0.5,
                                                       "RELATIONSHIP_MIN_OVERLAP": 0.0})
    checks = {(c["child"], c["parent"]): c for c in report["relationship_checks"]}
    assert "unmatched" not in checks[("ORDERS", "CUSTOMER")]["result"]["details"]["flags"]
    assert checks[("ORDERS", "CHANNEL")]["status"] == "flag"  # khong con bi loai
    assert not any(f["code"] == "join_unmatched" and f["column"] == "customer_id" for f in _flags(report))
    with pytest.raises(ValueError, match="JOIN_UNMATCHED"):
        quality.profile_problem("b1", thresholds={"JOIN_UNMATCHED": 0.5})


def test_ranges(fake_r2):
    report = quality.profile_problem("b1", ranges={"ORDERS.score": (1, 5), "CUSTOMER.segment": (None, 1)})
    rc = {(r["table"], r["column"]): r for r in report["range_checks"]}
    score = rc[("ORDERS", "score")]["result"]
    assert score["status"] == "flag" and (score["details"]["below"], score["details"]["above"]) == (1, 1)
    assert rc[("CUSTOMER", "segment")]["result"]["details"]["non_numeric"] == 11
    violations = [f for f in _flags(report) if f["code"] == "range_violation"]
    assert {(f["table"], f["column"]) for f in violations} == {("ORDERS", "score"), ("CUSTOMER", "segment")}
    assert "Khoảng giá trị:" in quality.format_summary(report)


@pytest.mark.parametrize("ranges, match", [
    ({"NOPE.score": (1, 5)}, "không có bảng"), ({"ORDERS.nope": (1, 5)}, "không có cột"),
    ({"ORDERS.score": (None, None)}, "ít nhất một cận"), ({"score": (1, 5)}, "BANG.cot"),
])
def test_bad_ranges(fake_r2, ranges, match):
    with pytest.raises(ValueError, match=match):
        quality.profile_problem("b1", ranges=ranges)


def test_cli_range(fake_r2, capsys):
    assert quality.main(["b1", "--no-samples", "--range", "ORDERS.score=1:", "--range", "ORDERS.score=:5"]) == 0
    out = capsys.readouterr().out
    assert "Khoảng giá trị:" in out
    report = quality.profile_problem("b1", ranges=dict([quality._parse_range("ORDERS.score=1:5")]))
    assert report["range_checks"][0]["min"] == 1 and report["range_checks"][0]["max"] == 5
    for bad in ("ORDERS.score=1-5", "ORDERS.score=:", "ORDERS.score=a:5"):
        with pytest.raises(SystemExit):
            quality.main(["b1", "--range", bad])
