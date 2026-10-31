"""Kiem tra analysis/meta_grader.py. Du lieu gia trong thu muc tam; khong goi R2, khong goi LLM.

Chay: python -m pytest tests/test_meta_grader.py -v
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from analysis import meta_grader as mg
from analysis.meta_grader import grade_meta
from ledger import Ledger
from runner import run_code as rc
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


# ---------- khai bao join khong khop phep noi that ----------

@pytest.fixture
def join_dirs(tmp_path):
    """CUSTOMER 5 khach; ORDER 12 don (moi khach co don co nhieu don, mot khach khong co don);
    work/order_counts.csv: bang trung gian da gop (1 dong / khach co don)."""
    inp, work = tmp_path / "input", tmp_path / "work"
    inp.mkdir()
    work.mkdir()
    (inp / "CUSTOMER.csv").write_text("cid\n" + "".join(f"K{i}\n" for i in range(1, 6)), encoding="utf-8")
    owners = ["K1"] * 4 + ["K2"] * 3 + ["K3"] * 3 + ["K4"] * 2  # K5 khong co don
    (inp / "ORDER.csv").write_text("oid,cid\n" + "".join(f"O{i},{c}\n" for i, c in enumerate(owners)),
                                   encoding="utf-8")
    (work / "order_counts.csv").write_text("cid,n\nK1,4\nK2,3\nK3,3\nK4,2\n", encoding="utf-8")
    return inp, work


def join_meta(right, after):
    return {"result_summary": {"n": 1}, "checks": [
        custom(),
        {"type": "row_count", "label": "noi khach", "before": 5, "after": after, "expect": "free"},
        {"type": "join", "label": "khach-don", "left": "CUSTOMER.csv", "right": right,
         "left_on": "cid", "right_on": "cid", "how": "left"},
    ]}


def test_join_declared_raw_table_but_real_join_keeps_rows(join_dirs):
    # giong R3/R4 bai 1: noi that voi bang da gop (5 -> 5) nhung khai ORDER.csv tho
    r = grade_meta(join_meta("ORDER.csv", 5), step(output_files=[]), *join_dirs)
    assert not r.ok and len(r.structural_errors) == 1
    err = r.structural_errors[0]
    assert "check join 'khach-don' khai báo nối với ORDER.csv làm tăng lên 13 dòng" in err
    assert "check row_count 'noi khach' của phép nối này ghi 5 → 5 dòng" in err and "AGENT_OUTPUT_DIR" in err
    assert "expected_ratio" in err and "trung gian" not in err
    assert by_name(r)["khach-don"]["status"] == "flag"  # check van duoc cham nhu cu
    for value in ("K1", "K2", "O1"):
        assert value not in err


def test_join_declared_intermediate_file_ok(join_dirs):
    r = grade_meta(join_meta("order_counts.csv", 5), step(output_files=["order_counts.csv"]), *join_dirs)
    assert r.ok and r.structural_errors == []
    assert "khóa trùng" not in by_name(r)["khach-don"]["reason"]  # 1 dong / khach: khong nhan ban


def test_join_really_duplicates_rows_keeps_old_behaviour(join_dirs):
    r = grade_meta(join_meta("ORDER.csv", 13), step(output_files=[]), *join_dirs)  # noi that 5 -> 13
    assert r.ok and r.structural_errors == []
    assert by_name(r)["khach-don"]["status"] == "flag" and "khóa trùng" in by_name(r)["khach-don"]["reason"]


def test_join_without_matching_row_count_keeps_old_behaviour(join_dirs):
    meta = join_meta("ORDER.csv", 5)
    meta["checks"] = [c for c in meta["checks"] if c["type"] != "row_count"]
    r = grade_meta(meta, step(output_files=[]), *join_dirs)
    assert r.ok and r.structural_errors == [] and by_name(r)["khach-don"]["status"] == "flag"
    # row_count co nhung khong khop so dong trai (vd. bang khac) -> cung khong doan
    meta = join_meta("ORDER.csv", 5)
    meta["checks"][1].update(before=12, after=12)
    r = grade_meta(meta, step(output_files=[]), *join_dirs)
    assert r.ok and r.structural_errors == []


# ---------- row_count nao thuoc phep noi nao ----------

CHECK_NAME = "khach-don (đối chiếu row_count)"


def labelled_meta(label):
    m = join_meta("ORDER.csv", 5)
    m["checks"][1]["label"] = label
    return m


@pytest.mark.parametrize("label", ["noi khach", "customer_order_merge", "Nối CUSTOMER với ORDER", "merge", "sau khi join",
                                   "Customer segment merge", "ghép đơn"])
def test_row_count_of_this_join_is_structural_error(join_dirs, label):
    r = grade_meta(labelled_meta(label), step(output_files=[]), *join_dirs)
    assert not r.ok and f"check row_count '{label}'" in r.structural_errors[0]


@pytest.mark.parametrize("label", ["customer", "CUSTOMER table load", "customer_df row count", "order_df", "touch customer"])
def test_row_count_of_one_table_is_ignored(join_dirs, label):
    # giong Q4 lan 2: row_count 'order' truoc sau bang nhau khong noi gi ve phep noi
    r = grade_meta(labelled_meta(label), step(output_files=[]), *join_dirs)
    assert r.ok and r.structural_errors == [] and CHECK_NAME not in by_name(r)


@pytest.mark.parametrize("label", ["sau bước 2", "row count", "customer and orders cleaned"])
def test_row_count_of_unknown_owner_is_flag(join_dirs, label):
    r = grade_meta(labelled_meta(label), step(output_files=[]), *join_dirs)
    assert r.ok and r.structural_errors == []
    c = by_name(r)[CHECK_NAME]
    assert c["status"] == "flag" and f"'{label}'" in c["reason"] and "13 dòng" in c["reason"]
    assert any(f.startswith(CHECK_NAME) for f in r.flags)


def test_declared_explosion_passes_even_with_unknown_row_count(join_dirs):
    m = labelled_meta("sau bước 2")
    m["checks"].insert(1, {"type": "row_count", "label": "nối đơn", "before": 5, "after": 13, "expect": "free",
                           "expected_ratio": [1, 4]})
    r = grade_meta(m, step(output_files=[]), *join_dirs)
    assert r.ok and CHECK_NAME not in by_name(r) and by_name(r)["nối đơn"]["status"] == "pass"
    assert "khóa trùng" in by_name(r)["khach-don"]["reason"]  # join_check van bao nhan dong


def test_row_count_of_other_join_named_in_label_is_ignored(join_dirs):
    inp, _ = join_dirs
    (inp / "STORE.csv").write_text("cid,s\nK1,A\nK2,A\nK3,A\nK4,A\nK5,A\n", encoding="utf-8")
    r = grade_meta(labelled_meta("customer_store_join"), step(output_files=[]), *join_dirs)
    assert r.ok and CHECK_NAME not in by_name(r)


def two_joins(row_count_at):
    joins = [{"type": "join", "label": "khach-don", "left": "CUSTOMER.csv", "right": "ORDER.csv",
              "left_on": "cid", "right_on": "cid", "how": "left"},
             {"type": "join", "label": "khach-gop", "left": "CUSTOMER.csv", "right": "order_counts.csv",
              "left_on": "cid", "right_on": "cid", "how": "left"}]
    checks = [custom(), *joins]
    checks.insert(row_count_at, {"type": "row_count", "label": "merge", "before": 5, "after": 5, "expect": "same"})
    return {"result_summary": {"n": 1}, "checks": checks}


def test_row_count_without_table_name_belongs_to_nearest_join(join_dirs):
    grade = lambda at: grade_meta(two_joins(at), step(output_files=[]), *join_dirs)  # noqa: E731
    assert not grade(1).ok  # ngay truoc 'khach-don', join nhan dong
    assert grade(3).ok and CHECK_NAME not in by_name(grade(3))  # ngay sau 'khach-gop', join khong nhan dong
    tie = grade(2)  # giua hai join: khong biet cua join nao
    assert tie.ok and by_name(tie)[CHECK_NAME]["status"] == "flag"


# ---------- dung code that cua Q4 (bai 2), du lieu gia ----------

Q4_ORDER = "order_id,customer_id,order_date\nO1,C1,10-01-2026\nO2,C2,15-02-2026\nO3,C1,01-06-2026\nO4,C3,05-01-2026\n"
Q4_TOUCH = ("touch_id,customer_id,campaign_name,channel,touch_date\nT1,C1,Sale,Web,2026-01-08\nT2,C1,Flash,Web,2026-01-09\n"
            "T3,C1,Sale,Web,2025-12-01\nT4,C2,Sale,Web,2026-02-14\nT5,C2,Flash,Web,2026-01-01\nT6,C2,Flash,Web,2025-01-01\n")


def run_q4(tmp_path, fixture):
    """Join ORDER, CAMPAIGN_TOUCH nhan 4 dong thanh 9; 2 don co touch trong 7 ngay, O1 boi 2 chien dich."""
    inp, work, run_dir, meta_dir = (tmp_path / n for n in ("input", "work", "run", "meta"))
    for d in (inp, work, run_dir, meta_dir):
        d.mkdir()
    (inp / "ORDER.csv").write_text(Q4_ORDER, encoding="utf-8")
    (inp / "CAMPAIGN_TOUCH.csv").write_text(Q4_TOUCH, encoding="utf-8")
    code = (Path(__file__).parent / "fixtures" / fixture).read_text(encoding="utf-8")
    run = rc.execute(code, inp, work, run_dir, meta_dir / "meta.json")
    assert run.ok and run.meta is not None
    return grade_meta(run.meta, step(output_files=["campaign_order_attribution.csv"]), inp, work)


def test_q4_attempt_2_code_passes(tmp_path):
    r = run_q4(tmp_path, "q4_lan2.py")
    assert r.ok and r.structural_errors == []
    assert r.result_summary["total_attributed_orders"] == 2 and r.result_summary["orders_multi_touched"] == 1
    join = by_name(r)["order and campaign_touch join"]
    assert join["status"] == "flag" and "dự kiến 9 dòng" in join["reason"]  # join no dong khong khai: van la co
    assert "order and campaign_touch join (đối chiếu row_count)" not in by_name(r)


def test_q4_attempt_3_code_passes_with_flag(tmp_path):
    # nhan 'orders and touches cleaned' nhac ca hai ben, khong noi la join: khong chan, cho nguoi xem
    r = run_q4(tmp_path, "q4_lan3.py")
    assert r.ok and r.structural_errors == [] and r.result_summary["total_attributed_orders"] == 2
    assert by_name(r)["order and campaign_touch join (đối chiếu row_count)"]["status"] == "flag"
