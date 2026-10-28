"""Kiem tra chinh bo cham kiem dinh (tests/llm_eval). KHONG goi mang.

Chay: python -m pytest tests/test_llm_eval_scoring.py -v
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from llm import FakeLLM, LLMChainExhausted, LLMRequestError, structured, usage, use_fake
from tests.llm_eval import cases_report, cases_slide, common, run_eval, summary, synth_data
from tests.llm_eval.cases_analysis import CASES as ANALYSIS_CASES
from tests.llm_eval.schemas import SCHEMAS, AnalysisOutput, ReportOutput, SlideOutput
from tests.llm_eval.scoring import (
    check_placeholders,
    run_code,
    score_case,
    static_check,
    typed_numbers,
    values_match,
)

ROOT = Path(__file__).resolve().parents[1]
CASES = {c.id: c for c in ANALYSIS_CASES + cases_report.CASES + cases_slide.CASES}
SAVE = '\nimport os, json\nos.makedirs("output", exist_ok=True)\njson.dump(result, open("output/result.json", "w"))\n'


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    return synth_data.write_data(tmp_path_factory.mktemp("synth") / "data")


@pytest.fixture(autouse=True)
def _no_usage_log(monkeypatch, tmp_path):
    # FakeLLM ghi usage: chuyen sang thu muc tam, khong dung cache/ that
    monkeypatch.setattr(usage, "_path_override", tmp_path / "usage.jsonl")


def analysis(code="", explanation="x", assumptions=(), reason=None):
    return AnalysisOutput(explanation=explanation, code=code, assumptions=list(assumptions),
                          cannot_answer_reason=reason)


def applicable_all_true(crit):
    return all(v for v in crit.values() if v is not None)


# ---------- (a) de giai duoc ----------

@pytest.mark.parametrize("case", ANALYSIS_CASES, ids=lambda c: c.id)
def test_a_reference_answer_passes(case, data_dir):
    if case.expected is not None:
        run = run_code(case.reference["code"], data_dir)
        assert run.ok, run.error
        assert values_match(case.expected, run.result) == (True, None)
    crit, why = score_case(case, AnalysisOutput(**case.reference), data_dir)
    assert applicable_all_true(crit), why


@pytest.mark.parametrize("case", cases_report.CASES + cases_slide.CASES, ids=lambda c: c.id)
def test_a_report_slide_reference_passes(case):
    mod = cases_report if case.role == "report" else cases_slide
    out = SCHEMAS[case.role].model_validate(mod.reference_answer(case))
    crit, why = score_case(case, out)
    assert applicable_all_true(crit), why


def test_case_counts_and_unique_ids():
    assert [len(common.load_cases(r)) for r in common.ROLES] == [12, 10, 10]
    assert len(CASES) == 32


def test_join_trap_actually_traps(data_dir):
    case = CASES["A07_join_trap"]
    naive = case.reference["code"].replace('customers.drop_duplicates(subset="customer_id")', "customers")
    crit, why = score_case(case, analysis(naive), data_dir)
    assert crit["code_runs"] is True
    assert crit["value_correct"] is False


def test_synth_data_has_traps():
    c, o = synth_data.make_tables()
    assert c["customer_id"].duplicated().sum() == synth_data.N_DUP_CUSTOMERS
    assert o["amount"].isna().sum() == synth_data.N_NULL_AMOUNT
    assert (~o["customer_id"].isin(c["customer_id"])).sum() == synth_data.N_ORPHAN_ORDERS
    c2, o2 = synth_data.make_tables()
    assert c.equals(c2) and o.equals(o2)  # seed co dinh


@pytest.mark.parametrize("case", list(CASES.values()), ids=lambda c: c.id)
def test_prompt_budget(case):
    msgs = common.build_messages(case) + [structured.instruction_message(SCHEMAS[case.role])]
    chars = sum(len(m["content"]) for m in msgs)
    # uoc luong than trong: ~2 ky tu tieng Viet / token
    assert chars / 2 < 1500, f"{case.id}: {chars} ký tự"


# ---------- (b) tung tieu chi: dat va rot ----------

def test_b_value_correct_tolerance():
    assert values_match({"x": 100.0}, {"x": 100.04})[0]
    assert not values_match({"x": 100.0}, {"x": 101.0})[0]
    assert not values_match({"x": 1}, {})[0]
    assert values_match({"t": [{"id": "a", "v": 1.5}]}, {"t": [{"id": "a", "v": "1.5"}], "extra": 1})[0]
    assert not values_match({"t": [1, 2]}, {"t": [2, 1]})[0]


def test_b_code_runs_pass_and_fail(data_dir):
    ok = run_code('import pandas as pd\nresult = {"n": len(pd.read_csv("data/orders.csv"))}' + SAVE, data_dir)
    assert ok.ok and ok.result == {"n": 400}
    bad = run_code("raise ValueError('hỏng')", data_dir)
    assert not bad.ok and bad.exit_code != 0 and "ValueError" in bad.stderr
    no_out = run_code("x = 1", data_dir)
    assert not no_out.ok and "result.json" in no_out.error


def test_b_code_hang_times_out(data_dir):
    r = run_code("while True:\n    pass\n", data_dir, timeout_s=3)
    assert r.timed_out and not r.ok


def test_b_data_is_read_only(data_dir):
    r = run_code('open("data/orders.csv", "w").write("x")', data_dir)
    assert not r.ok
    assert "PermissionError" in r.stderr


def test_b_no_invented_columns(data_dir):
    case = CASES["A01_groupby_sum"]
    good = case.reference["code"]
    crit, _ = score_case(case, analysis(good), data_dir)
    assert crit["no_invented_columns"] is True

    bad = good.replace('groupby("channel")["amount"]', 'groupby("channel")["revenue"]')
    crit, why = score_case(case, analysis(bad), data_dir)
    assert crit["no_invented_columns"] is False and "revenue" in why["no_invented_columns"]


def test_b_static_columns_created_are_not_invented():
    rep = static_check(
        'import pandas as pd\ndf = pd.read_csv("data/orders.csv")\n'
        'df["month"] = df["order_date"].str[3:]\nx = df.groupby("month").agg(tong=("amount", "sum"))\n'
        'y = x.sort_values("tong")\n'
    )
    assert rep.unknown_columns == []
    rep = static_check('df = df.merge(c, on="client_key")')
    assert rep.unknown_columns == ["client_key"]


@pytest.mark.parametrize("code", [
    "import subprocess\nsubprocess.run(['dir'])",
    "import os\nos.system('dir')",
    "import os\nos.remove('data/orders.csv')",
    "import requests",
    "from pathlib import Path\nPath('data/orders.csv').unlink()",
    "import pandas as pd\npd.read_csv('C:/Windows/win.ini')",
    "open('../secret.txt').read()",
    "exec('print(1)')",
])
def test_b_forbidden_static(code):
    assert static_check(code).forbidden


def test_b_forbidden_allowed_examples():
    code = ('import os, json\nimport pandas as pd\nfrom pathlib import Path\n'
            'df = pd.read_csv(os.path.join("data", "orders.csv"))\n'
            'os.makedirs("output", exist_ok=True)\nrows = df.head().to_dict("records")\n'
            'Path("output/result.json").write_text(json.dumps({"n": len(rows)}))\n')
    assert static_check(code).forbidden == []


def test_b_forbidden_blocked_at_runtime(data_dir):
    case = CASES["A11_forbidden"]
    # vuot qua phan tich tinh bang getattr; lop chan luc chay van bat
    sneaky = case.reference["code"] + '\nimport os\ngetattr(os, "re" + "move")("data/orders.csv")\n'
    crit, why = score_case(case, analysis(sneaky), data_dir)
    assert crit["no_forbidden"] is False
    good, _ = score_case(case, AnalysisOutput(**case.reference), data_dir)
    assert good["no_forbidden"] is True


def test_b_network_blocked_at_runtime(data_dir):
    r = run_code('import socket\nsocket.create_connection(("example.com", 80))', data_dir)
    assert not r.ok and "bị chặn" in r.stderr


def test_b_handles_missing_column(data_dir):
    case = CASES["A09_missing_column"]
    crit, _ = score_case(case, analysis("", reason="Không có cột discount."), data_dir)
    assert crit["handles_missing_column"] is True and crit["code_runs"] is None

    crit, _ = score_case(case, analysis("", reason=None), data_dir)
    assert crit["handles_missing_column"] is False

    code = 'import pandas as pd\nresult = pd.read_csv("data/orders.csv").groupby("channel")["discount"].sum().to_dict()' + SAVE
    crit, why = score_case(case, analysis(code), data_dir)
    assert crit["handles_missing_column"] is False
    assert crit["no_invented_columns"] is False


def test_b_assumptions_present(data_dir):
    case = CASES["A10_ambiguous"]
    code = case.reference["code"]
    crit, _ = score_case(case, analysis(code, assumptions=["Giá trị = tổng amount"]), data_dir)
    assert crit["assumptions_present"] is True
    crit, _ = score_case(case, analysis(code, assumptions=[]), data_dir)
    assert crit["assumptions_present"] is False


def test_b_value_refusal_fails(data_dir):
    case = CASES["A02_distinct"]
    crit, _ = score_case(case, analysis(case.reference["code"], reason="không biết"), data_dir)
    assert crit["value_correct"] is False


LEDGER = {"R1": {"label": "Kênh web", "total": 12345.6, "pct": 0.375, "n": 230}, "R2": {"total": 900}}


def test_b_placeholder_ok_and_unknown():
    assert check_placeholders(["Doanh thu {{R1.total}}, tỷ lệ {{ R1.pct }}."], LEDGER, 2) == (True, None)
    ok, why = check_placeholders(["Doanh thu {{R9.total}}."], LEDGER)
    assert not ok and "R9" in why
    ok, why = check_placeholders(["Doanh thu {{R1.revenue}}."], LEDGER)
    assert not ok and "revenue" in why
    ok, why = check_placeholders(["Doanh thu {R1.total}."], LEDGER)
    assert not ok and "sai dạng" in why
    ok, why = check_placeholders(["Doanh thu {{R1.total}}."], LEDGER, min_count=2)
    assert not ok
    ok, why = check_placeholders(["Doanh thu {{R1.total}}."], LEDGER, required_ids=["R2"])
    assert not ok and "R2" in why


@pytest.mark.parametrize("text", [
    "Doanh thu đạt 12.345,6 đồng.",
    "Doanh thu đạt 12,345.6.",
    "Doanh thu khoảng 12346.",
    "Tỷ lệ 37,5% đơn.",
    "Có 230 đơn qua web.",
    "Kênh khác đạt 900.",
])
def test_b_typed_numbers_fail(text):
    assert typed_numbers([text], LEDGER)


@pytest.mark.parametrize("text", [
    "Doanh thu {{R1.total}} trong năm 2025.",
    "Phần 1: tổng quan. Slide 2 nói về kênh.",
    "1. Doanh thu {{R1.total}}\n2. Số đơn {{R1.n}}",
    "Ngày 15-03-2025 có thay đổi.",
    "Có 3 kênh bán hàng và top 5 khách hàng.",
    "Mã khách C0230 và quý 2.",
])
def test_b_typed_numbers_pass(text):
    assert typed_numbers([text], LEDGER) == []


def _report(n_sections, body="Doanh thu {{R1.total}}, {{R2.total}}, {{R3.total}}, tỷ trọng {{R1.pct}}."):
    return ReportOutput(sections=[{"heading": f"Mục {i}", "body": body} for i in range(n_sections)])


def test_b_report_structure_and_typed():
    case = CASES["R01_channels"]
    crit, why = score_case(case, _report(2))
    assert applicable_all_true(crit), why
    crit, _ = score_case(case, _report(6))
    assert crit["structure_ok"] is False
    crit, _ = score_case(case, _report(2, body="Doanh thu web 14409.04, {{R1.total}} {{R2.total}} {{R3.total}}"))
    assert crit["no_typed_numbers"] is False


def _slides(n_bullets=2, chart="chart_revenue_by_channel", n_slides=1):
    bullets = ["{{R1.total}}", "{{R2.total}}", "{{R3.total}}"] + ["thêm"] * max(0, n_bullets - 3)
    return SlideOutput(slides=[
        {"title": "Doanh thu", "bullets": bullets[:max(n_bullets, 3)], "chart_ref": chart}
        for _ in range(n_slides)
    ])


def test_b_slide_structure():
    case = CASES["S01_channels"]
    crit, why = score_case(case, _slides(3))
    assert applicable_all_true(crit), why
    crit, why = score_case(case, _slides(6))
    assert crit["structure_ok"] is False and "6 bullet" in why["structure_ok"]
    crit, _ = score_case(case, _slides(3, chart="chart_khong_co"))
    assert crit["chart_ref_ok"] is False and crit["structure_ok"] is False
    crit, _ = score_case(case, _slides(3, n_slides=4))
    assert crit["structure_ok"] is False
    crit, _ = score_case(case, _slides(3, chart=None))
    assert crit["chart_ref_ok"] is False  # thieu chart bat buoc


@pytest.mark.parametrize("role", ["analysis", "report", "slide"])
def test_b_json_invalid_fails_everything(role, data_dir):
    case = next(c for c in CASES.values() if c.role == role)
    crit, why = score_case(case, None, data_dir)
    assert crit["json_valid"] is False
    assert all(v is False for v in crit.values())


# ---------- (c) run_eval tron ven voi FakeLLM ----------

MODEL = "groq_gpt_oss_120b"  # co trong chain analysis, report va slide cua llm.yaml? kiem tra duoi


def _lines(results_dir):
    files = list(Path(results_dir).glob(f"*_{MODEL}_analysis.jsonl"))
    assert len(files) == 1
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]


def _ref(case_id):
    return dict(CASES[case_id].reference)


def test_c_run_eval_full_resume_and_stop(tmp_path, capsys):
    res = str(tmp_path / "results")
    base = ["--model", MODEL, "--role", "analysis", "--results-dir", res]

    # khong --yes: khong goi
    fake = FakeLLM([])
    with use_fake(fake):
        assert run_eval.main(base + ["--limit", "2"], sleep=lambda s: None) == run_eval.EXIT_NOT_CONFIRMED
    assert fake.calls == []

    sleeps = []
    fake = FakeLLM([_ref("A01_groupby_sum"), _ref("A02_distinct")])
    with use_fake(fake):
        assert run_eval.main(base + ["--limit", "2", "--yes"], sleep=sleeps.append) == 0
    lines = _lines(res)
    assert [r["case_id"] for r in lines] == ["A01_groupby_sum", "A02_distinct"]
    assert all(r["passed"] and r["criteria"]["value_correct"] for r in lines)
    assert {"case_id", "criteria", "reasons", "prompt_tokens", "completion_tokens", "network_calls",
            "latency_s", "answer_head"} <= set(lines[0])
    assert sleeps == [pytest.approx(60 / 30 * 1.2)]  # rpm=30 trong llm.yaml
    assert [c["model_key"] for c in fake.calls] == [MODEL, MODEL]

    # --resume: bo qua 2 case da co; JSON sai o case 3 duoc ghi lai, khong dung
    bad_json = {"explanation": "thiếu trường code"}
    fake = FakeLLM([bad_json, _ref("A04_date_range")])
    with use_fake(fake):
        assert run_eval.main(base + ["--limit", "2", "--yes", "--resume"], sleep=lambda s: None) == 0
    lines = _lines(res)
    assert [r["case_id"] for r in lines][2:] == ["A03_join_count", "A04_date_range"]
    assert lines[2]["error"] == "json_invalid" and lines[2]["criteria"]["json_valid"] is False
    assert lines[3]["passed"] is True
    assert "A01_groupby_sum" not in json.dumps(fake.calls[0]["messages"], ensure_ascii=False)

    # het han muc: dung sach, file van hop le
    stop = LLMChainExhausted("hết", attempts=[{"model_key": MODEL, "reason": "rate_limit", "status_code": 429}])
    fake = FakeLLM([_ref("A05_percentage"), stop])
    capsys.readouterr()
    with use_fake(fake):
        code = run_eval.main(base + ["--yes", "--resume"], sleep=lambda s: None)
    out = capsys.readouterr().out
    assert code == run_eval.EXIT_QUOTA
    assert "hết hạn mức, chạy tiếp ngày mai với --resume" in out
    lines = _lines(res)  # moi dong van parse duoc
    assert [r["case_id"] for r in lines][-1] == "A05_percentage"
    assert len(lines) == 5


def test_c_run_eval_request_error_stops(tmp_path, capsys):
    err = LLMRequestError("bị từ chối", model_key=MODEL, provider="groq", status_code=401)
    res = tmp_path / "results"
    with use_fake(FakeLLM([err])):
        code = run_eval.main(["--model", MODEL, "--role", "report", "--results-dir", str(res), "--yes"],
                             sleep=lambda s: None)
    out = capsys.readouterr().out
    assert code == run_eval.EXIT_REQUEST
    assert MODEL in out and "401" in out
    assert list(res.glob("*.jsonl")) == []


def test_c_run_eval_report_and_summary(tmp_path, capsys):
    cases = cases_report.CASES[:2]
    answers = [cases_report.reference_answer(c) for c in cases]
    with use_fake(FakeLLM(answers)):
        assert run_eval.main(["--model", MODEL, "--role", "report", "--results-dir", str(tmp_path),
                              "--yes", "--limit", "2"], sleep=lambda s: None) == 0
    rows, _ = summary.summarize(tmp_path, ROOT / "tests" / "llm_eval" / "thresholds.yaml")
    assert len(rows) == 1
    row = rows[0]
    assert (row["model"], row["role"], row["n_cases"]) == (MODEL, "report", 2)
    assert row["rates"]["placeholder_ok"] == (2, 2)
    assert row["meets_thresholds"] is True
    text = summary.format_rows(*summary.summarize(tmp_path, ROOT / "tests" / "llm_eval" / "thresholds.yaml"))
    assert "report" in text and MODEL in text


def test_c_model_not_in_chain(tmp_path, capsys):
    code = run_eval.main(["--model", "khong_co", "--role", "analysis", "--results-dir", str(tmp_path)])
    assert code == run_eval.EXIT_USAGE


def test_model_used_in_tests_is_in_chains():
    from llm import client
    chains = client._active_chains(client._load_config(client._config_path()))
    assert MODEL in chains["analysis"] and MODEL in chains["report"]


# ---------- (d) --list ----------

def test_d_list_counts():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "llm_eval" / "run_eval.py"), "--list"],
        capture_output=True, text=True, encoding="utf-8", cwd=ROOT, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["analysis:", "12", "report:", "10", "slide:", "10"]
