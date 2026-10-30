"""Kiem tra dong bo ho so len R2 (storage/sync.py) va cac diem goi trong main.py. R2 gia, khong goi mang.

Chay: python -m pytest tests/test_sync.py -v
"""

import botocore.exceptions
import pytest

import llm
import main as app
from analysis import loop
from ledger import Ledger, Phase
from llm import LLMChainExhausted
from profiling import quality
from runner import run_code as runner
from storage import r2, sync

TASK = "bai0a"
SETTINGS = {"R2_BUCKET": "bucket", "R2_INPUT_PREFIX": "input/", "R2_OUTPUT_PREFIX": "output/"}
FILES = {f"input/{TASK}/de_bai.md": "Đếm sản phẩm.",
         f"input/{TASK}/PRODUCT.csv": "product_id,price\n" + "".join(f"P{i:04d},{i}\n" for i in range(1, 7))}
RESTATEMENT = {"restatement": "Đếm.", "goals": ["Số sản phẩm"]}
PLAN = {"understanding": "Đếm.", "requirements": ["Số sản phẩm"], "assumptions": [],
        "sub_questions": [{"key": "Q1", "question": "Bao nhiêu sản phẩm?", "covers": [1]}]}
STEP = {"explanation": "x", "method": "pandas", "can_be_wrong_if": ["Thiếu dòng"], "output_files": ["q1.csv"],
        "code": ("import json, os\nimport pandas as pd\n"
                 "pd.DataFrame({'n': [1]}).to_csv(os.path.join(os.environ['AGENT_OUTPUT_DIR'], 'q1.csv'), index=False)\n"
                 "meta = {'result_summary': {'n': 1}, 'checks': ["
                 "{'type': 'custom', 'name': 'x', 'passed': True, 'reason': 'ok'}]}\n"
                 "json.dump(meta, open(os.environ['AGENT_META_PATH'], 'w', encoding='utf-8'))\n")}


class FakeClient:
    """Client ghi gia: ghi lai key; fail: {duoi key: exception}."""

    def __init__(self):
        self.keys, self.fail = [], {}

    def upload_file(self, path, bucket, key):
        for suffix, exc in self.fail.items():
            if key.endswith(suffix):
                raise exc
        assert bucket == "bucket"
        self.keys.append(key)


@pytest.fixture
def env(tmp_path, monkeypatch):
    client = FakeClient()
    cache = tmp_path / "cache"

    def download_with_cache(key, cache_dir=None, grader=False):
        local = cache / key
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(FILES[key].encode("utf-8"))
        return local

    monkeypatch.setattr(r2, "CACHE_DIR", cache)
    monkeypatch.setattr(r2, "get_r2_settings", lambda: SETTINGS)
    monkeypatch.setattr(r2, "_write_client", lambda: client)
    monkeypatch.setattr(r2, "_read_client", lambda: pytest.fail("không được dùng token đọc để ghi"))
    monkeypatch.setattr(r2, "list_objects", lambda prefix, grader=False: [{"key": k, "etag": "e"} for k in FILES
                                                                          if k.startswith(prefix)])
    monkeypatch.setattr(r2, "download_with_cache", download_with_cache)
    monkeypatch.setattr(r2, "get_etag", lambda key, cache_dir=None: None)
    monkeypatch.setattr(runner, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr(quality, "OUTPUT_DIR", tmp_path / "output")
    return tmp_path, client


def record(env, files=None):
    tmp, _ = env
    root = tmp / "output" / TASK
    root.mkdir(parents=True, exist_ok=True)
    for rel, text in (files or {"results.json": "[]", "state.json": "{}", "work/a.csv": "x", "code/R1.py": "1"}).items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return root


def run(script, **kw):
    fake = llm.FakeLLM({"analysis": list(script)})
    kw.setdefault("yes", True)
    with llm.use_fake(fake):
        return app.run_cli(TASK, **kw), fake


@pytest.fixture
def calls(env, monkeypatch):
    """Thay sync_task, ghi lai moi lan goi; ho so luc goi van con nguyen."""
    seen = []

    def fake_sync(task, dry_run=False):
        seen.append(task)
        return sync.SyncResult()

    monkeypatch.setattr(sync, "sync_task", fake_sync)
    return seen


# 1. lan dau tai het, lan hai 0 file
def test_first_sync_uploads_all_second_uploads_nothing(env):
    record(env)
    r = sync.sync_task(TASK)
    assert sorted(r.uploaded) == ["code/R1.py", "results.json", "state.json", "work/a.csv"] and r.skipped == 0
    assert sorted(env[1].keys) == [f"output/{TASK}/{x}" for x in sorted(r.uploaded)]
    r = sync.sync_task(TASK)
    assert (r.uploaded, r.skipped, r.failed) == ([], 4, [])
    assert len(env[1].keys) == 4


def test_whole_record_goes_up_including_superseded(env):
    record(env, {"work/_superseded/20260101/old.csv": "x", "state.json": "{}"})
    assert "work/_superseded/20260101/old.csv" in sync.sync_task(TASK).uploaded


def test_ledger_temp_files_are_skipped(env):
    record(env, {"results.json": "[]", ".results.json.abc.tmp": "dở dang"})
    assert sync.sync_task(TASK).uploaded == ["results.json"]


def test_manifest_lives_in_cache_not_output(env):
    tmp, _ = env
    record(env)
    sync.sync_task(TASK)
    assert (tmp / "cache" / "sync_manifest" / f"{TASK}.json").is_file()
    assert not list((tmp / "output").rglob("*manifest*"))


def test_broken_manifest_means_upload_again_not_crash(env):
    tmp, _ = env
    record(env)
    sync.sync_task(TASK)
    (tmp / "cache" / "sync_manifest" / f"{TASK}.json").write_text("{hỏng", encoding="utf-8")
    assert len(sync.sync_task(TASK).uploaded) == 4


# 2. sua 1 file
def test_changed_file_is_the_only_upload(env):
    root = record(env)
    sync.sync_task(TASK)
    env[1].keys.clear()
    (root / "work" / "a.csv").write_text("đã đổi", encoding="utf-8")
    r = sync.sync_task(TASK)
    assert r.uploaded == ["work/a.csv"] and r.skipped == 3 and env[1].keys == [f"output/{TASK}/work/a.csv"]


def test_new_file_is_uploaded(env):
    root = record(env)
    sync.sync_task(TASK)
    (root / "new.json").write_text("{}", encoding="utf-8")
    assert sync.sync_task(TASK).uploaded == ["new.json"]


def test_deleted_local_file_deletes_nothing_on_r2(env):
    root = record(env)
    sync.sync_task(TASK)
    (root / "work" / "a.csv").unlink()
    assert sync.sync_task(TASK).uploaded == []
    assert not hasattr(env[1], "delete_object")  # client gia khong co ham xoa; sync khong goi


def test_sync_module_has_no_delete_call():
    with open(sync.__file__, encoding="utf-8") as f:
        assert "delete_object" not in f.read()


# 3. tu choi
@pytest.mark.parametrize("task", ["_fake", "../x", "a/b", "_fake/bai0a", "output/_fake/bai0a", "answers", "ANSWERS", ""])
def test_bad_task_refused_before_any_upload(env, task):
    record(env)
    with pytest.raises(ValueError):
        sync.sync_task(task)
    assert env[1].keys == []


def test_fake_output_mode_is_refused_even_when_called_by_hand(env):
    tmp, _ = env
    fake_root = tmp / "output" / "_fake" / TASK
    fake_root.mkdir(parents=True)
    (fake_root / "state.json").write_text("{}", encoding="utf-8")
    with app.fake_output(), pytest.raises(ValueError, match="giả lập"):
        sync.sync_task(TASK)
    assert env[1].keys == []


@pytest.mark.parametrize("key", ["input/bai0a/x", "answers/bai0a/x", "output/../input/x", "output/answers/x",
                                 "cache/x", "outputx/y"])
def test_key_outside_prefix_refused(key):
    with pytest.raises(ValueError):
        sync.check_key(key, SETTINGS)


def test_valid_key_accepted():
    sync.check_key("output/bai0a/work/a.csv", SETTINGS)


@pytest.mark.parametrize("settings", [{**SETTINGS, "R2_OUTPUT_PREFIX": "input/"}, {**SETTINGS, "R2_OUTPUT_PREFIX": ""},
                                      {**SETTINGS, "R2_OUTPUT_PREFIX": "answers/"},
                                      {**SETTINGS, "R2_OUTPUT_PREFIX": "output"}])
def test_bad_output_prefix_refused_before_upload(env, monkeypatch, settings):
    monkeypatch.setattr(r2, "get_r2_settings", lambda: settings)
    record(env)
    with pytest.raises(ValueError):
        sync.sync_task(TASK)
    assert env[1].keys == []


def test_answers_folder_inside_record_is_not_uploaded(env):
    record(env, {"state.json": "{}", "work/answers/x.csv": "x"})
    r = sync.sync_task(TASK)
    assert r.uploaded == ["state.json"] and r.failed == ["work/answers/x.csv"]
    assert not any("answers" in k for k in env[1].keys)


def test_upload_file_refuses_input_itself(env, tmp_path):
    f = tmp_path / "x"
    f.write_text("x")
    with pytest.raises(ValueError):
        r2.upload_file(f, "input/bai0a/x")


# 4. loi giua chung
def test_error_midway_continues_and_only_failed_file_is_retried(env):
    record(env)
    env[1].fail = {"state.json": RuntimeError("bucket bí mật")}
    r = sync.sync_task(TASK)
    assert r.failed == ["state.json"] and len(r.uploaded) == 3 and r.errors == ["RuntimeError"]

    env[1].fail, env[1].keys = {}, []
    r = sync.sync_task(TASK)
    assert r.uploaded == ["state.json"] and r.skipped == 3 and env[1].keys == [f"output/{TASK}/state.json"]


def test_connection_error_stops_this_round_then_resumes(env):
    record(env)
    env[1].fail = {"state.json": botocore.exceptions.EndpointConnectionError(endpoint_url="https://x")}
    r = sync.sync_task(TASK)
    assert r.failed == ["state.json"] and r.not_attempted == 1  # work/a.csv dung sau state.json
    env[1].fail = {}
    assert sorted(sync.sync_task(TASK).uploaded) == ["state.json", "work/a.csv"]


def test_safe_sync_never_raises_and_warns_one_line(env, capsys):
    record(env)
    env[1].fail = {".json": RuntimeError("bucket bí mật")}
    led = Ledger(env[0] / "output" / TASK)
    sync.safe_sync(led, TASK, "exit")
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and "Cảnh báo: đồng bộ R2" in out and "bí mật" not in out
    ev = [e for e in led.read_log() if e.step == "sync"][-1]
    assert ev.status == "error" and ev.action == "sync"
    assert (ev.extra["failed"], ev.extra["uploaded"], ev.extra["skipped"]) == (2, 2, 0)
    assert ev.extra["errors"] == ["RuntimeError"] and ev.extra["reason"] == "exit"


def test_safe_sync_survives_missing_config(env, monkeypatch, capsys):
    def missing():
        raise KeyError("R2_OUTPUT_PREFIX")

    monkeypatch.setattr(r2, "get_r2_settings", missing)
    led = Ledger(env[0] / "output" / TASK)
    sync.safe_sync(led, TASK, "exit")
    assert "KeyError" in capsys.readouterr().out


def test_safe_sync_ok_logs_counts_and_is_silent(env, capsys):
    record(env)
    led = Ledger(env[0] / "output" / TASK)
    sync.safe_sync(led, TASK, "done")
    assert capsys.readouterr().out == ""
    ev = [e for e in led.read_log() if e.step == "sync"][-1]
    assert ev.status == "ok" and ev.extra["uploaded"] == 4 and ev.extra["errors"] == []


# CLI
def test_dry_run_uploads_nothing_and_keeps_manifest(env, capsys):
    tmp, client = env
    record(env)
    assert sync.main([TASK, "--dry-run"]) == 0
    assert "Sẽ tải 4 file, bỏ qua 0 file." in capsys.readouterr().out
    assert client.keys == [] and not (tmp / "cache" / "sync_manifest").exists()


def test_cli_real_run_prints_counts(env, capsys):
    record(env)
    assert sync.main([TASK]) == 0
    assert "Đã tải 4 file, bỏ qua 0 file, lỗi 0 file." in capsys.readouterr().out
    assert sync.main([TASK]) == 0
    assert "Đã tải 0 file, bỏ qua 4 file, lỗi 0 file." in capsys.readouterr().out


def test_cli_reports_failure_with_exit_1(env, capsys):
    record(env)
    env[1].fail = {"state.json": RuntimeError("x")}
    assert sync.main([TASK]) == 1
    assert "lỗi 1 file" in capsys.readouterr().out


@pytest.mark.parametrize("task", ["_fake", "../x"])
def test_cli_refusal_exit_2(env, task, capsys):
    assert sync.main([task]) == 2 and "Từ chối" in capsys.readouterr().out


def test_cli_missing_record(env, capsys):
    assert sync.main(["baiKhongCo"]) == 2


# 5. main.py
def test_fake_never_syncs(env, calls):
    assert app.main([TASK, "--fake", "--yes"]) == loop.DONE
    assert calls == []


def test_fake_with_questions_never_syncs(env, calls):
    assert app.run_cli(TASK, fake=True, ask_user=lambda q: "y") == loop.DONE
    assert calls == []


def test_no_sync_never_syncs(env, calls):
    code, _ = run([RESTATEMENT, PLAN, STEP], sync=False, yes=False, ask_user=lambda q: "y")
    assert code == loop.DONE and calls == []


def test_main_no_sync_flag(env, calls, monkeypatch):
    fake = llm.FakeLLM({"analysis": [RESTATEMENT, PLAN, STEP]})
    with llm.use_fake(fake):
        assert app.main([TASK, "--no-sync", "--yes"]) == loop.DONE
    assert calls == []


def test_syncs_before_each_question_and_when_done(env, calls):
    asked = []

    def ask(q):
        asked.append(len(calls))
        return "y"

    code, _ = run([RESTATEMENT, PLAN, STEP], yes=False, ask_user=ask)
    assert code == loop.DONE
    assert asked == list(range(1, len(asked) + 1))  # moi cau hoi deu co dong bo ngay truoc do
    assert len(calls) == len(asked) + 1             # cong lan cuoi khi xong


def test_follow_up_prompt_is_not_a_checkpoint(env, calls):
    answers = iter(["n", "Sửa lại đi", "y"])
    run([RESTATEMENT, RESTATEMENT, LLMChainExhausted("hết")], yes=False, ask_user=lambda q: next(answers))
    # 2 cau xac nhan hieu de + 1 lan thoat; cau go chinh sua khong tinh
    assert len(calls) == 3


def test_yes_only_syncs_at_end(env, calls):
    assert run([RESTATEMENT, PLAN, STEP])[0] == loop.DONE
    assert len(calls) == 1


@pytest.mark.parametrize("error, code", [
    (LLMChainExhausted("hết"), loop.EXHAUSTED),
    (LLMChainExhausted("sai", attempts=[{"model_key": "a", "reason": "json_invalid", "status_code": None}]), loop.ERROR),
])
def test_syncs_when_exiting_with_error(env, calls, error, code):
    assert run([error])[0] == code
    assert calls == [TASK]


def test_syncs_when_stopped_by_user(env, calls):
    code, _ = run([RESTATEMENT, PLAN], yes=False, ask_user=lambda q: "n" if "câu hỏi nhỏ" in q else "y")
    assert code == loop.STOPPED and len(calls) >= 1


def test_syncs_when_r2_read_fails(env, calls, monkeypatch):
    def down(*a, **k):
        raise botocore.exceptions.EndpointConnectionError(endpoint_url="https://x")

    monkeypatch.setattr(r2, "list_objects", down)
    assert run([])[0] == loop.ERROR and calls == [TASK]


def test_keyboard_interrupt_caught_saved_and_synced(env, calls, capsys):
    def ask(q):
        raise KeyboardInterrupt

    code, _ = run([RESTATEMENT], yes=False, ask_user=ask)
    assert code == loop.STOPPED and "ngắt" in capsys.readouterr().out
    assert len(calls) == 2  # truoc cau hoi va luc thoat
    led = Ledger(env[0] / "output" / TASK)
    assert led.get_state().phase == Phase.UNDERSTAND
    assert [e for e in led.read_log() if e.action == "stop"][-1].status == "interrupted"


def test_unexpected_error_still_syncs_and_propagates(env, calls, monkeypatch):
    monkeypatch.setattr(loop, "load_context", lambda *a, **k: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        run([])
    assert calls == [TASK]


def test_sync_failure_does_not_stop_agent(env, capsys):
    env[1].fail = {".json": RuntimeError("x"), ".jsonl": RuntimeError("x"), ".py": RuntimeError("x"),
                   ".csv": RuntimeError("x")}
    code, _ = run([RESTATEMENT, PLAN, STEP], yes=False, ask_user=lambda q: "y")
    out = capsys.readouterr().out
    assert code == loop.DONE and "Cảnh báo: đồng bộ R2" in out
    led = Ledger(env[0] / "output" / TASK)
    assert led.get_state().phase == Phase.DONE
    assert [e for e in led.read_log() if e.step == "sync"][-1].status == "error"


def test_real_flow_puts_record_on_r2_and_retries_next_run(env):
    tmp, client = env
    client.fail = {"results.json": RuntimeError("x")}
    assert run([RESTATEMENT, PLAN, STEP])[0] == loop.DONE
    assert f"output/{TASK}/state.json" in client.keys and f"output/{TASK}/work/q1.csv" in client.keys
    assert f"output/{TASK}/results.json" not in client.keys

    client.fail = {}
    assert run([])[0] == loop.DONE  # bai da xong: van thu lai file loi
    assert f"output/{TASK}/results.json" in client.keys


def test_analysis_loop_cli_syncs_unless_no_sync(env, calls):
    fake = llm.FakeLLM({"analysis": [RESTATEMENT, PLAN, STEP]})
    with llm.use_fake(fake):
        assert loop.main([TASK, "--yes", "--no-sync"]) == loop.DONE
    assert calls == []
    assert loop.main([TASK, "--yes"]) == loop.DONE  # bai da xong, lan chay nay van dong bo
    assert calls == [TASK]


def test_run_task_direct_default_does_not_sync(env, calls):
    fake = llm.FakeLLM({"analysis": [RESTATEMENT, PLAN, STEP]})
    with llm.use_fake(fake):
        assert app.run_task(TASK, yes=True) == loop.DONE
    assert calls == []
