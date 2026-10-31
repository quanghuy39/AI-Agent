"""Kiem tra dieu phoi theo pha (main.py) bang FakeLLM va R2 gia. Khong goi R2 that, khong goi AI that.
Runner chay that (code ngan) tren ban chep du lieu gia.

Chay: python -m pytest tests/test_orchestrator.py -v
"""

import ctypes
import json
import runpy
import sys
from contextlib import contextmanager
from datetime import datetime

import pytest
from botocore.exceptions import EndpointConnectionError

import llm
import main as app
from analysis import loop
from ledger import Ledger, Phase, ReportLocked
from llm import LLMChainExhausted
from profiling import quality
from profiling.data_summary import SensitiveLeakError
from report.tables import result_tables
from runner import run_code as runner
from skills.report.schemas import SynthesisOutput
from storage import r2

TASK = "bai0a"
SPEC_NAME = "PRODUCT.product_id"
IDS = [f"P{i:04d}" for i in range(1, 7)]
FILES = {
    f"input/{TASK}/de_bai.md": "Đếm số sản phẩm, rồi tính tổng giá. Sản phẩm P0001 là mẫu.",
    f"input/{TASK}/PRODUCT.csv": "product_id,price\n" + "".join(f"{p},{i * 10}\n" for i, p in enumerate(IDS, 1)),
    f"answers/{TASK}/key.json": "{}",
}
RESTATEMENT = {"restatement": "DIENGIAI: đếm sản phẩm rồi cộng giá.", "goals": ["Số sản phẩm", "Tổng giá"],
               "ambiguities": ["Có tính sản phẩm mẫu không"]}
PLAN = {"understanding": "KEHOACH: đếm rồi cộng.", "requirements": ["Số sản phẩm", "Tổng giá"],
        "sub_questions": [{"key": "Q1", "question": "Có bao nhiêu sản phẩm?", "covers": [1]},
                          {"key": "Q2", "question": "Tổng giá là bao nhiêu?", "depends_on": ["Q1"], "covers": [2]}],
        "assumptions": [{"kind": "metric_definition", "text": "Tổng giá = cộng cột price", "required": True}]}


def step(name, passed=True):
    code = ("import json, os\nimport pandas as pd\n"
            "inp, out = os.environ['AGENT_INPUT_DIR'], os.environ['AGENT_OUTPUT_DIR']\n"
            "p = pd.read_csv(os.path.join(inp, 'PRODUCT.csv'), dtype=str)\n"
            f"p.to_csv(os.path.join(out, '{name}.csv'), index=False)\n"
            "meta = {'result_summary': {'n': int(len(p))}, 'checks': ["
            f"{{'type': 'custom', 'name': 'có dòng', 'passed': {passed}, 'reason': 'kiểm tra'}}]}}\n"
            "json.dump(meta, open(os.environ['AGENT_META_PATH'], 'w', encoding='utf-8'))\n")
    return {"explanation": "giải thích", "method": "pandas", "can_be_wrong_if": ["Thiếu dòng"], "code": code,
            "output_files": [f"{name}.csv"]}


STEP1, STEP2 = step("q1"), step("q2")
FULL = [RESTATEMENT, PLAN, STEP1, STEP2]


@pytest.fixture
def env(tmp_path, monkeypatch):
    cache = tmp_path / "cache"

    def download_with_cache(key, cache_dir=None, grader=False):
        assert "answers" not in key
        local = cache / key
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(FILES[key].encode("utf-8"))
        return local

    monkeypatch.setattr(r2, "CACHE_DIR", cache)
    monkeypatch.setattr(r2, "get_r2_settings", lambda: {"R2_INPUT_PREFIX": "input/"})
    monkeypatch.setattr(r2, "list_objects", lambda prefix, grader=False: [{"key": k, "etag": "e"} for k in FILES
                                                                          if k.startswith(prefix)])
    monkeypatch.setattr(r2, "download_with_cache", download_with_cache)
    monkeypatch.setattr(r2, "get_etag", lambda key, cache_dir=None: None)
    monkeypatch.setattr(runner, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(quality, "OUTPUT_DIR", tmp_path / "output")
    return Ledger(tmp_path / "output" / TASK)


def fake_ledger(env):
    return Ledger(env.root.parent / "_fake" / TASK)


def run(script, **kw):
    fake = llm.FakeLLM({"analysis": list(script)}, by_schema={SynthesisOutput: loop.demo_synthesis})
    kw.setdefault("yes", True)
    with llm.use_fake(fake):
        code = app.run_task(TASK, **kw)
    fake.all_calls, fake.calls = fake.calls, [c for c in fake.calls if c["role"] == "analysis"]  # tong hop xet rieng
    return code, fake


def schemas(fake):
    return [c["json_schema"].__name__ for c in fake.calls]


def prompts(fake):
    return [c["messages"][0]["content"] for c in fake.calls]


def say_no_to(fragment):
    return lambda q: "n" if fragment in q else "y"


# 1. chay tron
def test_full_run_reaches_done(env):
    code, fake = run(FULL)
    assert code == loop.DONE and env.get_state().phase == Phase.DONE
    assert schemas(fake) == ["ProblemRestatement", "AnalysisPlan", "AnalysisStep", "AnalysisStep"]
    assert env.results_by_question() == {"Q1": "R1", "Q2": "R2"}
    assert env.get_result("R2").depends_on == ["R1"]
    problem = env.get_problem()
    assert problem["problem"] == FILES[f"input/{TASK}/de_bai.md"] and problem["restatement"] == RESTATEMENT
    assert problem["user_corrections"] == [] and problem["confirmed_at"]
    plan_prompt = prompts(fake)[1]
    assert "DIENGIAI" in plan_prompt and "Số sản phẩm" in plan_prompt and "Có tính sản phẩm mẫu không" in plan_prompt
    assert env.read_log()[-1].action == "done"


def test_rerun_after_done_does_nothing(env, capsys):
    run(FULL)
    code, fake = run([])
    assert code == loop.DONE and fake.calls == [] and len(env.results()) == 2
    assert "đã xong" in capsys.readouterr().out


# 2. tu choi ke hoach
def test_reject_plan_then_rerun_keeps_plan_and_no_duplicate_decision(env):
    code, fake = run([RESTATEMENT, PLAN], yes=False, ask_user=say_no_to("kế hoạch"))
    assert code == loop.STOPPED and len(fake.calls) == 2
    state = env.get_state()
    assert state.phase == Phase.PLAN and state.plan["understanding"] == PLAN["understanding"]  # ghi truoc khi hoi
    assert [d.text for d in env.decisions()] == ["Tổng giá = cộng cột price"]

    code, fake = run([], yes=False, ask_user=say_no_to("kế hoạch"))  # tu choi lan nua: van khong goi AI
    assert code == loop.STOPPED and fake.calls == [] and len(env.open_items()) == 1

    code, fake = run([STEP1, STEP2])
    assert code == loop.DONE and schemas(fake) == ["AnalysisStep", "AnalysisStep"]
    assert [d.text for d in env.decisions()] == ["Tổng giá = cộng cột price", app.CONCLUSION_TEXT]
    assert "D1 [metric_definition]" in prompts(fake)[0]  # quyet dinh dung lai tu ledger


def test_accepted_plan_is_not_asked_again(env):
    run([RESTATEMENT, PLAN, STEP1, LLMChainExhausted("hết")])
    asked = []
    code, _ = run([STEP2], yes=False, ask_user=lambda q: asked.append(q) or "y")
    assert code == loop.DONE and not any("kế hoạch" in q for q in asked)


# 3. het han muc giua chung
def test_chain_exhausted_at_second_question_resumes(env, capsys):
    ts = 1893456000.0
    code, _ = run([RESTATEMENT, PLAN, STEP1, LLMChainExhausted("hết", next_available_at=ts)])
    assert code == loop.EXHAUSTED and env.get_state().phase == Phase.LOOP
    assert env.results_by_question() == {"Q1": "R1"}
    assert datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") in capsys.readouterr().out

    code, _ = run([LLMChainExhausted("hết")])  # van het: khong them open_item
    assert code == loop.EXHAUSTED and "không rõ" in capsys.readouterr().out

    code, fake = run([STEP2])
    assert code == loop.DONE and len(fake.calls) == 1 and "Q2:" in prompts(fake)[0]
    assert [r.question_key for r in env.results()] == ["Q1", "Q2"]
    assert env.get_result("R2").depends_on == ["R1"]
    assert len([o for o in env.open_items() if "hết hạn mức" in o.text]) == 1


def test_chain_exhausted_while_planning_stays_in_plan(env):
    code, _ = run([RESTATEMENT, LLMChainExhausted("hết")])
    state = env.get_state()
    assert code == loop.EXHAUSTED and (state.phase, state.plan) == (Phase.PLAN, None)
    code, fake = run([PLAN, STEP1, STEP2])
    assert code == loop.DONE and schemas(fake)[0] == "AnalysisPlan"


# 4. --restart
def test_restart_supersedes_and_returns_to_understand(env):
    run(FULL)
    texts, n_log = [d.text for d in env.decisions()], len(env.read_log())
    code, _ = run([LLMChainExhausted("hết")], restart=True)
    assert code == loop.EXHAUSTED
    state = env.get_state()
    assert (state.phase, state.plan) == (Phase.UNDERSTAND, None)
    assert [r.status.value for r in env.results()] == ["superseded", "superseded"]
    assert env.results_by_question() == {}
    assert [d.text for d in env.decisions()] == texts and env.active_decisions() == []
    assert len(env.read_log()) > n_log


def test_restart_full_rerun_has_one_active_decision(env):
    run(FULL)
    code, fake = run(FULL, restart=True)
    assert code == loop.DONE and len(fake.calls) == 4
    assert env.results_by_question() == {"Q1": "R3", "Q2": "R4"}
    assert [d.status.value for d in env.decisions()] == ["superseded", "superseded", "confirmed", "confirmed"]
    assert len(env.active_decisions()) == 2  # dinh nghia chi so va chap nhan ket luan


# 5. ho so cu
OLD_RESULT = {"id": "R1", "question": "Câu cũ", "method": "pandas", "result_summary": {"n": 1},
              "checks": [{"name": "c", "status": "pass", "reason": ""}]}


def test_old_record_without_state_and_question_key_is_readable(env, capsys):
    (env.root / "results.json").write_text(json.dumps([OLD_RESULT]), encoding="utf-8")
    assert env.get_state() is None
    assert env.get_result("R1").question_key is None and env.results_by_question() == {}
    assert "Câu cũ" in env.render_markdown()

    code, _ = run(FULL)
    assert code == loop.DONE and "Hồ sơ cũ chưa có state.json" in capsys.readouterr().out
    assert env.results_by_question() == {"Q1": "R2", "Q2": "R3"}
    assert env.get_result("R1").status.value == "valid"  # khong tu y doi ket qua cu


# dung o "Vẫn tiếp tục?": ket qua da vao so
def test_stop_at_flag_keeps_result_and_does_not_solve_again(env):
    flagged = step("q1", passed=False)
    code, _ = run([RESTATEMENT, PLAN, flagged], yes=False, ask_user=say_no_to("tiếp tục"))
    assert code == loop.STOPPED and env.results_by_question() == {"Q1": "R1"}
    assert env.get_state().phase == Phase.LOOP

    code, fake = run([STEP2])
    assert code == loop.DONE and len(fake.calls) == 1 and env.results_by_question() == {"Q1": "R1", "Q2": "R2"}


def test_unfinished_question_restarts_from_first_attempt(env):
    bad = step("q1")
    bad["code"] = "raise SystemExit(4)\n"
    code, _ = run([RESTATEMENT, PLAN, bad, LLMChainExhausted("hết")])
    assert code == loop.EXHAUSTED and env.results() == []
    code, fake = run([STEP1, STEP2])
    assert code == loop.DONE and "Code lỗi" not in prompts(fake)[0]  # khong mang loi lan truoc sang


# gop y ke hoach (s)
PLAN2 = {**PLAN, "understanding": "KEHOACH2: chỉ tính hàng thật.",
         "assumptions": [{"kind": "metric_definition", "text": "Tổng giá = cộng price hàng thật", "required": True}]}
FEEDBACK = "Bỏ sản phẩm mẫu"


def on_plan(*plan_answers):
    """Tra loi lan luot o cau hoi chap nhan ke hoach; cau khac y; o cau go gop y tra FEEDBACK."""
    queue = list(plan_answers)

    def ask(question):
        if "danh sách câu hỏi nhỏ" in question:
            return queue.pop(0)
        return FEEDBACK if question == loop.PLAN_CORRECTION_PROMPT else "y"
    return ask


def test_plan_feedback_goes_to_ai_and_replaces_plan(env):
    code, fake = run([RESTATEMENT, PLAN, PLAN2, STEP1, STEP2], yes=False, ask_user=on_plan("s", "y"))
    assert code == loop.DONE
    assert schemas(fake) == ["ProblemRestatement", "AnalysisPlan", "AnalysisPlan", "AnalysisStep", "AnalysisStep"]
    replan = prompts(fake)[2]
    assert f"{loop.PLAN_CORRECTION_PREFIX}{FEEDBACK}" in replan
    assert "Previous plan, to revise" in replan and "- Q1: Có bao nhiêu sản phẩm?" in replan
    assert env.get_problem()["user_corrections"] == [f"{loop.PLAN_CORRECTION_PREFIX}{FEEDBACK}"]
    state = env.get_state()
    assert state.plan["understanding"] == PLAN2["understanding"] and state.replaced_plan is None
    assert FEEDBACK in prompts(fake)[3]  # buoc giai cung thay gop y
    assert all("Previous plan" not in p for p in prompts(fake)[3:])
    assert [e.status for e in env.read_log() if e.action == "confirm_plan"] == ["correction", "ok"]


def test_plan_feedback_supersedes_decision_of_old_plan(env):
    code, fake = run([RESTATEMENT, PLAN, PLAN2, STEP1, STEP2], yes=False, ask_user=on_plan("s", "y"))
    status = {d.text: d.status.value for d in env.decisions()}
    assert code == loop.DONE and status["Tổng giá = cộng cột price"] == "superseded"
    assert status["Tổng giá = cộng price hàng thật"] == "confirmed"
    assert "Tổng giá = cộng cột price" not in prompts(fake)[3]


def test_plan_feedback_keeps_decision_new_plan_still_has_without_asking_again(env):
    asked = []
    plan_q = on_plan("s", "y")

    def ask(question):
        asked.append(question)
        return plan_q(question)

    code, _ = run([RESTATEMENT, PLAN, PLAN, STEP1, STEP2], yes=False, ask_user=ask)
    assert code == loop.DONE and sum("y = đồng ý" in q for q in asked) == 1
    assert [d.status.value for d in env.decisions() if d.source_key != app.CONCLUSION_KEY] == ["confirmed"]


def test_plan_feedback_interrupted_rerun_makes_new_plan(env):
    code, _ = run([RESTATEMENT, PLAN, LLMChainExhausted("hết")], yes=False, ask_user=on_plan("s"))
    state = env.get_state()
    assert code == loop.EXHAUSTED and state.phase == Phase.PLAN and state.plan is None and state.replaced_plan
    code, fake = run([PLAN2, STEP1, STEP2])
    assert code == loop.DONE and FEEDBACK in prompts(fake)[0] and "Previous plan" in prompts(fake)[0]
    status = {d.text: d.status.value for d in env.decisions()}
    assert status["Tổng giá = cộng cột price"] == "superseded"


def test_plan_only_n_stops(env):
    code, fake = run([RESTATEMENT, PLAN], yes=False, ask_user=on_plan("khác", "", "n"))
    assert code == loop.STOPPED and len(fake.calls) == 2 and env.get_state().plan is not None
    assert [o.text for o in env.open_items()] == [loop.PLAN_OPEN_ITEM]


def test_plan_invalid_answer_asks_again_then_y(env):
    code, _ = run(FULL, yes=False, ask_user=on_plan("khác", "", "y"))
    assert code == loop.DONE


def test_plan_empty_feedback_asks_again_without_calling_ai(env):
    ask = on_plan("s", "n")
    code, fake = run([RESTATEMENT, PLAN], yes=False,
                     ask_user=lambda q: "  " if q == loop.PLAN_CORRECTION_PROMPT else ask(q))
    assert code == loop.STOPPED and len(fake.calls) == 2 and env.get_state().plan is not None
    assert env.get_problem()["user_corrections"] == []


def test_plan_feedback_after_max_retries_offers_only_y_n(env):
    tries = loop.config.ANALYSIS_PLAN_MAX_RETRIES
    asked = []
    ask = on_plan(*["s"] * tries, "s", "n")  # s o lan cuoi khong con hop le: hoi lai
    code, fake = run([RESTATEMENT] + [PLAN] * (tries + 1), yes=False, ask_user=lambda q: asked.append(q) or ask(q))
    assert code == loop.STOPPED and schemas(fake).count("AnalysisPlan") == tries + 1
    assert len(env.get_problem()["user_corrections"]) == tries
    assert [o.text for o in env.open_items()] == [loop.PLAN_OPEN_ITEM]
    assert "s = gõ góp ý" not in asked[-1]


@pytest.mark.parametrize("stop", ["Diễn giải", "kết luận"])
def test_yes_no_stop_asks_again_on_invalid_answer(env, stop):
    seen = []

    def ask(question):
        if stop not in question:
            return "y"
        seen.append(question)
        return {1: "", 2: "chấp nhận"}.get(len(seen), "y")

    code, _ = run(FULL, yes=False, ask_user=ask)
    assert code == loop.DONE and len(seen) == 3
    assert env.get_insights() is not None  # go nham khong lam mat ban tong hop


def test_understand_blank_correction_still_stops(env):
    code, _ = run([RESTATEMENT], yes=False, ask_user=answers("n", ""))
    assert code == loop.STOPPED


def test_plan_question_offers_s_and_yes_skips_it(env, capsys):
    asked = []
    ask = on_plan("y")
    run(FULL, yes=False, ask_user=lambda q: asked.append(q) or ask(q))
    assert any("s = gõ góp ý" in q for q in asked)
    assert run(FULL, restart=True)[0] == loop.DONE and "[y/s/n] y (--yes)" in capsys.readouterr().out


def test_plan_feedback_stays_within_corrections_cap(env):
    long_text = "x" * (loop.config.CONTEXT_CORRECTIONS_MAX_CHARS * 2)
    ask = on_plan("s", "y")
    code, fake = run([RESTATEMENT, PLAN, PLAN, STEP1, STEP2], yes=False,
                     ask_user=lambda q: long_text if q == loop.PLAN_CORRECTION_PROMPT else ask(q))
    assert code == loop.DONE and long_text not in prompts(fake)[2]
    assert "x" * (loop.config.CONTEXT_CORRECTIONS_MAX_CHARS - 40) in prompts(fake)[2]
    assert env.get_problem()["user_corrections"][0].endswith(long_text)  # ho so giu du


def test_plan_template_has_no_feedback_text():
    from skills.analysis import load_prompt
    assert "Previous plan" not in load_prompt("plan") and "Góp ý" not in load_prompt("plan")


# pha hieu de
def answers(*items):
    it = iter(items)
    return lambda q: next(it)


def test_understand_correction_then_confirm(env):
    second = {**RESTATEMENT, "restatement": "DIENGIAI2"}
    code, fake = run([RESTATEMENT, second, PLAN, STEP1, STEP2], yes=False,
                     ask_user=answers("n", "Chỉ tính hàng thật", "y", "y", "y", "y"))  # de, D1, cau hoi, ket luan
    assert code == loop.DONE
    retry = prompts(fake)[1]
    assert "- Chỉ tính hàng thật" in retry and "DIENGIAI: đếm" in retry
    problem = env.get_problem()
    assert problem["restatement"]["restatement"] == "DIENGIAI2"
    assert problem["user_corrections"] == ["Chỉ tính hàng thật"]
    assert "DIENGIAI2" in prompts(fake)[2] and "Chỉ tính hàng thật" in prompts(fake)[2]


def test_understand_gives_up_after_three_retries(env):
    code, fake = run([RESTATEMENT] * 4, yes=False, ask_user=lambda q: "n")
    assert code == loop.STOPPED and len(fake.calls) == 4  # lan dau + 3 lan goi lai
    assert env.get_state().phase == Phase.UNDERSTAND and env.get_problem() is None
    assert any("cách hiểu đề" in o.text for o in env.open_items())


def test_understand_empty_correction_stops(env):
    code, fake = run([RESTATEMENT], yes=False, ask_user=answers("n", "  "))
    assert code == loop.STOPPED and len(fake.calls) == 1


def test_masked_value_not_sent_in_understand_prompt(env):
    code, fake = run(FULL, sensitive=["PRODUCT.product_id"])
    assert code == loop.DONE and "[đã che]" in prompts(fake)[0]
    assert not any(p in text for p in IDS for text in prompts(fake))


# pha synthesize
def conclusion(env):
    return [d for d in env.active_decisions() if d.source_key == app.CONCLUSION_KEY]


def n_report_calls(fake):
    return sum(c["role"] == "report" for c in fake.all_calls)


def synthesis_events(env, action):
    return [e for e in env.read_log() if e.action == action]


def test_done_has_insights_and_confirmed_conclusion(env, capsys):
    code, fake = run(FULL)
    assert code == loop.DONE and n_report_calls(fake) == 1
    insights = env.get_insights()
    assert [i["label"] for i in insights["insights"]] == ["verified", "verified"]
    assert insights["requirements"] == PLAN["requirements"] and insights["insights"][0]["chart"] is None
    assert [d.status.value for d in conclusion(env)] == ["confirmed"]
    assert synthesis_events(env, "confirm_conclusion")[-1].status == "ok"
    out = capsys.readouterr().out
    assert "Yêu cầu 1: Số sản phẩm" in out and "I2 [verified] Demo: ghi nhận 6. (R1)" in out


def test_conclusion_is_asked_even_without_flags(env):
    asked = []
    code, _ = run(FULL, yes=False, ask_user=lambda q: asked.append(q) or "y")
    assert code == loop.DONE and sum("Chấp nhận kết luận?" in q for q in asked) == 1


def test_reject_conclusion_stays_in_synthesize_and_rerun_asks_ai_again(env):
    code, fake = run(FULL, yes=False, ask_user=say_no_to("kết luận"))
    assert code == loop.STOPPED and env.get_state().phase == Phase.SYNTHESIZE and n_report_calls(fake) == 1
    assert env.get_insights() is None  # ban bi tu choi khong nam lai o ho so
    assert len(list((env.root / "_superseded").glob("insights_*.json"))) == 1
    assert [d.status.value for d in conclusion(env)] == ["pending"]
    assert app.CONCLUSION_OPEN_ITEM in [o.text for o in env.open_items()]
    assert synthesis_events(env, "confirm_conclusion")[-1].status == "rejected"

    code, fake = run([])
    assert code == loop.DONE and fake.calls == [] and n_report_calls(fake) == 1  # goi lai AI, khong goi lai analysis
    assert env.get_state().phase == Phase.DONE and env.get_insights() is not None
    assert [d.status.value for d in conclusion(env)] == ["confirmed"]  # khong sinh decision thu hai
    assert [o.status.value for o in env.open_items() if o.text == app.CONCLUSION_OPEN_ITEM] == ["closed"]


def test_interrupted_at_conclusion_reasks_without_calling_ai(env):
    def stop(question):
        if "kết luận" in question:
            raise KeyboardInterrupt
        return "y"

    code, fake = run(FULL, yes=False, ask_user=stop)
    assert code == loop.STOPPED and n_report_calls(fake) == 1 and env.get_insights() is not None
    code, fake = run([])
    assert code == loop.DONE and n_report_calls(fake) == 0


def test_wrong_synthesis_three_times_stops_and_stays(env):
    bad = {"summary": "Có 6 dòng.", "insights": [{"id": "I1", "requirement": 1, "text": "x", "result_ids": []}],
           "conclusion": "c", "limitations": "l"}
    fake = llm.FakeLLM({"analysis": FULL, "report": [bad] * 3})
    with llm.use_fake(fake):
        code = app.run_task(TASK, yes=True)
    assert code == loop.STOPPED and env.get_state().phase == Phase.SYNTHESIZE and env.get_insights() is None
    assert conclusion(env) == []
    assert run([])[0] == loop.DONE and [o.status.value for o in env.open_items()] == ["closed"]


def test_chain_exhausted_in_synthesize_exit_2_then_resume(env):
    fake = llm.FakeLLM({"analysis": FULL, "report": [LLMChainExhausted("hết")]})
    with llm.use_fake(fake):
        code = app.run_task(TASK, yes=True)
    assert code == loop.EXHAUSTED and env.get_state().phase == Phase.SYNTHESIZE
    assert run([])[0] == loop.DONE
    assert [o.status.value for o in env.open_items() if o.text == loop.CHAIN_OPEN_ITEM] == ["closed"]


def test_restart_discards_old_insights(env):
    run(FULL)
    code, _ = run([LLMChainExhausted("hết")], restart=True)
    assert code == loop.EXHAUSTED and env.get_insights() is None
    assert len(list((env.root / "_superseded").glob("insights_*.json"))) == 1
    code, _ = run(FULL)
    insights = env.get_insights()
    assert code == loop.DONE and insights["insights"][0]["result_ids"] == ["R3"]  # khong lan ket qua cu


def test_synthesis_prompt_masks_sensitive(env):
    code, fake = run(FULL, sensitive=["PRODUCT.product_id"])
    report = [c for c in fake.all_calls if c["role"] == "report"]
    assert code == loop.DONE and len(report) == 1 and "[đã che]" in report[0]["messages"][0]["content"]
    assert not any(p in report[0]["messages"][0]["content"] for p in IDS)


def test_table_from_failed_attempt_reaches_synthesis_prompt(env):
    half = step("q1")
    half["can_be_wrong_if"] = ["ý 1", "ý 2"]  # thieu check custom -> lan 1 chua dat, file da ve work/
    code, fake = run([RESTATEMENT, PLAN, half, STEP1, STEP2])
    assert code == loop.DONE and env.get_result("R1").output_path == "work/q1.csv"
    tables = result_tables(env, "R1")
    assert [(t["name"], t["columns"], t["rows"]) for t in tables] == [("q1.csv", ["product_id", "price"], 6)]
    prompt = [c for c in fake.all_calls if c["role"] == "report"][0]["messages"][0]["content"]
    assert "tables: q1.csv [product_id, price]" in prompt and "P0001" not in prompt.split("# Results")[1]


# --resynthesize
def done_snapshot(env):
    keep = [d for d in env.decisions() if d.source_key != app.CONCLUSION_KEY]
    return env.results(), keep, env.get_state().plan


def test_resynthesize_after_done_only_redoes_synthesis(env, capsys):
    run(FULL)
    before, old = done_snapshot(env), env.get_insights()
    asked = []
    code, fake = run([], yes=False, ask_user=lambda q: asked.append(q) or "y", resynthesize=True)
    assert code == loop.DONE and fake.calls == [] and n_report_calls(fake) == 1
    assert asked == ["Chấp nhận kết luận? [y/n] "]
    assert done_snapshot(env) == before and env.get_state().phase == Phase.DONE
    assert env.get_insights()["created_at"] and env.get_insights()["insights"] == old["insights"]
    assert len(list((env.root / "_superseded").glob("insights_*.json"))) == 1
    assert [d.status.value for d in env.decisions() if d.source_key == app.CONCLUSION_KEY] == ["superseded", "confirmed"]
    assert synthesis_events(env, "resynthesize")[-1].extra["superseded_insights"].startswith("_superseded/")
    assert "Tổng hợp lại" in capsys.readouterr().out


def test_resynthesize_old_conclusion_decision_not_sent_to_ai(env):
    run(FULL)
    _, fake = run([], resynthesize=True)
    prompt = [c for c in fake.all_calls if c["role"] == "report"][0]["messages"][0]["content"]
    assert app.CONCLUSION_TEXT not in prompt


def test_resynthesize_reject_stays_in_synthesize(env):
    run(FULL)
    code, _ = run([], yes=False, ask_user=say_no_to("kết luận"), resynthesize=True)
    assert code == loop.STOPPED and env.get_state().phase == Phase.SYNTHESIZE and env.get_insights() is None
    assert [d.status.value for d in conclusion(env)] == ["pending"]
    code, _ = run([])
    assert code == loop.DONE and [d.status.value for d in conclusion(env)] == ["confirmed"]


def test_resynthesize_from_synthesize_phase(env):
    code, _ = run(FULL, yes=False, ask_user=say_no_to("kết luận"))
    assert code == loop.STOPPED
    code, fake = run([], resynthesize=True)
    assert code == loop.DONE and n_report_calls(fake) == 1
    assert [d.status.value for d in env.decisions() if d.source_key == app.CONCLUSION_KEY] == ["superseded", "confirmed"]


def test_resynthesize_when_insights_missing_still_runs(env):
    run(FULL)
    (env.root / "insights.json").unlink()  # ho so tao truoc khi co pha tong hop
    code, fake = run([], resynthesize=True)
    assert code == loop.DONE and n_report_calls(fake) == 1 and env.get_insights() is not None


def test_resynthesize_refused_before_synthesize(env, capsys):
    run([RESTATEMENT, PLAN, LLMChainExhausted("hết")])
    before = env.get_state()
    code, fake = run([], resynthesize=True)
    out = capsys.readouterr().out
    assert code == loop.STOPPED and fake.all_calls == [] and env.get_state() == before
    assert "--resynthesize chỉ dùng khi" in out and "đang ở pha loop" in out


def test_resynthesize_refused_without_state(env, capsys):
    code, fake = run([], resynthesize=True)
    assert code == loop.STOPPED and fake.all_calls == [] and env.get_state() is None
    assert "chưa có tiến độ" in capsys.readouterr().out


@pytest.mark.parametrize("extra", [["--restart"], ["--sensitive", SPEC_NAME]])
def test_cli_resynthesize_rejects_other_options(env, extra):
    with pytest.raises(SystemExit):
        app.main([TASK, "--fake", "--yes", "--resynthesize", *extra])


def test_cli_resynthesize_fake(env):
    assert app.main([TASK, "--fake", "--yes"]) == loop.DONE
    assert app.main([TASK, "--fake", "--yes", "--resynthesize"]) == loop.DONE
    led = fake_ledger(env)
    assert len(led.results()) == 1 and led.get_state().phase == Phase.DONE and led.get_insights() is not None


# bao cao Word
def docx_path(led):
    return led.root / "report" / f"bao_cao_{TASK}.docx"


def test_done_builds_docx(env, capsys):
    assert run(FULL)[0] == loop.DONE and docx_path(env).is_file()
    assert str(docx_path(env)) in capsys.readouterr().out
    assert [e.status for e in env.read_log() if e.step == "report" and e.action == "build"] == ["ok"]


def test_no_docx_before_conclusion_accepted(env):
    run(FULL, yes=False, ask_user=say_no_to("kết luận"))
    assert not docx_path(env).exists()


def test_build_report_does_not_call_ai_and_rebuilds(env):
    run(FULL)
    docx_path(env).unlink()
    results, decisions = env.results(), env.decisions()
    code, fake = run([], build_report=True)
    assert code == loop.DONE and fake.all_calls == [] and docx_path(env).is_file()
    assert (env.results(), env.decisions()) == (results, decisions) and env.get_state().phase == Phase.DONE


@pytest.mark.parametrize("phase_stop", ["loop", "none"])
def test_build_report_refused_before_done(env, capsys, phase_stop):
    if phase_stop == "loop":
        run([RESTATEMENT, PLAN, LLMChainExhausted("hết")])
    code, fake = run([], build_report=True)
    assert code == loop.STOPPED and fake.all_calls == [] and "--build-report chỉ dùng khi" in capsys.readouterr().out


def test_build_report_without_insights_says_so(env, capsys):
    run(FULL)
    (env.root / "insights.json").unlink()
    code, _ = run([], build_report=True)
    assert code == loop.STOPPED and "Chưa có insights.json" in capsys.readouterr().out


def test_report_error_keeps_done_state_and_build_report_recovers(env, monkeypatch, capsys):
    def boom(*a, **k):
        raise app.ReportError("Báo cáo còn placeholder chưa điền.")

    with monkeypatch.context() as m:
        m.setattr(app.report_build, "build_report", boom)
        code, _ = run(FULL)
    assert code == loop.STOPPED and env.get_state().phase == Phase.DONE and not docx_path(env).exists()
    assert "Không dựng được báo cáo" in capsys.readouterr().out
    assert app.REPORT_OPEN_ITEM in [o.text for o in env.open_items()]
    assert run([], build_report=True)[0] == loop.DONE and docx_path(env).is_file()
    assert [o.status.value for o in env.open_items() if o.text == app.REPORT_OPEN_ITEM] == ["closed"]


@pytest.mark.parametrize("extra", ["--restart", "--resynthesize"])
def test_cli_build_report_rejects_other_options(env, extra):
    with pytest.raises(SystemExit):
        app.main([TASK, "--fake", "--yes", "--build-report", extra])


def test_restart_and_resynthesize_move_old_report(env):
    run(FULL)
    run([], resynthesize=True)
    assert len(list((env.root / "_superseded").glob("report_*"))) == 1 and docx_path(env).is_file()
    run([LLMChainExhausted("hết")], restart=True)
    assert not (env.root / "report").exists() and len(list((env.root / "_superseded").glob("report_*"))) == 2


# report/ dang mo trong Word
windows_only = pytest.mark.skipif(sys.platform != "win32", reason="khoa file kieu Windows")


@contextmanager
def opened_in_word(path):
    """Giu file nhu Word: co quyen ghi, chi cho chuong trinh khac doc."""
    kernel = ctypes.windll.kernel32
    kernel.CreateFileW.restype = ctypes.c_void_p
    kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                                   ctypes.c_uint32, ctypes.c_void_p]
    kernel.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel.CreateFileW(str(path), 0xC0000000, 1, None, 3, 0, None)  # doc + ghi, FILE_SHARE_READ, OPEN_EXISTING
    assert handle != ctypes.c_void_p(-1).value
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


def tree(led):
    return {p.relative_to(led.root).as_posix(): p.read_bytes() for p in led.root.rglob("*") if p.is_file()}


LOCK_MESSAGE = f"Đóng file bao_cao_{TASK}.docx (đang mở trong Word) rồi chạy lại"


@windows_only
@pytest.mark.parametrize("flag", ["restart", "resynthesize", "build_report"])
def test_locked_report_stops_and_changes_nothing(env, capsys, flag):
    run(FULL)
    capsys.readouterr()
    with opened_in_word(docx_path(env)):
        before = tree(env)
        code, fake = run([], **{flag: True})
        assert tree(env) == before
    assert code == loop.STOPPED and fake.calls == [] and capsys.readouterr().out.strip().endswith(LOCK_MESSAGE)


@windows_only
def test_locked_report_runs_after_closing(env):
    run(FULL)
    with opened_in_word(docx_path(env)):
        assert run([], build_report=True)[0] == loop.STOPPED
    assert run([], build_report=True)[0] == loop.DONE


@windows_only
def test_word_owner_file_not_named_beside_document(env):
    run(FULL)
    owner = docx_path(env).with_name("~$o_cao.docx")
    owner.write_bytes(b"owner")
    with opened_in_word(docx_path(env)), opened_in_word(owner):
        assert env.locked_report_files() == [docx_path(env).name]


@windows_only
def test_only_owner_file_locked_still_named(env):
    run(FULL)
    owner = docx_path(env).with_name("~$o_cao.docx")
    owner.write_bytes(b"owner")
    with opened_in_word(owner):
        assert env.locked_report_files() == [owner.name]


def test_unlocked_or_missing_report_has_no_locked_files(env):
    assert env.locked_report_files() == []
    run(FULL)
    assert env.locked_report_files() == []


@windows_only
def test_discard_on_locked_report_leaves_no_partial_copy(env):
    run(FULL)
    with opened_in_word(docx_path(env)):
        with pytest.raises(ReportLocked) as caught:
            env.discard_insights()
        assert str(caught.value) == LOCK_MESSAGE
        assert not list(env.root.glob("_superseded/report_*"))
    assert env.get_insights() is not None and docx_path(env).is_file()


@pytest.mark.parametrize("flag", ["restart", "resynthesize"])
def test_lock_missed_by_check_stops_without_traceback(env, capsys, monkeypatch, flag):
    run(FULL)
    results, decisions, state = env.results(), env.decisions(), env.get_state()

    def locked(self, target):
        raise PermissionError(13, "dang bi khoa")

    with monkeypatch.context() as patch:
        patch.setattr(type(env.root), "rename", locked)
        code, fake = run([], **{flag: True})
    assert code == loop.STOPPED and fake.calls == []
    assert "Đóng file report (đang mở trong Word) rồi chạy lại" in capsys.readouterr().out
    assert (env.results(), env.decisions(), env.get_state()) == (results, decisions, state)
    assert docx_path(env).is_file() and env.get_insights() is not None


def test_fake_restart_yes_reaches_done_with_docx(env):
    assert app.main([TASK, "--fake", "--restart", "--yes"]) == loop.DONE
    led = fake_ledger(env)
    assert led.get_state().phase == Phase.DONE and docx_path(led).is_file()
    assert (led.root / "report" / "charts" / "I1.png").is_file()
    assert app.main([TASK, "--fake", "--build-report"]) == loop.DONE


def test_fake_cli_reaches_done_with_insights(env):
    assert app.main([TASK, "--fake", "--yes"]) == loop.DONE
    led = fake_ledger(env)
    insights = led.get_insights()
    assert led.get_state().phase == Phase.DONE and (led.root / "insights.json").is_file()
    assert insights["model"] == "fake" and insights["insights"][0]["label"] == "verified"
    assert "{{" not in insights["insights"][0]["text_filled"]
    assert app.main([TASK, "--fake", "--yes", "--restart"]) == loop.DONE  # id ket qua doi: R2
    assert fake_ledger(env).get_insights()["insights"][0]["result_ids"] == ["R2"]


# ma thoat
def test_sensitive_leak_stops_without_duplicate_open_item(env, monkeypatch):
    def leak(*a, **k):
        raise SensitiveLeakError("Giá trị của cột bị che lọt vào bản tóm tắt")

    monkeypatch.setattr(loop, "load_context", leak)
    assert run([])[0] == loop.STOPPED and run([])[0] == loop.STOPPED
    assert len(env.open_items()) == 1 and env.read_log()[-1].status == "sensitive_leak"


def test_r2_error_exit_1_prints_only_error_name(env, monkeypatch, capsys):
    def down(*a, **k):
        raise EndpointConnectionError(endpoint_url="https://bi-mat.example")

    monkeypatch.setattr(r2, "list_objects", down)
    code, fake = run([])
    out = capsys.readouterr().out
    assert code == loop.ERROR and fake.calls == [] and "EndpointConnectionError" in out and "bi-mat" not in out
    assert env.read_log()[-1].status == "r2_error"


# Ledger
def test_state_roundtrip_and_not_in_markdown(env):
    env.set_problem({"title": "Bài"})
    state = env.set_state(Phase.PLAN, {"understanding": "KEHOACH"})
    again = Ledger(env.root).get_state()
    assert again == state and again.phase == Phase.PLAN and state.updated_at
    assert "KEHOACH" not in env.render_markdown()
    assert not list(env.root.glob(".*.tmp"))


def test_results_by_question_only_valid(env):
    checks = [{"name": "c", "status": "pass"}]
    env.add_result("a", "m", {}, checks, question_key="Q1")
    env.add_result("b", "m", {}, checks, depends_on=["R1"], question_key="Q2")
    env.add_result("c", "m", {}, checks)
    env.mark_stale("R1")
    assert env.results_by_question() == {}
    env.add_result("a", "m", {}, checks, question_key="Q1")
    assert env.results_by_question() == {"Q1": "R4"}


def test_add_open_item_once_reopens_after_close(env):
    first = env.add_open_item_once("việc")
    assert env.add_open_item_once("việc").id == first.id
    env.close_open_item(first.id)
    assert env.add_open_item_once("việc").id != first.id


# cot che chot trong state
SPEC = "PRODUCT.product_id"


def test_sensitive_saved_on_first_run_and_reused(env):
    code, fake = run([RESTATEMENT, PLAN, STEP1, LLMChainExhausted("hết")], sensitive=[SPEC])
    assert code == loop.EXHAUSTED and env.get_state().sensitive == [SPEC]
    code, fake = run([STEP2])  # khong truyen: van che theo state
    assert code == loop.DONE and env.get_state().sensitive == [SPEC]
    assert not any(p in text for p in IDS for text in prompts(fake))


def test_same_sensitive_on_resume_is_accepted(env):
    run([RESTATEMENT, PLAN, LLMChainExhausted("hết")], sensitive=[SPEC])
    assert run([STEP1, STEP2], sensitive=[SPEC])[0] == loop.DONE


def test_different_sensitive_stops_and_suggests_restart(env, capsys):
    run([RESTATEMENT, PLAN, LLMChainExhausted("hết")], sensitive=[SPEC])
    state = env.get_state()
    code, fake = run([], sensitive=["PRODUCT.price"])
    assert code == loop.STOPPED and fake.calls == [] and "--restart" in capsys.readouterr().out
    assert env.get_state().sensitive == [SPEC] and env.get_state().phase == state.phase


@pytest.mark.parametrize("product_id_type, masked", [
    ("string (PK, SENSITIVE)", {(("PRODUCT", "product_id"), "dictionary"), (("PRODUCT", "contact"), "auto_email")}),
    ("string (PK)", {(("PRODUCT", "contact"), "auto_email")}),
])
def test_masking_comes_from_dictionary_tag_without_sensitive_arg(env, monkeypatch, product_id_type, masked):
    rows = "".join(f"{p},{p.lower()}@vidu.vn\n" for p in IDS)
    monkeypatch.setitem(FILES, f"input/{TASK}/PRODUCT.csv", "product_id,contact\n" + rows)
    monkeypatch.setitem(FILES, f"input/{TASK}/data_dictionary.md",
                        f"## PRODUCT\n\n| Cột | Kiểu | Định nghĩa |\n|---|---|---|\n| product_id | {product_id_type} | Mã |\n"
                        "| contact | string | Liên hệ |\n")
    ctx = loop.load_context(TASK)
    got = {((m["table"], m["column"]), m["reason"]) for m in ctx.summary["masked_columns"]}
    assert got == masked and ctx.sensitive == []
    assert ("P0002" in ctx.summary["text"]) == (product_id_type == "string (PK)")


# etag dictionary luu trong profile
DICT_KEY = f"input/{TASK}/data_dictionary.md"
DICT_MD = "## PRODUCT\n\n| Cột | Kiểu | Định nghĩa |\n|---|---|---|\n| product_id | string (PK) | Mã |\n"


def change_dictionary_etag(monkeypatch):
    monkeypatch.setattr(r2, "list_objects", lambda prefix, grader=False: [
        {"key": k, "etag": "moi" if k == DICT_KEY else "e"} for k in FILES if k.startswith(prefix)])


def test_same_dictionary_etag_resumes_normally(env, monkeypatch):
    monkeypatch.setitem(FILES, DICT_KEY, DICT_MD)
    assert run([RESTATEMENT, PLAN, LLMChainExhausted("hết")])[0] == loop.EXHAUSTED
    assert env.get_data_profile()[quality.REPORT_KEY]["dictionary"]["etag"] == "e"
    assert run([STEP1, STEP2])[0] == loop.DONE


def test_changed_dictionary_etag_stops_with_refresh_hint(env, monkeypatch, capsys):
    monkeypatch.setitem(FILES, DICT_KEY, DICT_MD)
    run([RESTATEMENT, PLAN, LLMChainExhausted("hết")])
    phase = env.get_state().phase
    before = env.get_data_profile()
    change_dictionary_etag(monkeypatch)
    code, fake = run([])
    out = capsys.readouterr().out
    assert code == loop.STOPPED == 3 and fake.calls == []
    assert f"python -m profiling.data_summary {TASK} --refresh" in out and "--set/--range" in out
    assert env.get_state().phase == phase and env.get_data_profile() == before  # khong tu profile lai


def test_old_profile_without_etag_stops(env):
    run([RESTATEMENT, PLAN, LLMChainExhausted("hết")])
    profile = env.get_data_profile()
    del profile[quality.REPORT_KEY]["dictionary"]["etag"]
    env.set_data_profile(profile)
    assert run([])[0] == loop.STOPPED


@pytest.mark.parametrize("flag", ["restart", "resynthesize"])
def test_changed_dictionary_stops_before_touching_record(env, monkeypatch, flag):
    monkeypatch.setitem(FILES, DICT_KEY, DICT_MD)
    run(FULL)
    results, insights = env.results_by_question(), env.get_insights()
    change_dictionary_etag(monkeypatch)
    assert run([], **{flag: True})[0] == loop.STOPPED
    assert env.results_by_question() == results and env.get_insights() == insights
    assert env.get_state().phase == Phase.DONE


def test_build_report_stops_when_dictionary_changed(env, monkeypatch):
    monkeypatch.setitem(FILES, DICT_KEY, DICT_MD)
    run(FULL)
    change_dictionary_etag(monkeypatch)
    assert run([], build_report=True)[0] == loop.STOPPED


def test_restart_can_change_sensitive_and_keeps_it_otherwise(env):
    run(FULL, sensitive=[SPEC])
    run([LLMChainExhausted("hết")], restart=True)
    assert env.get_state().sensitive == [SPEC]  # khong truyen: khong am tham bo che
    run([LLMChainExhausted("hết")], restart=True, sensitive=["PRODUCT.price"])
    assert env.get_state().sensitive == ["PRODUCT.price"]


def test_invalid_sensitive_stops_and_is_not_saved(env, capsys):
    code, fake = run([], sensitive=["PRODUCT.khong_co"])
    assert code == loop.STOPPED and fake.calls == [] and "Cột che không hợp lệ" in capsys.readouterr().out
    assert env.get_state().sensitive is None
    assert run(FULL)[0] == loop.DONE  # sua lai duoc ma khong can --restart


def test_cli_passes_sensitive(env):
    assert app.main([TASK, "--fake", "--yes", "--sensitive", SPEC]) == loop.DONE
    assert fake_ledger(env).get_state().sensitive == [SPEC]


# previous_results va work/ sau --restart
def work_files(env):
    work = env.root / "work"
    return sorted(p.relative_to(work).as_posix() for p in work.rglob("*") if p.is_file())


def test_restart_moves_work_files_without_deleting(env):
    run(FULL)
    run([LLMChainExhausted("hết")], restart=True)
    files = work_files(env)
    assert len(files) == 2 and all(f.startswith("_superseded/") for f in files)
    assert {f.rsplit("/", 1)[1] for f in files} == {"q1.csv", "q2.csv"}
    r1 = env.get_result("R1")
    assert r1.output_path.startswith("work/_superseded/") and (env.root / r1.output_path).is_file()
    assert (env.root / "work" / "_superseded").exists()


def test_restart_also_moves_file_left_by_failed_attempt(env):
    # lan thu hong ghi file roi lan dat ghi y het: output_path khong co file nay nhung no van phai roi khoi work/
    half = step("q1")
    half["can_be_wrong_if"] = ["ý 1", "ý 2"]  # thieu check custom -> lan 1 chua dat, file da ve work/
    run([RESTATEMENT, PLAN, half, STEP1, STEP2])
    assert env.get_result("R1").output_path == "work/q1.csv"
    run([LLMChainExhausted("hết")], restart=True)
    assert all(f.startswith("_superseded/") for f in work_files(env))


def test_two_restarts_keep_separate_archives(env):
    run(FULL)
    run(FULL, restart=True)
    run([LLMChainExhausted("hết")], restart=True)
    assert len({f.split("/")[1] for f in work_files(env)}) == 2 and len(work_files(env)) == 4


def test_ai_does_not_see_superseded_after_restart(env, monkeypatch):
    run(FULL)
    probe = step("q1")
    probe["code"] = probe["code"].replace(
        "p.to_csv(", "assert not os.path.exists(os.path.join(out, '_superseded'))\np.to_csv(", 1)
    code, fake = run([RESTATEMENT, PLAN, probe, STEP2], restart=True)
    assert code == loop.DONE  # runner khong chep _superseded/ vao AGENT_OUTPUT_DIR
    for text in prompts(fake):
        assert "_superseded" not in text and "R1 |" not in text and "R1:" not in text
    assert "Files in output directory: (none)" in prompts(fake)[2]


def test_short_previous_only_valid(env, monkeypatch):
    run(FULL)
    run([LLMChainExhausted("hết")], restart=True)
    env.add_result("Câu mới", "m", {"a": 1}, [{"name": "c", "status": "pass"}], question_key="Q1")
    ctx = loop.load_context(TASK)
    text = loop._short_previous(ctx, env.results_by_question())
    assert "Câu mới" in text and "Có bao nhiêu sản phẩm?" not in text and "_superseded" not in text


def test_summary_shows_only_valid(env, capsys):
    run(FULL)
    run(FULL, restart=True)
    out = capsys.readouterr().out.rsplit("Kết quả:", 1)[1]
    assert "R3 |" in out and "R1 |" not in out


# open_item het han muc
def chain_items(env):
    return [o for o in env.open_items() if o.text == loop.CHAIN_OPEN_ITEM]


def test_chain_open_item_closes_after_a_question_is_solved(env):
    run([RESTATEMENT, PLAN, STEP1, LLMChainExhausted("hết")])
    assert [o.status.value for o in chain_items(env)] == ["open"]
    run([LLMChainExhausted("hết")])  # van het, chua giai them: con mo
    assert [o.status.value for o in chain_items(env)] == ["open"]
    assert run([STEP2])[0] == loop.DONE
    assert [o.status.value for o in chain_items(env)] == ["closed"]


def test_chain_open_item_closes_even_if_user_stops_at_flag(env):
    run([RESTATEMENT, PLAN, LLMChainExhausted("hết")])
    code, _ = run([step("q1", passed=False)], yes=False, ask_user=say_no_to("tiếp tục"))
    assert code == loop.STOPPED and [o.status.value for o in chain_items(env)] == ["closed"]


def test_other_open_items_stay_open(env):
    env.add_open_item("việc khác")
    run([RESTATEMENT, PLAN, LLMChainExhausted("hết")])
    run([STEP1, STEP2])
    assert [o.status.value for o in env.open_items()] == ["open", "closed"]


def test_new_exhaustion_after_close_opens_new_item(env):
    run([RESTATEMENT, PLAN, LLMChainExhausted("hết")])
    run([STEP1, LLMChainExhausted("hết")])
    assert [o.status.value for o in chain_items(env)] == ["closed", "open"]


# --fake chay tiep o pha bat ky
@pytest.mark.parametrize("stop_at, phase", [("kế hoạch", Phase.PLAN), ("kết luận", Phase.SYNTHESIZE)])
def test_fake_stop_then_resume_reaches_done(env, capsys, stop_at, phase):
    led = fake_ledger(env)
    assert app.run_cli(TASK, fake=True, ask_user=say_no_to(stop_at)) == loop.STOPPED
    assert led.get_state().phase == phase
    assert app.run_cli(TASK, fake=True, ask_user=lambda q: "y") == loop.DONE
    assert led.get_state().phase == Phase.DONE and len(led.results()) == 1
    assert "hạn mức" not in capsys.readouterr().out
    assert not any(o.text == loop.CHAIN_OPEN_ITEM for o in led.open_items())


def test_fake_interrupted_during_loop_resumes(env):
    fake = llm.FakeLLM([LLMChainExhausted("hết")], by_schema={loop.ProblemRestatement: RESTATEMENT,
                                                              loop.AnalysisPlan: PLAN})
    with app.fake_output(), llm.use_fake(fake):
        assert app.run_task(TASK, yes=True) == loop.EXHAUSTED
    led = fake_ledger(env)
    assert led.get_state().phase == Phase.LOOP
    assert app.run_cli(TASK, fake=True, yes=True) == loop.DONE
    assert led.get_state().phase == Phase.DONE and led.results_by_question().keys() == {"Q1", "Q2"}


# --fake khong dung ho so that
def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_fake_leaves_real_record_unchanged(env):
    run([RESTATEMENT, PLAN, STEP1, LLMChainExhausted("hết")])
    before = snapshot(env.root)
    assert len(before) > 5 and "state.json" in before

    assert app.main([TASK, "--fake", "--yes"]) == loop.DONE
    assert snapshot(env.root) == before
    assert app.main([TASK, "--fake", "--yes", "--restart"]) == loop.DONE  # ca --restart cua che do gia lap
    assert snapshot(env.root) == before
    assert env.get_state().phase == Phase.LOOP


def test_fake_record_has_everything_under_fake_dir(env):
    assert app.main([TASK, "--fake", "--yes"]) == loop.DONE
    assert snapshot(env.root) == {}  # khong tao gi o ho so that
    files = snapshot(fake_ledger(env).root)
    assert {"state.json", "results.json", "run_log.jsonl", "data_profile.json", "work/demo_row_counts.csv"} <= set(files)
    assert any(f.startswith("code/") for f in files)
    assert fake_ledger(env).get_result("R1").output_path == "work/demo_row_counts.csv"


def test_real_run_after_fake_starts_fresh(env):
    app.main([TASK, "--fake", "--yes"])
    assert env.get_state() is None
    code, fake = run(FULL)
    assert code == loop.DONE and schemas(fake)[0] == "ProblemRestatement"
    assert "Demo" not in env.get_state().plan["understanding"]


def test_fake_banner_printed_first(env, capsys):
    app.main([TASK, "--fake", "--yes"])
    assert capsys.readouterr().out.splitlines()[0] == f"Chế độ giả lập, hồ sơ ở output/_fake/{TASK}"


def test_no_banner_without_fake(env, capsys):
    run(FULL)
    assert "Chế độ giả lập" not in capsys.readouterr().out


def test_fake_output_restored_after_error(env, monkeypatch):
    before = runner.OUTPUT_DIR, quality.OUTPUT_DIR
    monkeypatch.setattr(loop, "load_context", lambda *a, **k: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        app.run_cli(TASK, fake=True, yes=True)
    assert (runner.OUTPUT_DIR, quality.OUTPUT_DIR) == before


# sai schema khong phai het han muc
def test_wrong_schema_from_fake_is_not_reported_as_quota(env, capsys):
    code, fake = run([RESTATEMENT, PLAN], yes=False, ask_user=say_no_to("kế hoạch"))
    code, fake = run([RESTATEMENT])  # lech kich ban: ban dien giai roi vao buoc giai
    out = capsys.readouterr().out
    assert code == loop.ERROR and "sai schema" in out and "Hết hạn mức" not in out
    assert not any(o.text == loop.CHAIN_OPEN_ITEM for o in env.open_items())
    assert env.read_log()[-1].status == "json_invalid"
    assert env.get_state().phase == Phase.LOOP  # tien do van giu


def test_all_models_json_invalid_is_not_quota(env):
    bad = [{"model_key": m, "reason": "json_invalid", "status_code": None} for m in ("a", "b")]
    code, _ = run([RESTATEMENT, LLMChainExhausted("hết", attempts=bad)])
    assert code == loop.ERROR and env.open_items() == []


def test_mixed_failures_stay_exhausted(env):
    tried = [{"model_key": "a", "reason": "json_invalid", "status_code": None},
             {"model_key": "b", "reason": "rate_limit", "status_code": 429}]
    code, _ = run([RESTATEMENT, LLMChainExhausted("hết", attempts=tried)])
    assert code == loop.EXHAUSTED and [o.text for o in env.open_items()] == [loop.CHAIN_OPEN_ITEM]


# CLI
def test_cli_main_fake(env, capsys):
    assert app.main([TASK, "--fake", "--yes"]) == loop.DONE
    assert "R1 | Mỗi bảng có bao nhiêu dòng?" in capsys.readouterr().out
    assert fake_ledger(env).get_state().phase == Phase.DONE


def test_cli_rejects_bad_task(env):
    with pytest.raises(SystemExit):
        app.main(["../x", "--fake", "--yes"])


def test_analysis_loop_as_module_uses_same_flow(env, monkeypatch):
    # chay dung kieu python -m: module la __main__, main.py nap ban analysis.loop thu hai
    monkeypatch.setattr(sys, "argv", ["analysis.loop", TASK, "--use-fake", "--yes"])
    with pytest.warns(RuntimeWarning, match="found in sys.modules"), pytest.raises(SystemExit) as e:
        runpy.run_module("analysis.loop", run_name="__main__")
    assert e.value.code == loop.DONE and fake_ledger(env).get_state().phase == Phase.DONE
