"""Cong cu cham tests/grade.py: R2 gia, khong doc answers/ that."""

import json
import tempfile
from contextlib import contextmanager
from pathlib import Path

import pytest

from storage import r2
from tests import grade as grader
from tests import grade_bai0a
from tests import grade_bai1

ANSWER = "## Kết quả\nDoanh thu tổng: 1234\n\n## Câu agent phải hỏi\n- Kỳ dữ liệu nào được dùng?\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Ho so output/bai9 + answers gia; calls ghi moi lan doc R2."""
    calls = {"list": [], "download": [], "paths": []}

    def list_objects(prefix, grader=False):
        calls["list"].append((prefix, grader))
        return [{"key": f"{prefix}answer.md"}]

    @contextmanager
    def grader_download(key):
        calls["download"].append(key)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "answer.md"
            path.write_text(ANSWER, encoding="utf-8")
            calls["paths"].append(path)
            yield path

    monkeypatch.setattr(r2, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(r2, "list_objects", list_objects)
    monkeypatch.setattr(r2, "grader_download", grader_download)
    monkeypatch.setattr(grader, "OUTPUT_ROOT", tmp_path / "output")
    out = tmp_path / "output" / "bai9"
    out.mkdir(parents=True)
    results = [{"id": "R1", "status": "valid", "result_summary": {"doanh_thu": 1234}},
               {"id": "R2", "status": "superseded", "result_summary": {"doanh_thu": 9}}]
    (out / "results.json").write_text(json.dumps(results), encoding="utf-8")
    (out / "decisions.json").write_text(json.dumps([{"id": "D1", "text": "Kỳ dữ liệu được dùng"}]), encoding="utf-8")
    return calls


def test_grades_the_named_task_with_grader_rights(env, capsys):
    assert grader.main(["bai9"]) == 0
    text = capsys.readouterr().out
    assert env["list"] == [("answers/bai9/", True)]
    assert env["download"] == ["answers/bai9/answer.md"]
    assert "số 1234 -> KHỚP" in text and "ĐÃ NÊU" in text and "Kết quả agent: ['R1']" in text


def test_temp_answer_removed_and_cache_clean(env, capsys):
    assert grader.grade("bai9") == 0
    assert not env["paths"][0].exists()
    assert "cache/: không có đường dẫn chứa 'answers'" in capsys.readouterr().out


def test_superseded_decision_does_not_count_as_mentioned(env, capsys):
    old = [{"id": "D1", "text": "Kỳ dữ liệu được dùng", "status": "superseded"}]
    (grader.OUTPUT_ROOT / "bai9" / "decisions.json").write_text(json.dumps(old), encoding="utf-8")
    assert grader.grade("bai9") == 0
    assert "ĐÃ NÊU 0" in capsys.readouterr().out


def test_answers_in_cache_fails(env, capsys):
    bad = r2.CACHE_DIR / "answers" / "bai9"
    bad.mkdir(parents=True)
    (bad / "answer.md").write_text("x", encoding="utf-8")
    assert grader.grade("bai9") == 1
    assert "LỖI: cache/" in capsys.readouterr().out


@pytest.mark.parametrize("task", ["../bai9", "bai9/", "", "a b"])
def test_bad_task_name_never_reaches_r2(env, task):
    assert grader.grade(task) == 2
    assert env["list"] == []


def test_no_agent_results_does_not_read_answers(env):
    assert grader.grade("bai_khac") == 1
    assert env["list"] == []


def test_no_answer_files(env, monkeypatch, capsys):
    monkeypatch.setattr(r2, "list_objects", lambda prefix, grader=False: [])
    assert grader.grade("bai9") == 1
    assert "Không có đáp án trong answers/bai9/" in capsys.readouterr().out


def test_grade_bai1_keeps_working(monkeypatch):
    seen = []
    monkeypatch.setattr(grade_bai1, "grade", lambda task: seen.append(task) or 0)
    assert grade_bai1.main() == 0
    assert seen == ["bai1"]


# grade_bai0a muc 3: y "nhom nhieu san pham nhat"
NOTE = "Nhóm nhiều sản phẩm nhất là CAT001 với 36 sản phẩm"


def judge(summary, agent_cat=None):
    return grade_bai0a.judge_note(NOTE, agent_cat or {}, summary, [], True)[0]


TOP = {"top_category_id": "CAT001", "top_category_name": "Beverages (Coffee/Malt)", "top_category_product_count": 36}


def test_bai0a_top_group_passes_when_agent_list_has_name_and_code():
    assert judge(TOP) == "ĐẠT"  # bai0a that: khong co CSV tong theo nhom nen agent_cat rong


def test_bai0a_top_group_fails_on_wrong_number():
    assert judge({**TOP, "top_category_product_count": 35}) == "RỚT"


def test_bai0a_top_group_fails_on_wrong_code():
    assert judge({**TOP, "top_category_id": "CAT002"}) == "RỚT"


def test_bai0a_top_group_fails_when_agent_lists_extra_code():
    assert judge({**TOP, "top_category_id": "CAT001, CAT002"}) == "RỚT"


def test_bai0a_top_group_fails_without_code():
    assert judge({"top_category_name": "Beverages (Coffee/Malt)", "top_category_product_count": 36}) == "RỚT"


def test_bai0a_other_note_kinds_unchanged():
    assert grade_bai0a.judge_note("CAT002 có 7 sản phẩm", {"CAT002": 8}, {}, [], True)[0] == "RỚT"
    assert grade_bai0a.judge_note("CAT002 có 7 sản phẩm", {"CAT002": 7}, {}, [], True)[0] == "ĐẠT"
