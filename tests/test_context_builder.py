"""Kiem tra context_builder: ngan sach theo role, thu tu cat, data summary thu nho, dung khi khong cat noi.
FakeLLM va R2 gia, khong goi that.

Chay: python -m pytest tests/test_context_builder.py -v
"""

import math

import pytest
import yaml

import config
import context_builder as cb
import llm
import main as app
from analysis import loop
from ledger import Ledger, Phase
from llm import LLMConfigError, client
from profiling import quality
from runner import run_code as runner
from skills.analysis import load_prompt
from skills.analysis.schemas import SubQuestion
from storage import r2

TASK = "bai0a"
IDS = [f"CUST{i:04d}" for i in range(1, 7)]
WIDE = [f"chi_so_{i:02d}" for i in range(40)]  # nhieu cot de ban tom tat du dai
PROBLEM = "De bai goc: dem khach theo phan khuc."
FILES = {
    f"input/{TASK}/de_bai.md": PROBLEM,
    f"input/{TASK}/CUSTOMER.csv": ",".join(["customer_id", *WIDE]) + "\n"
                                  + "".join(",".join([c, *[str(i * 7) for i in range(40)]]) + "\n" for c in IDS),
}
RESTATEMENT = {"restatement": "DIENGIAI: dem khach.", "goals": ["So khach"], "ambiguities": ["Co tinh khach cu"]}
PLAN = {"understanding": "Dem khach.", "requirements": ["So khach"], "assumptions": [],
        "sub_questions": [{"key": "Q1", "question": "CAUHOI1: co bao nhieu khach?", "covers": [1]}]}
STEP = {"explanation": "x", "method": "pandas", "can_be_wrong_if": ["Thieu dong"], "output_files": ["q1.csv"],
        "code": ("import json, os\nimport pandas as pd\n"
                 "pd.DataFrame({'n': [1]}).to_csv(os.path.join(os.environ['AGENT_OUTPUT_DIR'], 'q1.csv'), index=False)\n"
                 "meta = {'result_summary': {'n': 1}, 'checks': ["
                 "{'type': 'custom', 'name': 'x', 'passed': True, 'reason': 'ok'}]}\n"
                 "json.dump(meta, open(os.environ['AGENT_META_PATH'], 'w', encoding='utf-8'))\n")}
SENSITIVE = ["CUSTOMER.customer_id"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    cache = tmp_path / "cache"

    def download_with_cache(key, cache_dir=None, grader=False):
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


def run(script, **kw):
    fake = llm.FakeLLM({"analysis": list(script)})
    kw.setdefault("yes", True)
    with llm.use_fake(fake):
        code = app.run_task(TASK, **kw)
    return code, fake


def context(env, corrections=()):
    env.set_state(Phase.LOOP)
    env.set_problem({"problem": PROBLEM, "restatement": RESTATEMENT, "user_corrections": list(corrections),
                     "confirmed_at": "2026-10-30T00:00:00+07:00"})
    return loop.load_context(TASK, SENSITIVE)


def fill(env, n_results=0, n_decisions=0):
    for i in range(n_results):
        env.add_result(f"Cau {i}", "m", {"a": 1}, [{"name": "c", "status": "pass"}], question_key=f"Q{i + 1}")
    for i in range(n_decisions):
        d = env.add_decision("assumption", f"Gia dinh so {i} " + "x" * 200)
        env.confirm_decision(d.id)


def limit_exactly(env, ctx, monkeypatch, make_text, target_cuts=None):
    """limit nho nhat de moi bac cat duoc dung het va prompt van vua."""
    monkeypatch.setattr(cb, "min_input_tokens", lambda role: 1)
    with pytest.raises(loop.StopLoop):
        make_text()
    tokens = [e for e in ctx.ledger.read_log() if e.action == "prompt_fit"][-1].extra["tokens_after"]
    limit = math.ceil(tokens / (1 - config.PROMPT_MARGIN)) + 1
    monkeypatch.setattr(cb, "min_input_tokens", lambda role: limit)
    return limit


Q1 = SubQuestion(key="Q1", question="CAUHOI1: co bao nhieu khach?")


# 1. ngan sach theo role
@pytest.mark.parametrize("role", ["analysis", "report", "slide"])
def test_budget_uses_smallest_model_of_each_role(role):
    with open(config.ROOT / "config" / "llm.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    expected = min(cfg["models"][i["model"]]["max_input_tokens"] for i in cfg["roles"][role]["chain"])
    assert cb.min_input_tokens(role) == expected
    assert cb.budget_tokens(expected) == int(expected * (1 - config.PROMPT_MARGIN))


def test_budget_known_values_now():
    assert (cb.min_input_tokens("analysis"), cb.min_input_tokens("report"), cb.min_input_tokens("slide")) \
        == (9000, 30000, 5000)


def test_build_prompt_reports_role_and_prompt(monkeypatch):
    monkeypatch.setattr(cb, "min_input_tokens", lambda role: {"report": 10 ** 6}[role])
    text, info = cb.build_prompt("report", "plan", lambda **v: v["a"], {"a": "x"}, {})
    assert text == "x" and (info["role"], info["prompt"], info["limit"]) == ("report", "plan", 10 ** 6)


def test_estimate_uses_chars_ratio_and_schema_tokens():
    assert cb.estimate_tokens("x" * 34) == math.ceil(34 / config.PROMPT_CHARS_PER_TOKEN) + config.PROMPT_SCHEMA_TOKENS


# 2. de, cau hoi, contract khong bao gio cat
def test_never_cut_parts_are_not_declared_cuttable():
    for prompt, cuts in cb.PROMPT_CUTS.items():
        assert not set(cuts) & set(cb.NEVER_CUT), prompt
    assert cb.SUMMARY == cb.PROMPT_CUTS["step"][-1] == cb.PROMPT_CUTS["plan"][-1]  # luon la bac cuoi


def test_problem_question_contract_survive_every_cut(env, monkeypatch):
    ctx = context(env, ["Sua 1"])
    fill(env, 3, 25)
    ctx.decisions = [loop._decision_line(d) for d in env.decisions()]
    make = lambda: loop._step_prompt(ctx, Q1, env.results_by_question(), "E" * 3000)  # noqa: E731
    limit_exactly(env, ctx, monkeypatch, make)
    text = make()
    assert PROBLEM in text and "Q1: CAUHOI1: co bao nhieu khach?" in text
    assert load_prompt("contract").strip() in text


def test_plan_keeps_problem_and_contract_when_cut(env, monkeypatch):
    ctx = context(env)
    make = lambda: loop.make_plan(ctx)  # noqa: E731
    fake = llm.FakeLLM({"analysis": [PLAN]})
    with llm.use_fake(fake):
        limit_exactly(env, ctx, monkeypatch, make)
        make()
    text = fake.calls[0]["messages"][0]["content"]
    assert PROBLEM in text and load_prompt("contract").strip() in text


# 3. cat dung thu tu, cuoi cung thu nho data summary
def test_step_cuts_in_order_then_shrinks_summary_without_leak(env, monkeypatch):
    ctx = context(env, ["Sua 1"])
    fill(env, 3, 25)
    ctx.decisions = [loop._decision_line(d) for d in env.decisions()]
    calls = []
    real = cb.build_data_summary

    def spy(report, sensitive=None, max_chars=None, **kw):
        calls.append((max_chars, sensitive))
        return real(report, sensitive=sensitive, max_chars=max_chars, **kw)

    monkeypatch.setattr(cb, "build_data_summary", spy)
    make = lambda: loop._step_prompt(ctx, Q1, env.results_by_question(), "E" * 3000)  # noqa: E731
    limit_exactly(env, ctx, monkeypatch, make)
    text = make()

    info = [e for e in env.read_log() if e.action == "prompt_fit"][-1].extra
    head = ["previous_results", "error_feedback", "confirmed_decisions", "confirmed_understanding"]
    assert info["cut"][:4] == head and info["cut"][4].startswith("data_summary:")
    assert [int(c.split(":")[1]) for c in info["cut"][4:]] == sorted((int(c.split(":")[1]) for c in info["cut"][4:]),
                                                                     reverse=True)
    assert calls and all(s == SENSITIVE for _, s in calls)  # dung lai danh sach cot che, khong cat chuoi tho
    assert len(text) < info["chars_before"]
    assert not any(c in text for c in IDS)


def test_shrunk_summary_keeps_columns_masked(env):
    ctx = context(env)
    shrink = cb.summary_shrinker(ctx.report, ctx.sensitive)
    for n in config.CONTEXT_SUMMARY_MAX_CHARS:
        text = shrink(n)
        assert not any(c in text for c in IDS)
    assert len(shrink(config.CONTEXT_SUMMARY_MAX_CHARS[-1])) < len(ctx.summary["text"])


def test_cut_step_without_change_is_not_counted():
    render = lambda **v: v["previous_results"] + v["error_feedback"]  # noqa: E731
    variables = {"previous_results": "p" * 3400, "error_feedback": ""}
    _, info = cb.fit_prompt("step", render, variables, {"previous_results": "p", "error_feedback": ""}, limit=1)
    assert info["cut"] == ["previous_results"]


def test_stops_at_first_sufficient_step():
    render = lambda **v: v["previous_results"] + v["confirmed_decisions"]  # noqa: E731
    variables = {"previous_results": "p" * 34000, "confirmed_decisions": "d"}
    text, info = cb.fit_prompt("step", render, variables, {"previous_results": "p", "confirmed_decisions": ""}, 10 ** 4)
    assert info["cut"] == ["previous_results"] and text == "pd"


def test_understand_cut_order(env):
    corrections = ["x" * 300, "y" * 300, "z" * 300]
    render = lambda **v: v["previous_restatement"] + v["user_corrections"]  # noqa: E731
    variables = {"previous_restatement": "r" * 340, "user_corrections": "\n".join(corrections)}
    _, info = cb.fit_prompt("understand", render, variables,
                            {"previous_restatement": "(none)",
                             "user_corrections": loop._corrections_text(corrections, 400)}, limit=1)
    assert info["cut"] == ["previous_restatement", "user_corrections"]


# tran cac ban rut gon
def test_corrections_cap_keeps_newest():
    text = loop._corrections_text(["cu " * 50, "moi nhat"], 40)
    assert "moi nhat" in text and "cu cu" not in text and "1 older corrections omitted" in text
    assert loop._corrections_text([], 40) == "(none)"
    assert len(loop._corrections_text(["z" * 500], 40)) <= 40


def test_understanding_caps_corrections_but_problem_json_keeps_all(env):
    long = ["c" * 900, "d" * 900, "e" * 900]
    ctx = context(env, long)
    assert long[0] not in loop._confirmed_understanding(ctx) and long[2] in loop._confirmed_understanding(ctx)
    assert env.get_problem()["user_corrections"] == long
    short = loop._confirmed_understanding(ctx, short=True)
    assert "So khach" not in short and "DIENGIAI" in short


def test_short_previous_caps_results_and_keeps_dependency(env):
    ctx = context(env)
    fill(env, config.CONTEXT_PREVIOUS_MAX_RESULTS + 4)
    by_key = env.results_by_question()
    text = loop._short_previous(ctx, by_key, keep_ids={"R1"})
    assert "R1: Cau 0" in text and "Q1 -> R1" in text          # cau hien tai phu thuoc
    assert "R2: Cau 1" not in text and "Q2 -> R2" not in text  # cu, khong lien quan
    assert f"R{config.CONTEXT_PREVIOUS_MAX_RESULTS + 4}:" in text and "3 older results omitted" in text


def test_short_previous_caps_files(env):
    ctx = context(env)
    ctx.work_dir.mkdir(parents=True)
    for i in range(config.CONTEXT_PREVIOUS_MAX_FILES + 5):
        (ctx.work_dir / f"f{i:02d}.csv").write_text("x")
    line = loop._short_previous(ctx, {}).splitlines()[-1]
    assert line.count(".csv") == config.CONTEXT_PREVIOUS_MAX_FILES and "+5 older files" in line


def test_short_decisions_caps_count_and_priority(env):
    ctx = context(env)
    accept = env.add_decision("anomaly_handling", "chấp nhận R1 dù có cờ x", source_key=f"{loop.ACCEPT_FLAGS_KEY}R1")
    env.confirm_decision(accept.id)
    for i in range(config.CONTEXT_DECISIONS_MAX):
        env.confirm_decision(env.add_decision("assumption", f"gia dinh {i}").id)
    env.confirm_decision(env.add_decision("anomaly_handling", "xu ly bat thuong").id)
    env.confirm_decision(env.add_decision("metric_definition", "dinh nghia chi so").id)
    lines = loop._short_decisions(ctx).splitlines()
    assert lines[0] == "- dinh nghia chi so" and lines[1] == "- xu ly bat thuong"
    assert not any("chấp nhận R1" in x for x in lines)
    assert lines[-1] == "(3 less important decisions omitted)"


def test_short_decisions_empty():
    class Ctx:
        class ledger:
            decisions = staticmethod(lambda: [])

    assert loop._short_decisions(Ctx) == "(none)"


# 4. khong cat noi
@pytest.mark.parametrize("n_ok, prompt, n_calls", [(0, "understand", 0), (1, "plan", 1), (2, "step", 2)])
def test_too_long_stops_code_3_without_calling_ai(env, monkeypatch, n_ok, prompt, n_calls):
    limits = iter([9000] * n_ok)
    monkeypatch.setattr(cb, "min_input_tokens", lambda role: next(limits, 100))
    code, fake = run([RESTATEMENT, PLAN, STEP])
    assert code == loop.STOPPED and len(fake.calls) == n_calls
    (item,) = env.open_items()
    assert "prompt quá dài so với model nhỏ nhất" in item.text and f"prompt {prompt}" in item.text
    assert [e for e in env.read_log() if e.action == "prompt_fit"][-1].status == "too_long"


def test_too_long_twice_does_not_duplicate_open_item(env, monkeypatch):
    monkeypatch.setattr(cb, "min_input_tokens", lambda role: 100)
    assert run([])[0] == loop.STOPPED
    assert run([])[0] == loop.STOPPED
    assert len(env.open_items()) == 1


def test_too_long_at_step_twice_does_not_duplicate_open_item(env, monkeypatch):
    limits = iter([9000, 9000])
    monkeypatch.setattr(cb, "min_input_tokens", lambda role: next(limits, 100))
    run([RESTATEMENT, PLAN])
    run([])
    assert len(env.open_items()) == 1 and env.open_items()[0].text.startswith("Q1: ")


# 5. step nhan ban dien giai
def test_step_prompt_has_confirmed_understanding_with_corrections(env):
    code, fake = run([RESTATEMENT, RESTATEMENT, PLAN, STEP], yes=False,
                     ask_user=iter_answers("n", "Chi tinh khach moi", "y"))
    assert code == loop.DONE
    text = fake.calls[-1]["messages"][0]["content"]
    assert "# Understanding confirmed by the user" in text
    assert "DIENGIAI: dem khach." in text and "Chi tinh khach moi" in text and "Co tinh khach cu" in text


def iter_answers(*items):
    it = iter(items)
    return lambda q: next(it, "y")


def test_step_understanding_is_masked(env):
    ctx = context(env, [f"Bo khach {IDS[0]}"])
    text = loop._step_prompt(ctx, Q1, {}, "")
    assert IDS[0] not in text and "[đã che]" in text


# 6. thieu max_input_tokens
def test_missing_max_input_tokens_fails_when_loading_config(tmp_path):
    with open(config.ROOT / "config" / "llm.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    del cfg["models"]["gemma_4_31b"]["max_input_tokens"]
    path = tmp_path / "llm.yaml"
    path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")
    with pytest.raises(LLMConfigError, match=r"gemma_4_31b.*max_input_tokens"):
        client._load_config(path)


def test_real_config_has_max_input_tokens():
    cfg = client._load_config(client._config_path())
    assert all("max_input_tokens" in m for m in cfg["models"].values())


# run_log
def test_every_prompt_logged_with_role_prompt_tokens_budget_cuts(env):
    assert run([RESTATEMENT, PLAN, STEP])[0] == loop.DONE
    events = [e for e in env.read_log() if e.action == "prompt_fit"]
    assert [e.extra["prompt"] for e in events] == ["understand", "plan", "step"]
    for e in events:
        assert e.extra["role"] == "analysis" and e.extra["budget"] == 8100
        assert e.extra["tokens_after"] > 0 and e.extra["cut"] == [] and e.status == "ok"
    assert events[-1].inputs == {"question": "Q1"}
