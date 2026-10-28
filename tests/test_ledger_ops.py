"""Kiem tra ledger/ledger.py. Dung tmp_path, khong mang.

Chay: python -m pytest tests/test_ledger_ops.py -v
"""

import json

import pytest

from ledger import Check, CheckStatus, DecisionStatus, Ledger, ResultStatus

OK = [Check(name="no_dup_keys", status="pass")]


@pytest.fixture
def led(tmp_path):
    return Ledger(tmp_path / "bai1")


def add(led, n=1, **kw):
    return led.add_result(f"Câu hỏi {n}", "pandas sum", {"total": n}, kw.pop("checks", OK), **kw)


# ---------- add_result ----------

def test_creates_folder_without_existing_files(tmp_path):
    root = tmp_path / "output" / "bai1"
    led = Ledger(root)
    assert root.is_dir()
    assert led.results() == [] and led.decisions() == [] and led.get_problem() is None


def test_add_result_assigns_ids(led):
    r1 = add(led, 1)
    r2 = add(led, 2)
    assert (r1.id, r2.id) == ("R1", "R2")
    assert led.get_result("R2").result_summary == {"total": 2}
    assert r1.status is ResultStatus.VALID


def test_add_result_id_is_max_plus_one(led, tmp_path):
    add(led, 1)
    add(led, 2)
    data = json.loads((led.root / "results.json").read_text(encoding="utf-8"))
    data = [d for d in data if d["id"] != "R1"]  # con R2
    (led.root / "results.json").write_text(json.dumps(data), encoding="utf-8")
    assert add(led, 3).id == "R3"


def test_add_result_requires_checks(led):
    with pytest.raises(ValueError, match="checks"):
        add(led, checks=[])
    with pytest.raises(ValueError):
        add(led, checks=None)
    assert led.results() == []
    # unchecked van duoc, mien la khai
    r = add(led, checks=[Check(name="cross_check", status="unchecked", reason="chưa có số đối chiếu")])
    assert r.checks[0].status is CheckStatus.UNCHECKED


def test_add_result_accepts_check_dicts(led):
    r = add(led, checks=[{"name": "rows", "status": "pass"}])
    assert r.checks[0].name == "rows"


def test_add_result_unknown_dependency(led):
    add(led, 1)
    with pytest.raises(ValueError, match="R9"):
        add(led, 2, depends_on=["R1", "R9"])
    assert [r.id for r in led.results()] == ["R1"]


# ---------- mark_stale ----------

def test_mark_stale_chain(led):
    add(led, 1)
    add(led, 2, depends_on=["R1"])
    add(led, 3, depends_on=["R2"])
    assert led.mark_stale("R1") == ["R2", "R3"]
    status = {r.id: r.status for r in led.results()}
    assert status == {"R1": ResultStatus.SUPERSEDED, "R2": ResultStatus.STALE, "R3": ResultStatus.STALE}


def test_mark_stale_leaves_independent_results(led):
    add(led, 1)
    add(led, 2, depends_on=["R1"])
    add(led, 3)                      # khong phu thuoc
    add(led, 4, depends_on=["R3"])   # phu thuoc R3, khong lien quan R1
    assert led.mark_stale("R1") == ["R2"]
    status = {r.id: r.status for r in led.results()}
    assert status["R3"] is ResultStatus.VALID and status["R4"] is ResultStatus.VALID


def test_mark_stale_keeps_superseded_and_handles_cycle(led):
    add(led, 1)
    add(led, 2, depends_on=["R1"])
    add(led, 3, depends_on=["R2"])
    led.mark_stale("R2")  # R2 superseded, R3 stale
    # tao vong R1 -> R3 bang tay de kiem tra an toan
    data = json.loads((led.root / "results.json").read_text(encoding="utf-8"))
    data[0]["depends_on"] = ["R3"]
    (led.root / "results.json").write_text(json.dumps(data), encoding="utf-8")

    assert led.mark_stale("R1") == []  # R2 superseded giu nguyen, R3 da stale
    status = {r.id: r.status for r in led.results()}
    assert status == {"R1": ResultStatus.SUPERSEDED, "R2": ResultStatus.SUPERSEDED, "R3": ResultStatus.STALE}


def test_mark_stale_logs_event_and_unknown_id(led):
    add(led, 1)
    add(led, 2, depends_on=["R1"])
    led.mark_stale("R1")
    events = led.read_log()
    assert events[-1].action == "mark_stale"
    assert events[-1].inputs == {"result_id": "R1"} and events[-1].extra == {"stale": ["R2"]}
    with pytest.raises(KeyError):
        led.mark_stale("R9")


# ---------- get_summary ----------

def test_summary_excludes_stale_and_superseded(led):
    add(led, 1)
    add(led, 2, depends_on=["R1"])
    add(led, 3)
    led.mark_stale("R1")
    text = led.get_summary()
    assert "R3 |" in text
    assert "R1 |" not in text and "R2 |" not in text


def test_summary_respects_max_chars_with_50_results(led):
    for n in range(1, 51):
        add(led, n)
    led.add_decision("metric_definition", "Doanh thu = tổng amount đơn completed")
    text = led.get_summary(max_chars=800)
    assert len(text) <= 800
    assert "R50 |" in text          # giu ket qua moi nhat
    assert "R1 |" not in text       # bo ket qua cu nhat
    assert "... còn" in text and "kết quả nữa" in text
    assert "D1" in text and "Số phát hiện: 0" in text
    shown = text.count(" | Câu hỏi")
    assert f"... còn {50 - shown} kết quả nữa" in text


def test_summary_fits_without_truncation(led):
    add(led, 1)
    text = led.get_summary()
    assert "còn" not in text and "R1 | Câu hỏi 1" in text


def test_summary_tiny_limit(led):
    led.add_decision("assumption", "x" * 500)
    assert len(led.get_summary(max_chars=50)) <= 50


# ---------- log_event ----------

def test_log_event_appends_one_json_line(led):
    path = led.root / "run_log.jsonl"
    led.log_event("analysis", "run_code", inputs={"case": "A01"}, code_file="code/a.py",
                  checks=[{"name": "runs", "status": "pass"}])
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1
    led.log_event("report", "render", status="error", extra={"llm": {"model_key": "x"}})
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["inputs"] == {"case": "A01"} and first["checks"][0]["status"] == "pass"
    assert second["status"] == "error" and second["extra"] == {"llm": {"model_key": "x"}}


# ---------- decisions ----------

def test_pending_required_and_confirm(led):
    d1 = led.add_decision("metric_definition", "Doanh thu = tổng amount đơn completed")
    d2 = led.add_decision("assumption", "Bỏ đơn thiếu amount", required=False)
    d3 = led.add_decision("anomaly_handling", "Bỏ dòng khách hàng trùng")
    assert (d1.id, d2.id, d3.id) == ("D1", "D2", "D3")
    assert [d.id for d in led.pending_required()] == ["D1", "D3"]

    c = led.confirm_decision("D1", at="2026-10-28T10:00:00+07:00")
    assert c.status is DecisionStatus.CONFIRMED and c.confirmed_at == "2026-10-28T10:00:00+07:00"
    assert [d.id for d in led.pending_required()] == ["D3"]
    with pytest.raises(ValueError):
        led.confirm_decision("D1")
    with pytest.raises(KeyError):
        led.confirm_decision("D9")


# ---------- findings ----------

def test_add_finding(led):
    add(led, 1)
    f = led.add_finding("Web dẫn đầu ({{R1.total}})", "evidence", ["R1"])
    assert f.id == "F1"
    assert led.add_finding("Có thể do khuyến mãi", "hypothesis").id == "F2"
    with pytest.raises(ValueError, match="R7"):
        led.add_finding("x", "evidence", ["R7"])
    with pytest.raises(ValueError):
        led.add_finding("x", "evidence", [])  # schema: evidence phai co result_ids


# ---------- doc lai ----------

def test_reload_with_new_ledger(led):
    led.set_problem({"title": "Bài 1", "question": "Doanh thu theo kênh?"})
    led.set_data_profile({"orders": {"rows": 400}})
    add(led, 1)
    add(led, 2, depends_on=["R1"])
    led.add_decision("metric_definition", "Doanh thu = tổng amount")
    led.confirm_decision("D1")
    led.add_finding("Web dẫn đầu", "evidence", ["R1"])
    led.add_open_item("Hỏi lại định nghĩa khách mới")
    led.log_event("x", "y")

    again = Ledger(led.root)
    assert again.results() == led.results()
    assert again.decisions() == led.decisions()
    assert again.findings() == led.findings()
    assert again.open_items() == led.open_items()
    assert again.get_problem() == {"title": "Bài 1", "question": "Doanh thu theo kênh?"}
    assert again.get_data_profile() == {"orders": {"rows": 400}}
    assert len(again.read_log()) == 1
    assert again.add_result("q", "m", {"total": 3}, OK).id == "R3"


def test_json_files_utf8_indented(led):
    led.add_decision("assumption", "Giả định có dấu tiếng Việt")
    raw = (led.root / "decisions.json").read_text(encoding="utf-8")
    assert "Giả định có dấu tiếng Việt" in raw  # ensure_ascii=False
    assert '\n  {' in raw  # indent=2
    assert not list(led.root.glob("*.tmp"))  # khong de lai file tam


# ---------- render_markdown ----------

def test_render_markdown(led, tmp_path):
    led.set_problem({"title": "Bài 1"})
    add(led, 1, checks=[Check(name="rows", status="flag", reason="thiếu 9 dòng amount")])
    add(led, 2, depends_on=["R1"])
    led.mark_stale("R1")
    led.add_decision("metric_definition", "Doanh thu = tổng amount")
    led.add_finding("Web dẫn đầu", "evidence", ["R2"])
    o = led.add_open_item("Hỏi lại định nghĩa")
    led.close_open_item(o.id)

    out = tmp_path / "report" / "ledger.md"
    md = led.render_markdown(out)
    assert out.read_text(encoding="utf-8") == md
    for part in ["## Đề bài", "Bài 1", "## Quyết định", "D1", "đang chờ", "## Kết quả",
                 "### R1: Câu hỏi 1", "superseded", "stale", "rows: flag (thiếu 9 dòng amount)",
                 "Phụ thuộc: R1", "## Phát hiện", "F1", "dựa trên R2", "## Việc còn mở", "[x] **O1**"]:
        assert part in md, part


def test_render_markdown_empty(led):
    md = led.render_markdown()
    assert "## Kết quả" in md and "_Chưa có._" in md and "## Đề bài" not in md
