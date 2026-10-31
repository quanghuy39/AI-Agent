"""Dien so vao van ban cua AI va kiem tra ban tong hop. Chi code, khong AI: moi con so lay tu result_summary.

Placeholder {{R3.field}}: field la MOT khoa cua result_summary, khong co khoa long nhau. Ly do: AgentMeta
(SummaryValue) chi cho gia tri don, va toan bo ket qua trong ho so that (bai0a, bai1, bai2) deu phang.
{{R3.a.b}} khong khop PLACEHOLDER_RE nen bi bat la placeholder con sot.
"""

import math
import re
from decimal import ROUND_HALF_UP, Context, Decimal

import config
from ledger import CheckStatus, ResultStatus

PLACEHOLDER_RE = re.compile(r"\{\{\s*(R\d+)\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
PERCENT_RE = re.compile(PLACEHOLDER_RE.pattern + r"\s*%")
IDENTIFIER_RE = re.compile(r"\b[RQID]\d+\b")  # ma dinh danh duoc phep co chu so
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*")
BRACE_RE = re.compile(r"[{}]")
NO_DATA = "không có dữ liệu"  # gia tri null hoac NaN trong result_summary
TEXT_FIELDS = ("summary", "conclusion", "limitations")
VERIFIED, HYPOTHESIS = "verified", "hypothesis"
_ROUNDING = Context(prec=400, rounding=ROUND_HALF_UP)  # du cho float lon nhat, quantize khong bao loi


# ---------- dien so ----------

def find_placeholders(text):
    """[(id ket qua, truong)] theo thu tu xuat hien."""
    return PLACEHOLDER_RE.findall(text)


def _vn_digits(formatted):
    """Doi dang 1,234.5 thanh 1.234,5."""
    return formatted.replace(",", "_").replace(".", ",").replace("_", ".")


def format_value(value):
    """Dinh dang kieu Viet Nam, bo so 0 thua, khong doi ty le sang %. |x| >= 1: toi da REPORT_MAX_DECIMALS chu so thap
    phan. 0 < |x| < 1: REPORT_SIG_DIGITS chu so co nghia."""
    if isinstance(value, bool):
        return "có" if value else "không"
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        return NO_DATA
    if isinstance(value, int):
        return _vn_digits(f"{value:,}")
    if isinstance(value, float):
        number = Decimal(str(value))
        small = 0 < abs(number) < 1
        step = Decimal(1).scaleb(number.adjusted() - config.REPORT_SIG_DIGITS + 1 if small
                                 else -config.REPORT_MAX_DECIMALS)
        rounded = number.quantize(step, context=_ROUNDING)
        text = f"{rounded:,f}"
        if "." in text:
            text = text.rstrip("0").rstrip(".")
        return "0" if text == "-0" else _vn_digits(text)
    return str(value)


def fill(text, results):
    """Thay placeholder bang gia tri trong result_summary; placeholder khong tra duoc thi giu nguyen."""
    by_id = {r.id: r for r in results}

    def value(match):
        result = by_id.get(match[1])
        if result is None or match[2] not in result.result_summary:
            return match[0]
        return format_value(result.result_summary[match[2]])

    return PLACEHOLDER_RE.sub(value, text)


# ---------- nhan ----------

def _lineage(result_ids, by_id):
    """Cac id trich dan va moi id trong chuoi depends_on cua chung (bac cau); seen chan vong."""
    seen, stack = set(), list(result_ids)
    while stack:
        rid = stack.pop()
        if rid not in seen:
            seen.add(rid)
            if rid in by_id:
                stack += by_id[rid].depends_on
    return seen


def _all_pass(result):
    return bool(result.checks) and all(c.status == CheckStatus.PASS for c in result.checks)


def assign_label(result_ids, results):
    """verified: co trich dan ket qua, va moi ket qua trich dan cung toan bo chuoi depends_on cua chung deu valid, moi
    check deu pass. Con lai hypothesis (ca khi khong trich dan ket qua nao, vi khong co gi lam can cu)."""
    valid = {r.id: r for r in results if r.status == ResultStatus.VALID}
    ok = bool(result_ids) and all(rid in valid and _all_pass(valid[rid]) for rid in _lineage(result_ids, valid))
    return VERIFIED if ok else HYPOTHESIS


# ---------- kiem tra ----------

def requirements_of(problem):
    """Danh sach yeu cau cua de: problem["requirements"] (cua ke hoach, dung so covers), khong co thi goals."""
    return problem.get("requirements") or (problem.get("restatement") or {}).get("goals") or []


def _is_percent_field(field):
    return any(hint in field.lower() for hint in config.REPORT_PERCENT_FIELD_HINTS)


def _text_errors(where, text):
    """Loi khong phu thuoc ket qua: chu so tu go, dau % sai cho, placeholder viet sai dang."""
    errors = []
    bare = PLACEHOLDER_RE.sub(" ", text)
    digits = NUMBER_RE.findall(IDENTIFIER_RE.sub(" ", bare))
    if digits:
        errors.append(f"{where}: có chữ số tự gõ ({', '.join(digits[:3])}); phải dùng placeholder {{{{Rx.field}}}}.")
    # chi % ngay sau truong phan tram la hop le: bo cum do roi con % nao la sai
    rest = PERCENT_RE.sub(lambda m: " " if _is_percent_field(m[2]) else m[0], text)
    if "%" in rest:
        errors.append(f"{where}: dấu % chỉ được viết ngay sau placeholder có tên trường chứa pct hoặc percent.")
    if BRACE_RE.search(bare):  # placeholder dung dang nhung khong tra duoc da bao o ham duoi
        errors.append(f"{where}: còn placeholder sai dạng; chỉ dùng dạng {{{{Rx.field}}}}.")
    return errors


def _placeholder_errors(where, text, allowed, valid, scope):
    errors = []
    for rid, field in find_placeholders(text):
        if rid not in allowed:
            errors.append(f"{where}: {{{{{rid}.{field}}}}} trỏ tới {rid} không nằm trong {scope}.")
        elif rid in valid and field not in valid[rid].result_summary:
            errors.append(f"{where}: {rid} không có trường {field}; "
                          f"các trường có: {', '.join(valid[rid].result_summary) or '(không có)'}.")
    return errors


def validate_synthesis(output, problem, results):
    """Danh sach loi (cau ngan, gui lai duoc cho AI); rong la dat. results: moi ket qua trong so, ke ca khong valid."""
    errors = []
    known = {r.id: r for r in results}
    valid = {r.id: r for r in results if r.status == ResultStatus.VALID}
    for ins in output.insights:
        for rid in ins.result_ids:
            if rid not in known:
                errors.append(f"{ins.id}: result_ids có {rid} không tồn tại.")
            elif rid not in valid:
                errors.append(f"{ins.id}: {rid} có status={known[rid].status.value}, chỉ được dùng kết quả valid.")
        errors += _placeholder_errors(ins.id, ins.text, ins.result_ids, valid, "result_ids của insight này")
        errors += _text_errors(ins.id, ins.text)

    cited = {rid for ins in output.insights for rid in ins.result_ids}
    for name in TEXT_FIELDS:
        text = getattr(output, name)
        errors += _placeholder_errors(name, text, cited, valid, "result_ids của insight nào")
        errors += _text_errors(name, text)

    n = len(requirements_of(problem))
    for ins in output.insights:
        if not 1 <= ins.requirement <= n:
            errors.append(f"{ins.id}: requirement {ins.requirement} ngoài khoảng 1..{n}.")
    covered = {ins.requirement for ins in output.insights}
    errors += [f"Yêu cầu {i} chưa có insight nào." for i in range(1, n + 1) if i not in covered]
    return errors
