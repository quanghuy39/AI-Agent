"""Kiem tra skills/analysis: schema va render prompt. Khong goi LLM.

Chay: python -m pytest tests/test_skill_analysis.py -v
"""

import re

import pytest
from pydantic import ValidationError

from ledger import Ledger
from skills.analysis import load_prompt, render_prompt
from skills.analysis.schemas import AgentMeta, AnalysisPlan, AnalysisStep, Assumption, ProposedRange, SubQuestion

PLACEHOLDER_RE = re.compile(r"\$(?:[A-Za-z_][A-Za-z0-9_]*|\{[A-Za-z_][A-Za-z0-9_]*\})")
PLAN_VARS = {"problem": "", "data_summary": ""}
STEP_VARS = {"problem": "", "data_summary": "", "confirmed_decisions": "", "previous_results": "",
             "question": "", "error_feedback": ""}
ASSUMPTION = {"kind": "metric_definition", "text": "Doanh thu = tổng amount của đơn hoàn tất", "required": True}


# ---------- Assumption, ProposedRange, SubQuestion ----------

def test_assumption_matches_ledger_add_decision(tmp_path):
    a = Assumption(**ASSUMPTION)
    d = Ledger(tmp_path).add_decision(**a.model_dump())
    assert d.kind.value == "metric_definition" and d.required
    assert Assumption(kind="anomaly_handling", text="x").required is True


@pytest.mark.parametrize("bad", [{**ASSUMPTION, "kind": "guess"}, {**ASSUMPTION, "text": ""},
                                 {**ASSUMPTION, "extra": 1}])
def test_assumption_invalid(bad):
    with pytest.raises(ValidationError):
        Assumption(**bad)


def test_proposed_range():
    assert ProposedRange(column="T.score", min=1, max=5, reason="thang 1-5").max == 5
    assert ProposedRange(column="T.a.b", min=0, reason="r").max is None
    for bad in ({"column": "T.score", "reason": "r"},                       # thieu ca min va max
                {"column": "T.score", "min": 5, "max": 1, "reason": "r"},
                {"column": "score", "min": 1, "reason": "r"}):              # khong co BANG.
        with pytest.raises(ValidationError):
            ProposedRange(**bad)


def test_sub_question_key():
    assert SubQuestion(key="Q12", question="?").depends_on == []
    with pytest.raises(ValidationError):
        SubQuestion(key="R1", question="?")


# ---------- AnalysisPlan ----------

PLAN = {"understanding": "Tính doanh thu theo nhóm",
        "requirements": ["Doanh thu theo nhóm"],
        "sub_questions": [{"key": "Q1", "question": "Lọc đơn"},
                          {"key": "Q2", "question": "Tổng", "depends_on": ["Q1"], "covers": [1]}],
        "assumptions": [ASSUMPTION],
        "proposed_ranges": [{"column": "T.amount", "min": 0, "reason": "không âm"}]}


def test_plan_valid_and_cannot_answer():
    assert len(AnalysisPlan(**PLAN).sub_questions) == 2
    p = AnalysisPlan(understanding="?", requirements=["Doanh thu theo ngày"], sub_questions=[], assumptions=[],
                     cannot_answer_reason="thiếu cột ngày")
    assert p.proposed_ranges == []


@pytest.mark.parametrize("change", [
    {"sub_questions": []},                                                           # rong, khong co ly do
    {"sub_questions": [{"key": "Q1", "question": "a"}, {"key": "Q1", "question": "b"}]},
    {"sub_questions": [{"key": "Q1", "question": "a", "depends_on": ["Q9"]}]},
    {"sub_questions": [{"key": "Q1", "question": "a", "depends_on": ["Q1"]}]},
    {"assumptions": [{**ASSUMPTION, "kind": "other"}]},
    {"proposed_ranges": [{"column": "T.x", "reason": "r"}]},
    {"extra": True},
    {"requirements": []},                                                            # khong co yeu cau
    {"requirements": ["Doanh thu theo nhóm", "Số đơn"]},                             # yeu cau 2 chua co cau hoi
    {"sub_questions": [{"key": "Q1", "question": "a", "covers": [0]}]},              # chi so bat dau tu 1
    {"sub_questions": [{"key": "Q1", "question": "a", "covers": [1, 2]}]},           # ngoai 1..n
])
def test_plan_invalid(change):
    with pytest.raises(ValidationError):
        AnalysisPlan(**{**PLAN, **change})


def test_plan_requirements_coverage():
    plan = AnalysisPlan(**{**PLAN, "requirements": ["Doanh thu theo nhóm", "Số đơn"],
                           "sub_questions": [{"key": "Q1", "question": "a", "covers": [1, 2]}]})
    assert plan.sub_questions[0].covers == [1, 2]
    with pytest.raises(ValidationError, match=r"yêu cầu \[2\] chưa có sub_question"):
        AnalysisPlan(**{**PLAN, "requirements": ["Doanh thu theo nhóm", "Số đơn"]})
    # co cannot_answer_reason: khong bat covers
    AnalysisPlan(**{**PLAN, "requirements": ["x", "y"], "sub_questions": [], "cannot_answer_reason": "thiếu cột"})


# ---------- AnalysisStep ----------

STEP = {"explanation": "Lọc rồi cộng", "method": "groupby sum", "can_be_wrong_if": ["Có đơn trùng"],
        "code": "print(1)", "output_files": ["q1.csv"], "new_assumptions": [ASSUMPTION]}


def test_step_valid_and_cannot_answer():
    assert AnalysisStep(**STEP).output_files == ["q1.csv"]
    s = AnalysisStep(explanation="", method="", can_be_wrong_if=[], code="", output_files=[],
                     cannot_answer_reason="không có dữ liệu")
    assert s.new_assumptions == []


@pytest.mark.parametrize("change", [{"can_be_wrong_if": []}, {"can_be_wrong_if": ["  "]}, {"code": " "},
                                    {"new_assumptions": [{**ASSUMPTION, "kind": "x"}]}, {"foo": 1}])
def test_step_invalid(change):
    with pytest.raises(ValidationError):
        AnalysisStep(**{**STEP, **change})


# ---------- AgentMeta ----------

META = {
    "result_summary": {"total": 12.5, "n": 3, "top_group": "A", "ok": True, "missing": None},
    "checks": [
        {"type": "row_count", "label": "lọc", "before": 10, "after": 8, "expect": "not_more",
         "expected_ratio": [0.5, 1]},
        {"type": "sum", "label": "nhóm", "parts": [1, 2.5], "total": 3.5},
        {"type": "range", "label": "điểm", "file": "q1.csv", "column": "score", "min": 1, "max": None},
        {"type": "join", "label": "nối", "left": "A.csv", "right": "B.csv", "left_on": ["k1", "k2"],
         "right_on": "k"},
        {"type": "custom", "name": "không âm", "passed": True, "reason": "min >= 0"},
    ],
}


def test_meta_valid():
    m = AgentMeta(**META)
    assert [c.type for c in m.checks] == ["row_count", "sum", "range", "join", "custom"]
    assert m.checks[0].expected_ratio == (0.5, 1.0) and m.checks[3].how == "left" and m.checks[0].expect == "not_more"
    assert m.result_summary == META["result_summary"] and type(m.result_summary["n"]) is int
    assert type(m.result_summary["ok"]) is bool
    assert AgentMeta(result_summary={}, checks=[{"type": "row_count", "label": "x", "before": 1, "after": 1}]) \
        .checks[0].expect == "free"


@pytest.mark.parametrize("change", [
    {"checks": []},                                                                       # khong co check
    {"checks": [{"type": "magic", "label": "x"}]},                                        # type la
    {"checks": [{"type": "range", "label": "x", "file": "f", "column": "c"}]},            # range thieu min, max
    {"checks": [{"type": "row_count", "label": "x", "before": 1, "after": 1, "expect": "more"}]},
    {"checks": [{"type": "row_count", "label": "x", "before": 1, "after": 1, "expected_ratio": [2, 1]}]},
    {"checks": [{"type": "join", "label": "x", "left": "a", "right": "b", "left_on": "k", "right_on": "k",
                 "how": "outer"}]},
    {"checks": [{"type": "custom", "name": "x", "passed": True}]},                        # thieu reason
    {"result_summary": {"tổng": 1}},                                                      # khoa khong ASCII
    {"result_summary": {"a b": 1}},
    {"result_summary": {"x": [1, 2]}},                                                    # gia tri khong phai so/chuoi
])
def test_meta_invalid(change):
    with pytest.raises(ValidationError):
        AgentMeta(**{**META, **change})


# ---------- prompt ----------

@pytest.mark.parametrize("name, variables", [("plan", PLAN_VARS), ("step", STEP_VARS)])
def test_render_fills_all_placeholders(name, variables):
    filled = {k: f"<{k.upper()}>" for k in variables}
    text = render_prompt(name, **filled)
    assert not PLACEHOLDER_RE.search(text)
    assert all(v in text for v in filled.values())
    assert "AGENT_META_PATH" in text  # contract duoc chen tu dong


@pytest.mark.parametrize("name, variables", [("plan", PLAN_VARS), ("step", STEP_VARS)])
def test_render_missing_variable(name, variables):
    for missing in variables:
        with pytest.raises(KeyError):
            render_prompt(name, **{k: "" for k in variables if k != missing})


def test_prompt_lengths():
    assert len(render_prompt("plan", **PLAN_VARS)) < 6000
    assert len(render_prompt("step", **STEP_VARS)) < 7000


def test_prompts_are_generic():
    text = "\n".join(load_prompt(n) for n in ("plan", "step", "contract")).lower()
    for specific in ("bai0", "bai1", "bai2", "customer", "order", "csat", "support_ticket", "khóa trùng",
                     "duplicate key", "drop_duplicates", ".csv"):
        assert specific not in text


def test_load_prompt_unknown():
    with pytest.raises(FileNotFoundError):
        load_prompt("nope")
