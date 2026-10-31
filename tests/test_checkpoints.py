"""Kiem tra diem dung xac nhan va decision cho xac nhan (Ngay 6 muc 2, 3). FakeLLM va R2 gia, khong goi that.
Runner chay that (code ngan) tren ban chep du lieu gia.

Chay: python -m pytest tests/test_checkpoints.py -v
"""

import json

import pytest

import llm
import main as app
from analysis import loop
from ledger import Ledger, Phase
from llm import LLMChainExhausted
from llm.fake import FromModel
from profiling import flags as fl
from profiling import quality
from runner import run_code as runner
from skills.report.schemas import SynthesisOutput
from storage import r2

TASK = "bai0a"       # du lieu sach, khong co co muc flag
FLAGGED = "baiF"     # hai bang con noi cung mot cot cha co khoa trung
INFO_ONLY = "baiI"   # chi co co info: mot so khach khong co don
CUSTOMERS = [f"C{i:03d}" for i in range(1, 9)]
FILES = {
    f"input/{INFO_ONLY}/de_bai.md": "Đếm đơn theo khách.",
    f"input/{INFO_ONLY}/CUSTOMER.csv": "customer_id,segment\n" + "".join(f"{c},A\n" for c in CUSTOMERS),
    f"input/{INFO_ONLY}/ORDERS.csv": "order_id,customer_id\n" + "".join(f"O{i:03d},C{i:03d}\n" for i in range(1, 6)),
    f"input/{TASK}/de_bai.md": "Đếm số sản phẩm, rồi tính tổng giá.",
    f"input/{TASK}/PRODUCT.csv": "product_id,price\n" + "".join(f"P{i:04d},{i * 10}\n" for i in range(1, 7)),
    f"input/{FLAGGED}/de_bai.md": "Đếm đơn và đơn trả theo khách.",
    f"input/{FLAGGED}/CUSTOMER.csv": "customer_id,segment\n" + "".join(f"{c},A\n" for c in CUSTOMERS) + "C008,B\n",
    f"input/{FLAGGED}/ORDERS.csv": "order_id,customer_id\n" + "".join(f"O{i:03d},C{i:03d}\n" for i in range(1, 6)),
    f"input/{FLAGGED}/RETURNS.csv": "return_id,customer_id\n" + "".join(f"T{i:03d},C{i:03d}\n" for i in range(1, 4)),
    f"input/{FLAGGED}/data_dictionary.md": ("## CUSTOMER\n| Cột | Kiểu | Định nghĩa |\n|---|---|---|\n"
                                            "| customer_id | string (PK) | |\n| segment | string | |\n"
                                            "## ORDERS\n| Cột | Kiểu | Định nghĩa |\n|---|---|---|\n"
                                            "| order_id | string (PK) | |\n| customer_id | string (FK) | |\n"
                                            "## RETURNS\n| Cột | Kiểu | Định nghĩa |\n|---|---|---|\n"
                                            "| return_id | string (PK) | |\n| customer_id | string (FK) | |\n"),
}
DUP_ORDERS = "join_right_duplicate_keys|CUSTOMER|customer_id|ORDERS.customer_id"
DUP_RETURNS = "join_right_duplicate_keys|CUSTOMER|customer_id|RETURNS.customer_id"

RESTATEMENT = {"restatement": "Đếm rồi cộng.", "goals": ["Số sản phẩm", "Tổng giá"]}
M1, M2 = "Số sản phẩm = số dòng", "Tổng giá = cộng cột price"
ANOMALY, SCOPE, RANGE_TEXT = "Bỏ dòng thiếu giá", "Tính mọi sản phẩm", "Khoảng hợp lệ của PRODUCT.price: >= 0 (không âm)"
PLAN = {"understanding": "Đếm rồi cộng.", "requirements": ["Số sản phẩm", "Tổng giá"],
        "sub_questions": [{"key": "Q1", "question": "Có bao nhiêu sản phẩm?", "covers": [1]},
                          {"key": "Q2", "question": "Tổng giá là bao nhiêu?", "depends_on": ["Q1"], "covers": [2]}],
        "assumptions": [{"kind": "metric_definition", "text": M1}, {"kind": "metric_definition", "text": M2},
                        {"kind": "anomaly_handling", "text": ANOMALY}, {"kind": "assumption", "text": SCOPE}],
        "proposed_ranges": [{"column": "PRODUCT.price", "min": 0, "reason": "không âm"}]}
SIMPLE_PLAN = {**PLAN, "assumptions": [], "proposed_ranges": []}


def step(name, passed=True, **kw):
    code = ("import json, os\nimport pandas as pd\n"
            "inp, out = os.environ['AGENT_INPUT_DIR'], os.environ['AGENT_OUTPUT_DIR']\n"
            "n = len(os.listdir(inp))\n"
            f"pd.DataFrame({{'n': [n]}}).to_csv(os.path.join(out, '{name}.csv'), index=False)\n"
            "meta = {'result_summary': {'n': n}, 'checks': ["
            f"{{'type': 'custom', 'name': 'có dòng', 'passed': {passed}, 'reason': 'kiểm tra'}}]}}\n"
            "json.dump(meta, open(os.environ['AGENT_META_PATH'], 'w', encoding='utf-8'))\n")
    return {"explanation": "giải thích", "method": "pandas", "can_be_wrong_if": ["Thiếu dòng"], "code": code,
            "output_files": [f"{name}.csv"], **kw}


STEP1, STEP2 = step("q1"), step("q2")


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
    monkeypatch.delenv("CAUTION_LEVEL", raising=False)
    return Ledger(tmp_path / "output" / TASK)


def run(script, task=TASK, **kw):
    fake = llm.FakeLLM({"analysis": list(script)}, by_schema={SynthesisOutput: loop.demo_synthesis})
    kw.setdefault("yes", True)
    with llm.use_fake(fake):
        code = app.run_task(task, **kw)
    fake.all_calls, fake.calls = fake.calls, [c for c in fake.calls if c["role"] == "analysis"]  # tong hop xet rieng
    return code, fake


class Answers:
    """Tra loi theo danh sach (rule (doan cau hoi, tra loi) dung truoc), het thi "y"; ghi lai moi cau hoi."""

    def __init__(self, *items, rules=()):
        self.items, self.rules, self.asked = list(items), list(rules), []

    def __call__(self, question):
        self.asked.append(question)
        for fragment, answer in self.rules:
            if fragment in question:
                return answer
        return self.items.pop(0) if self.items else "y"

    def count(self, fragment):
        return sum(fragment in q for q in self.asked)


EACH = "y = đồng ý"
LIST = "y = chấp nhận tất cả"


def plan_decisions(led):
    """Decision cua pha plan va loop: bo decision cua diem dung ket luan."""
    return [d for d in led.decisions() if d.source_key != app.CONCLUSION_KEY]


def active_plan_decisions(led):
    return [d for d in led.active_decisions() if d.source_key != app.CONCLUSION_KEY]


def statuses(led):
    return {d.text: d.status.value for d in plan_decisions(led)}


def open_texts(led):
    return [o.text for o in led.open_items() if o.status.value == "open"]


# 1. dung giua chung
def test_stop_while_asking_metric_keeps_pending_and_resumes_without_duplicates(env):
    code, _ = run([RESTATEMENT, PLAN], yes=False, ask_user=Answers("y", "y", "n"))  # de, M1, dung o M2
    assert code == loop.STOPPED and env.get_state().phase == Phase.PLAN
    assert statuses(env) == {M1: "confirmed", M2: "pending", ANOMALY: "pending", SCOPE: "pending",
                             RANGE_TEXT: "pending"}
    assert {d["status"] for d in json.loads((env.root / "decisions.json").read_text(encoding="utf-8"))} \
        == {"confirmed", "pending"}
    assert open_texts(env) == [loop.PLAN_OPEN_ITEM]

    ans = Answers()
    code, fake = run([STEP1, STEP2], yes=False, ask_user=ans)
    assert code == loop.DONE and len(fake.calls) == 2  # khong goi lai understand, plan
    assert ans.count(EACH) == 2  # M2, ANOMALY; M1 khong hoi lai
    assert len(plan_decisions(env)) == 5 and set(statuses(env).values()) == {"confirmed"}
    assert open_texts(env) == []


def test_all_assumptions_written_pending_before_first_question(env):
    seen = []

    def ask(q):
        if EACH in q:
            seen.append([d.status.value for d in plan_decisions(env)])
            return "n"
        return "y"

    run([RESTATEMENT, PLAN], yes=False, ask_user=ask)
    assert seen == [["pending"] * 5]


def test_question_order_metric_anomaly_list_then_subquestions(env, capsys):
    ans = Answers()
    run([RESTATEMENT, PLAN, STEP1, STEP2], yes=False, ask_user=ans)
    out = capsys.readouterr().out
    assert out.index("Định nghĩa chỉ số:") < out.index("Xử lý dữ liệu bất thường:") < out.index("Giả định khác")
    kinds = [q for q in ans.asked if EACH in q or LIST in q or "câu hỏi nhỏ" in q]
    assert [("each" if EACH in q else "list" if LIST in q else "sub") for q in kinds] \
        == ["each", "each", "each", "list", "sub"]


# 2. con pending bat buoc
def test_pending_required_blocks_loop_phase(env, monkeypatch):
    run([RESTATEMENT, PLAN, STEP1, LLMChainExhausted("hết")])
    env.add_decision("metric_definition", "Decision còn chờ")
    monkeypatch.setattr(runner, "run_code", lambda *a, **k: pytest.fail("không được vào pha loop"))

    code, fake = run([], yes=False, ask_user=Answers(rules=[(EACH, "n")]))
    assert code == loop.STOPPED and fake.calls == [] and env.get_state().phase == Phase.LOOP


def test_gate_stops_even_if_nothing_was_asked(env, monkeypatch):
    run([RESTATEMENT, PLAN, STEP1, LLMChainExhausted("hết")])
    env.add_decision("metric_definition", "Decision còn chờ")
    monkeypatch.setattr(loop, "review_decisions", lambda *a, **k: None)
    code, fake = run([])
    assert code == loop.STOPPED and fake.calls == [] and open_texts(env)[-1] == loop.PENDING_OPEN_ITEM


def test_optional_pending_does_not_block(env, monkeypatch):
    run([RESTATEMENT, PLAN, STEP1, LLMChainExhausted("hết")])
    env.add_decision("metric_definition", "Không bắt buộc", required=False)
    monkeypatch.setattr(loop, "review_decisions", lambda *a, **k: None)
    assert run([STEP2])[0] == loop.DONE


# 3. chon s
def test_choose_s_saves_user_text_confirmed(env):
    code, fake = run([RESTATEMENT, PLAN, STEP1, STEP2], yes=False,
                     ask_user=Answers("y", "s", "Số sản phẩm = số mã khác nhau"))
    assert code == loop.DONE
    d = plan_decisions(env)[0]
    assert (d.text, d.status.value) == ("Số sản phẩm = số mã khác nhau", "confirmed")
    assert "Số sản phẩm = số mã khác nhau" in fake.calls[2]["messages"][0]["content"]
    assert M1 not in statuses(env)


def test_user_edit_is_not_recreated_on_rerun(env):
    run([RESTATEMENT, PLAN], yes=False, ask_user=Answers("y", "s", "Nội dung mới", "n"))
    assert run([STEP1, STEP2])[0] == loop.DONE
    assert M1 not in statuses(env) and len(plan_decisions(env)) == 5


def test_empty_s_asks_again(env):
    code, _ = run([RESTATEMENT, PLAN, STEP1, STEP2], yes=False, ask_user=Answers("y", "s", "  ", "y"))
    assert code == loop.DONE and statuses(env)[M1] == "confirmed"


def test_list_edit_by_number_then_accept_all(env):
    ans = Answers("y", "y", "y", "y", "2", "Khoảng mới")  # de, M1, M2, ANOMALY, sua muc 2
    code, _ = run([RESTATEMENT, PLAN, STEP1, STEP2], yes=False, ask_user=ans)
    assert code == loop.DONE and statuses(env)["Khoảng mới"] == "confirmed"
    assert RANGE_TEXT not in statuses(env) and statuses(env)[SCOPE] == "confirmed"


def test_list_n_stops_and_keeps_pending(env):
    code, _ = run([RESTATEMENT, PLAN], yes=False, ask_user=Answers(rules=[(LIST, "n")]))
    assert code == loop.STOPPED and statuses(env)[SCOPE] == "pending" and statuses(env)[M1] == "confirmed"


def test_invalid_answer_asks_again(env):
    ans = Answers("y", "x", "y")
    assert run([RESTATEMENT, PLAN, STEP1, STEP2], yes=False, ask_user=ans)[0] == loop.DONE


def test_new_assumption_goes_through_pending(env):
    extra = step("q1", new_assumptions=[{"kind": "metric_definition", "text": "Giả định mới"}])
    code, _ = run([RESTATEMENT, SIMPLE_PLAN, extra, STEP2], yes=False,
                  ask_user=Answers("y", "y", "s", "Giả định đã sửa"))  # de, cau hoi nho, chon s
    assert code == loop.DONE and statuses(env) == {"Giả định đã sửa": "confirmed"}


def test_new_assumption_n_stops_before_recording(env):
    extra = step("q1", new_assumptions=[{"kind": "metric_definition", "text": "Giả định mới"}])
    code, _ = run([RESTATEMENT, SIMPLE_PLAN, extra], yes=False, ask_user=Answers(rules=[(EACH, "n")]))
    assert code == loop.STOPPED and env.results() == [] and statuses(env) == {"Giả định mới": "pending"}


# 4. co chat luong
def flagged_ledger(env):
    return Ledger(env.root.parent / FLAGGED)


def test_two_children_of_same_parent_get_two_source_keys(env):
    report = quality.profile_problem(FLAGGED)
    keys = [f["source_key"] for f in fl.all_flags(report)]
    assert DUP_ORDERS in keys and DUP_RETURNS in keys
    assert len(keys) == len(set(keys))


def test_relationship_rejected_keys_are_unique():
    def rejected(parent):
        return {"child": "C", "child_col": "x", "parent": parent, "parent_col": "x", "status": "rejected",
                "result": {"overlap": 0.0, "min_overlap": 0.1}}

    report = {"relationship_checks": [rejected("P1"), rejected("P2")]}
    a, b = fl._relationship_flags(report)
    assert (a["code"], a["table"], a["column"]) == (b["code"], b["table"], b["column"])
    assert a["source_key"] != b["source_key"]


def test_source_key_does_not_depend_on_counts():
    a = fl._flag("whitespace", fl.FLAG, "T", "c", "3 giá trị", 3)
    b = fl._flag("whitespace", fl.FLAG, "T", "c", "9 giá trị", 9)
    assert a["source_key"] == b["source_key"] == "whitespace|T|c|"


def test_flag_decisions_pending_required_and_not_duplicated_after_reprofiling(env):
    code, _ = run([RESTATEMENT, SIMPLE_PLAN], task=FLAGGED, yes=False, ask_user=Answers(rules=[(EACH, "n")]))
    assert code == loop.STOPPED
    led = flagged_ledger(env)
    first = plan_decisions(led)
    by_key = {d.source_key: d for d in first}
    assert DUP_ORDERS in by_key and DUP_RETURNS in by_key
    d = by_key[DUP_ORDERS]
    assert (d.kind.value, d.status.value, d.required) == ("anomaly_handling", "pending", True)

    quality.profile_problem(FLAGGED)
    quality.profile_problem(FLAGGED)
    code, _ = run([], task=FLAGGED, yes=False, ask_user=Answers(rules=[(EACH, "n")]))
    assert code == loop.STOPPED and plan_decisions(led) == first


def test_flag_decision_asked_again_after_restart_without_active_duplicate(env):
    assert run([RESTATEMENT, SIMPLE_PLAN, STEP1, STEP2], task=FLAGGED)[0] == loop.DONE
    led = flagged_ledger(env)
    n = len(plan_decisions(led))
    quality.profile_problem(FLAGGED)
    ans = Answers()
    assert run([RESTATEMENT, SIMPLE_PLAN, STEP1, STEP2], task=FLAGGED, yes=False, ask_user=ans,
               restart=True)[0] == loop.DONE
    assert ans.count(EACH) == n and len(active_plan_decisions(led)) == n and len(plan_decisions(led)) == 2 * n
    assert sorted(d.source_key for d in plan_decisions(led)[:n]) == sorted(d.source_key for d in active_plan_decisions(led))


# --restart: decision cua lan truoc khong dung lai
NEW_M2 = "Tổng giá tính sau thuế"
NEW_PLAN = {**PLAN, "assumptions": [{"kind": "metric_definition", "text": M1},
                                    {"kind": "metric_definition", "text": NEW_M2},
                                    {"kind": "anomaly_handling", "text": ANOMALY}], "proposed_ranges": []}


def test_restart_asks_metric_and_anomaly_again(env):
    assert run([RESTATEMENT, PLAN, STEP1, STEP2])[0] == loop.DONE
    ans = Answers()
    assert run([RESTATEMENT, NEW_PLAN, STEP1, STEP2], yes=False, ask_user=ans, restart=True)[0] == loop.DONE
    assert ans.count(EACH) == 3  # M1, ANOMALY giong het lan truoc van hoi lai
    active = active_plan_decisions(env)
    assert {d.text for d in active} == {M1, NEW_M2, ANOMALY} and {d.status.value for d in active} == {"confirmed"}


def test_restart_keeps_old_decisions_as_superseded_in_file(env):
    run([RESTATEMENT, PLAN, STEP1, STEP2])
    old = {d.id: d for d in plan_decisions(env)}
    run([RESTATEMENT, NEW_PLAN, STEP1, STEP2], restart=True)
    raw = {d["id"]: d for d in json.loads((env.root / "decisions.json").read_text(encoding="utf-8"))}
    assert all(raw[i]["status"] == "superseded" and raw[i]["text"] == d.text and raw[i]["confirmed_at"] == d.confirmed_at
               for i, d in old.items())
    assert len(raw) == len(old) + 5  # 3 decision moi va 2 decision chap nhan ket luan


def test_superseded_decision_not_in_step_prompt_or_render(env):
    run([RESTATEMENT, PLAN, STEP1, STEP2])
    code, fake = run([RESTATEMENT, NEW_PLAN, STEP1, STEP2], restart=True)
    steps = [c["messages"][0]["content"] for c in fake.calls if c["json_schema"].__name__ == "AnalysisStep"]
    assert code == loop.DONE and len(steps) == 2
    assert all(NEW_M2 in p and M2 not in p and SCOPE not in p for p in steps)
    text = env.render_markdown()
    assert NEW_M2 in text and M2 not in text
    ctx = loop.load_context(TASK)
    assert not any(M2 in line for line in ctx.decisions) and M2 not in loop._short_decisions(ctx)


def test_restart_supersedes_pending_and_closes_confirm_open_items(env):
    assert run([RESTATEMENT, PLAN], yes=False, ask_user=Answers("y", "n"))[0] == loop.STOPPED
    assert open_texts(env) == [loop.PLAN_OPEN_ITEM] and env.pending_required()
    env.add_open_item_once(loop.PENDING_OPEN_ITEM)
    env.add_open_item("Việc khác")
    code, _ = run([LLMChainExhausted("hết")], restart=True)
    assert code == loop.EXHAUSTED and env.pending_required() == []
    assert set(open_texts(env)) == {"Việc khác", loop.CHAIN_OPEN_ITEM}


def test_report_saved_before_source_key_gets_keys_on_load(env):
    quality.profile_problem(FLAGGED)
    led = flagged_ledger(env)
    profile = led.get_data_profile()
    report = profile[quality.REPORT_KEY]
    for f in fl.all_flags(report):
        del f["source_key"]
    led.set_data_profile(profile)
    ctx = loop.load_context(FLAGGED)
    assert all(f.get("source_key") for f in fl.all_flags(ctx.report))
    assert f"[key: {DUP_ORDERS}]" in ctx.summary["text"]


def test_plan_feedback_asks_flag_again_when_proposal_changes(env):
    def plan_with(text):
        return {**SIMPLE_PLAN, "assumptions": [{"kind": "anomaly_handling", "text": text, "flag_key": DUP_ORDERS}]}

    ans = Answers(rules=[("Gõ góp ý", "Đổi cách xử lý")])
    replies = iter(["s", "y"])
    ask = lambda q: next(replies) if "danh sách câu hỏi nhỏ" in q else ans(q)  # noqa: E731
    code, _ = run([RESTATEMENT, plan_with("Lấy dòng đầu"), plan_with("Lấy dòng cuối"), STEP1, STEP2], task=FLAGGED,
                  yes=False, ask_user=ask)
    assert code == loop.DONE
    versions = [d for d in plan_decisions(flagged_ledger(env)) if d.source_key == DUP_ORDERS]
    assert [d.status.value for d in versions] == ["superseded", "confirmed"]
    assert "Lấy dòng cuối" in versions[1].text


def test_old_decisions_without_source_key_are_readable(env):
    (env.root / "decisions.json").write_text(json.dumps([{"id": "D1", "kind": "assumption", "text": "cũ"}]),
                                             encoding="utf-8")
    assert plan_decisions(env)[0].source_key is None


def test_ai_proposal_shown_next_to_flag_not_separate_decision(env, capsys):
    plan = {**SIMPLE_PLAN, "assumptions": [{"kind": "anomaly_handling", "text": "Lấy dòng đầu mỗi khóa",
                                            "flag_key": DUP_ORDERS}]}
    code, _ = run([RESTATEMENT, plan, STEP1, STEP2], task=FLAGGED, yes=False, ask_user=Answers())
    assert code == loop.DONE
    d = next(x for x in plan_decisions(flagged_ledger(env)) if x.source_key == DUP_ORDERS)
    assert "khóa trùng" in d.text and "→ Xử lý: Lấy dòng đầu mỗi khóa" in d.text
    assert d.status.value == "confirmed" and "Lấy dòng đầu mỗi khóa" in capsys.readouterr().out
    assert not any(x.text == "Lấy dòng đầu mỗi khóa" for x in plan_decisions(flagged_ledger(env)))


def test_user_can_replace_ai_proposal_with_s(env):
    plan = {**SIMPLE_PLAN, "assumptions": [{"kind": "anomaly_handling", "text": "Lấy dòng đầu", "flag_key": DUP_ORDERS}]}
    led = flagged_ledger(env)

    def ask(q):  # decision dang hoi la decision pending dau tien; chi chon s o dung co nay
        if "Nội dung mới" in q:
            return "Bỏ mọi khóa trùng"
        current = next((d for d in plan_decisions(led) if d.status.value == "pending"), None)
        return "s" if EACH in q and current.source_key == DUP_ORDERS else "y"

    code, _ = run([RESTATEMENT, plan, STEP1, STEP2], task=FLAGGED, yes=False, ask_user=ask)
    assert code == loop.DONE
    d = next(x for x in plan_decisions(led) if x.source_key == DUP_ORDERS)
    assert (d.text, d.status.value) == ("Bỏ mọi khóa trùng", "confirmed")


def test_unknown_flag_key_is_plain_assumption(env):
    plan = {**SIMPLE_PLAN, "assumptions": [{"kind": "anomaly_handling", "text": "Xử lý X", "flag_key": "khong|co"}]}
    assert run([RESTATEMENT, plan, STEP1, STEP2])[0] == loop.DONE
    assert statuses(env) == {"Xử lý X": "confirmed"}


# 5. co info
def test_info_flags_create_no_decision_but_are_printed(env, capsys):
    run([RESTATEMENT, SIMPLE_PLAN, STEP1, STEP2], task=FLAGGED)
    out = capsys.readouterr().out
    led = flagged_ledger(env)
    info = [f for f in fl.all_flags(led.get_data_profile()[quality.REPORT_KEY]) if f["severity"] == fl.INFO]
    assert info and "Lưu ý về dữ liệu" in out and all(f["message"] in out for f in info)
    keys = {d.source_key for d in plan_decisions(led)}
    assert not keys & {f["source_key"] for f in info}


def test_summary_shows_key_only_for_flag_severity(env):
    report = quality.profile_problem(FLAGGED)
    text = loop.build_data_summary(report)["text"]
    assert f"[key: {DUP_ORDERS}]" in text
    for f in fl.all_flags(report):
        if f["severity"] == fl.INFO:
            assert f"[key: {f['source_key']}]" not in text


# diem dung xu ly bat thuong luon dung
def info_ledger(env):
    return Ledger(env.root.parent / INFO_ONLY)


def test_info_only_asks_one_question_and_rerun_does_not_duplicate(env, capsys):
    ans = Answers(rules=[(EACH, "n")])
    code, _ = run([RESTATEMENT, SIMPLE_PLAN], task=INFO_ONLY, yes=False, ask_user=ans)
    out = capsys.readouterr().out
    assert code == loop.STOPPED and ans.count(EACH) == 1
    assert out.index("Lưu ý về dữ liệu") < out.index(loop.KEEP_DATA_QUESTION)
    led = info_ledger(env)
    (d,) = plan_decisions(led)
    assert (d.kind.value, d.status.value, d.required, d.source_key, d.text) \
        == ("anomaly_handling", "pending", True, loop.KEEP_DATA_KEY, loop.KEEP_DATA_TEXT)
    assert led.get_state().phase == Phase.PLAN

    ans = Answers()
    code, _ = run([STEP1, STEP2], task=INFO_ONLY, yes=False, ask_user=ans)
    assert code == loop.DONE and ans.count(EACH) == 1
    (d,) = plan_decisions(led)
    assert d.status.value == "confirmed"

    ans = Answers()
    code, _ = run([RESTATEMENT, SIMPLE_PLAN, STEP1, STEP2], task=INFO_ONLY, yes=False, ask_user=ans, restart=True)
    assert code == loop.DONE and ans.count(EACH) == 1 and len(plan_decisions(led)) == 2
    assert len(active_plan_decisions(led)) == 1


def test_info_only_s_saves_user_text_and_keeps_key(env):
    code, _ = run([RESTATEMENT, SIMPLE_PLAN, STEP1, STEP2], task=INFO_ONLY, yes=False,
                  ask_user=Answers("y", "s", "Bỏ khách không có đơn"))
    assert code == loop.DONE
    (d,) = plan_decisions(info_ledger(env))
    assert (d.text, d.status.value, d.source_key) == ("Bỏ khách không có đơn", "confirmed", loop.KEEP_DATA_KEY)


def test_info_only_decision_reaches_ai_as_statement(env):
    _, fake = run([RESTATEMENT, SIMPLE_PLAN, STEP1, STEP2], task=INFO_ONLY)
    step_prompt = fake.calls[2]["messages"][0]["content"]
    assert loop.KEEP_DATA_TEXT in step_prompt and loop.KEEP_DATA_QUESTION not in step_prompt


def test_no_flags_prints_nothing_abnormal_and_does_not_ask(env, capsys):
    ans = Answers()
    code, _ = run([RESTATEMENT, SIMPLE_PLAN, STEP1, STEP2], yes=False, ask_user=ans)
    assert code == loop.DONE and "Không có bất thường" in capsys.readouterr().out
    assert ans.count(EACH) == 0 and plan_decisions(env) == []


def test_info_with_ai_anomaly_decision_does_not_add_keep_question(env):
    plan = {**SIMPLE_PLAN, "assumptions": [{"kind": "anomaly_handling", "text": "Bỏ khách không có đơn"}]}
    ans = Answers()
    assert run([RESTATEMENT, plan, STEP1, STEP2], task=INFO_ONLY, yes=False, ask_user=ans)[0] == loop.DONE
    assert ans.count(EACH) == 1 and [d.text for d in plan_decisions(info_ledger(env))] == ["Bỏ khách không có đơn"]


def test_flag_with_info_does_not_add_keep_question(env):
    assert run([RESTATEMENT, SIMPLE_PLAN, STEP1, STEP2], task=FLAGGED)[0] == loop.DONE
    assert loop.KEEP_DATA_KEY not in {d.source_key for d in plan_decisions(flagged_ledger(env))}


def test_accept_flags_decision_does_not_count_as_answered(env):
    info_ledger(env).add_decision("anomaly_handling", "chấp nhận R1 dù có cờ x", source_key="accept_flags|R1")
    assert run([RESTATEMENT, SIMPLE_PLAN, STEP1, STEP2], task=INFO_ONLY)[0] == loop.DONE
    assert loop.KEEP_DATA_KEY in {d.source_key for d in plan_decisions(info_ledger(env))}


def test_flag_without_ai_proposal_uses_default_text(env):
    run([RESTATEMENT, SIMPLE_PLAN, STEP1, STEP2], task=FLAGGED)
    d = next(x for x in plan_decisions(flagged_ledger(env)) if x.source_key == DUP_ORDERS)
    assert d.text.endswith("→ Xử lý: (chưa có đề xuất của AI) giữ nguyên, không xử lý riêng")


# 6. model_switched
def from_a(item):
    return FromModel("model_a", item)


def test_model_switch_adds_flag_check_and_stops_to_ask(env):
    ans = Answers(rules=[("tiếp tục", "n")])
    code, _ = run([from_a(RESTATEMENT), from_a(SIMPLE_PLAN), from_a(STEP1), FromModel("model_b", STEP2)], yes=False,
                  ask_user=ans)
    assert code == loop.STOPPED and ans.count("tiếp tục") == 1
    r1, r2_ = env.results()
    assert [c.name for c in r1.checks] == ["có dòng"]
    check = r2_.checks[-1]
    assert (check.name, check.status.value, check.reason) == ("model_switched", "flag", "model_a → model_b")
    assert env.get_state().model == "model_b"


def test_cache_hit_is_not_a_model_switch(env):
    cached = FromModel("model_b", STEP2, cached=True)
    code, _ = run([from_a(RESTATEMENT), from_a(SIMPLE_PLAN), from_a(STEP1), cached])
    assert code == loop.DONE
    assert all(c.name != "model_switched" for r in env.results() for c in r.checks)
    assert env.get_state().model == "model_a"


def test_model_switch_detected_in_new_process(env):
    code, _ = run([from_a(RESTATEMENT), from_a(SIMPLE_PLAN), from_a(STEP1), LLMChainExhausted("hết")])
    assert code == loop.EXHAUSTED and env.get_state().model == "model_a"
    code, _ = run([FromModel("model_b", STEP2)])  # FakeLLM moi: khong nho gi tu lan truoc, chi con state.json
    assert code == loop.DONE
    assert env.get_result("R2").checks[-1].reason == "model_a → model_b"


def test_switch_then_stop_before_result_is_still_detected_on_resume(env):
    bad = FromModel("model_b", {**step("q2"), "code": "raise SystemExit(4)\n"})
    code, _ = run([from_a(RESTATEMENT), from_a(SIMPLE_PLAN), from_a(STEP1), bad, LLMChainExhausted("hết")])
    assert code == loop.EXHAUSTED and env.get_state().model == "model_a"  # chua co ket qua nao mang co
    assert run([FromModel("model_b", STEP2)])[0] == loop.DONE
    assert env.get_result("R2").checks[-1].name == "model_switched"


def test_switch_during_plan_flags_first_result(env):
    code, _ = run([from_a(RESTATEMENT), FromModel("model_b", SIMPLE_PLAN), FromModel("model_b", STEP1),
                   FromModel("model_b", STEP2)])
    assert code == loop.DONE
    assert env.get_result("R1").checks[-1].reason == "model_a → model_b"
    assert all(c.name != "model_switched" for c in env.get_result("R2").checks)


def test_no_switch_no_flag(env):
    assert run([RESTATEMENT, SIMPLE_PLAN, STEP1, STEP2])[0] == loop.DONE
    assert all(c.status.value == "pass" for r in env.results() for c in r.checks)


def test_restart_forgets_model(env):
    run([from_a(RESTATEMENT), from_a(SIMPLE_PLAN), from_a(STEP1), from_a(STEP2)])
    code, _ = run([FromModel("model_b", RESTATEMENT), LLMChainExhausted("hết")], restart=True)
    assert code == loop.EXHAUSTED and env.get_state().model == "model_b"


# 7. "Vẫn tiếp tục?"
def test_continue_with_flag_records_confirmed_decision(env):
    code, _ = run([RESTATEMENT, SIMPLE_PLAN, step("q1", passed=False), STEP2], yes=False, ask_user=Answers())
    assert code == loop.DONE
    d = next(x for x in plan_decisions(env) if "dù có cờ" in x.text)
    assert (d.text, d.status.value) == ("chấp nhận R1 dù có cờ có dòng", "confirmed")


def test_yes_flag_also_records_decision(env):
    assert run([RESTATEMENT, SIMPLE_PLAN, step("q1", passed=False), STEP2])[0] == loop.DONE
    assert statuses(env) == {"chấp nhận R1 dù có cờ có dòng": "confirmed"}


def test_n_at_flag_stops_and_asks_again_on_rerun(env):
    code, _ = run([RESTATEMENT, SIMPLE_PLAN, step("q1", passed=False)], yes=False,
                  ask_user=Answers(rules=[("tiếp tục", "n")]))
    assert code == loop.STOPPED and statuses(env) == {"chấp nhận R1 dù có cờ có dòng": "pending"}
    assert env.results_by_question() == {"Q1": "R1"}

    ans = Answers()
    code, fake = run([STEP2], yes=False, ask_user=ans)
    assert code == loop.DONE and ans.count(EACH) == 1 and len(fake.calls) == 1
    assert statuses(env) == {"chấp nhận R1 dù có cờ có dòng": "confirmed"}
    assert open_texts(env) == []


# 8. open_item tu dong
def test_plan_open_item_closes_when_plan_accepted(env):
    code, _ = run([RESTATEMENT, PLAN], yes=False, ask_user=Answers(rules=[("câu hỏi nhỏ", "n")]))
    assert code == loop.STOPPED and open_texts(env) == [loop.PLAN_OPEN_ITEM]
    assert run([STEP1, STEP2])[0] == loop.DONE
    assert [o.status.value for o in env.open_items()] == ["closed"]


def test_old_record_open_item_text_also_closes():
    assert loop.PLAN_OPEN_ITEM == ("Kế hoạch phân tích chưa được chấp nhận; chạy lại để xem lại, "
                                   "hoặc --restart để làm lại từ đầu.")  # dung chu trong ho so bai0a (O1)


# G, H
def test_caution_level_not_standard_warns(env, monkeypatch, capsys):
    monkeypatch.setenv("CAUTION_LEVEL", "cao")
    run([RESTATEMENT, SIMPLE_PLAN, STEP1, STEP2])
    assert "CAUTION_LEVEL=cao chưa hỗ trợ, dùng mức chuẩn" in capsys.readouterr().out


def test_caution_level_standard_is_silent(env, monkeypatch, capsys):
    monkeypatch.setenv("CAUTION_LEVEL", "chuan")
    run([RESTATEMENT, SIMPLE_PLAN, STEP1, STEP2])
    assert "CAUTION_LEVEL" not in capsys.readouterr().out


def test_yes_prints_warning_only_with_yes(env, capsys):
    run([RESTATEMENT, SIMPLE_PLAN, STEP1, STEP2])
    assert "tự xác nhận mọi decision" in capsys.readouterr().out
    Ledger(env.root).set_state(Phase.DONE)
    run([], yes=False, ask_user=Answers())
    assert "tự xác nhận mọi decision" not in capsys.readouterr().out
