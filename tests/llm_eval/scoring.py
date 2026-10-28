"""Cac ham cham cho bo kiem dinh. Khong goi mang.

run_code chi de cham kiem dinh (Ngay 5 se viet runner/ rieng). No chay code cua model trong tien trinh
con voi: cwd la thu muc tam, ban sao ./data/ chi doc, moi truong toi gian (python -I), va mot lop chan
o muc Python (socket, subprocess, os.system, xoa file). Lop chan nay KHONG phai sandbox that: code co
chu dich van co the vuot qua. Chi chay voi code tu model da duoc dua prompt kiem dinh.
"""

import ast
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from tests.llm_eval.synth_data import KNOWN_COLUMNS

BLOCKED_MSG = "bị chặn trong môi trường chấm"
OUTPUT_LIMIT = 4000

_GUARD = '''
import os, runpy, shutil, socket, subprocess, sys
MSG = "%s"
def _deny(*args, **kwargs):
    raise PermissionError(MSG)
for _n in ("connect", "connect_ex", "bind", "sendto", "sendall", "send"):
    setattr(socket.socket, _n, _deny)
socket.create_connection = _deny
socket.getaddrinfo = _deny
for _n in ("run", "call", "check_call", "check_output", "Popen", "getoutput", "getstatusoutput"):
    setattr(subprocess, _n, _deny)
for _n in dir(os):
    if _n in ("system", "popen", "remove", "unlink", "rmdir", "removedirs", "rename", "renames",
              "replace", "startfile", "kill", "truncate") or _n.startswith(("spawn", "exec")):
        setattr(os, _n, _deny)
shutil.rmtree = _deny
shutil.move = _deny
code_path = sys.argv[1]
sys.argv = [code_path]
runpy.run_path(code_path, run_name="__main__")
''' % BLOCKED_MSG


@dataclass
class RunResult:
    ok: bool  # ma thoat 0 va output/result.json la mot object JSON
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str
    result: dict | None
    error: str | None  # mo ta ngan khi khong ok
    duration_s: float


def _decode(b):
    if b is None:
        return ""
    text = b.decode("utf-8", errors="replace") if isinstance(b, bytes) else b
    return text[-OUTPUT_LIMIT:]


def _rmtree(path):
    def _retry(func, p, _exc):
        os.chmod(p, stat.S_IWRITE)
        func(p)
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_retry)
    else:  # pragma: no cover
        shutil.rmtree(path, onerror=lambda f, p, e: _retry(f, p, e))


def run_code(code, data_dir, timeout_s=30):
    """Chay code trong tien trinh con. cwd co ./data/ (ban sao chi doc) va ./output/ rong."""
    work = Path(tempfile.mkdtemp(prefix="llm_eval_"))
    try:
        data = work / "data"
        shutil.copytree(data_dir, data)
        for f in data.iterdir():
            os.chmod(f, stat.S_IREAD)
        (work / "output").mkdir()
        (work / "tmp").mkdir()
        (work / "solution.py").write_text(code, encoding="utf-8")
        (work / "_guard.py").write_text(_GUARD, encoding="utf-8")

        env = {"PATH": os.path.dirname(sys.executable), "TEMP": str(work / "tmp"), "TMP": str(work / "tmp")}
        if "SYSTEMROOT" in os.environ:  # Windows can bien nay de khoi dong Python
            env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
        cmd = [sys.executable, "-I", "-X", "utf8", "_guard.py", "solution.py"]

        t0 = time.perf_counter()
        try:
            proc = subprocess.run(cmd, cwd=work, env=env, capture_output=True, timeout=timeout_s)
        except subprocess.TimeoutExpired as e:
            return RunResult(False, None, True, _decode(e.stdout), _decode(e.stderr), None,
                             f"quá thời gian {timeout_s}s", round(time.perf_counter() - t0, 3))
        duration = round(time.perf_counter() - t0, 3)
        stdout, stderr = _decode(proc.stdout), _decode(proc.stderr)
        if proc.returncode != 0:
            last = stderr.strip().splitlines()[-1] if stderr.strip() else ""
            return RunResult(False, proc.returncode, False, stdout, stderr, None,
                             f"mã thoát {proc.returncode}: {last[:200]}", duration)

        result_path = work / "output" / "result.json"
        if not result_path.exists():
            return RunResult(False, 0, False, stdout, stderr, None, "không có output/result.json", duration)
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            return RunResult(False, 0, False, stdout, stderr, None, f"result.json không phải JSON: {e}", duration)
        if not isinstance(result, dict):
            return RunResult(False, 0, False, stdout, stderr, None, "result.json không phải object JSON", duration)
        return RunResult(True, 0, False, stdout, stderr, result, None, duration)
    finally:
        _rmtree(work)


# ---------- So sanh gia tri ----------

def _as_number(x):
    if isinstance(x, bool):
        return None
    if isinstance(x, (int, float)):
        return float(x)
    if isinstance(x, str):
        try:
            return float(x.strip())
        except ValueError:
            return None
    return None


def values_match(expected, actual, rel_tol=1e-3, abs_tol=1e-6, path="$"):
    """So sanh de quy. Tra ve (ok, ly_do). Dict: moi khoa cua expected phai co (cho phep khoa thua)."""
    if isinstance(expected, bool) or expected is None:
        ok = actual == expected
        return ok, None if ok else f"{path}: cần {expected!r}, nhận {actual!r}"
    if isinstance(expected, (int, float)):
        a = _as_number(actual)
        if a is None:
            return False, f"{path}: cần số {expected!r}, nhận {actual!r}"
        ok = math.isclose(a, float(expected), rel_tol=rel_tol, abs_tol=abs_tol)
        return ok, None if ok else f"{path}: cần {expected!r}, nhận {actual!r}"
    if isinstance(expected, str):
        ok = isinstance(actual, str) and actual.strip() == expected.strip()
        return ok, None if ok else f"{path}: cần {expected!r}, nhận {actual!r}"
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False, f"{path}: cần object, nhận {type(actual).__name__}"
        for k, v in expected.items():
            if k not in actual:
                return False, f"{path}: thiếu khóa {k!r}"
            ok, why = values_match(v, actual[k], rel_tol, abs_tol, f"{path}.{k}")
            if not ok:
                return False, why
        return True, None
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            n = len(actual) if isinstance(actual, list) else type(actual).__name__
            return False, f"{path}: cần danh sách {len(expected)} phần tử, nhận {n}"
        for i, (e, a) in enumerate(zip(expected, actual)):
            ok, why = values_match(e, a, rel_tol, abs_tol, f"{path}[{i}]")
            if not ok:
                return False, why
        return True, None
    return False, f"{path}: kiểu expected không hỗ trợ {type(expected).__name__}"


# ---------- Placeholder va "khong tu go so" ----------

PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_MALFORMED_RE = re.compile(r"\{\{|\}\}|\{[A-Za-z][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\}")
_DATE_RE = re.compile(r"\b\d{1,4}[-/]\d{1,2}[-/]\d{1,4}\b")
_NUMBER_RE = re.compile(r"(?<![\w.,])[-+]?\d+(?:[.,]\d+)*(?!\w)")
_ORDINAL_PREFIXES = ("phần", "mục", "slide", "bước", "chương", "quý", "tháng", "top", "thứ", "hạng", "#")


def find_placeholders(text):
    return PLACEHOLDER_RE.findall(text)


def check_placeholders(texts, ledger, min_count=0, required_ids=()):
    """Moi {{id.field}} phai co trong so ket qua; khong co placeholder sai dang; du so luong toi thieu."""
    found, problems = [], []
    for text in texts:
        for rid, fld in find_placeholders(text):
            found.append((rid, fld))
            if rid not in ledger:
                problems.append(f"không có ID {rid}")
            elif fld not in ledger[rid]:
                problems.append(f"{rid} không có trường {fld}")
        rest = PLACEHOLDER_RE.sub("", text)
        for m in _MALFORMED_RE.finditer(rest):
            problems.append(f"placeholder sai dạng gần '{rest[max(0, m.start() - 10):m.end() + 10]}'")
    if len(found) < min_count:
        problems.append(f"chỉ có {len(found)} placeholder, cần ít nhất {min_count}")
    missing_ids = [r for r in required_ids if r not in {rid for rid, _ in found}]
    if missing_ids:
        problems.append(f"thiếu ID bắt buộc {missing_ids}")
    return not problems, "; ".join(dict.fromkeys(problems)) or None


def _interpretations(token):
    """Cac cach doc mot token so -> [(gia tri, so chu so thap phan)]. Ho tro ca 1.234,5 va 1,234.5."""
    sign = -1 if token.startswith("-") else 1
    token = token.lstrip("+-")
    parts = re.split(r"[.,]", token)
    if len(parts) == 1:
        return [(sign * float(token), 0)]
    out = []
    if all(len(p) == 3 for p in parts[1:]):  # tat ca la dau phan nghin
        out.append((sign * float("".join(parts)), 0))
    head, frac = "".join(parts[:-1]), parts[-1]  # dau cuoi la dau thap phan
    out.append((sign * float(f"{head}.{frac}"), len(frac)))
    return out


def _ledger_numbers(ledger):
    nums = []
    for entry in ledger.values():
        for v in entry.values():
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                continue
            nums.append(float(v))
            if abs(v) <= 1 and not float(v).is_integer():
                nums.append(float(v) * 100)  # ty le 0.375 viet thanh 37,5%
    return nums


def _allowed_context(text, start, token):
    plain = token.lstrip("+-")
    if plain.isdigit() and len(plain) == 4 and 1990 <= int(plain) <= 2100:
        return True  # nam
    before = text[:start].lower().rstrip()
    if before.endswith(_ORDINAL_PREFIXES):
        return True  # so thu tu, ten muc
    line_start = text.rfind("\n", 0, start) + 1
    after = text[start + len(token):start + len(token) + 2]
    if not text[line_start:start].strip() and after[:1] in (".", ")") and after[1:2] in (" ", ""):
        return True  # danh so dau dong "1. ", "2) "
    return False


def typed_numbers(texts, ledger):
    """Tra ve cac token so (sau khi bo placeholder) trung voi gia tri trong so ket qua."""
    nums = _ledger_numbers(ledger)
    hits = []
    for text in texts:
        clean = _DATE_RE.sub(" ", PLACEHOLDER_RE.sub(" ", text))
        for m in _NUMBER_RE.finditer(clean):
            token = m.group(0)
            if _allowed_context(clean, m.start(), token):
                continue
            for value, decimals in _interpretations(token):
                if any(abs(round(v, decimals) - value) <= 1e-9 * max(1.0, abs(value)) for v in nums):
                    hits.append(token)
                    break
    return hits


# ---------- Phan tich tinh code ----------

FORBIDDEN_MODULES = {
    "socket", "requests", "urllib", "urllib3", "http", "httpx", "aiohttp", "ftplib", "smtplib",
    "telnetlib", "subprocess", "shutil", "ctypes", "multiprocessing", "webbrowser", "paramiko", "pty",
}
FORBIDDEN_OS_CALLS = {
    "system", "popen", "remove", "unlink", "rmdir", "removedirs", "rename", "renames", "replace",
    "startfile", "kill", "chmod", "truncate",
}
FORBIDDEN_BUILTINS = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_METHODS = {"unlink", "rmdir", "rmtree"}
WRITE_FUNCS = {"to_csv", "to_json", "to_excel", "to_parquet", "to_pickle", "to_feather"}
READ_FUNCS = {"read_csv", "read_excel", "read_json", "read_parquet", "read_table", "read_pickle"}
COLUMN_METHODS = {
    "groupby", "sort_values", "drop_duplicates", "dropna", "set_index", "pivot_table", "pivot",
    "value_counts", "nlargest", "nsmallest", "duplicated", "fillna", "explode", "melt",
}
COLUMN_KWARGS = {"on", "left_on", "right_on", "by", "subset", "index", "values", "usecols", "parse_dates", "id_vars"}


def _strs(node):
    """Chuoi hang trong node (Constant str hoac List/Tuple cac Constant str)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return []


def _const_path(node):
    """Duong dan hang so (chuoi, os.path.join / Path(...) / Path / 'x' cua cac chuoi). None neu khong biet."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left, right = _const_path(node.left), _const_path(node.right)
        return f"{left}/{right}" if left is not None and right is not None else None
    if isinstance(node, ast.Call):
        name = _call_name(node)
        if name in ("os.path.join", "Path", "pathlib.Path", "PurePath") and node.args:
            parts = [_const_path(a) for a in node.args]
            return "/".join(parts) if all(p is not None for p in parts) else None
    return None


def _call_name(call):
    f = call.func
    names = []
    while isinstance(f, ast.Attribute):
        names.append(f.attr)
        f = f.value
    if isinstance(f, ast.Name):
        names.append(f.id)
    return ".".join(reversed(names))


def _path_allowed(path, write):
    p = path.replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    if p.startswith("/") or re.match(r"^[A-Za-z]:", p) or ".." in p.split("/"):
        return False
    if write:
        return p == "output" or p.startswith("output/")
    return p.startswith(("data/", "output/")) or p in ("data", "output")


@dataclass
class StaticReport:
    parse_error: str | None = None
    used_columns: set = field(default_factory=set)
    created_columns: set = field(default_factory=set)
    strings: set = field(default_factory=set)
    forbidden: list = field(default_factory=list)

    @property
    def unknown_columns(self):
        return sorted(self.used_columns - KNOWN_COLUMNS - self.created_columns)


def static_check(code):
    rep = StaticReport()
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        rep.parse_error = f"SyntaxError dòng {e.lineno}: {e.msg}"
        return rep

    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            rep.strings.add(node.value)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in FORBIDDEN_MODULES:
                    rep.forbidden.append(f"import {a.name}")
        elif isinstance(node, ast.ImportFrom):
            mod = (node.module or "").split(".")[0]
            if mod in FORBIDDEN_MODULES:
                rep.forbidden.append(f"from {node.module} import ...")
            if mod == "os":
                for a in node.names:
                    if a.name in FORBIDDEN_OS_CALLS:
                        rep.forbidden.append(f"from os import {a.name}")
        elif isinstance(node, ast.Subscript):
            keys = _strs(node.slice)
            if isinstance(node.ctx, ast.Store):
                rep.created_columns.update(keys)
            else:
                rep.used_columns.update(keys)
        elif isinstance(node, ast.Call):
            _check_call(node, rep)
    return rep


def _check_call(node, rep):
    name = _call_name(node)
    last = name.rsplit(".", 1)[-1]
    if name in FORBIDDEN_BUILTINS or name == "importlib.import_module":
        rep.forbidden.append(f"{name}()")
    if name.startswith("os.") and last in FORBIDDEN_OS_CALLS or name.startswith(("os.spawn", "os.exec")):
        rep.forbidden.append(f"{name}()")
    if last in FORBIDDEN_METHODS and not name.startswith("os."):
        rep.forbidden.append(f"{name}()")

    # duong dan doc/ghi
    if name in ("open", "io.open") and node.args:
        path = _const_path(node.args[0])
        mode = "r"
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            mode = str(node.args[1].value)
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = str(kw.value.value)
        write = any(c in mode for c in "wax+")
        if path is not None and not _path_allowed(path, write):
            rep.forbidden.append(f"open({path!r}, {mode!r}) ngoài thư mục cho phép")
    if last in READ_FUNCS and node.args:
        path = _const_path(node.args[0])
        if path is not None and not _path_allowed(path, write=False):
            rep.forbidden.append(f"{last}({path!r}) ngoài ./data/")
    if last in WRITE_FUNCS and node.args:
        path = _const_path(node.args[0])
        if path is not None and not _path_allowed(path, write=True):
            rep.forbidden.append(f"{last}({path!r}) ngoài ./output/")

    # cot duoc dung / duoc tao
    if last in COLUMN_METHODS:
        for a in node.args[:1]:
            rep.used_columns.update(_strs(a))
    for kw in node.keywords:
        if kw.arg in COLUMN_KWARGS:
            rep.used_columns.update(_strs(kw.value))
    if last == "merge":
        for kw in node.keywords:
            if kw.arg == "suffixes":
                rep.created_columns.update(_strs(kw.value))
    if last in ("assign", "agg", "aggregate", "named_agg"):
        for kw in node.keywords:
            if kw.arg:
                rep.created_columns.add(kw.arg)
                if isinstance(kw.value, ast.Tuple) and kw.value.elts:
                    rep.used_columns.update(_strs(kw.value.elts[0]))
        if last in ("agg", "aggregate") and node.args and isinstance(node.args[0], ast.Dict):
            for k in node.args[0].keys:
                rep.used_columns.update(_strs(k) if k is not None else [])
    if last == "rename":
        for kw in node.keywords:
            if kw.arg == "columns" and isinstance(kw.value, ast.Dict):
                for k, v in zip(kw.value.keys, kw.value.values):
                    rep.used_columns.update(_strs(k) if k is not None else [])
                    rep.created_columns.update(_strs(v))
    if last == "reset_index":
        for kw in node.keywords:
            if kw.arg == "name":
                rep.created_columns.update(_strs(kw.value))
    if last in ("DataFrame", "Series"):
        for kw in node.keywords:
            if kw.arg in ("columns", "name", "index"):
                rep.created_columns.update(_strs(kw.value))
        if node.args and isinstance(node.args[0], ast.Dict):
            for k in node.args[0].keys:
                rep.created_columns.update(_strs(k) if k is not None else [])


_KEYERROR_RE = re.compile(r"KeyError: ['\"]([^'\"]+)['\"]")
_NOT_IN_INDEX_RE = re.compile(r"\[['\"]([^'\"]+)['\"]\] not in index|None of \[Index\(\[['\"]([^'\"]+)['\"]")


def runtime_missing_columns(stderr):
    names = set(_KEYERROR_RE.findall(stderr or ""))
    for a, b in _NOT_IN_INDEX_RE.findall(stderr or ""):
        names.add(a or b)
    return names


# ---------- Cham tung role ----------

def _fail_all(names, reason):
    return {n: False for n in names}, {n: reason for n in names}


def score_analysis(case, out, data_dir, timeout_s=30):
    kind = case.checks["kind"]
    names = ["code_runs", "no_invented_columns", "no_forbidden"]
    names += {"value": ["value_correct"], "missing_column": ["handles_missing_column"],
              "ambiguous": ["assumptions_present"]}[kind]
    if out is None:
        crit, why = _fail_all(names, "không có JSON hợp lệ")
        return {"json_valid": False, **crit}, {"json_valid": "JSON sai schema sau khi thử lại", **why}

    crit, why = {"json_valid": True}, {}
    code = (out.code or "").strip()
    rep = static_check(code) if code else StaticReport()
    run = run_code(code, data_dir, timeout_s) if code and not rep.parse_error else None

    # code_runs
    if kind == "missing_column" and not code:
        crit["code_runs"] = None
    else:
        crit["code_runs"] = bool(run and run.ok)
        if not crit["code_runs"]:
            why["code_runs"] = rep.parse_error or (run.error if run else "code rỗng")

    # no_forbidden: tinh + luc chay
    forbidden = list(rep.forbidden)
    if run and BLOCKED_MSG in run.stderr:
        forbidden.append("bị chặn khi chạy (mạng/subprocess/xóa file)")
    crit["no_forbidden"] = not forbidden
    if forbidden:
        why["no_forbidden"] = "; ".join(forbidden)

    # no_invented_columns: tinh + KeyError luc chay
    invented = set(rep.unknown_columns)
    if run:
        invented |= runtime_missing_columns(run.stderr) - KNOWN_COLUMNS - rep.created_columns
    crit["no_invented_columns"] = not invented
    if invented:
        why["no_invented_columns"] = f"dùng cột không có: {sorted(invented)}"

    if kind == "value":
        if out.cannot_answer_reason:
            crit["value_correct"] = False
            why["value_correct"] = "model từ chối trả lời một đề trả lời được"
        elif not (run and run.ok):
            crit["value_correct"] = False
            why["value_correct"] = "code không chạy được"
        else:
            ok, reason = values_match(case.expected, run.result)
            crit["value_correct"] = ok
            if not ok:
                why["value_correct"] = reason
    elif kind == "missing_column":
        col = case.checks["missing_column"]
        has_reason = bool(out.cannot_answer_reason and out.cannot_answer_reason.strip())
        uses_col = col in rep.strings
        crit["handles_missing_column"] = has_reason and not uses_col
        if not has_reason:
            why["handles_missing_column"] = "cannot_answer_reason rỗng"
        elif uses_col:
            why["handles_missing_column"] = f"code vẫn dùng cột {col!r}"
    elif kind == "ambiguous":
        crit["assumptions_present"] = any(a.strip() for a in out.assumptions)
        if not crit["assumptions_present"]:
            why["assumptions_present"] = "đề mơ hồ nhưng assumptions rỗng"
    return crit, why


def _text_checks(case, texts, crit, why):
    ok, reason = check_placeholders(
        texts, case.ledger, case.checks.get("min_placeholders", 0), case.checks.get("required_ids", ())
    )
    crit["placeholder_ok"] = ok
    if not ok:
        why["placeholder_ok"] = reason
    hits = typed_numbers(texts, case.ledger)
    crit["no_typed_numbers"] = not hits
    if hits:
        why["no_typed_numbers"] = f"tự gõ số trùng sổ kết quả: {hits[:10]}"


def score_report(case, out):
    names = ["structure_ok", "placeholder_ok", "no_typed_numbers"]
    if out is None:
        crit, why = _fail_all(names, "không có JSON hợp lệ")
        return {"json_valid": False, **crit}, {"json_valid": "JSON sai schema sau khi thử lại", **why}
    crit, why = {"json_valid": True}, {}
    lo, hi = case.checks.get("min_sections", 1), case.checks.get("max_sections", 10)
    problems = []
    if not lo <= len(out.sections) <= hi:
        problems.append(f"{len(out.sections)} mục, cần {lo}-{hi}")
    if any(not s.heading.strip() or not s.body.strip() for s in out.sections):
        problems.append("có mục thiếu heading hoặc body")
    crit["structure_ok"] = not problems
    if problems:
        why["structure_ok"] = "; ".join(problems)
    _text_checks(case, [t for s in out.sections for t in (s.heading, s.body)], crit, why)
    return crit, why


def score_slide(case, out):
    names = ["structure_ok", "chart_ref_ok", "placeholder_ok", "no_typed_numbers"]
    if out is None:
        crit, why = _fail_all(names, "không có JSON hợp lệ")
        return {"json_valid": False, **crit}, {"json_valid": "JSON sai schema sau khi thử lại", **why}
    crit, why = {"json_valid": True}, {}
    max_slides = case.checks["max_slides"]
    max_bullets = case.checks.get("max_bullets", 5)
    allowed = set(case.chart_refs)

    bad_refs = [s.chart_ref for s in out.slides if s.chart_ref is not None and s.chart_ref not in allowed]
    missing_refs = [r for r in case.checks.get("required_charts", ()) if r not in {s.chart_ref for s in out.slides}]
    crit["chart_ref_ok"] = not bad_refs and not missing_refs
    if bad_refs or missing_refs:
        why["chart_ref_ok"] = "; ".join(filter(None, [
            f"chart_ref không hợp lệ: {bad_refs}" if bad_refs else "",
            f"thiếu chart bắt buộc: {missing_refs}" if missing_refs else "",
        ]))

    problems = []
    if not 1 <= len(out.slides) <= max_slides:
        problems.append(f"{len(out.slides)} slide, tối đa {max_slides}")
    for i, s in enumerate(out.slides, 1):
        if not s.title.strip():
            problems.append(f"slide {i} thiếu title")
        if len(s.bullets) > max_bullets:
            problems.append(f"slide {i} có {len(s.bullets)} bullet, tối đa {max_bullets}")
    if bad_refs:
        problems.append("chart_ref không hợp lệ")
    crit["structure_ok"] = not problems
    if problems:
        why["structure_ok"] = "; ".join(problems)

    texts = [t for s in out.slides for t in (s.title, *s.bullets)]
    _text_checks(case, texts, crit, why)
    return crit, why


def score_case(case, out, data_dir=None, timeout_s=30):
    """Cham mot case. out: doi tuong pydantic da validate hoac None (JSON sai). Tra ve (criteria, reasons)."""
    if case.role == "analysis":
        return score_analysis(case, out, data_dir, timeout_s)
    if case.role == "report":
        return score_report(case, out)
    if case.role == "slide":
        return score_slide(case, out)
    raise ValueError(f"role không hỗ trợ: {case.role}")


def passed(criteria):
    """Dat ca case: moi tieu chi ap dung duoc (khac None) deu True."""
    return all(v for v in criteria.values() if v is not None)
