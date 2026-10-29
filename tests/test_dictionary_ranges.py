"""Kiem tra khoang hop le khai bao trong dictionary: parse, tu chay range_check, ghi de bang --range, tom tat.
Du lieu tu tao trong code, khong goi mang (R2 gia bang make_r2).

Chay: python -m pytest tests/test_dictionary_ranges.py -v
"""

import pandas as pd
import pytest

from profiling import data_summary as ds
from profiling import dictionary as dd
from profiling import quality
from tests.test_profiling_dictionary import make_r2

MD = """
## SURVEY
| Cột | Kiểu | Định nghĩa | Khoảng hợp lệ |
|---|---|---|---|
| survey_id | string (PK) | Mã | |
| score | int | Điểm | 1..5 |
| wait | float | Phút chờ | >= 0 |
| delta | float | Chênh lệch | -2.5..-0.5 |
| pct | float | Phần trăm | <= 100 |
| bad1 | int | | 5..1 |
| bad2 | int | | 1-5 |
| ghost | int | Không có trong dữ liệu | 0..9 |
"""


def ranges_of(tables, table="SURVEY"):
    return {c["name"]: c["valid_range"] for c in tables[table]["columns"]}


# ---------- parse ----------

@pytest.mark.parametrize("text, expected", [
    ("1..5", {"min": 1, "max": 5}), (" 0 .. 9.5 ", {"min": 0, "max": 9.5}), ("-3..-1", {"min": -3, "max": -1}),
    (">= 0", {"min": 0, "max": None}), (">=-1.5", {"min": -1.5, "max": None}),
    ("<= 100", {"min": None, "max": 100}), ("", None), (None, None),
])
def test_parse_valid_range_forms(text, expected):
    assert dd.parse_valid_range(text) == (expected, None)


@pytest.mark.parametrize("text", ["5..1", "1-5", "> 0", "1..", "a..b", "1..5..9", "between 1 and 5"])
def test_parse_valid_range_bad(text):
    value, error = dd.parse_valid_range(text)
    assert value is None and error


def test_markdown_with_range_column():
    tables, warnings = dd.parse_dictionary_md(MD)
    r = ranges_of(tables)
    assert r["survey_id"] is None and r["score"] == {"min": 1, "max": 5}
    assert r["wait"] == {"min": 0, "max": None} and r["pct"] == {"min": None, "max": 100}
    assert r["delta"] == {"min": -2.5, "max": -0.5}
    assert r["bad1"] is None and r["bad2"] is None  # cot van con, chi bo khoang
    assert len(warnings) == 2
    assert all("SURVEY" in w and "Dòng" in w for w in warnings)
    assert any("bad1" in w and "cận dưới lớn hơn" in w for w in warnings) and any("bad2" in w for w in warnings)


@pytest.mark.parametrize("header", ["Valid range", "Range", "khoang hop le"])
def test_markdown_range_header_aliases(header):
    md = f"## T\n| Column | Type | Definition | {header} |\n|---|---|---|---|\n| x | int | | 0..1 |\n"
    assert ranges_of(dd.parse_dictionary_md(md)[0], "T") == {"x": {"min": 0, "max": 1}}


@pytest.mark.parametrize("col", ["valid_range", "Khoảng hợp lệ", "khoang hop le"])
def test_csv_with_range_column(col):
    df = pd.DataFrame({"table": ["T", "T", "T"], "column": ["a", "b", "c"], "type": ["int"] * 3,
                       "definition": ["", "", ""], col: ["1..5", None, "x"]})
    tables, warnings = dd.parse_dictionary_csv(df)
    assert ranges_of(tables, "T") == {"a": {"min": 1, "max": 5}, "b": None, "c": None}
    assert len(warnings) == 1 and "Dòng 4" in warnings[0] and "'c'" in warnings[0]


def test_without_range_column_unchanged():
    md = "## T\n| Cột | Kiểu | Định nghĩa |\n|---|---|---|\n| a | int (PK) | Mã |\n"
    tables, warnings = dd.parse_dictionary_md(md)
    assert tables["T"]["columns"] == [{"name": "a", "type": "int", "key": "PK", "sensitive": False,
                                       "definition": "Mã", "valid_range": None}]
    assert warnings == []


# ---------- profile_problem ----------

SURVEY = ("survey_id,score,wait,delta,pct,bad1,bad2\n"
          + "".join(f"S{i:02d},{i % 5 + 1},{i},-1,{i * 5},1,1\n" for i in range(18))
          + "S18,0,-3,-1,50,1,1\nS19,9,2,-1,101,1,1\n")


@pytest.fixture
def report(tmp_path, monkeypatch):
    make_r2(tmp_path, monkeypatch, {"input/r1/data_dictionary.md": MD, "input/r1/SURVEY.csv": SURVEY})
    return quality.profile_problem("r1")


def checks_by_col(report):
    return {r["column"]: r for r in report["range_checks"]}


def test_profile_problem_runs_dictionary_ranges(report):
    rc = checks_by_col(report)
    assert set(rc) == {"score", "wait", "delta", "pct"}  # ghost khong co trong du lieu; bad1/bad2 khong co khoang
    assert all(r["source"] == "dictionary" for r in rc.values())
    score = rc["score"]["result"]
    assert score["status"] == "flag" and (score["details"]["below"], score["details"]["above"]) == (1, 1)
    assert rc["wait"]["result"]["details"]["below"] == 1 and rc["pct"]["result"]["details"]["above"] == 1
    assert rc["delta"]["result"]["status"] == "pass"
    flagged = {f["column"] for f in report["tables"]["SURVEY"]["quality_flags"] if f["code"] == "range_violation"}
    assert flagged == {"score", "wait", "pct"}
    assert "Dòng" in " ".join(report["dictionary"]["parse_warnings"])  # dang sai -> parse_warnings


def test_argument_range_overrides_dictionary(tmp_path, monkeypatch):
    make_r2(tmp_path, monkeypatch, {"input/r1/data_dictionary.md": MD, "input/r1/SURVEY.csv": SURVEY})
    report = quality.profile_problem("r1", ranges={"SURVEY.score": (0, 10), "SURVEY.bad1": (None, 0)})
    rc = checks_by_col(report)
    assert rc["score"]["source"] == "argument" and (rc["score"]["min"], rc["score"]["max"]) == (0, 10)
    assert rc["score"]["note"] == "ghi đè khoảng của dictionary (1..5)"
    assert rc["score"]["result"]["status"] == "pass"
    assert rc["bad1"]["source"] == "argument" and "note" not in rc["bad1"]  # dictionary khong co khoang cot nay
    assert rc["wait"]["source"] == "dictionary"
    assert [r["column"] for r in report["range_checks"]][:4] == ["score", "wait", "delta", "pct"]  # giu thu tu


def test_cli_range_overrides(tmp_path, monkeypatch, capsys):
    make_r2(tmp_path, monkeypatch, {"input/r1/data_dictionary.md": MD, "input/r1/SURVEY.csv": SURVEY})
    assert quality.main(["r1", "--no-samples", "--range", "SURVEY.score=0:10"]) == 0
    saved = quality.Ledger(tmp_path / "output" / "r1").get_data_profile()[quality.REPORT_KEY]
    score = next(r for r in saved["range_checks"] if r["column"] == "score")
    assert score["source"] == "argument" and "ghi đè" in score["note"]


def test_summary_shows_valid_range(report):
    text = ds.build_data_summary(report)["text"]
    lines = {l[2:].split(":")[0].split(" ")[0]: l for l in text.splitlines() if l.startswith("- ")}
    assert "valid range 1..5" in lines["score"]
    assert "valid range >= 0" in lines["wait"] and "valid range <= 100" in lines["pct"]
    assert "valid range -2.5..-0.5" in lines["delta"]
    assert "valid range" not in lines["survey_id"]
    assert "- range_violation SURVEY.score" in text
    masked = ds.build_data_summary(report, sensitive=["SURVEY.score"])["text"]
    assert "valid range 1..5" in masked  # khai bao, khong phai gia tri: van in voi cot bi che
