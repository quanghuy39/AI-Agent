"""Chay code Python do AI sinh ra, co 4 lop chan.

Chay thu: python -m runner.run_code <task> <file_code.py> [--step TEN] [--timeout GIAY]

1. Du lieu: moi lan liet ke input/<task>/ tren R2 va download_with_cache tung file (so etag, chi tai khi doi);
   R2 loi ket noi thi dung cache kem canh bao. CHEP sang thu muc tam va dat chi doc. Code AI chi biet thu muc
   tam (AGENT_INPUT_DIR), khong biet cache/ hay answers/.
2. Moi truong tien trinh con: bo moi bien R2_*, *_API_KEY, LLM_CONFIG_PATH (va PYTHONPATH); them AGENT_INPUT_DIR,
   AGENT_OUTPUT_DIR, PYTHONIOENCODING=utf-8. AGENT_OUTPUT_DIR la thu muc tam <tmp>/work/: truoc khi chay chep
   output/<task>/work/ vao do (de doc file trung gian cua buoc truoc); chi khi chay tron (ok) moi chep file
   moi/da doi ve (loi, qua gio, bi chan: khong chep, files_created rong).
   AGENT_META_PATH tro toi <tmp>/meta/meta.json: code ghi JSON meta vao do; runner doc vao RunResult.meta
   (chi khi chay tron), khong chep ve dau ca. Code AI khong biet duong dan nao cua du an.
3. Quet tinh (ast) truoc khi chay: import cam (config.RUNNER_FORBIDDEN_IMPORTS) va duong dan cam trong hang chuoi
   (config.RUNNER_FORBIDDEN_PATTERNS) -> tu choi, khong chay.
4. subprocess voi sys.executable, cwd = thu muc lam viec tam, timeout; qua gio thi kill, timed_out=True.
Quet tinh khong chan duoc moi cach lach (vd. ghep chuoi luc chay); lop 1 va 2 moi la hang rao chinh.
"""

import argparse
import ast
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import botocore.exceptions
from pydantic import BaseModel, Field

import config
from config import ROOT
from ledger import Ledger
from ledger.ledger import SUPERSEDED_DIR
from storage import r2

OUTPUT_DIR = ROOT / "output"
SCRIPT_NAME = "main.py"
META_NAME = "meta.json"
META_SNIPPET_CHARS = 300  # meta_error chi kem noi dung file khi ngan hon chung nay
_STEP_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_TASK_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


class RunResult(BaseModel):
    ok: bool
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    blocked_reason: str | None = None
    files_created: list[str] = Field(default_factory=list)  # file doi noi dung, duong dan that trong output/<task>/work/
    files_written: list[str] = Field(default_factory=list)  # moi file lan chay nay ghi, ke ca ghi lai y het
    code_path: str | None = None
    input_hashes: dict[str, str] = Field(default_factory=dict)  # ten file -> sha256
    duration_sec: float = 0.0
    warnings: list[str] = Field(default_factory=list)  # vd. R2 khong truy cap duoc, dung cache
    meta: dict | None = None        # JSON code ghi vao AGENT_META_PATH; chi co khi chay tron (ok)
    meta_error: str | None = None   # meta co nhung hong/qua lon


# ---------- lop 3: quet tinh ----------

def _strings(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (str, bytes)):
            yield node.value.decode("utf-8", "replace") if isinstance(node.value, bytes) else node.value


def _root(name):
    return (name or "").split(".")[0]


def scan_code(code):
    """Ly do tu choi (str) hoac None. Code khong parse duoc: None (de Python bao SyntaxError khi chay)."""
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return None
    forbidden = set(config.RUNNER_FORBIDDEN_IMPORTS)
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                return f"dòng {node.lineno}: import tương đối không được phép"
            names = [node.module]
        elif isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            fn = node.func
            fname = fn.id if isinstance(fn, ast.Name) else fn.attr if isinstance(fn, ast.Attribute) else ""
            if fname in ("__import__", "import_module"):
                names = [node.args[0].value]
        for name in names:
            if _root(name) in forbidden:
                return f"dòng {node.lineno}: import module bị cấm '{_root(name)}'"
    for s in _strings(tree):
        for pattern in config.RUNNER_FORBIDDEN_PATTERNS:
            if re.search(pattern, s):
                return f"chuỗi chứa đường dẫn bị cấm (mẫu {pattern})"
    return None


# ---------- lop 1: du lieu dau vao ----------

def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_answers(path_parts):
    return any(p.lower() == "answers" for p in path_parts)


R2_UNREACHABLE = "R2 không truy cập được, dùng cache có thể cũ"
# Loi ket noi (khong gom ClientError nhu 403/404: loi cau hinh thi bao that, khong lui ve cache)
_CONNECTION_ERRORS = (botocore.exceptions.ConnectionError, botocore.exceptions.HTTPClientError,
                      ConnectionError, TimeoutError)


def _cached_files(prefix):
    local = r2.CACHE_DIR / prefix
    files = {}
    if local.is_dir():
        for p in sorted(local.rglob("*")):
            rel = p.relative_to(local)
            if p.is_file() and not p.name.endswith(".etag") and not _is_answers(rel.parts):
                files[rel.as_posix()] = p
    return files


def input_files(task):
    """({ten tuong doi: duong dan trong cache}, canh bao) cua input/<task>/.

    Moi lan: list_objects tren R2 roi download_with_cache tung file (ham nay so etag voi R2, chi tai khi doi);
    file chi con trong cache ma khong con tren R2 thi khong dung. R2 loi ket noi -> dung moi file trong cache,
    kem canh bao R2_UNREACHABLE."""
    prefix = f"{r2.get_r2_settings()['R2_INPUT_PREFIX']}{task}/"
    try:
        files = {}
        for obj in r2.list_objects(prefix):
            rel = obj["key"][len(prefix):]
            if rel and not rel.endswith("/") and not _is_answers(Path(rel).parts):
                files[rel] = Path(r2.download_with_cache(obj["key"]))
        return files, []
    except _CONNECTION_ERRORS as e:
        print(f"Cảnh báo: {R2_UNREACHABLE} ({type(e).__name__})", file=sys.stderr)
        return _cached_files(prefix), [f"{R2_UNREACHABLE} ({type(e).__name__})"]


def _make_writable(path):
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)


def _rmtree(path):
    """Xoa thu muc tam; tra lai quyen ghi truoc (Windows khong xoa duoc file chi doc)."""
    for p in Path(path).rglob("*"):
        try:
            _make_writable(p)
        except OSError:
            pass

    def retry(func, p, _exc):
        _make_writable(p)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=retry)
    else:  # pragma: no cover
        shutil.rmtree(path, onerror=retry)


# ---------- luu code, chup thu muc output ----------

def _save_code(code, task, step_name):
    code_dir = OUTPUT_DIR / task / "code"
    code_dir.mkdir(parents=True, exist_ok=True)
    path, n = code_dir / f"{step_name}.py", 1
    while path.exists():
        n += 1
        path = code_dir / f"{step_name}_{n}.py"
    path.write_text(code, encoding="utf-8")
    return path


def _snapshot(directory):
    """{duong dan tuong doi: sha256} (so noi dung, khong dua vao mtime cua ban chep)."""
    return {p.relative_to(directory).as_posix(): _sha256(p) for p in directory.rglob("*") if p.is_file()}


def _mtimes(directory):
    """{duong dan tuong doi: mtime}. copytree giu mtime, nen file doi mtime nghia la code da ghi lai no."""
    return {p.relative_to(directory).as_posix(): p.stat().st_mtime_ns for p in directory.rglob("*") if p.is_file()}


def _truncate(text, limit=None):
    limit = config.RUNNER_MAX_OUTPUT_CHARS if limit is None else limit
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n... [bỏ {len(text) - 2 * half} ký tự ở giữa] ...\n{text[-half:]}"


def _decode(data):
    if data is None:
        return ""
    return data.decode("utf-8", "replace") if isinstance(data, bytes) else data


# ---------- lop 2: moi truong ----------

def child_env(input_dir, output_dir, meta_path):
    env = {k: v for k, v in os.environ.items()
           if not k.upper().startswith("R2_") and not k.upper().endswith("_API_KEY")
           and k.upper() not in ("LLM_CONFIG_PATH", "PYTHONPATH")}
    env.update(AGENT_INPUT_DIR=str(input_dir), AGENT_OUTPUT_DIR=str(output_dir), AGENT_META_PATH=str(meta_path),
               PYTHONIOENCODING="utf-8")
    return env


def read_meta(path):
    """(meta dict | None, meta_error | None). Khong co file -> (None, None). Qua lon, khong phai JSON object
    -> (None, mo ta loi ngan; chi kem noi dung khi file <= META_SNIPPET_CHARS ky tu)."""
    path = Path(path)
    if not path.is_file():
        return None, None
    size = path.stat().st_size
    if size > config.RUNNER_META_MAX_BYTES:
        return None, f"file meta quá lớn: {size} byte (tối đa {config.RUNNER_META_MAX_BYTES})"
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None, "file meta không phải UTF-8"
    snippet = f"; nội dung: {text!r}" if len(text) <= META_SNIPPET_CHARS else ""
    try:
        meta = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"meta không phải JSON hợp lệ: {e.msg} (dòng {e.lineno}, cột {e.colno}){snippet}"
    if not isinstance(meta, dict):
        return None, f"meta phải là một object JSON, nhận {type(meta).__name__}{snippet}"
    return meta, None


# ---------- ham chinh ----------

def execute(code, input_dir, work_dir, run_dir, meta_path, timeout=None, result=None):
    """Ham loi (lop 2 + 4): ghi run_dir/main.py roi chay voi cwd=run_dir, AGENT_INPUT_DIR/AGENT_OUTPUT_DIR/
    AGENT_META_PATH tro toi cac duong dan da cho; doc meta khi chay tron. Khong quet tinh, khong cham R2,
    khong chep ve, khong ghi log: nguoi goi lo thu muc (va don dep). Dien vao result (hoac RunResult moi)."""
    timeout = config.RUNNER_TIMEOUT if timeout is None else timeout
    result = RunResult(ok=False) if result is None else result
    (Path(run_dir) / SCRIPT_NAME).write_text(code, encoding="utf-8")
    start = time.perf_counter()
    try:
        proc = subprocess.run([sys.executable, "-X", "utf8", SCRIPT_NAME], cwd=run_dir,
                              env=child_env(input_dir, work_dir, meta_path), capture_output=True,
                              timeout=timeout)
        result.returncode = proc.returncode
        result.stdout, result.stderr = _decode(proc.stdout), _decode(proc.stderr)
    except subprocess.TimeoutExpired as e:  # subprocess.run da kill tien trinh con
        result.timed_out = True
        result.stdout, result.stderr = _decode(e.stdout), _decode(e.stderr)
        result.stderr += f"\n[runner] quá thời gian {timeout} giây, đã dừng tiến trình."
    result.duration_sec = round(time.perf_counter() - start, 3)
    result.stdout, result.stderr = _truncate(result.stdout), _truncate(result.stderr)
    result.ok = result.returncode == 0 and not result.timed_out
    if result.ok:  # chay hong thi khong tin meta: meta=None
        result.meta, result.meta_error = read_meta(meta_path)
    return result


def run_code(code, task, step_name, timeout=None):
    """Chay code cua AI cho bai task. Tra ve RunResult (khong raise khi code loi/bi chan/qua gio)."""
    if not _TASK_RE.match(task or "") or task.lower() == "answers":
        raise ValueError(f"task không hợp lệ: {task!r}")
    if not _STEP_RE.match(step_name or ""):
        raise ValueError(f"step_name chỉ gồm chữ, số, '_' và '-': {step_name!r}")
    timeout = config.RUNNER_TIMEOUT if timeout is None else timeout
    code_path = _save_code(code, task, step_name)
    result = RunResult(ok=False, code_path=str(code_path))

    reason = scan_code(code)
    if reason:
        result.blocked_reason = reason
        _log(task, step_name, code_path, result, "blocked")
        return result

    real_work = OUTPUT_DIR / task / "work"
    real_work.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="agent_run_"))
    try:
        # input/: ban chep chi doc; work/: AGENT_OUTPUT_DIR (ban chep cua output/<task>/work/);
        # run/: cwd, chua main.py (de main.py khong lan vao file ket qua); meta/: AGENT_META_PATH (rieng,
        # khong nam trong work/ nen khong bao gio duoc chep ve)
        input_dir, work_dir, run_dir = tmp / "input", tmp / "work", tmp / "run"
        meta_path = tmp / "meta" / META_NAME
        run_dir.mkdir(parents=True)
        meta_path.parent.mkdir()
        sources, result.warnings = input_files(task)
        if not sources and result.warnings:
            result.blocked_reason = "không lấy được dữ liệu đầu vào: R2 không truy cập được và cache trống"
            _log(task, step_name, code_path, result, "blocked")
            return result  # finally van don thu muc tam
        for rel, src in sources.items():
            dst = input_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dst)
            os.chmod(dst, stat.S_IREAD)
            result.input_hashes[rel] = _sha256(dst)
        input_dir.mkdir(exist_ok=True)
        # file trung gian cua cac buoc truoc; khong dua file cua lan lam truoc (--restart) cho code AI
        shutil.copytree(real_work, work_dir, ignore=shutil.ignore_patterns(SUPERSEDED_DIR))

        before, before_times = _snapshot(work_dir), _mtimes(work_dir)
        execute(code, input_dir, work_dir, run_dir, meta_path, timeout, result)
        if result.ok:  # chi chep ve khi chay tron; loi/qua gio thi file that giu nguyen, files_created rong
            changed = sorted(k for k, v in _snapshot(work_dir).items() if before.get(k) != v)
            for rel in changed:
                dst = real_work / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(work_dir / rel, dst)
            result.files_created = [str(real_work / rel) for rel in changed]
            # lan thu truoc da ghi y het thi file khong "doi", nhung van la file cua lan chay nay
            written = sorted(k for k, v in _mtimes(work_dir).items() if before_times.get(k) != v)
            result.files_written = [str(real_work / rel) for rel in written]
    finally:
        _rmtree(tmp)
    _log(task, step_name, code_path, result, "timeout" if result.timed_out else "ok" if result.ok else "error")
    return result


def _log(task, step_name, code_path, result, status):
    """1 su kien run_log cua bai (khong kem stdout/stderr)."""
    try:
        rel = code_path.relative_to(OUTPUT_DIR / task).as_posix()
    except ValueError:
        rel = str(code_path)
    Ledger(OUTPUT_DIR / task).log_event(
        "runner", step_name, inputs={"input_hashes": result.input_hashes}, code_file=rel, status=status,
        extra={"returncode": result.returncode, "duration_sec": result.duration_sec,
               "files_created": result.files_created, "files_written": result.files_written, "blocked_reason": result.blocked_reason,
               "warnings": result.warnings, "has_meta": result.meta is not None,  # khong ghi noi dung meta
               "meta_error": result.meta_error})


def main(argv=None):
    p = argparse.ArgumentParser(description="Chạy thử code Python cho một bài trong runner có chặn.")
    p.add_argument("task")
    p.add_argument("code_file")
    p.add_argument("--step", help="tên bước (mặc định: tên file code)")
    p.add_argument("--timeout", type=int, default=None, help=f"giây (mặc định {config.RUNNER_TIMEOUT})")
    args = p.parse_args(argv)
    code = Path(args.code_file).read_text(encoding="utf-8")
    step = args.step or re.sub(r"[^A-Za-z0-9_\-]", "_", Path(args.code_file).stem)
    r = run_code(code, args.task, step, timeout=args.timeout)
    for w in r.warnings:
        print(f"Cảnh báo: {w}")
    if r.blocked_reason:
        print(f"BỊ CHẶN: {r.blocked_reason}")
    else:
        print(f"ok={r.ok} returncode={r.returncode} timed_out={r.timed_out} {r.duration_sec}s")
        print(f"files_created: {r.files_created}")
        if r.meta_error:
            print(f"meta: lỗi - {r.meta_error}")
        else:
            print("meta: " + (f"có ({len(r.meta.get('checks') or [])} check)" if r.meta is not None else "không có"))
        if r.stdout:
            print("--- stdout ---\n" + r.stdout)
        if r.stderr:
            print("--- stderr ---\n" + r.stderr)
    print(f"code: {r.code_path}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
