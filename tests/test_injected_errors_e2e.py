"""Test dau-cuoi bang du lieu loi co y: dung bai gia trong code, chay profile_problem, kiem co, tom tat cho AI,
va mo phong mot buoc AI (merge + row_count_check + ghi so ket qua). Khong goi mang (R2 gia bang make_r2).

Chay: python -m pytest tests/test_injected_errors_e2e.py -v
"""

import io
import json

import pandas as pd
import pytest

import config
from ledger import Ledger
from profiling import data_summary as ds
from profiling import quality
from tests.test_profiling_dictionary import make_r2
from validators import row_count_check, to_ledger_check

N_PARENT, N_CHILD = 20, 80
DUP_KEYS = ["P001", "P002", "P003"]            # Ca 1: ban sao y het
LOWER_ROWS, SPACE_ROWS, ORPHAN_ROWS = [5, 6], [7], [8, 9, 10, 11]  # dong CHILD bi sua khoa
REGIONS = ["North", "South", "East", "West"]

DICT_MD = """
## PARENT
| Cột | Kiểu | Định nghĩa |
|---|---|---|
| pid | string (PK) | Mã cha |
| name | string | Tên |
| region | string | Vùng |

## CHILD
| Cột | Kiểu | Định nghĩa |
|---|---|---|
| cid | string (PK) | Mã con |
| pid | string (FK) | Mã cha, tham chiếu PARENT |
| amount | int | Số tiền |
| score | int | Điểm 1-5 |
"""


def build_case(dirty):
    """Tra ve (files cho make_r2, truth: cac so ky vong tinh tu luc cai loi)."""
    parent = pd.DataFrame({"pid": [f"P{i:03d}" for i in range(1, N_PARENT + 1)],
                           "name": [f"Name {i:02d}" for i in range(1, N_PARENT + 1)],
                           "region": [REGIONS[i % len(REGIONS)] for i in range(N_PARENT)]})
    child = pd.DataFrame({"cid": [f"C{i:04d}" for i in range(1, N_CHILD + 1)],
                          "pid": [parent["pid"][i % N_PARENT] for i in range(N_CHILD)],  # moi cha deu duoc tham chieu
                          "amount": [str(100 + 7 * i) for i in range(N_CHILD)],
                          "score": [str(i % 5 + 1) for i in range(N_CHILD)]}, dtype=object)
    truth = {"dup_keys": 0, "dup_rows": 0, "extra_rows": 0, "null_score": 0, "unmatched": 0, "recoverable": 0}
    if dirty:
        parent = pd.concat([parent, parent[parent["pid"].isin(DUP_KEYS)]], ignore_index=True)
        touched = LOWER_ROWS + SPACE_ROWS + ORPHAN_ROWS
        assert not child.loc[touched, "pid"].isin(DUP_KEYS).any()  # dong bi sua khong tham chieu khoa trung
        for i in LOWER_ROWS:
            child.at[i, "pid"] = child.at[i, "pid"].lower()
        for i in SPACE_ROWS:
            child.at[i, "pid"] = child.at[i, "pid"] + " "
        for n, i in enumerate(ORPHAN_ROWS):
            child.at[i, "pid"] = f"P9{n:02d}"
        null_rows = [i for i in range(N_CHILD) if i % 5 in (0, 1)]  # Ca 2: 40% null
        child.loc[null_rows, "score"] = None
        truth.update(dup_keys=len(DUP_KEYS), dup_rows=int(parent.duplicated().sum()),
                     extra_rows=int(child["pid"].isin(DUP_KEYS).sum()), null_score=len(null_rows),
                     unmatched=len(touched), recoverable=len(LOWER_ROWS) + len(SPACE_ROWS))
    files = {"data_dictionary.md": DICT_MD, "PARENT.csv": parent.to_csv(index=False),
             "CHILD.csv": child.to_csv(index=False)}
    truth["n_child"], truth["n_parent"] = len(child), len(parent)
    return files, truth


def run(tmp_path, monkeypatch, problem_id, dirty):
    files, truth = build_case(dirty)
    make_r2(tmp_path, monkeypatch, {f"input/{problem_id}/{k}": v for k, v in files.items()})
    return quality.profile_problem(problem_id), files, truth


def all_flags(report):
    return report["quality_flags"] + [f for t in report["tables"].values() for f in t["quality_flags"]]


def find(report, code, table=None, column=None):
    return [f for f in all_flags(report) if f["code"] == code
            and (table is None or f["table"] == table) and (column is None or f["column"] == column)]


# ---------- A. bai co loi ----------

@pytest.fixture
def dirty(tmp_path, monkeypatch):
    return run(tmp_path, monkeypatch, "bai_test", dirty=True)


def test_duplicate_parent_rows_multiply_join(dirty):
    report, _, truth = dirty
    parent = report["tables"]["PARENT"]
    assert parent["key_source"] == "dictionary"
    assert (parent["duplicate_key_count"], parent["duplicate_key_rows"]) == (truth["dup_keys"], truth["dup_rows"])
    assert [f["severity"] for f in find(report, "duplicate_key", "PARENT")] == ["flag"]
    f = find(report, "join_right_duplicate_keys", "PARENT", "pid")
    assert [x["severity"] for x in f] == ["flag"] and f[0]["value"] == truth["dup_keys"]
    rel = next(c for c in report["relationship_checks"] if (c["child"], c["parent"]) == ("CHILD", "PARENT"))
    assert rel["result"]["details"]["extra_rows"] == truth["extra_rows"]
    assert f"tăng {truth['extra_rows']} dòng" in f[0]["message"]


def test_null_score(dirty):
    report, _, truth = dirty
    score = report["tables"]["CHILD"]["columns"]["score"]
    assert score["null_count"] == truth["null_score"]
    f = find(report, "null_ratio_high", "CHILD", "score")
    assert [x["severity"] for x in f] == ["flag"] and f[0]["value"] == truth["null_score"] / truth["n_child"]


def test_unmatched_and_key_format(dirty):
    report, _, truth = dirty
    f = find(report, "join_unmatched", "CHILD", "pid")
    assert [x["severity"] for x in f] == ["flag"]
    d = next(c for c in report["relationship_checks"] if c["child"] == "CHILD")["result"]["details"]
    assert (d["left_unmatched_raw"], d["recoverable"]) == (truth["unmatched"], truth["recoverable"])
    assert d["left_unmatched_normalized"] == truth["unmatched"] - truth["recoverable"]  # = so dong mo coi
    assert f"{truth['recoverable']} dòng khớp được" in f[0]["message"]
    assert [x["severity"] for x in find(report, "pattern_deviation", "CHILD", "pid")] == ["flag"]
    assert [x["severity"] for x in find(report, "whitespace", "CHILD", "pid")] == ["flag"]


def test_summary_counts_and_saved_profile(dirty, tmp_path):
    report, _, _ = dirty
    flags = all_flags(report)
    s = report["quality_flags_summary"]
    assert (s["flag"], s["info"]) == (sum(f["severity"] == "flag" for f in flags),
                                      sum(f["severity"] == "info" for f in flags))
    for name, counts in s["by_table"].items():
        mine = report["tables"][name]["quality_flags"]
        assert counts == {"flag": sum(f["severity"] == "flag" for f in mine),
                          "info": sum(f["severity"] == "info" for f in mine)}
    saved = json.loads((tmp_path / "output" / "bai_test" / "data_profile.json").read_text(encoding="utf-8"))
    assert saved[quality.REPORT_KEY]["quality_flags_summary"] == s


def test_ai_summary_of_dirty_report(dirty):
    report, _, _ = dirty
    summary = ds.build_data_summary(report)
    text = summary["text"]
    for code in ("duplicate_key", "join_right_duplicate_keys", "null_ratio_high", "join_unmatched",
                 "pattern_deviation", "whitespace"):
        assert f"- {code} " in text
    assert summary["chars"] <= config.SUMMARY_MAX_CHARS

    examples = {str(v) for c in report["relationship_checks"] if c["status"] != "rejected"
                for vs in c["result"]["details"]["examples"].values() for v in vs}
    assert examples  # bai loi co vi du khoa khong khop / khoa trung
    no_samples = ds.build_data_summary(report, include_samples=False)["text"]
    assert not [v for v in examples if v.strip() and v.strip() in no_samples]
    shown = {str(r.get(c)) for t in report["tables"].values() for r in t.get("sample_records") or [] for c in r}
    shown |= {str(v["value"]) for t in report["tables"].values() for i in t["columns"].values()
              for v in i.get("top_values") or []}
    leaked = [v for v in examples - shown if v.strip() and f"| {v.strip()} |" in text]
    assert not leaked  # vi du chi xuat hien neu no cung la gia tri mau/top hop le


# ---------- B. bai sach ----------

def test_clean_problem_has_no_flags_and_no_info(tmp_path, monkeypatch):
    report, _, truth = run(tmp_path, monkeypatch, "bai_clean", dirty=False)
    assert set(report["tables"]) == {"PARENT", "CHILD"}
    assert report["tables"]["CHILD"]["n_rows"] == truth["n_child"]
    flags = all_flags(report)
    assert [(f["code"], f["table"], f["column"]) for f in flags] == []
    assert report["quality_flags_summary"]["flag"] == 0 and report["quality_flags_summary"]["info"] == 0
    assert [c["status"] for c in report["relationship_checks"]] == ["ok"]


# ---------- C. mo phong mot buoc AI ----------

def _frames(files):
    read = lambda name: pd.read_csv(io.StringIO(files[name]), dtype=str)  # noqa: E731
    return read("CHILD.csv"), read("PARENT.csv")


@pytest.mark.parametrize("dirty_case", [True, False])
def test_ai_step_merge_row_count_and_ledger(tmp_path, dirty_case):
    files, truth = build_case(dirty_case)
    child, parent = _frames(files)
    merged = pd.merge(child, parent, on="pid", how="left")
    assert len(merged) - len(child) == truth["extra_rows"]
    result = row_count_check(len(child), len(merged), expect="same", label="merge CHILD-PARENT")
    assert result["status"] == ("flag" if dirty_case else "pass")
    assert result["details"]["after"] - result["details"]["before"] == truth["extra_rows"]

    led = Ledger(tmp_path / "ledger")
    entry = led.add_result("Tổng amount theo vùng", "merge CHILD với PARENT theo pid rồi group by region",
                           {"rows": len(merged)}, checks=[to_ledger_check(result)])
    saved = led.get_result(entry.id)
    assert [(c.name, c.status.value) for c in saved.checks] == [(result["name"], result["status"])]
