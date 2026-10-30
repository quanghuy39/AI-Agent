"""Cac ham cham cho bo kiem dinh. Khong goi mang.

Code cua model (role analysis) chay bang runner/run_code.py (ham loi runner.execute, cung quet tinh scan_code)
dung quy uoc cua vong lap that: thu muc tam input/ (ban chep chi doc cua moi file du lieu gia, dat thang
trong input/ nhu runner chep file cua bai), work/ (AGENT_OUTPUT_DIR, rong), meta/meta.json (AGENT_META_PATH),
run/ (cwd). So lieu cham lay tu result_summary trong meta; meta duoc cham rieng bang analysis.meta_grader.
Runner KHONG phai sandbox that (xem runner/run_code.py).
"""

import ast
import math
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from analysis.meta_grader import GradeResult, grade_meta
from runner import run_code as runner
from tests.llm_eval.synth_data import KNOWN_COLUMNS

OUTPUT_LIMIT = 4000


@dataclass
class EvalRun:
    ok: bool  # khong bi chan, ma thoat 0, khong qua gio
    returncode: int | None
    timed_out: bool
    blocked_reason: str | None
    stdout: str
    stderr: str
    meta: dict | None  # JSON code ghi vao AGENT_META_PATH (chi khi ok)
    meta_error: str | None
    grade: GradeResult | None  # grade_meta (chi khi co meta)
    inputs_changed: list[str]  # file dau vao bi xoa/sua sau khi chay
    error: str | None  # mo ta ngan khi khong ok
    duration_s: float

    @property
    def result_summary(self):
        rs = (self.meta or {}).get("result_summary")
        return rs if isinstance(rs, dict) else None


def _error(r):
    if r.blocked_reason:
        return f"bị chặn: {r.blocked_reason}"
    if r.timed_out:
        return "quá thời gian"
    if r.returncode != 0:
        last = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""
        return f"mã thoát {r.returncode}: {last[:200]}"
    return None


def run_step(code, data_dir, step=None, timeout_s=30):
    """Chay code trong runner voi du lieu cua data_dir; cham meta bang grade_meta(meta, step, ...) khi co
    step va meta. Khong raise khi code loi/bi chan/qua gio."""
    blocked = runner.scan_code(code)
    if blocked:
        return EvalRun(False, None, False, blocked, "", "", None, None, None, [], f"bị chặn: {blocked}", 0.0)
    tmp = Path(tempfile.mkdtemp(prefix="llm_eval_"))
    try:
        input_dir, work_dir, run_dir = tmp / "input", tmp / "work", tmp / "run"
        meta_path = tmp / "meta" / runner.META_NAME
        for d in (input_dir, work_dir, run_dir, meta_path.parent):
            d.mkdir(parents=True)
        hashes = {}
        for src in sorted(Path(data_dir).iterdir()):
            if src.is_file():
                dst = input_dir / src.name
                shutil.copyfile(src, dst)
                dst.chmod(stat.S_IREAD)
                hashes[src.name] = runner._sha256(dst)
        r = runner.execute(code, input_dir, work_dir, run_dir, meta_path, timeout_s)
        changed = [n for n, h in hashes.items()
                   if not (input_dir / n).is_file() or runner._sha256(input_dir / n) != h]
        grade = grade_meta(r.meta, step, input_dir, work_dir) if r.meta is not None and step is not None else None
        return EvalRun(r.ok, r.returncode, r.timed_out, None, r.stdout[-OUTPUT_LIMIT:], r.stderr[-OUTPUT_LIMIT:],
                       r.meta, r.meta_error, grade, changed, _error(r), r.duration_sec)
    finally:
        runner._rmtree(tmp)


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


_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def summary_key(parts):
    """Duong dan trong dap an -> khoa result_summary (dinh danh ASCII): noi bang '_', chi so danh sach tu 1,
    ky tu khac chu/so/_ thanh '_', bat dau bang so thi them 'k'. Vd ("top", 0, "total") -> "top_1_total",
    ("2025-01",) -> "k2025_01"."""
    key = re.sub(r"[^A-Za-z0-9_]", "_", "_".join(str(p) for p in parts))
    return key if _IDENT_RE.match(key) else f"k{key}"


def flatten_expected(expected, parts=()):
    """Dap an long nhau -> dict phang {khoa result_summary: gia tri} (result_summary chi chua gia tri phang)."""
    if isinstance(expected, dict):
        out = {}
        for k, v in expected.items():
            out.update(flatten_expected(v, (*parts, k)))
        return out
    if isinstance(expected, list):
        out = {}
        for i, v in enumerate(expected, 1):
            out.update(flatten_expected(v, (*parts, i)))
        return out
    return {summary_key(parts): expected}


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



# ---------- Bien DataFrame xac dinh duoc (cho no_invented_columns) ----------
# Chi x["c"] / x.c voi x la DataFrame xac dinh duoc moi tinh la dung cot; Series, dict, nhan index hay bien
# khong xac dinh duoc thi bo qua (cot bia that van bi bat qua KeyError luc chay).
FRAME_METHODS = {
    "merge", "join", "copy", "dropna", "drop_duplicates", "assign", "query", "sort_values", "fillna", "head",
    "tail", "sample", "drop", "rename", "astype", "reset_index", "set_index", "filter", "nlargest", "nsmallest",
    "where", "mask", "replace",
}
FRAME_FUNCS = {"merge", "concat"}  # pd.merge / pd.concat co it nhat mot doi so la DataFrame
DATAFRAME_ATTRS = set(dir(pd.DataFrame))
FRAME, GROUPBY = "frame", "groupby"


class _Frames:
    """Gan nhan "frame" (DataFrame) / "groupby" (DataFrameGroupBy) cho bieu thuc va ten bien. Mot ten la frame
    chi khi MOI lan gan ten do deu la frame (gan bang dict, vong for, unpack... -> khong xac dinh)."""

    def __init__(self, tree):
        sites = {}  # ten -> [bieu thuc gan | None neu khong xac dinh]
        direct = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for t in targets:
                    if isinstance(t, ast.Name):
                        sites.setdefault(t.id, []).append(node.value)
                        direct.add(id(t))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and id(node) not in direct:
                sites.setdefault(node.id, []).append(None)
        self.names = {}
        for _ in range(10):  # lap toi khi on dinh (bien phu thuoc bien khac)
            new = {}
            for name, values in sites.items():
                kinds = {self.kind(v) if v is not None else None for v in values}
                if len(kinds) == 1 and None not in kinds:
                    new[name] = kinds.pop()
            if new == self.names:
                break
            self.names = new

    def kind(self, node):
        if isinstance(node, ast.Name):
            return self.names.get(node.id)
        if isinstance(node, ast.Call):
            name = _call_name(node)
            last = name.rsplit(".", 1)[-1]
            if last in READ_FUNCS and name.count(".") <= 1:
                return FRAME
            if isinstance(node.func, ast.Attribute):
                base = self.kind(node.func.value)
                if base == FRAME and last in FRAME_METHODS:
                    return FRAME
                if base == FRAME and last == "groupby":
                    return GROUPBY
                if base is None and last in FRAME_FUNCS and name.count(".") == 1 and any(
                        self.kind(a) == FRAME for a in node.args + [k.value for k in node.keywords]):
                    return FRAME
            elif last in FRAME_FUNCS and any(self.kind(a) == FRAME for a in node.args):
                return FRAME
            return None
        if isinstance(node, ast.Subscript):
            value, sl = node.value, node.slice
            if isinstance(value, ast.Attribute) and value.attr in ("loc", "iloc") and self.kind(value.value) == FRAME:
                return None if isinstance(sl, ast.Tuple) else FRAME  # df.loc[mask] -> frame; df.loc[m, "c"] -> ?
            if self.kind(value) == FRAME:
                if isinstance(sl, ast.Constant):
                    return None  # df["c"] -> Series
                return FRAME     # df[["a", "b"]], df[mask]
        return None


@dataclass
class StaticReport:
    parse_error: str | None = None
    parse_lineno: int | None = None
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
        rep.parse_lineno = e.lineno
        return rep

    frames = _Frames(tree)
    call_funcs = {id(n.func) for n in ast.walk(tree) if isinstance(n, ast.Call)}
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
            elif frames.kind(node.value) in (FRAME, GROUPBY):  # df["c"], df.groupby(...)["c"]
                rep.used_columns.update(keys)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and id(node) not in call_funcs:
            if node.attr not in DATAFRAME_ATTRS and frames.kind(node.value) == FRAME:  # df.c
                rep.used_columns.add(node.attr)
        elif isinstance(node, ast.Assign):
            for t in node.targets:  # X.columns = [...]
                if isinstance(t, ast.Attribute) and t.attr == "columns":
                    rep.created_columns.update(_strs(node.value))
        if isinstance(node, ast.Call):
            _check_call(node, rep, frames)
    return rep


def _check_call(node, rep, frames):
    name = _call_name(node)
    last = name.rsplit(".", 1)[-1]
    if name in FORBIDDEN_BUILTINS or name == "importlib.import_module":
        rep.forbidden.append(f"{name}()")
    if name.startswith("os.") and last in FORBIDDEN_OS_CALLS or name.startswith(("os.spawn", "os.exec")):
        rep.forbidden.append(f"{name}()")
    if last in FORBIDDEN_METHODS and not name.startswith("os."):
        rep.forbidden.append(f"{name}()")

    # duong dan doc/ghi: hop dong bat moi file di qua AGENT_INPUT_DIR / AGENT_OUTPUT_DIR / AGENT_META_PATH,
    # nen duong dan hang so (biet tron luc phan tich tinh) la vi pham
    if name in ("open", "io.open") and node.args:
        path = _const_path(node.args[0])
        if path is not None:
            rep.forbidden.append(f"open({path!r}) không qua AGENT_*")
    if last in READ_FUNCS and node.args:
        path = _const_path(node.args[0])
        if path is not None:
            rep.forbidden.append(f"{last}({path!r}) không qua AGENT_INPUT_DIR")
    if last in WRITE_FUNCS and node.args:
        path = _const_path(node.args[0])
        if path is not None:
            rep.forbidden.append(f"{last}({path!r}) không qua AGENT_OUTPUT_DIR")

    # cot duoc dung / duoc tao
    if last in COLUMN_METHODS and isinstance(node.func, ast.Attribute) and frames.kind(node.func.value) == FRAME:
        for a in node.args[:1]:
            rep.used_columns.update(_strs(a))
    if last == "set_axis" and node.args:
        rep.created_columns.update(_strs(node.args[0]))
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


LABEL_MAX_CHARS = 100


def meta_details(out, run):
    """Thong tin de soat loi meta, khong chua gia tri du lieu: loai + label/name cua tung check (khong passed,
    reason, so lieu), so y can_be_wrong_if, so check custom, structural_errors cua grade_meta (von chi gom so dem,
    ten cot, ten file). meta_check_types / n_custom_checks = None khi khong co meta."""
    checks = (run.meta or {}).get("checks") if run else None
    types = None
    if isinstance(checks, list):
        types = []
        for c in checks:
            c = c if isinstance(c, dict) else {}
            name_key = "name" if c.get("type") == "custom" else "label"
            types.append({"type": c.get("type"), name_key: str(c.get(name_key, ""))[:LABEL_MAX_CHARS]})
    return {
        "meta_check_types": types,
        "n_can_be_wrong_if": len([x for x in out.can_be_wrong_if if x.strip()]) if out is not None else None,
        "n_custom_checks": sum(1 for t in types if t["type"] == "custom") if types is not None else None,
        "structural_errors": list(run.grade.structural_errors) if run and run.grade else [],
        "syntax_error_context": None,
    }


# chuoi dong trong dong, hoac (nhanh cuoi) chuoi khong dong: tu dau nhay toi het dong
_STRING_RE = re.compile(r"""'{3}.*?'{3}|"{3}.*?"{3}|'(?:\\.|[^'\\])*'|"(?:\\.|[^"\\])*"|['"].*$""")


def _mask_strings(line):
    """Moi chuoi trong dau nhay -> "..." (chuoi goc co dau \\ thi thanh "...\\..." de con thay loi escape);
    chuoi khong dong o cuoi dong -> "..." toi het dong."""
    def mask(m):
        return '"...\\..."' if "\\" in m.group(0) else '"..."'
    return _STRING_RE.sub(mask, line)


def syntax_error_context(code, lineno, radius=1):
    """3 dong code quanh dong loi (dang "so: noi dung"), moi chuoi da thay bang "..."."""
    if not lineno:
        return None
    lines = code.splitlines()
    lo, hi = max(1, lineno - radius), min(len(lines), lineno + radius)
    return [f"{i}: {_mask_strings(lines[i - 1])}" for i in range(lo, hi + 1)]


def score_analysis(case, out, data_dir, timeout_s=30, details=None):
    """out: AnalysisStep (hoac None khi JSON sai). Tieu chi rieng meta_valid: meta co, doc duoc va grade_meta
    khong co structural_errors. value_correct so dap an (lam phang) voi result_summary trong meta.
    details (dict, tuy chon): duoc dien meta_details(out, run) de ghi vao dong ket qua."""
    if details is not None:
        details.update(meta_details(out, None))
    kind = case.checks["kind"]
    names = ["code_runs", "meta_valid", "no_invented_columns", "no_forbidden"]
    names += {"value": ["value_correct"], "missing_column": ["handles_missing_column"],
              "ambiguous": ["assumptions_present"]}[kind]
    if out is None:
        crit, why = _fail_all(names, "không có JSON hợp lệ")
        return {"json_valid": False, **crit}, {"json_valid": "JSON sai schema sau khi thử lại", **why}

    crit, why = {"json_valid": True}, {}
    code = (out.code or "").strip()
    rep = static_check(code) if code else StaticReport()
    run = run_step(code, data_dir, out, timeout_s) if code and not rep.parse_error else None
    if details is not None:
        details.update(meta_details(out, run))
        if rep.parse_error:
            details["syntax_error_context"] = syntax_error_context(code, rep.parse_lineno)

    # code_runs, meta_valid
    if kind == "missing_column" and not code:
        crit["code_runs"] = crit["meta_valid"] = None
    else:
        crit["code_runs"] = bool(run and run.ok)
        if not crit["code_runs"]:
            why["code_runs"] = rep.parse_error or (run.error if run else "code rỗng")
        problems = []
        if not crit["code_runs"]:
            problems.append("code không chạy được")
        elif run.meta_error:
            problems.append(run.meta_error)
        elif run.meta is None:
            problems.append("không có file meta (AGENT_META_PATH)")
        else:
            problems += run.grade.structural_errors
        crit["meta_valid"] = not problems
        if problems:
            why["meta_valid"] = "; ".join(problems)

    # no_forbidden: tinh + runner chan + file dau vao bi dong vao
    forbidden = list(rep.forbidden)
    if run and run.blocked_reason:
        forbidden.append(f"runner chặn: {run.blocked_reason}")
    if run and run.inputs_changed:
        forbidden.append(f"file đầu vào bị xóa/sửa: {run.inputs_changed}")
    if run and "PermissionError" in run.stderr:
        forbidden.append("PermissionError khi chạy (ghi/xóa file chỉ đọc)")
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
        elif run.result_summary is None:
            crit["value_correct"] = False
            why["value_correct"] = "meta không có result_summary"
        else:
            ok, reason = values_match(flatten_expected(case.expected), run.result_summary)
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
        crit["assumptions_present"] = any(a.text.strip() for a in out.new_assumptions)
        if not crit["assumptions_present"]:
            why["assumptions_present"] = "đề mơ hồ nhưng new_assumptions rỗng"
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


def score_case(case, out, data_dir=None, timeout_s=30, details=None):
    """Cham mot case. out: doi tuong pydantic da validate hoac None (JSON sai). Tra ve (criteria, reasons).
    details: chi role analysis dien (xem meta_details)."""
    if case.role == "analysis":
        return score_analysis(case, out, data_dir, timeout_s, details)
    if case.role == "report":
        return score_report(case, out)
    if case.role == "slide":
        return score_slide(case, out)
    raise ValueError(f"role không hỗ trợ: {case.role}")


def passed(criteria):
    """Dat ca case: moi tieu chi ap dung duoc (khac None) deu True."""
    return all(v for v in criteria.values() if v is not None)
