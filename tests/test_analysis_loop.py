"""Kiem tra analysis/loop.py bang FakeLLM va R2 gia (thu muc tam). Khong goi R2 that, khong goi AI that.
Runner chay that (code ngan) tren ban chep du lieu gia.

Chay: python -m pytest tests/test_analysis_loop.py -v
"""

import json

import pytest
from pydantic import ValidationError

import llm
from analysis import loop
from ledger import Ledger
from llm import LLMChainExhausted, structured
from profiling import quality
from runner import run_code as runner
from skills.analysis.schemas import AnalysisPlan, SubQuestion
from storage import r2

TASK = "baiL"
IDS = [f"CUST{i:04d}" for i in range(1, 6)]
FILES = {
    f"input/{TASK}/de_bai.md": "Tính tổng amount theo khách hàng, rồi số khách có tổng > 70.",
    f"input/{TASK}/CUSTOMER.csv": "customer_id,segment\n" + "".join(f"{c},S{i % 2}\n" for i, c in enumerate(IDS)),
    f"input/{TASK}/ORDERS.csv": "order_id,customer_id,amount\n"
                                + "".join(f"O{i:03d},{IDS[i % 5]},{i * 3}\n" for i in range(1, 16)),
    f"answers/{TASK}/key.json": "{}",
}

RESTATEMENT = {"restatement": "Tính tổng theo khách rồi đếm khách lớn.", "goals": ["Tổng theo khách", "Số khách lớn"]}
PLAN = {"understanding": "Tổng theo khách rồi đếm khách lớn.",
        "requirements": ["Tổng amount theo khách hàng", "Số khách có tổng > 70"],
        "sub_questions": [{"key": "Q1", "question": "Tổng amount theo khách?", "covers": [1]},
                          {"key": "Q2", "question": "Bao nhiêu khách có tổng > 70?", "depends_on": ["Q1"],
                           "covers": [2]}],
        "assumptions": [{"kind": "metric_definition", "text": "Tổng = cộng amount mọi đơn", "required": True}],
        "proposed_ranges": [{"column": "ORDERS.amount", "min": 0, "reason": "không âm"}]}

META_END = ("json.dump(meta, open(os.environ['AGENT_META_PATH'], 'w', encoding='utf-8'))\n")
Q1_CODE = ("import json, os\nimport pandas as pd\n"
           "inp, out = os.environ['AGENT_INPUT_DIR'], os.environ['AGENT_OUTPUT_DIR']\n"
           "o = pd.read_csv(os.path.join(inp, 'ORDERS.csv'), dtype=str)\n"
           "o['amount'] = o['amount'].astype(float)\n"
           "t = o.groupby('customer_id', as_index=False)['amount'].sum()\n"
           "t.to_csv(os.path.join(out, 'q1.csv'), index=False)\n"
           "meta = {'result_summary': {'n_customers': int(len(t))}, 'checks': ["
           "{'type': 'sum', 'label': 'tổng', 'parts': [float(x) for x in t['amount']], 'total': float(o['amount'].sum())},"
           "{'type': 'custom', 'name': 'không âm', 'passed': bool((o['amount'] >= 0).all()), 'reason': 'kiểm tra'}]}\n"
           + META_END)
Q2_CODE = ("import json, os\nimport pandas as pd\n"
           "out = os.environ['AGENT_OUTPUT_DIR']\n"
           "t = pd.read_csv(os.path.join(out, 'q1.csv'), dtype={'amount': float})\n"
           "big = t[t['amount'] > 70]\n"
           "big[['amount']].to_csv(os.path.join(out, 'q2.csv'), index=False)\n"
           "meta = {'result_summary': {'n_big': int(len(big))}, 'checks': ["
           "{'type': 'row_count', 'label': 'lọc', 'before': int(len(t)), 'after': int(len(big)), 'expect': 'not_more',"
           " 'expected_ratio': [0, 1]},"
           "{'type': 'custom', 'name': 'q1 đủ', 'passed': bool(len(t) == 5), 'reason': 'kiểm tra'}]}\n" + META_END)


def step(code, output_files, risks=("Thiếu đơn",), **kw):
    return {"explanation": "giải thích", "method": "pandas groupby", "can_be_wrong_if": list(risks),
            "code": code, "output_files": output_files, **kw}


STEP1, STEP2 = step(Q1_CODE, ["q1.csv"]), step(Q2_CODE, ["q2.csv"])


@pytest.fixture
def env(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    state = {"listed": 0}

    def list_objects(prefix, grader=False):
        state["listed"] += 1
        return [{"key": k, "etag": "e"} for k in FILES if k.startswith(prefix)]

    def download_with_cache(key, cache_dir=None, grader=False):
        assert "answers" not in key
        local = cache / key
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(FILES[key].encode("utf-8"))
        return local

    monkeypatch.setattr(r2, "CACHE_DIR", cache)
    monkeypatch.setattr(r2, "get_r2_settings", lambda: {"R2_INPUT_PREFIX": "input/"})
    monkeypatch.setattr(r2, "list_objects", list_objects)
    monkeypatch.setattr(r2, "download_with_cache", download_with_cache)
    monkeypatch.setattr(r2, "get_etag", lambda key, cache_dir=None: None)
    monkeypatch.setattr(runner, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(quality, "OUTPUT_DIR", tmp_path / "output")
    return tmp_path


def run(script, **kw):
    fake = llm.FakeLLM({"analysis": [RESTATEMENT, *script]})  # pha understand goi AI truoc ke hoach
    kw.setdefault("yes", True)
    kw.setdefault("sensitive", ["CUSTOMER.customer_id"])
    with llm.use_fake(fake):
        code = loop.run_loop(TASK, **kw)
    return code, fake


def ledger(env):
    return Ledger(env / "output" / TASK)


def prompts(fake):
    return [c["messages"][0]["content"] for c in fake.calls]


# (a) thanh cong 2 cau hoi co phu thuoc
def test_success_two_questions_with_dependency(env):
    code, fake = run([PLAN, STEP1, STEP2])
    assert code == loop.DONE
    led = ledger(env)
    r1, r2_ = led.results()
    assert (r1.question, r1.depends_on, r1.result_summary) == ("Tổng amount theo khách?", [], {"n_customers": 5})
    totals = {}
    for i in range(1, 16):
        totals[IDS[i % 5]] = totals.get(IDS[i % 5], 0) + i * 3
    assert r2_.depends_on == [r1.id] and r2_.result_summary == {"n_big": sum(v > 70 for v in totals.values())}
    assert r1.code_file == "code/R1_Q1.py" and r2_.code_file == "code/R2_Q2.py"
    assert r1.output_path == "work/q1.csv" and all(c.status.value == "pass" for c in r1.checks + r2_.checks)
    decisions = led.decisions()
    assert len(decisions) == 2 and all(d.status.value == "confirmed" for d in decisions)
    assert "ORDERS.amount: >= 0" in decisions[1].text
    assert all(c["task_id"] == TASK and c["role"] == "analysis" for c in fake.calls)
    step2_prompt = prompts(fake)[3]
    assert "D1 [metric_definition]" in step2_prompt and "q1.csv" in step2_prompt and "R1" in step2_prompt
    actions = [e.action for e in led.read_log() if e.step == "analysis"]
    assert actions[0] == "load_context" and "plan" in actions and actions[-1] == "done"
    assert sum(1 for e in led.read_log() if e.step == "llm") == 4  # usage cua FakeLLM vao run_log


# (b) loi runner roi sua duoc
def test_runner_error_then_fixed(env):
    bad = step("import os\nprint(1/0)\n", ["q1.csv"])
    code, fake = run([PLAN, bad, STEP1, STEP2])
    assert code == loop.DONE and len(ledger(env).results()) == 2
    retry_prompt = prompts(fake)[3]
    assert "Code lỗi" in retry_prompt and "ZeroDivisionError" in retry_prompt
    assert "Errors from the previous attempt" in retry_prompt


# (c) het max_retries
def test_retries_exhausted_stops_with_open_item(env):
    bad = step("raise SystemExit(4)\n", ["q1.csv"])
    code, fake = run([PLAN, bad, bad], max_retries=1)
    assert code == loop.STOPPED and len(fake.calls) == 4
    led = ledger(env)
    assert led.results() == []
    assert any("Q1: không giải được sau 2 lần thử" in o.text for o in led.open_items())


# (d) thieu check custom -> structural_errors phan hoi cho AI
def test_missing_custom_checks_fed_back(env):
    thin = step(Q1_CODE, ["q1.csv"], risks=("rủi ro 1", "rủi ro 2"))
    code, fake = run([PLAN, thin, STEP1, STEP2])
    assert code == loop.DONE
    retry_prompt = prompts(fake)[3]
    assert "Lỗi cấu trúc cần sửa" in retry_prompt and "2 ý nhưng meta chỉ có 1 check custom" in retry_prompt


def test_missing_meta_fed_back(env):
    no_meta = step("print('xong')\n", ["q1.csv"])
    code, fake = run([PLAN, no_meta, STEP1, STEP2])
    assert code == loop.DONE and "không có file meta hợp lệ" in prompts(fake)[3]


# (e) n o buoc xac nhan
def test_reject_plan_stops_without_runner(env, monkeypatch):
    monkeypatch.setattr(runner, "run_code", lambda *a, **k: pytest.fail("không được chạy runner"))
    code, fake = run([PLAN], yes=False, ask_user=lambda q: "n" if "kế hoạch" in q else "y")
    assert code == loop.STOPPED and len(fake.calls) == 2
    led = ledger(env)
    assert led.decisions() == [] and any("chưa được chấp nhận" in o.text for o in led.open_items())


def test_cannot_answer_plan_stops(env):
    code, _ = run([{**PLAN, "sub_questions": [], "cannot_answer_reason": "thiếu cột ngày"}])
    assert code == loop.STOPPED and "thiếu cột ngày" in ledger(env).open_items()[0].text


def test_flags_recorded_with_finding_and_n_stops(env):
    flagged = step(Q1_CODE.replace("'passed': bool((o['amount'] >= 0).all())", "'passed': False"), ["q1.csv"])
    answers = iter(["y", "y", "n"])  # xac nhan de, chap nhan ke hoach, roi dung khi co co
    code, _ = run([PLAN, flagged], yes=False, ask_user=lambda q: next(answers))
    assert code == loop.STOPPED
    led = ledger(env)
    (r1,) = led.results()
    assert [c.status.value for c in r1.checks] == ["pass", "flag"]
    (f,) = led.findings()
    assert f.kind.value == "hypothesis" and f.result_ids == [r1.id]


# (f) het han muc chuoi
def test_chain_exhausted_exit_2(env):
    code, _ = run([PLAN, LLMChainExhausted("hết", attempts=[], next_available_at=1234567890.0)])
    assert code == loop.EXHAUSTED
    assert any("hết hạn mức chuỗi" in o.text for o in ledger(env).open_items())


# (g) che gia tri
def test_mask_text():
    values = {"CUST0001", "CUST00012", "abc"}
    assert loop.mask_text("x CUST00012 y CUST0001, abc", values) == "x [đã che] y [đã che], abc"  # "abc" < 4 ky tu
    assert loop.mask_text("", values) == "" and loop.mask_text("CUST0001", set()) == "CUST0001"


def test_masked_values_never_reach_ai(env):
    leaky = step("import os\nimport pandas as pd\n"
                 "c = pd.read_csv(os.path.join(os.environ['AGENT_INPUT_DIR'], 'CUSTOMER.csv'), dtype=str)\n"
                 "print(c.head())\nraise ValueError('khách lỗi: ' + c['customer_id'][0])\n", ["q1.csv"])
    code, fake = run([PLAN, leaky, STEP1, STEP2])
    assert code == loop.DONE
    retry = prompts(fake)[3]
    assert "[đã che]" in retry and "ValueError" in retry
    for p in prompts(fake):
        assert not any(c in p for c in IDS)
    assert not any(c in str(e.model_dump()) for e in ledger(env).read_log() for c in IDS)


# (h) sap xep phu thuoc
def test_topo_order_and_cycle():
    qs = [SubQuestion(key="Q1", question="a", depends_on=["Q3"]), SubQuestion(key="Q2", question="b"),
          SubQuestion(key="Q3", question="c", depends_on=["Q2"])]
    assert [q.key for q in loop.topo_order(qs)] == ["Q2", "Q3", "Q1"]
    cyc = [SubQuestion(key="Q1", question="a", depends_on=["Q2"]), SubQuestion(key="Q2", question="b", depends_on=["Q1"])]
    with pytest.raises(ValueError, match="vòng phụ thuộc"):
        loop.topo_order(cyc)


def test_cycle_in_plan_stops(env):
    plan = {**PLAN, "sub_questions": [{"key": "Q1", "question": "a", "depends_on": ["Q2"], "covers": [1]},
                                      {"key": "Q2", "question": "b", "depends_on": ["Q1"], "covers": [2]}]}
    code, _ = run([plan])
    assert code == loop.STOPPED and any("vòng phụ thuộc" in o.text for o in ledger(env).open_items())


def test_print_plan_maps_requirements(capsys):
    plan = AnalysisPlan(**{**PLAN, "requirements": ["Tổng theo khách", "Số khách lớn", "Tổng và số khách"],
                           "sub_questions": [{"key": "Q1", "question": "a", "covers": [1, 3]},
                                             {"key": "Q2", "question": "b", "depends_on": ["Q1"], "covers": [2, 3]}]})
    loop.print_plan(plan)
    out = capsys.readouterr().out
    assert "Yêu cầu của đề:" in out
    assert "1. Tổng theo khách -> Q1" in out and "2. Số khách lớn -> Q2" in out and "3. Tổng và số khách -> Q1, Q2" in out


def test_plan_missing_requirement_is_schema_error():
    # ValidationError tu structured.parse la tin hieu de ask_model goi lai model (json_retries)
    bad = json.dumps({**PLAN, "requirements": PLAN["requirements"] + ["Yêu cầu bị bỏ sót"]}, ensure_ascii=False)
    with pytest.raises(ValidationError, match=r"yêu cầu \[3\] chưa có sub_question"):
        structured.parse(bad, AnalysisPlan)


def test_plan_without_coverage_stops_loop(env):
    plan = {**PLAN, "requirements": PLAN["requirements"] + ["Yêu cầu bị bỏ sót"]}
    code, fake = run([plan])
    assert code == loop.ERROR and len(fake.calls) == 2  # FakeLLM khong thu lai; khong giai cau nao


# ---------- do dai prompt ----------

FIT_VARS = {"previous_results": "P" * 3400, "error_feedback": "E" * 1700, "confirmed_decisions": "D" * 1700,
            "question": "Q1: ?"}
FIT_SHORT = {"previous_results": "p" * 10, "confirmed_decisions": "d" * 10}


def _join(**v):
    return "\n".join(v[k] for k in ("previous_results", "error_feedback", "confirmed_decisions", "question"))


def _limit_for(**v):
    """limit nho nhat ma prompt nay vua (ngan sach = limit * (1 - bien))."""
    import math
    return math.ceil(loop.estimate_tokens(_join(**v)) / (1 - loop.config.PROMPT_MARGIN)) + 1


def test_fit_prompt_unchanged_when_fits():
    text, info = loop.fit_prompt(_join, FIT_VARS, FIT_SHORT, limit=10 ** 6)
    assert text == _join(**FIT_VARS) and info["cut"] == []
    assert info["chars_before"] == info["chars_after"] == len(text)


@pytest.mark.parametrize("n_cuts", [1, 2, 3])
def test_fit_prompt_cuts_in_order(n_cuts):
    stages = [FIT_VARS, {**FIT_VARS, "previous_results": "p" * 10}]
    stages.append({**stages[1], "error_feedback": "E" * loop.config.ANALYSIS_FEEDBACK_SHORT_CHARS})
    stages.append({**stages[2], "confirmed_decisions": "d" * 10})
    text, info = loop.fit_prompt(_join, FIT_VARS, FIT_SHORT, limit=_limit_for(**stages[n_cuts]))
    assert info["cut"] == ["previous_results", "error_feedback", "confirmed_decisions"][:n_cuts]
    assert text == _join(**stages[n_cuts]) and info["tokens_after"] < info["tokens_before"]


def test_fit_prompt_still_too_long():
    smallest = {"previous_results": "p" * 10, "error_feedback": "E" * 600, "confirmed_decisions": "d" * 10,
                "question": "Q1: ?"}
    text, info = loop.fit_prompt(_join, FIT_VARS, FIT_SHORT, limit=_limit_for(**smallest) - 2)
    assert text is None and len(info["cut"]) == 3


def test_loop_cuts_prompt_and_still_calls_ai(env, monkeypatch):
    real, seen = loop.fit_prompt, []

    def tight(render, variables, shortened, limit):
        # limit vua thieu 1 token cho prompt day du -> buoc phai cat
        full = render(**variables)
        text, info = real(render, variables, shortened,
                          int((loop.estimate_tokens(full) - 1) / (1 - loop.config.PROMPT_MARGIN)))
        seen.append(info)
        return text, info

    monkeypatch.setattr(loop, "fit_prompt", tight)
    code, fake = run([PLAN, STEP1, STEP2])
    assert code == loop.DONE and len(fake.calls) == 4 and len(seen) == 2  # da cat va van goi AI
    order = ["previous_results", "error_feedback", "confirmed_decisions"]
    for info in seen:
        assert info["cut"] and info["cut"] == order[:len(info["cut"])]  # cat dung thu tu, dung o buoc du
        assert info["tokens_after"] < info["tokens_before"]
    assert "keys: n_customers" in prompts(fake)[3]  # Q2 nhan previous_results ban rut gon


def test_loop_stops_when_prompt_too_long(env, monkeypatch):
    monkeypatch.setattr(loop, "min_input_tokens", lambda role="analysis": 100)
    code, fake = run([PLAN, STEP1])
    assert code == loop.STOPPED
    assert [c["json_schema"].__name__ for c in fake.calls] == ["ProblemRestatement", "AnalysisPlan"]  # 0 lan goi AI cho buoc giai
    led = ledger(env)
    assert any("prompt quá dài so với model nhỏ nhất" in o.text for o in led.open_items())
    ev = next(e for e in led.read_log() if e.action == "prompt_fit")
    assert ev.status == "too_long"
    assert set(ev.extra) == {"limit", "budget", "chars_before", "tokens_before", "chars_after", "tokens_after", "cut"}


def test_cli_use_fake(env, capsys):
    assert loop.main([TASK, "--use-fake", "--yes"]) == loop.DONE
    out = capsys.readouterr().out
    assert "R1 | Mỗi bảng có bao nhiêu dòng?" in out
