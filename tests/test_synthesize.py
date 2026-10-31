"""Kiem tra pha tong hop: dien so, dinh dang, validate_synthesis, nhan, thu lai, ngan sach prompt. FakeLLM, khong goi that.

Chay: python -m pytest tests/test_synthesize.py -v
"""

import json

import pandas as pd
import pytest

import config
import context_builder as cb
import llm
from analysis import loop
from ledger import Ledger, Phase
from ledger.schema import ResultEntry
from llm import LLMChainExhausted
from report.placeholders import (
    HYPOTHESIS,
    VERIFIED,
    assign_label,
    fill,
    find_placeholders,
    format_value,
    requirements_of,
    validate_synthesis,
)
from report.synthesize import _results_text, build_variables, print_insights, synthesize
from skills.report import load_prompt, render_prompt
from skills.report.schemas import SynthesisOutput

TASK = "bai0a"
REQUIREMENTS = ["Tổng doanh thu", "Tỷ lệ rời bỏ"]
PROBLEM = {"problem": "Đề gốc: tính doanh thu và tỷ lệ rời bỏ.", "requirements": REQUIREMENTS}
PASS = [{"name": "c", "status": "pass"}]
GOOD = {"summary": "Doanh thu đạt {{R1.total}}.",
        "insights": [{"id": "I1", "requirement": 1, "text": "Tổng doanh thu là {{R1.total}} (Q1, D1).",
                      "result_ids": ["R1"]},
                     {"id": "I2", "requirement": 2, "text": "Tỷ lệ rời bỏ {{R2.churn_pct}}%, nhóm {{R2.top}}.",
                      "result_ids": ["R2"]}],
        "conclusion": "Nên giữ nhóm {{R2.top}}.", "limitations": "Theo D1, chưa kiểm định."}


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path / "output" / TASK)
    ledger.set_state(Phase.SYNTHESIZE, plan={"requirements": REQUIREMENTS})
    ledger.set_problem({"problem": PROBLEM["problem"], "restatement": {"goals": ["khác"]}})
    ledger.add_result("Tổng doanh thu?", "pandas sum", {"total": 1234567.891, "note": None}, PASS, question_key="Q1")
    ledger.add_result("Tỷ lệ rời bỏ?", "pandas mean", {"churn_pct": 26.96, "rate": 0.2696, "top": "Value"},
                      PASS, question_key="Q2")
    return ledger


def output(**changes):
    return SynthesisOutput.model_validate({**json.loads(json.dumps(GOOD)), **changes})


def with_insight(**changes):
    """GOOD voi I1 doi vai truong."""
    first = {**GOOD["insights"][0], **changes}
    return output(insights=[first, GOOD["insights"][1]])


def errors_of(led, out):
    return validate_synthesis(out, PROBLEM, led.results())


def run(script, led, **kw):
    fake = llm.FakeLLM({"report": list(script)})
    with llm.use_fake(fake):
        return synthesize(led, TASK, **kw), fake


# 1. dinh dang so
@pytest.mark.parametrize("value, text", [
    (1234567, "1.234.567"), (0, "0"), (-1200, "-1.200"), (1234.5, "1.234,5"), (1234.567, "1.234,57"), (0.125, "0,125"),
    (2.675, "2,68"), (0.2696, "0,27"), (1000.0, "1.000"), (2.50, "2,5"), (-0.001, "-0,001"), (1e22, "10.000.000.000.000.000.000.000"),
    ("Value", "Value"), ("1234.5", "1234.5"), (True, "có"), (False, "không"), (None, "không có dữ liệu"),
    (float("nan"), "không có dữ liệu"),
])
def test_format_value(value, text):
    assert format_value(value) == text


@pytest.mark.parametrize("value, text", [
    (0.0513, "0,0513"), (0.0519, "0,0519"), (0.2642, "0,264"), (1250000, "1.250.000"), (12.345, "12,35"), (0, "0"),
    (0.0, "0"), (-0.0, "0"), (0.5, "0,5"), (0.10, "0,1"), (0.0001234, "0,000123"), (-0.2642, "-0,264"), (0.9996, "1"),
    (1.0, "1"), (1.004, "1"), (-12.345, "-12,35"),
])
def test_format_small_numbers_by_significant_digits(value, text):
    assert format_value(value) == text


def test_rate_is_not_converted_to_percent():
    assert format_value(0.5) == "0,5"


def test_find_placeholders_and_spaces():
    assert find_placeholders("a {{R1.x}} b {{ R12.y_2 }} {{R1.a.b}} {R3.z}") == [("R1", "x"), ("R12", "y_2")]


def test_fill_uses_result_summary_and_keeps_unknown(led):
    text = "{{R1.total}} / {{R2.top}} / {{R2.khong_co}} / {{R9.x}} / {{ R1.note }}"
    assert fill(text, led.results()) == "1.234.567,89 / Value / {{R2.khong_co}} / {{R9.x}} / không có dữ liệu"


# 2. kiem tra
def test_good_output_has_no_errors(led):
    assert errors_of(led, output()) == []


def test_a_unknown_result(led):
    errors = errors_of(led, with_insight(text="Không có số.", result_ids=["R9"]))
    assert "I1: result_ids có R9 không tồn tại." in errors


def test_a_result_not_valid(led):
    led.mark_stale("R1")
    assert any("R1 có status=superseded" in e for e in errors_of(led, output()))


def test_b_placeholder_outside_result_ids_of_that_insight(led):
    errors = errors_of(led, with_insight(text="{{R2.top}} và {{R1.total}}"))
    assert errors == ["I1: {{R2.top}} trỏ tới R2 không nằm trong result_ids của insight này."]


def test_b_unknown_field_lists_real_fields(led):
    errors = errors_of(led, with_insight(text="{{R1.tong}}"))
    assert errors == ["I1: R1 không có trường tong; các trường có: total, note."]


def test_b_free_text_must_cite_result_used_by_some_insight(led):
    only_r1 = output(insights=[GOOD["insights"][0], {**GOOD["insights"][1], "text": "Chưa trả lời được.",
                                                     "result_ids": []}])
    errors = errors_of(led, only_r1)
    assert errors == [f"{name}: {{{{R2.top}}}} trỏ tới R2 không nằm trong result_ids của insight nào."
                      for name in ("conclusion",)]


@pytest.mark.parametrize("field", ["summary", "conclusion", "limitations"])
def test_c_typed_number_in_free_text(led, field):
    errors = errors_of(led, output(**{field: "Tăng 12,5 trong năm 2024."}))
    assert len(errors) == 1 and errors[0].startswith(f"{field}: có chữ số tự gõ (12,5, 2024)")


def test_c_typed_number_in_insight(led):
    assert errors_of(led, with_insight(text="{{R1.total}} tăng 5"))[0].startswith("I1: có chữ số tự gõ (5)")


def test_c_identifiers_allowed_others_not(led):
    assert errors_of(led, with_insight(text="Xem R1, Q2, I3, D4.")) == []
    assert "có chữ số tự gõ" in errors_of(led, with_insight(text="Xem B5."))[0]
    assert "có chữ số tự gõ" in errors_of(led, with_insight(text="Xem R1a2."))[0]


def test_d_requirement_without_insight(led):
    out = output(insights=[GOOD["insights"][0]], summary="Tóm tắt.", conclusion="Kết luận.")
    assert errors_of(led, out) == ["Yêu cầu 2 chưa có insight nào."]


@pytest.mark.parametrize("n", [3, 0])
def test_d_requirement_out_of_range(led, n):
    errors = errors_of(led, with_insight(requirement=n))
    assert f"I1: requirement {n} ngoài khoảng 1..2." in errors and "Yêu cầu 1 chưa có insight nào." in errors


@pytest.mark.parametrize("text", ["{{R1.total.x}}", "{R1.total}", "{{R1 total}}", "{{total}}"])
def test_e_malformed_placeholder_left(led, text):
    assert any("placeholder sai dạng" in e for e in errors_of(led, with_insight(text=text)))


def test_e_nothing_left_after_filling_valid_output(led):
    for ins in output().insights:
        assert "{" not in fill(ins.text, led.results())


@pytest.mark.parametrize("text, bad", [("{{R2.churn_pct}}%", False), ("{{R2.churn_pct}} %", False),
                                       ("{{R2.rate}}%", True), ("khoảng % khách", True)])
def test_percent_only_after_percent_field(led, text, bad):
    out = output(insights=[GOOD["insights"][0], {**GOOD["insights"][1], "text": text}])
    assert any("dấu %" in e for e in errors_of(led, out)) == bad


def test_requirements_fall_back_to_goals():
    assert requirements_of({"requirements": [], "restatement": {"goals": ["a"]}}) == ["a"]
    assert requirements_of({"requirements": ["b"], "restatement": {"goals": ["a"]}}) == ["b"]


def test_schema_rejects_duplicate_insight_ids():
    with pytest.raises(ValueError):
        output(insights=[GOOD["insights"][0], GOOD["insights"][0]])


# 3. nhan
def test_label_verified_only_when_all_checks_pass(led):
    led.add_result("Cờ", "m", {"a": 1}, [{"name": "x", "status": "pass"}, {"name": "y", "status": "flag"}])
    led.add_result("Chưa kiểm", "m", {"a": 1}, [{"name": "x", "status": "unchecked"}])
    results = led.results()
    assert assign_label(["R1", "R2"], results) == VERIFIED
    assert assign_label(["R1", "R3"], results) == HYPOTHESIS
    assert assign_label(["R4"], results) == HYPOTHESIS
    assert assign_label([], results) == HYPOTHESIS  # khong co can cu
    assert assign_label(["R9"], results) == HYPOTHESIS


def entry(rid, depends_on=(), check="pass", status="valid"):
    return ResultEntry(id=rid, question="q", method="m", result_summary={"a": 1}, depends_on=list(depends_on),
                       checks=[{"name": "c", "status": check}], status=status)


def test_label_dependency_with_flag_is_hypothesis():
    results = [entry("R1", check="flag"), entry("R2", ["R1"])]
    assert assign_label(["R2"], results) == HYPOTHESIS


def test_label_chain_all_pass_is_verified():
    results = [entry("R1"), entry("R2", ["R1"]), entry("R3", ["R2"])]
    assert assign_label(["R3"], results) == VERIFIED


def test_label_flag_deep_in_chain_is_hypothesis():
    results = [entry("R1", check="unchecked"), entry("R2", ["R1"]), entry("R3", ["R2"])]
    assert assign_label(["R3"], results) == HYPOTHESIS


def test_label_dependency_missing_is_hypothesis():
    assert assign_label(["R2"], [entry("R2", ["R9"])]) == HYPOTHESIS


@pytest.mark.parametrize("status", ["stale", "superseded"])
def test_label_dependency_not_valid_is_hypothesis(status):
    assert assign_label(["R2"], [entry("R1", status=status), entry("R2", ["R1"])]) == HYPOTHESIS


def test_label_cited_result_not_valid_is_hypothesis():
    assert assign_label(["R1"], [entry("R1", status="stale")]) == HYPOTHESIS


def test_label_dependency_cycle_ends():
    assert assign_label(["R1"], [entry("R1", ["R2"]), entry("R2", ["R1"])]) == VERIFIED
    assert assign_label(["R1"], [entry("R1", ["R2"]), entry("R2", ["R1"], check="flag")]) == HYPOTHESIS


def test_record_label_follows_dependency(led):
    led.add_result("Có cờ", "m", {"a": 1}, [{"name": "y", "status": "flag"}])
    led.add_result("Phụ thuộc", "m", {"b": 2}, PASS, depends_on=["R3"])
    insights = [{"id": "I1", "requirement": 1, "text": "{{R4.b}}", "result_ids": ["R4"]},
                {"id": "I2", "requirement": 2, "text": "{{R1.total}}", "result_ids": ["R1"]}]
    record, _ = run([{"summary": "s", "insights": insights, "conclusion": "c", "limitations": "l"}], led)
    assert [i["label"] for i in record["insights"]] == [HYPOTHESIS, VERIFIED]


# 4. synthesize
def test_success_writes_insights_with_label_and_filled_text(led):
    record, fake = run([GOOD], led)
    assert led.get_insights() == record
    first, second = record["insights"]
    assert first["text_filled"] == "Tổng doanh thu là 1.234.567,89 (Q1, D1)." and first["text"] == GOOD["insights"][0]["text"]
    assert second["text_filled"] == "Tỷ lệ rời bỏ 26,96%, nhóm Value."
    assert first["label"] == VERIFIED and first["chart"] is None
    assert record["summary_filled"] == "Doanh thu đạt 1.234.567,89." and record["conclusion_filled"] == "Nên giữ nhóm Value."
    assert record["requirements"] == REQUIREMENTS and record["model"] == "fake"


def test_call_uses_report_role_fixed_task_id_and_no_cache(led):
    _, fake = run([GOOD], led)
    call = fake.calls[0]
    assert (call["role"], call["json_schema"], call["task_id"], call["use_cache"]) \
        == ("report", SynthesisOutput, f"{TASK}/report", False)
    fit = [e for e in led.read_log() if e.action == "prompt_fit"][-1]
    assert fit.extra["prompt"] == "synthesize" and fit.extra["role"] == "report" and fit.extra["budget"] == 27000


def test_prompt_has_all_inputs(led):
    d = led.add_decision("metric_definition", "Doanh thu gồm thuế")
    led.confirm_decision(d.id)
    led.add_decision("assumption", "CHUAXACNHAN")
    led.add_result("Câu cũ", "m", {"cu": 1}, PASS)
    led.mark_stale("R3")
    led.add_result("Có cờ", "m", {"a": 1}, [{"name": "lech", "status": "flag", "reason": "lý do lệch"}])
    flags = [{"severity": "flag", "code": "nulls", "message": "CỘT THIẾU"}]
    _, fake = run([{**GOOD}], led, flags=flags)
    prompt = fake.calls[0]["messages"][0]["content"]
    assert PROBLEM["problem"] in prompt and "[1] Tổng doanh thu" in prompt and "[2] Tỷ lệ rời bỏ" in prompt
    assert '{"total": 1234567.891, "note": null}' in prompt and "method: pandas mean" in prompt
    assert "D1 [metric_definition] Doanh thu gồm thuế" in prompt and "CHUAXACNHAN" not in prompt
    assert "Câu cũ" not in prompt and "CỘT THIẾU" in prompt and "R4, check lech: flag (lý do lệch)" in prompt


def test_mask_applies_to_prompt(led):
    led.set_problem({"problem": "Khách BIMAT01 mua nhiều."})
    _, fake = run([GOOD], led, mask=lambda s: s.replace("BIMAT01", "[đã che]"))
    assert "BIMAT01" not in fake.calls[0]["messages"][0]["content"]


def test_retry_with_errors_then_success(led, capsys):
    bad = {**GOOD, "summary": "Doanh thu 100."}
    record, fake = run([bad, GOOD], led)
    assert len(fake.calls) == 2 and record["summary_filled"] == "Doanh thu đạt 1.234.567,89."
    first, second = (c["messages"][0]["content"] for c in fake.calls)
    assert "có chữ số tự gõ" not in first and "summary: có chữ số tự gõ (100)" in second
    assert "Doanh thu 100." in second  # kem cau tra loi truoc de AI sua
    assert [e.status for e in led.read_log() if e.action == "synthesize_attempt"] == ["retry", "ok"]
    assert "lần 1 có 1 lỗi" in capsys.readouterr().out


def test_retry_prompt_differs_so_chain_not_stuck(led):
    bad = {**GOOD, "summary": "Doanh thu 100."}
    _, fake = run([bad, {**GOOD, "summary": "Doanh thu 200."}, GOOD], led)
    assert len(fake.calls) == 3


def test_stops_after_three_wrong_answers_without_writing(led):
    bad = {**GOOD, "summary": "Doanh thu 100."}
    fake = llm.FakeLLM({"report": [bad] * 5})
    with llm.use_fake(fake), pytest.raises(loop.StopLoop) as e:
        synthesize(led, TASK)
    assert e.value.code == loop.STOPPED and len(fake.calls) == config.REPORT_SYNTH_MAX_RETRIES + 1 == 3
    assert led.get_insights() is None and not (led.root / "insights.json").exists()
    assert [o.text for o in led.open_items()] == ["AI chưa viết được bản tổng hợp đúng quy tắc; chạy lại để thử lại."]
    assert led.read_log()[-1].action == "synthesize" and led.read_log()[-1].status == "invalid"


def test_chain_exhausted_propagates(led):
    fake = llm.FakeLLM({"report": [LLMChainExhausted("hết")]})
    with llm.use_fake(fake), pytest.raises(LLMChainExhausted):
        synthesize(led, TASK)
    assert led.get_insights() is None


def test_no_requirements_stops_without_calling_ai(led):
    led.set_state(Phase.SYNTHESIZE)
    led.set_problem({"problem": "Đề."})
    fake = llm.FakeLLM({"report": []})
    with llm.use_fake(fake), pytest.raises(loop.StopLoop):
        synthesize(led, TASK)
    assert fake.calls == []


def test_print_groups_by_requirement(led, capsys):
    record, _ = run([GOOD], led)
    capsys.readouterr()
    print_insights(record)
    out = capsys.readouterr().out
    assert out.index("Yêu cầu 1: Tổng doanh thu") < out.index("I1 [verified]") < out.index("Yêu cầu 2: Tỷ lệ rời bỏ") \
        < out.index("I2 [verified]")
    assert "(R2)" in out and "Kết luận: Nên giữ nhóm Value." in out


# 5. prompt
RENDER_VARS = {"problem": "P", "requirements": "[1] R", "results": "(none)", "confirmed_decisions": "(none)",
               "quality_flags": "(none)", "previous_answer": "(none)", "error_feedback": "(none)"}


def test_prompt_length():
    assert len(render_prompt("synthesize", **RENDER_VARS)) < 3000


def test_prompt_missing_variable_raises():
    with pytest.raises(KeyError):
        render_prompt("synthesize", problem="P")


def test_prompt_is_generic():
    text = load_prompt("synthesize").lower()
    assert "bai1" not in text and "churn" not in text and "campaign" not in text


def test_prompt_states_placeholder_rules():
    text = load_prompt("synthesize")
    assert "{{Rx.field}}" in text and "pct hoặc percent" in text and "Không tự gõ chữ số" in text


def big_ledger(tmp_path):
    ledger = Ledger(tmp_path / "big")
    ledger.set_state(Phase.SYNTHESIZE, plan={"requirements": [f"Yêu cầu dài {i} " + "x" * 200 for i in range(10)]})
    ledger.set_problem({"problem": "Đề bài " + "y" * 3000})
    for i in range(12):
        summary = {f"chi_so_dai_{j}": 1234567.891 * j for j in range(30)}
        ledger.add_result(f"Câu hỏi {i} " + "z" * 200, "phương pháp " + "m" * 800, summary, PASS)
    for i in range(30):
        ledger.confirm_decision(ledger.add_decision("assumption", f"Giả định {i} " + "d" * 300).id)
    flags = [{"severity": "info", "code": f"c{i}", "message": "lưu ý " + "f" * 150} for i in range(40)]
    return ledger, flags


def test_big_record_fits_report_budget(tmp_path):
    ledger, flags = big_ledger(tmp_path)
    variables, shortened = build_variables(ledger, flags)
    text, info = cb.build_prompt("report", "synthesize", lambda **v: render_prompt("synthesize", **v), variables, shortened)
    assert text is not None and info["tokens_after"] <= cb.budget_tokens(30000) == 27000


def test_cuts_keep_every_result_key_and_requirements(tmp_path, monkeypatch):
    ledger, flags = big_ledger(tmp_path)
    variables, shortened = build_variables(ledger, flags)
    limit = 10000  # du cho results rut gon, buoc phai dung het cac bac cat
    monkeypatch.setattr(cb, "min_input_tokens", lambda role: limit)
    text, info = cb.build_prompt("report", "synthesize", lambda **v: render_prompt("synthesize", **v), variables, shortened)
    assert text is not None and info["cut"] == ["quality_flags", "confirmed_decisions", "results"]
    for r in ledger.results():
        assert json.dumps(r.result_summary, ensure_ascii=False) in text
    assert "phương pháp" not in text and "[10] Yêu cầu dài" in text


def test_too_long_stops_with_open_item(led, monkeypatch):
    monkeypatch.setattr(cb, "min_input_tokens", lambda role: 1)
    fake = llm.FakeLLM({"report": []})
    with llm.use_fake(fake), pytest.raises(loop.StopLoop):
        synthesize(led, TASK)
    assert fake.calls == [] and "quá dài" in led.open_items()[0].text


def test_short_results_only_drop_method(led):
    valid = led.results()
    assert "method:" in _results_text(valid, {}) and "method:" not in _results_text(valid, {}, short=True)
    assert _results_text([], {}) == "(none)"


# 6. bang ket qua
def table_ledger(led):
    work = led.root / "work"
    work.mkdir()
    (work / "by_group.csv").write_text("group,total\nBIMAT_DONG,98765\n", encoding="utf-8")
    pd.DataFrame({"a": ["GIATRI_DONG"], "b": [1]}).to_parquet(work / "wide.parquet")
    (work / "top.json").write_text("{}", encoding="utf-8")
    (work / "empty.csv").write_text("", encoding="utf-8")
    led.add_result("Bảng?", "m", {"n": 1}, PASS, question_key="Q3",
                   output_path="work/by_group.csv; work/wide.parquet; work/top.json; work/empty.csv; work/da_chuyen.csv")
    return led


def test_prompt_lists_table_files_and_columns_without_row_values(led):
    _, fake = run([GOOD], table_ledger(led))
    prompt = fake.calls[0]["messages"][0]["content"]
    assert "tables: by_group.csv [group, total]; empty.csv; wide.parquet [a, b]" in prompt
    assert "BIMAT_DONG" not in prompt and "GIATRI_DONG" not in prompt and "98765" not in prompt
    assert "top.json" not in prompt and "da_chuyen" not in prompt


def test_result_without_table_has_no_tables_part(led):
    variables, _ = build_variables(led)
    assert "tables:" not in variables["results"]


def test_file_from_failed_attempt_comes_from_run_log(led):
    # dung ca that bai0a: lan thu 2 ghi bang, lan thu 3 ghi y het nen output_path thieu bang
    work = led.root / "work"
    work.mkdir()
    table = work / "by_brand.csv"
    table.write_text("brand,n\nA,1\n", encoding="utf-8")
    led.add_result("Bảng?", "m", {"n": 1}, PASS, question_key="Q3", output_path=None)
    led.log_event("runner", "R3_Q3", extra={"files_created": [str(table)]})
    led.log_event("runner", "R1_Q1", extra={"files_created": [str(work / "khac.csv")]})
    variables, _ = build_variables(led)
    line = next(x for x in variables["results"].splitlines() if x.startswith("R3 "))
    assert line.endswith("tables: by_brand.csv [brand, n]")


def test_file_in_both_output_path_and_log_listed_once(led):
    led = table_ledger(led)
    led.log_event("runner", "R3_Q3", extra={"files_created": [str(led.root / "work" / "by_group.csv")]})
    variables, _ = build_variables(led)
    assert variables["results"].count("by_group.csv") == 1


def test_columns_are_capped(led, monkeypatch):
    monkeypatch.setattr(config, "REPORT_TABLE_MAX_COLUMNS", 1)
    variables, _ = build_variables(table_ledger(led))
    assert "by_group.csv [group] (+1 more columns)" in variables["results"]


def test_cut_results_keep_file_name_and_summary(led):
    _, shortened = build_variables(table_ledger(led))
    line = next(x for x in shortened["results"].splitlines() if x.startswith("R3 "))
    assert 'result_summary: {"n": 1}' in line and "by_group.csv" in line and "[group" not in line


def test_prompt_states_table_and_recommendation_rules():
    text = load_prompt("synthesize")
    assert "bảng của R2" in text and "Không mô tả quy trình" in text
    assert "Không đưa khuyến nghị" in text and "việc nên làm" not in text and "chưa trả lời được" in text
