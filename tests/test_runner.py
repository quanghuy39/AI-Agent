"""Kiem tra runner/run_code.py. Khong goi R2 that, khong goi LLM: cache gia trong tmp_path (monkeypatch).

Chay: python -m pytest tests/test_runner.py -v
"""

import hashlib

import pytest
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError

from ledger import Ledger
from runner import run_code as rc
from storage import r2

TASK = "baiX"
SALES = "category,revenue\nA,10\nB,20\nA,5\n"


@pytest.fixture
def fake_r2(tmp_path, monkeypatch):
    """R2 gia: remote = {key: bytes}; download_with_cache ghi file vao cache gia nhu ham that.
    Cache co san OLD.csv (khong con tren R2) va answers/ (khong bao gio duoc dua vao input)."""
    cache = tmp_path / "cache"
    src = cache / "input" / TASK
    src.mkdir(parents=True)
    (src / "OLD.csv").write_bytes(b"stale\n1\n")
    (src / "OLD.csv.etag").write_text("old", encoding="utf-8")
    answers = cache / "answers" / TASK
    answers.mkdir(parents=True)
    (answers / "key.json").write_text('{"x": 1}', encoding="utf-8")
    state = {"remote": {f"input/{TASK}/SALES.csv": SALES.encode("utf-8"),  # bytes: Windows khong doi \n
                        f"input/{TASK}/answers/k.json": b"{}"},
             "listed": [], "downloaded": [], "fail": None}

    def list_objects(prefix, grader=False):
        state["listed"].append(prefix)
        if state["fail"] == "list":
            raise EndpointConnectionError(endpoint_url="https://r2.example")
        return [{"key": k} for k in state["remote"] if k.startswith(prefix)]

    def download_with_cache(key, cache_dir=None, grader=False):
        assert "answers" not in key
        if state["fail"] == "download":
            raise ReadTimeoutError(endpoint_url="https://r2.example")
        if state["fail"] == "forbidden":
            raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadObject")
        state["downloaded"].append(key)
        local = cache / key
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(state["remote"][key])
        return local

    monkeypatch.setattr(r2, "CACHE_DIR", cache)
    monkeypatch.setattr(r2, "get_r2_settings", lambda: {"R2_INPUT_PREFIX": "input/"})
    monkeypatch.setattr(r2, "list_objects", list_objects)
    monkeypatch.setattr(r2, "download_with_cache", download_with_cache)
    return state


@pytest.fixture
def env(tmp_path, monkeypatch, fake_r2):
    monkeypatch.setattr(rc, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "r2-secret-xyz")
    monkeypatch.setenv("GROQ_API_KEY", "groq-secret-xyz")
    monkeypatch.setenv("LLM_CONFIG_PATH", "config/llm.yaml")
    return tmp_path


GOOD = """
import os
import pandas as pd
df = pd.read_csv(os.path.join(os.environ["AGENT_INPUT_DIR"], "SALES.csv"))
out = df.groupby("category", as_index=False)["revenue"].sum()
out.to_csv(os.path.join(os.environ["AGENT_OUTPUT_DIR"], "revenue_by_category.csv"), index=False)
print(out.to_string(index=False))
"""


def test_valid_code_reads_input_and_writes_output(env):
    r = rc.run_code(GOOD, TASK, "R1_revenue_by_category")
    assert r.ok and r.returncode == 0 and not r.timed_out and r.blocked_reason is None, r.stderr
    real = env / "output" / TASK / "work" / "revenue_by_category.csv"
    assert r.files_created == [str(real)]  # duong dan that, khong phai thu muc tam
    out = real.read_text(encoding="utf-8")
    assert out.splitlines() == ["category,revenue", "A,15", "B,20"]
    assert r.code_path == str(env / "output" / TASK / "code" / "R1_revenue_by_category.py")
    assert (env / "output" / TASK / "code" / "R1_revenue_by_category.py").read_text(encoding="utf-8") == GOOD
    assert r.input_hashes == {"SALES.csv": hashlib.sha256(SALES.encode()).hexdigest()}
    assert "A" in r.stdout and r.duration_sec > 0
    log = Ledger(env / "output" / TASK).read_log()
    assert log[-1].step == "runner" and log[-1].action == "R1_revenue_by_category" and log[-1].status == "ok"
    assert log[-1].code_file == "code/R1_revenue_by_category.py"


def test_same_step_name_gets_suffix_and_unchanged_files_not_reported(env):
    first = rc.run_code(GOOD, TASK, "R1")
    second = rc.run_code("print('khong ghi gi')", TASK, "R1")
    third = rc.run_code("print(1)", TASK, "R1")
    assert first.code_path.endswith("R1.py") and second.code_path.endswith("R1_2.py")
    assert third.code_path.endswith("R1_3.py")
    assert second.files_created == []  # file cu khong doi thi khong tinh


def test_overwriting_input_does_not_touch_cache(env):
    code = """
import os
p = os.path.join(os.environ["AGENT_INPUT_DIR"], "SALES.csv")
try:
    open(p, "w").write("hacked")
    print("WROTE")
except PermissionError:
    print("READONLY")
os.chmod(p, 0o666)
open(p, "w").write("hacked")
"""
    r = rc.run_code(code, TASK, "overwrite")
    assert "READONLY" in r.stdout  # file input chi doc
    assert (env / "cache" / "input" / TASK / "SALES.csv").read_bytes() == SALES.encode("utf-8")
    assert r.warnings == []
    assert not list((env / "output").rglob("SALES.csv"))


def test_environment_has_no_secrets(env):
    code = "import os\nfor k, v in sorted(os.environ.items()):\n    print(k, '=', v)\n"
    r = rc.run_code(code, TASK, "env")
    assert r.ok
    names = [line.split(" = ")[0] for line in r.stdout.splitlines() if " = " in line]
    assert not [n for n in names if n.upper().startswith("R2_") or n.upper().endswith("_API_KEY")]
    assert "LLM_CONFIG_PATH" not in names
    assert "r2-secret-xyz" not in r.stdout and "groq-secret-xyz" not in r.stdout
    assert {"AGENT_INPUT_DIR", "AGENT_OUTPUT_DIR", "PYTHONIOENCODING"} <= set(names)
    input_dir = next(l for l in r.stdout.splitlines() if l.startswith("AGENT_INPUT_DIR"))
    assert "cache" not in input_dir.lower() and "answers" not in input_dir.lower()


def test_agent_dirs_are_temporary_not_project_paths(env):
    import config
    code = "import os\nprint(os.environ['AGENT_INPUT_DIR'])\nprint(os.environ['AGENT_OUTPUT_DIR'])\nprint(os.getcwd())"
    r = rc.run_code(code, TASK, "dirs")
    assert r.ok, r.stderr
    printed = r.stdout.lower()
    for project_path in (config.ROOT, rc.OUTPUT_DIR, env / "cache", rc.OUTPUT_DIR / TASK / "work"):
        assert str(project_path).lower() not in printed
    assert "answers" not in printed and "cache" not in printed


def test_intermediate_files_carry_over_between_runs(env):
    step1 = ("import os, pandas as pd\n"
             "df = pd.read_csv(os.path.join(os.environ['AGENT_INPUT_DIR'], 'SALES.csv'))\n"
             "df[df.revenue > 5].to_csv(os.path.join(os.environ['AGENT_OUTPUT_DIR'], 'step1.csv'), index=False)\n")
    step2 = ("import os, pandas as pd\n"
             "out = os.environ['AGENT_OUTPUT_DIR']\n"
             "df = pd.read_csv(os.path.join(out, 'step1.csv'))\n"
             "os.makedirs(os.path.join(out, 'sub'))\n"
             "df.to_csv(os.path.join(out, 'sub', 'step2.csv'), index=False)\n"
             "print(len(df))\n")
    work = env / "output" / TASK / "work"
    r1 = rc.run_code(step1, TASK, "S1")
    assert r1.ok and r1.files_created == [str(work / "step1.csv")]
    r2_ = rc.run_code(step2, TASK, "S2")
    assert r2_.ok, r2_.stderr
    assert r2_.stdout.strip() == "2"  # doc duoc file trung gian cua lan truoc
    assert r2_.files_created == [str(work / "sub" / "step2.csv")]  # step1.csv khong doi -> khong tinh
    assert (work / "step1.csv").exists() and (work / "sub" / "step2.csv").exists()


def test_failed_step_does_not_overwrite_real_files(env):
    work = env / "output" / TASK / "work"
    ok = rc.run_code("import os\nopen(os.path.join(os.environ['AGENT_OUTPUT_DIR'], 'step1.csv'), 'w')"
                     ".write('good')\n", TASK, "S1")
    assert ok.ok and ok.files_created == [str(work / "step1.csv")]
    bad = rc.run_code("import os\nout = os.environ['AGENT_OUTPUT_DIR']\n"
                      "open(os.path.join(out, 'step1.csv'), 'w').write('broken')\n"
                      "open(os.path.join(out, 'new.csv'), 'w').write('x')\n"
                      "raise RuntimeError('hong giua chung')\n", TASK, "S2")
    assert not bad.ok and bad.files_created == []
    assert (work / "step1.csv").read_text() == "good" and not (work / "new.csv").exists()


def test_timeout_does_not_copy_back(env):
    code = "import os\nopen(os.path.join(os.environ['AGENT_OUTPUT_DIR'], 'partial.csv'), 'w').write('x')\nwhile True:\n    pass\n"
    r = rc.run_code(code, TASK, "loop_write", timeout=2)
    assert r.timed_out and r.files_created == []
    assert not (env / "output" / TASK / "work" / "partial.csv").exists()


def test_input_dir_has_no_answers(env):
    r = rc.run_code("import os\nfor root, d, f in os.walk(os.environ['AGENT_INPUT_DIR']):\n    print(root, f)",
                    TASK, "walk")
    assert r.ok and "SALES.csv" in r.stdout and "key.json" not in r.stdout and ".etag" not in r.stdout


def test_infinite_loop_times_out(env):
    r = rc.run_code("while True:\n    pass\n", TASK, "loop", timeout=2)
    assert r.timed_out and not r.ok and r.returncode is None
    assert 2 <= r.duration_sec < 30


@pytest.mark.parametrize("code, why", [
    ("open('answers/baiX/key.json').read()", "đường dẫn"),
    ("import os\np = os.path.join('cache', 'input')\nq = 'cache/input/baiX/SALES.csv'", "đường dẫn"),
    ("open('cache\\\\answers.csv')", "đường dẫn"),
    ("open('../x')", "đường dẫn"),
    ("import os\nos.path.join('..', 'x')", "đường dẫn"),
    ("from dotenv import load_dotenv\nload_dotenv('.env')", "đường dẫn"),
    ("import boto3", "boto3"),
    ("from botocore.config import Config", "botocore"),
    ("import storage.r2", "storage"),
    ("from llm import ask_model", "llm"),
    ("import litellm", "litellm"),
    ("import requests", "requests"),
    ("import httpx", "httpx"),
    ("from urllib.request import urlopen", "urllib"),
    ("import socket", "socket"),
    ("import subprocess", "subprocess"),
    ("__import__('socket')", "socket"),
    ("import importlib\nimportlib.import_module('boto3')", "boto3"),
])
def test_blocked_code_does_not_run(env, code, why):
    marker = env / "ran.txt"
    r = rc.run_code(code + f"\nopen(r'{marker}', 'w').write('x')\n", TASK, "blocked")
    assert not r.ok and r.blocked_reason and why in r.blocked_reason
    assert r.returncode is None and not marker.exists()  # khong chay
    assert r.code_path  # van luu code de truy vet
    assert Ledger(env / "output" / TASK).read_log()[-1].status == "blocked"


def test_not_blocked_false_positives(env):
    code = """
import functools
cached = {}
@functools.lru_cache(maxsize=None)
def f(x):
    return x * 2
print("Loading...", f(2), cached, "answer:", 42, "my_cache_dir", "a.env_var")
"""
    r = rc.run_code(code, TASK, "lru")
    assert r.blocked_reason is None and r.ok, r.stderr
    assert "Loading... 4 {} answer: 42" in r.stdout


def test_error_code_returns_traceback(env):
    r = rc.run_code("def f():\n    raise ValueError('loi co y')\nf()\n", TASK, "error")
    assert not r.ok and r.returncode != 0 and not r.timed_out
    assert "Traceback" in r.stderr and "ValueError: loi co y" in r.stderr
    assert Ledger(env / "output" / TASK).read_log()[-1].status == "error"


def test_syntax_error_runs_and_reports(env):
    r = rc.run_code("def f(:\n", TASK, "syntax")
    assert r.blocked_reason is None and not r.ok and "SyntaxError" in r.stderr


def test_long_output_truncated(env):
    r = rc.run_code("print('x' * 50000)\nprint('END')", TASK, "long")
    assert r.ok and len(r.stdout) < 20_200 and r.stdout.rstrip().endswith("END") and "bỏ" in r.stdout


def test_temp_dir_cleaned(env, monkeypatch, tmp_path):
    made = []
    real = rc.tempfile.mkdtemp

    def track(*a, **k):
        made.append(real(*a, **k))
        return made[-1]

    monkeypatch.setattr(rc.tempfile, "mkdtemp", track)
    rc.run_code(GOOD, TASK, "clean")
    assert made and not any(__import__("os").path.exists(p) for p in made)  # da xoa ca file chi doc


LIST_INPUT = "import os\nprint(sorted(os.listdir(os.environ['AGENT_INPUT_DIR'])))\n"


def test_always_lists_r2_and_ignores_stale_cache(env, fake_r2):
    for step in ("a", "b"):
        r = rc.run_code(LIST_INPUT, TASK, step)
        assert r.ok and r.warnings == [] and r.stdout.strip() == "['SALES.csv']"  # OLD.csv chi con trong cache
    assert fake_r2["listed"] == [f"input/{TASK}/"] * 2  # moi lan chay deu liet ke R2
    assert fake_r2["downloaded"] == [f"input/{TASK}/SALES.csv"] * 2  # khong tai answers/


@pytest.mark.parametrize("fail", ["list", "download"])
def test_r2_unreachable_falls_back_to_cache(env, fake_r2, capsys, fail):
    (env / "cache" / "input" / TASK / "SALES.csv").write_bytes(SALES.encode("utf-8"))  # cache cu
    fake_r2["fail"] = fail
    r = rc.run_code(LIST_INPUT, TASK, f"offline_{fail}")
    assert r.ok and r.stdout.strip() == "['OLD.csv', 'SALES.csv']"  # dung moi file trong cache (tru answers)
    assert len(r.warnings) == 1 and r.warnings[0].startswith(rc.R2_UNREACHABLE)
    assert rc.R2_UNREACHABLE in capsys.readouterr().err
    log = Ledger(env / "output" / TASK).read_log()[-1]
    assert log.status == "ok" and log.extra["warnings"] == r.warnings


def test_r2_unreachable_and_empty_cache_blocks(env, fake_r2):
    for p in (env / "cache" / "input" / TASK).iterdir():
        p.unlink()
    fake_r2["fail"] = "list"
    marker = env / "ran.txt"
    r = rc.run_code(f"open(r'{marker}', 'w').write('x')\n", TASK, "offline_empty")
    assert not r.ok and "cache trống" in r.blocked_reason and r.warnings and not marker.exists()
    assert Ledger(env / "output" / TASK).read_log()[-1].status == "blocked"


def test_r2_permission_error_is_not_hidden(env, fake_r2):
    fake_r2["fail"] = "forbidden"  # loi cau hinh/quyen: khong lui ve cache
    with pytest.raises(ClientError):
        rc.run_code(LIST_INPUT, TASK, "forbidden")


@pytest.mark.parametrize("task, step", [("../x", "s"), ("answers", "s"), ("bai1", "a/b"), ("bai1", "")])
def test_bad_task_or_step(env, task, step):
    with pytest.raises(ValueError):
        rc.run_code("print(1)", task, step)
