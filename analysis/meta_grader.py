"""Cham file meta do code AI ghi (AgentMeta) bang validators/. Khong goi R2, khong goi LLM.

grade_meta(meta, step, input_dir, work_dir) -> GradeResult:
- structural_errors: loi cau truc can AI sua (meta sai schema, thieu file khai bao, thieu check custom,
  file/cot trong check khong co, check khong cham duoc, khai bao join khong khop phep noi that). Co loi -> ok=False.
- checks: [{name, status, reason}] dung dinh dang Ledger.add_result (qua to_ledger_check).
- flags: tom tat cac check status flag.
reason/flags/structural_errors chi chua so dem, ten cot, ten file; KHONG chua gia tri du lieu (exception la
chi ghi ten loi). Rieng reason cua check custom lay nguyen tu meta (do code AI tu viet).
"""

from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field, ValidationError

from skills.analysis.schemas import AgentMeta
from storage import r2
from validators import join_check, range_check, row_count_check, sum_consistency, to_ledger_check
from validators.result import make_result

TABLE_EXTS = (".csv", ".parquet")
MAX_SCHEMA_ERRORS = 10


class GradeResult(BaseModel):
    ok: bool
    structural_errors: list[str] = Field(default_factory=list)
    result_summary: dict = Field(default_factory=dict)
    checks: list[dict] = Field(default_factory=list)  # {name, status, reason}
    flags: list[str] = Field(default_factory=list)


class _GradeError(Exception):
    """Loi da co thong bao an toan (khong chua gia tri du lieu) de dua thang vao reason."""


def _schema_errors(err):
    """Loi pydantic -> cau ngan: vi tri + thong bao, khong kem gia tri dau vao."""
    out = []
    for e in err.errors()[:MAX_SCHEMA_ERRORS]:
        loc = ".".join(str(p) for p in e["loc"]) or "meta"
        out.append(f"meta sai schema tại {loc}: {e['msg']}")
    if len(err.errors()) > MAX_SCHEMA_ERRORS:
        out.append(f"meta sai schema: còn {len(err.errors()) - MAX_SCHEMA_ERRORS} lỗi nữa")
    return out


def _safe_rel(name):
    rel = Path(name)
    if rel.is_absolute() or ".." in rel.parts:
        raise _GradeError(f"tên file không hợp lệ '{name}' (chỉ dùng tên file trong thư mục input/output)")
    return rel


def _in_dir(name, base):
    try:
        return (Path(base) / _safe_rel(name)).is_file()
    except _GradeError:
        return False


def _find_file(name, input_dir, work_dir):
    """Tim file theo ten trong work_dir truoc, roi input_dir. Khong cho ra ngoai hai thu muc."""
    rel = _safe_rel(name)
    for base in (work_dir, input_dir):
        path = Path(base) / rel
        if path.is_file():
            return path
    raise _GradeError(f"không có file '{name}' trong thư mục output hoặc input")


def read_local_table(path):
    """Doc bang cuc bo dang chuoi (dtype=str). CSV: nhan dien ma hoa/dau phan cach bang storage."""
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        df = pd.read_parquet(path)
        df = df.astype(object).where(df.notna(), None)
        return df.apply(lambda col: col.map(lambda v: v if v is None else str(v)))
    if suffix == ".csv":
        fmt = r2.detect_csv_format(path)
        return pd.read_csv(path, dtype=str, sep=fmt["delimiter"], encoding=fmt["encoding"])
    raise _GradeError(f"file '{path.name}' không phải .csv/.parquet")


def _columns(df, cols, filename):
    cols = [cols] if isinstance(cols, str) else list(cols)
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise _GradeError(f"file '{filename}' không có cột {missing}")
    return cols


def _grade_check(check, input_dir, work_dir, cache):
    def table(name):
        path = _find_file(name, input_dir, work_dir)
        if path not in cache:
            cache[path] = read_local_table(path)
        return cache[path]

    if check.type == "custom":
        return make_result(check.name, check.passed, check.reason)
    if check.type == "row_count":
        r = row_count_check(check.before, check.after, expect=check.expect,
                            expected_ratio=list(check.expected_ratio) if check.expected_ratio else None)
    elif check.type == "sum":
        r = sum_consistency(check.parts, check.total)
    elif check.type == "range":
        df = table(check.file)
        (col,) = _columns(df, check.column, check.file)
        # gia tri khong doi duoc sang so: range_check dem la non_numeric va gan co (khong bo qua im lang)
        r = range_check(df[col], check.min, check.max, max_examples=0)
        if r["details"]["non_numeric"]:
            r["reason"] += f" (cột {col} trong '{check.file}')"
    elif check.type == "join":
        left, right = table(check.left), table(check.right)
        lo, ro = _columns(left, check.left_on, check.left), _columns(right, check.right_on, check.right)
        r = join_check(left, right, lo, ro, how=check.how, max_examples=0)
    else:  # pragma: no cover - AgentMeta da loc type
        raise _GradeError(f"loại check không hỗ trợ: {check.type}")
    r["name"] = check.label
    return r


def _join_declaration_mismatch(check, result, all_checks):
    """Khai bao join khong khop phep noi that: join_check du bao nhan ban dong (expected_rows > so dong trai)
    nhung trong cung meta co row_count before = after = so dong trai (phep noi that giu nguyen so dong).
    Khong co row_count nhu vay -> None (giu hanh vi cu: chi co). Chi dung so dem, ten file, label."""
    details = result.get("details") or {}
    n_left, expected = details.get("n_left"), details.get("expected_rows")
    if n_left is None or expected is None or expected <= n_left:
        return None
    if not any(c.type == "row_count" and c.before == n_left and c.after == n_left for c in all_checks):
        return None
    return (f"check join '{check.label}' khai báo nối với {check.right} làm tăng lên {expected} dòng, nhưng row_count "
            f"cho thấy kết quả giữ {n_left} dòng; nếu nối với bảng trung gian (đã gộp) thì ghi bảng đó vào "
            f"AGENT_OUTPUT_DIR và khai file đó trong check join")


def grade_meta(meta, step, input_dir, work_dir):
    """Cham meta cua mot buoc. Khong nem exception vi loi cua meta/du lieu; moi loi thanh structural_errors."""
    input_dir, work_dir = Path(input_dir), Path(work_dir)
    errors = []
    try:
        parsed = AgentMeta.model_validate(meta)
    except ValidationError as e:
        return GradeResult(ok=False, structural_errors=_schema_errors(e))

    for name in step.output_files:
        if not _in_dir(name, work_dir):
            errors.append(f"file khai báo trong output_files không có trong thư mục output: '{name}'")

    n_custom = sum(1 for c in parsed.checks if c.type == "custom")
    n_risks = len([x for x in step.can_be_wrong_if if x.strip()])
    if n_risks > n_custom:
        errors.append(f"can_be_wrong_if có {n_risks} ý nhưng meta chỉ có {n_custom} check custom "
                      f"(mỗi ý cần ít nhất một check custom)")

    checks, cache = [], {}
    for c in parsed.checks:
        label = c.name if c.type == "custom" else c.label
        try:
            result = _grade_check(c, input_dir, work_dir, cache)
        except _GradeError as e:
            result = make_result(label, False, f"không chấm được: {e}")
            errors.append(f"check '{label}': {e}")
        except Exception as e:  # noqa: BLE001  khong de sap; chi ghi ten loi (thong bao co the chua gia tri)
            result = make_result(label, False, f"không chấm được: {type(e).__name__}")
            errors.append(f"check '{label}' ({c.type}) không chấm được: {type(e).__name__}")
        if c.type == "join":
            mismatch = _join_declaration_mismatch(c, result, parsed.checks)
            if mismatch:
                errors.append(mismatch)
        checks.append(to_ledger_check(result))

    flags = [f"{c['name']}: {c['reason']}" for c in checks if c["status"] == "flag"]
    return GradeResult(ok=not errors, structural_errors=errors, result_summary=dict(parsed.result_summary),
                       checks=checks, flags=flags)
