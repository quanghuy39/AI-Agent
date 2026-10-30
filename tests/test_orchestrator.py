"""Kiem tra dieu phoi theo pha (main.py) bang FakeLLM va R2 gia. Khong goi R2 that, khong goi AI that.
Runner chay that (code ngan) tren ban chep du lieu gia.

Chay: python -m pytest tests/test_orchestrator.py -v
"""

import json
import runpy
import sys
from datetime import datetime

import pytest
from botocore.exceptions import EndpointConnectionError

import llm
import main as app
from analysis import loop
from ledger import Ledger, Phase
from llm import LLMChainExhausted
from profiling import quality
from profiling.data_summary import SensitiveLeakError
from runner import run_code as runner
from storage import r2

TASK = "bai0a"
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
    fake = llm.FakeLLM({"analysis": list(script)})
    kw.setdefault("yes", True)
    with llm.use_fake(fake):
        code = app.run_task(TASK, **kw)
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
    assert env.decisions() == []

    code, fake = run([], yes=False, ask_user=say_no_to("kế hoạch"))  # tu choi lan nua: van khong goi AI
    assert code == loop.STOPPED and fake.calls == [] and len(env.open_items()) == 1

    code, fake = run([STEP1, STEP2])
    assert code == loop.DONE and schemas(fake) == ["AnalysisStep", "AnalysisStep"]
    assert [d.text for d in env.decisions()] == ["Tổng giá = cộng cột price"]
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
    decisions, n_log = env.decisions(), len(env.read_log())
    code, _ = run([LLMChainExhausted("hết")], restart=True)
    assert code == loop.EXHAUSTED
    state = env.get_state()
    assert (state.phase, state.plan) == (Phase.UNDERSTAND, None)
    assert [r.status.value for r in env.results()] == ["superseded", "superseded"]
    assert env.results_by_question() == {}
    assert env.decisions() == decisions and len(env.read_log()) > n_log


def test_restart_full_rerun_has_no_duplicate_decision(env):
    run(FULL)
    code, fake = run(FULL, restart=True)
    assert code == loop.DONE and len(fake.calls) == 4
    assert env.results_by_question() == {"Q1": "R3", "Q2": "R4"}
    assert len(env.decisions()) == 1


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


# pha hieu de
def answers(*items):
    it = iter(items)
    return lambda q: next(it)


def test_understand_correction_then_confirm(env):
    second = {**RESTATEMENT, "restatement": "DIENGIAI2"}
    code, fake = run([RESTATEMENT, second, PLAN, STEP1, STEP2], yes=False,
                     ask_user=answers("n", "Chỉ tính hàng thật", "y", "y", "y"))  # de, ke hoach, ket luan
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
def test_reject_conclusion_stays_in_synthesize(env):
    code, _ = run(FULL, yes=False, ask_user=say_no_to("kết luận"))
    assert code == loop.STOPPED and env.get_state().phase == Phase.SYNTHESIZE
    code, fake = run([])
    assert code == loop.DONE and fake.calls == [] and env.get_state().phase == Phase.DONE


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


def test_default_sensitive_of_task_does_not_count_as_different(env, monkeypatch):
    monkeypatch.setitem(loop.config.ANALYSIS_DEFAULT_SENSITIVE, TASK, [SPEC])
    run([RESTATEMENT, PLAN, LLMChainExhausted("hết")], sensitive=["PRODUCT.price"])
    assert set(env.get_state().sensitive) == {SPEC, "PRODUCT.price"}
    assert run([STEP1, STEP2], sensitive=["PRODUCT.price"])[0] == loop.DONE


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
    assert "q1.csv" not in (env.get_result("R1").output_path or "")
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
