"""Kiem tra analysis/meta_grader.py. Du lieu gia trong thu muc tam; khong goi R2, khong goi LLM.

Chay: python -m pytest tests/test_meta_grader.py -v
"""

import json

import pandas as pd
import pytest

from analysis import meta_grader as mg
from analysis.meta_grader import grade_meta
from ledger import Ledger
from skills.analysis.schemas import AnalysisStep

SECRET = "SECRET_VALUE"


@pytest.fixture
def dirs(tmp_path):
    inp, work = tmp_path / "input", tmp_path / "work"
    inp.mkdir()
    work.mkdir()
    (inp / "PARENT.csv").write_text("pid,email\nP1,a@x.com\nP2,b@x.com\nP2,b@x.com\n", encoding="utf-8")
    (inp / "CHILD.csv").write_text(f"cid;pid\nc1;P1\nc2;P2\nc3;{SECRET}\n", encoding="utf-8")  # dau ;
    (work / "scores.csv").write_text(f"k,score\na,1\nb,5\nc,9\nd,{SECRET}\n", encoding="utf-8")
    pd.DataFrame({"k": ["a", "b"], "score": [2.0, 3.0]}).to_parquet(work / "scores.parquet")
    (work / "out.csv").write_text("x\n1\n", encoding="utf-8")
    return inp, work


def step(**kw):
    base = {"explanation": "e", "method": "m", "can_be_wrong_if": ["rủi ro 1"], "code": "print(1)",
            "output_files": ["out.csv"]}
    return AnalysisStep(**{**base, **kw})


def custom(name="c1", passed=True, reason="ok"):
    return {"type": "custom", "name": name, "passed": passed, "reason": reason}


def meta(*checks, summary=None):
    return {"result_summary": summary or {"total": 1}, "checks": [custom(), *checks]}


def by_name(result):
    return {c["name"]: c for c in result.checks}


# ---------- tung loai check ----------

def test_all_pass_and_ledger_format(dirs, tmp_path):
    m = meta({"type": "row_count", "label": "lọc", "before": 10, "after": 10, "expect": "same"},
             {"type": "sum", "label": "tổng", "parts": [1, 2], "total": 3},
             {"type": "range", "label": "parquet", "file": "scores.parquet", "column": "score", "min": 0, "max": 5},
             {"type": "join", "label": "nối", "left": "CHILD.csv", "right": "CHILD.csv",
              "left_on": "cid", "right_on": "cid", "how": "inner"},
             summary={"total": 3, "label": "A"})
    r = grade_meta(m, step(), *dirs)
    assert r.ok and r.structural_errors == [] and r.flags == []
    assert r.result_summary == {"total": 3, "label": "A"}
    assert [c["name"] for c in r.checks] == ["c1", "lọc", "tổng", "parquet", "nối"]
    assert all(set(c) == {"name", "status", "reason"} and c["status"] == "pass" for c in r.checks)
    entry = Ledger(tmp_path / "led").add_result("q", "m", r.result_summary, r.checks)  # dung dinh dang so ket qua
    assert len(entry.checks) == 5


@pytest.mark.parametrize("check, name", [
    ({"type": "row_count", "label": "lọc", "before": 10, "after": 0}, "lọc"),
    ({"type": "row_count", "label": "lọc", "before": 10, "after": 2, "expected_ratio": [0.5, 1]}, "lọc"),
    ({"type": "sum", "label": "tổng", "parts": [1, 2], "total": 4}, "tổng"),
    ({"type": "range", "label": "điểm", "file": "scores.parquet", "column": "score", "max": 2.5}, "điểm"),
    ({"type": "join", "label": "nối", "left": "CHILD.csv", "right": "PARENT.csv", "left_on": "pid",
      "right_on": "pid"}, "nối"),
])
def test_each_check_flag(dirs, check, name):
    r = grade_meta(meta(check), step(), *dirs)
    assert r.ok  # co flag khong phai loi cau truc
    assert by_name(r)[name]["status"] == "flag" and any(f.startswith(f"{name}: ") for f in r.flags)


def test_custom_flag_uses_meta_reason(dirs):
    r = grade_meta({"result_summary": {}, "checks": [custom("không âm", False, "có 3 giá trị âm")]}, step(), *dirs)
    assert r.ok and r.checks == [{"name": "không âm", "status": "flag", "reason": "có 3 giá trị âm"}]


def test_join_reads_semicolon_csv_and_reports_duplicates(dirs):
    r = grade_meta(meta({"type": "join", "label": "nối", "left": "CHILD.csv", "right": "PARENT.csv",
                         "left_on": "pid", "right_on": "pid"}), step(), *dirs)
    reason = by_name(r)["nối"]["reason"]
    assert "1 khóa trùng" in reason and "1/3 dòng bên trái không khớp" in reason


def test_range_non_numeric_counted(dirs):
    r = grade_meta(meta({"type": "range", "label": "điểm", "file": "scores.csv", "column": "score",
                         "min": 1, "max": 5}), step(), *dirs)
    c = by_name(r)["điểm"]
    assert c["status"] == "flag" and "1 giá trị không phải số" in c["reason"] and "1 trên" in c["reason"]
    assert "cột score trong 'scores.csv'" in c["reason"]


def test_work_dir_searched_before_input(dirs):
    inp, work = dirs
    (inp / "scores.csv").write_text("k,score\na,100\n", encoding="utf-8")  # cung ten o input: khong duoc dung
    r = grade_meta(meta({"type": "range", "label": "điểm", "file": "scores.csv", "column": "score", "max": 10}),
                   step(), inp, work)
    reason = by_name(r)["điểm"]["reason"]
    assert "1 giá trị không phải số" in reason  # ban trong work (co gia tri chu)
    assert "trên" not in reason                 # ban trong input (100 > 10) khong duoc dung


# ---------- loi cau truc ----------

def test_missing_output_file(dirs):
    r = grade_meta(meta(), step(output_files=["out.csv", "missing.csv", "../out.csv"]), *dirs)
    assert not r.ok and len(r.structural_errors) == 2
    assert any("missing.csv" in e for e in r.structural_errors)


def test_missing_file_and_column_in_checks(dirs):
    r = grade_meta(meta({"type": "range", "label": "r1", "file": "nope.csv", "column": "x", "min": 0},
                        {"type": "range", "label": "r2", "file": "scores.csv", "column": "nope", "min": 0},
                        {"type": "join", "label": "j1", "left": "CHILD.csv", "right": "PARENT.csv",
                         "left_on": "pid", "right_on": ["pid", "nope"]},
                        {"type": "range", "label": "r3", "file": "../../etc/passwd", "column": "x", "min": 0}),
                   step(), *dirs)
    assert not r.ok
    c = by_name(r)
    assert all(c[n]["status"] == "flag" and c[n]["reason"].startswith("không chấm được") for n in ("r1", "r2", "j1", "r3"))
    assert "không có file 'nope.csv'" in c["r1"]["reason"] and "không có cột ['nope']" in c["r2"]["reason"]
    assert "không hợp lệ" in c["r3"]["reason"]
    assert len(r.structural_errors) == 4


def test_fewer_custom_than_risks(dirs):
    r = grade_meta(meta(), step(can_be_wrong_if=["a", "b", "c"]), *dirs)
    assert not r.ok and "3 ý nhưng meta chỉ có 1 check custom" in r.structural_errors[0]


@pytest.mark.parametrize("bad", [
    {"result_summary": {}, "checks": []},
    {"result_summary": {}, "checks": [{"type": "magic", "label": "x"}]},
    {"result_summary": {"tổng": 1}, "checks": [custom()]},
    {"checks": [custom()]},
    {"result_summary": {}, "checks": [{"type": "row_count", "label": "x", "before": f"{SECRET}", "after": 1}]},
])
def test_meta_schema_errors(dirs, bad):
    r = grade_meta(bad, step(), *dirs)
    assert not r.ok and r.structural_errors and r.checks == [] and r.result_summary == {}
    assert all(e.startswith("meta sai schema") for e in r.structural_errors)
    assert SECRET not in json.dumps(r.model_dump(), ensure_ascii=False)  # khong lap lai gia tri dau vao


def test_exception_becomes_flag(dirs, monkeypatch):
    def boom(*a, **k):
        raise ValueError(f"không đổi được '{SECRET}'")

    monkeypatch.setattr(mg, "range_check", boom)
    (dirs[1] / "broken.parquet").write_bytes(b"not parquet " + SECRET.encode())
    r = grade_meta(meta({"type": "range", "label": "r", "file": "scores.csv", "column": "score", "min": 0},
                        {"type": "join", "label": "j", "left": "broken.parquet", "right": "PARENT.csv",
                         "left_on": "pid", "right_on": "pid"},
                        {"type": "sum", "label": "s", "parts": [1], "total": 1}),
                   step(), *dirs)
    c = by_name(r)
    assert c["r"] == {"name": "r", "status": "flag", "reason": "không chấm được: ValueError"}
    assert c["j"]["status"] == "flag" and c["j"]["reason"].startswith("không chấm được")
    assert c["s"]["status"] == "pass"  # check khac van duoc cham
    assert not r.ok and len(r.structural_errors) == 2


def test_reasons_never_contain_masked_values(dirs):
    r = grade_meta(meta({"type": "range", "label": "điểm", "file": "scores.csv", "column": "score", "min": 1},
                        {"type": "join", "label": "nối", "left": "CHILD.csv", "right": "PARENT.csv",
                         "left_on": "pid", "right_on": "pid"},
                        {"type": "join", "label": "email", "left": "PARENT.csv", "right": "PARENT.csv",
                         "left_on": "email", "right_on": "email"}),
                   step(), *dirs)
    dumped = json.dumps(r.model_dump(), ensure_ascii=False)
    for value in (SECRET, "a@x.com", "b@x.com", "P2"):
        assert value not in dumped
