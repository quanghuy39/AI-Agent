"""Co chat luong du lieu theo nguong, sinh tu bao cao cua profile_problem. Chi BAO CAO.

Moi co: code, severity ("flag" can xem | "info" luu y), table, column, message, value, threshold.
message KHONG chua gia tri that cua du lieu (chi ten bang/cot, so dem, ty le, ten cau truc dang AAAA999999)
vi cac co ve sau duoc dua vao ban tom tat gui AI.
"""

import config
from profiling import quality  # quality cung import module nay: chi dung quality.name_words luc goi ham

FLAG, INFO = "flag", "info"
# Ten hang trong config.py ma build_flags doc; thresholds chi duoc ghi de cac khoa nay.
THRESHOLD_KEYS = ("NULL_RATIO_FLAG", "MINORITY_PATTERN_RATIO", "KEY_NAME_HINTS",
                  "JOIN_UNMATCHED_FLAG_RATIO", "RELATIONSHIP_MIN_OVERLAP")
DATE_COLUMN_FLAGS = {
    "mixed_date_formats": "trộn nhiều định dạng ngày",
    "ambiguous_day_month": "không phân biệt được ngày/tháng",
    "date_like_unknown_format": "giống ngày nhưng định dạng không nhận ra",
}


def resolve_thresholds(thresholds=None):
    """Gia tri nguong dang dung: hang trong config.py, ghi de bang thresholds. Khoa la -> ValueError."""
    thresholds = dict(thresholds or {})
    unknown = sorted(k for k in thresholds if k not in THRESHOLD_KEYS)
    if unknown:
        raise ValueError(f"Khóa ngưỡng không hợp lệ: {unknown}. Khóa hợp lệ: {list(THRESHOLD_KEYS)}")
    return {k: thresholds.get(k, getattr(config, k)) for k in THRESHOLD_KEYS}


def _flag(code, severity, table, column, message, value=None, threshold=None):
    return {"code": code, "severity": severity, "table": table, "column": column,
            "message": message, "value": value, "threshold": threshold}


def _pct(x):
    return f"{x:.1%}"


def _dict_key_columns(report, table_report):
    """Ten cot (chuan hoa) co PK/FK trong bang dictionary da ghep voi bang nay."""
    check = table_report.get("dictionary_check")
    tables = (report.get("dictionary") or {}).get("tables") or {}
    if not check or check.get("dictionary_table") not in tables:
        return set()
    return {c["name"].strip().lower() for c in tables[check["dictionary_table"]]["columns"] if c.get("key")}


def is_key_column(table_report, column, dict_key_columns, hints):
    """Cot khoa/noi bang: PK/FK trong dictionary, nam trong key_cols, hoac ten co tu thuoc hints."""
    if column.strip().lower() in dict_key_columns or column in (table_report.get("key_cols") or []):
        return True
    words = set(quality.name_words(column))
    return any(h.lower() in words for h in hints)


def _table_flags(name, t, file_flags, dict_key_columns, th):
    out = []
    add = out.append
    if t["n_rows"] == 0:
        add(_flag("empty_table", FLAG, name, None, f"{name}: bảng không có dòng nào", 0))
    if "suspect_wrong_delimiter" in file_flags:
        add(_flag("suspect_wrong_delimiter", FLAG, name, None,
                  f"{name}: nghi đọc sai dấu phân cách (chỉ ra 1 cột)", t["n_cols"]))
    keys = ", ".join(t.get("key_cols") or [])
    for code, field, text in (("duplicate_key", "duplicate_key_count", "khóa trùng"),
                              ("key_conflict", "key_conflict_count", "khóa có nhiều dòng khác nội dung"),
                              ("null_key_rows", "null_key_rows", "dòng thiếu khóa")):
        n = t.get(field)
        if n:
            add(_flag(code, FLAG, name, None, f"{name}: {n} {text} ({keys})", n, 0))
    if t.get("exact_duplicate_rows"):
        n = t["exact_duplicate_rows"]
        add(_flag("exact_duplicate_rows", FLAG, name, None, f"{name}: {n} dòng trùng y hệt", n, 0))
    if t["n_rows"] == 0:
        return out  # bang rong: khong xet co theo cot

    for col, i in t["columns"].items():
        where = f"{name}.{col}"
        key = is_key_column(t, col, dict_key_columns, th["KEY_NAME_HINTS"])
        kind = "cột khóa" if key else "cột"
        sev = FLAG if key else INFO

        if i["null_count"] >= t["n_rows"]:
            add(_flag("all_null", FLAG, name, col, f"{where}: cột toàn null", i["null_count"]))
        elif i["null_count"]:
            limit = th["NULL_RATIO_FLAG"]
            code, s = ("null_ratio_high", FLAG) if i["null_ratio"] >= limit else ("nulls_present", INFO)
            add(_flag(code, s, name, col, f"{where}: {i['null_count']} null ({_pct(i['null_ratio'])})",
                      i["null_ratio"], limit))

        ratio = i.get("pattern_dominant_ratio")
        min_dominant = 1 - th["MINORITY_PATTERN_RATIO"]
        if i.get("pattern_other_count") and ratio is not None and ratio >= min_dominant:
            add(_flag("pattern_deviation", sev, name, col,
                      f"{where} ({kind}): {i['pattern_other_count']} giá trị khác cấu trúc chính "
                      f"{i['pattern_dominant']} ({i['pattern_n_distinct']} cấu trúc)",
                      i["pattern_other_count"], round(min_dominant, 4)))
        if i.get("whitespace_count"):
            add(_flag("whitespace", sev, name, col,
                      f"{where} ({kind}): {i['whitespace_count']} giá trị có khoảng trắng thừa",
                      i["whitespace_count"], 0))
        if i.get("case_variant_groups"):
            add(_flag("case_variants", INFO, name, col,
                      f"{where}: {i['case_variant_groups']} nhóm giá trị chỉ khác hoa/thường",
                      i["case_variant_groups"], 0))
        if i["n_unique"] == 1 and t["n_rows"] > 1:
            add(_flag("constant_column", INFO, name, col, f"{where}: chỉ có 1 giá trị khác nhau", 1))

        if i["inferred_type"] in ("date", "datetime") and i.get("date_invalid_count"):
            add(_flag("date_invalid", FLAG, name, col,
                      f"{where}: {i['date_invalid_count']} giá trị ngày không đọc được",
                      i["date_invalid_count"], 0))
        for code, text in DATE_COLUMN_FLAGS.items():
            if code in i.get("flags", []):
                add(_flag(code, FLAG, name, col, f"{where}: {text}"))
    return out


def _dictionary_flags(name, t):
    d = t.get("dictionary_check")
    if not d:
        return []
    out = []
    for m in d["missing_in_data"]:
        col = m["column"]
        if m["derived_hint"]:
            out.append(_flag("dict_missing_column", INFO, name, col,
                             f"{name}.{col}: có trong dictionary, không có trong dữ liệu (dictionary nói là cột phải tính)"))
        else:
            out.append(_flag("dict_missing_column", FLAG, name, col,
                             f"{name}.{col}: có trong dictionary, không có trong dữ liệu"))
    for col in d["undocumented_in_dictionary"]:
        out.append(_flag("dict_undocumented_column", INFO, name, col, f"{name}.{col}: không có trong dictionary"))
    for m in d["type_mismatches"]:
        out.append(_flag("dict_type_mismatch", FLAG, name, m["column"],
                         f"{name}.{m['column']}: dictionary ghi {m['dictionary_type']}, dữ liệu là {m['inferred_type']}",
                         m["inferred_type"], m["dictionary_type"]))
    for m in d["date_format_mismatches"]:
        out.append(_flag("dict_date_format_mismatch", FLAG, name, m["column"],
                         f"{name}.{m['column']}: dictionary ghi định dạng {m['dictionary_format']}, "
                         f"dữ liệu là {m['data_format']}", m["data_format"], m["dictionary_format"]))
    if d["pk_missing"]:
        out.append(_flag("dict_pk_missing", FLAG, name, None,
                         f"{name}: PK trong dictionary không có trong dữ liệu ({', '.join(d['pk_missing'])})",
                         len(d["pk_missing"])))
    return out


def _problem_flags(report):
    """Co khong gan voi bang du lieu nao (table = None hoac ten bang trong dictionary)."""
    d = report.get("dictionary") or {}
    out = []
    if d.get("status") == "error":
        out.append(_flag("dictionary_error", FLAG, None, None,
                         f"Không đọc được dictionary {d.get('file')} ({len(d.get('parse_warnings') or [])} cảnh báo)"))
    elif d.get("status") == "unsupported":
        out.append(_flag("dictionary_unsupported", FLAG, None, None,
                         f"Dictionary {d.get('file')} có định dạng không hỗ trợ (chỉ đọc .md/.csv)"))
    for tname in d.get("tables_without_file") or []:
        out.append(_flag("dict_table_without_file", INFO, tname, None,
                         f"Bảng {tname} có trong dictionary nhưng không có file"))
    return out


def _relationship_flags(report):
    """Co tu relationship_checks, relationship_notes va range_checks (ket qua da tinh trong profile_problem)."""
    out = []
    for c in report.get("relationship_checks") or []:
        child, ccol, parent, pcol = c["child"], c["child_col"], c["parent"], c["parent_col"]
        link = f"{child}.{ccol} -> {parent}.{pcol}"
        if c["status"] == "rejected":
            ov = c["result"]["overlap"]
            out.append(_flag("relationship_rejected", INFO, child, ccol,
                             f"{link}: tên cột trùng nhưng giá trị không giao nhau (overlap {_pct(ov)}), bỏ qua",
                             ov, c["result"]["min_overlap"]))
            continue
        d = c["result"]["details"]
        if "right_duplicate_keys" in d["flags"]:
            out.append(_flag("join_right_duplicate_keys", FLAG, parent, pcol,
                             f"{parent}.{pcol}: {d['right_duplicate_keys']} khóa trùng, ghép từ {child}.{ccol} "
                             f"tăng {d['extra_rows']} dòng", d["right_duplicate_keys"], 0))
        if "unmatched" in d["flags"]:
            out.append(_flag("join_unmatched", FLAG, child, ccol,
                             f"{link}: {d['left_unmatched_raw']} dòng không khớp ({_pct(d['unmatched_ratio'])}), "
                             f"{d['recoverable']} dòng khớp được nếu chuẩn hóa khóa",
                             d["unmatched_ratio"], d["unmatched_threshold"]))
        if d["key_type_mismatch"]:
            types = ", ".join(f"{t['left']}/{t['right']}" for t in d["key_types"])
            out.append(_flag("join_key_type_mismatch", FLAG, child, ccol, f"{link}: khác nhóm kiểu khóa ({types})"))
        if d["right_unmatched"]:
            out.append(_flag("join_right_unmatched", INFO, parent, pcol,
                             f"{parent}.{pcol}: {d['right_unmatched']} dòng không có trong {child}.{ccol}",
                             d["right_unmatched"]))
    for n in report.get("relationship_notes") or []:
        if n["code"] == "ambiguous_direction":
            out.append(_flag("relationship_ambiguous_direction", INFO, n["table"], n["column"],
                             f"{n['table']}.{n['column']} và {n['other_table']}.{n['other_column']}: cùng là khóa "
                             f"ở hai bảng, không rõ hướng quan hệ"))
        elif n["code"] == "fk_without_reference":
            out.append(_flag("fk_without_reference", INFO, n["table"], n["column"],
                             f"{n['table']}.{n['column']}: dictionary ghi FK nhưng không ghép được bảng nào"))
    for r in report.get("range_checks") or []:
        res = r["result"]
        if res["status"] == "flag":
            d = res["details"]
            out.append(_flag("range_violation", FLAG, r["table"], r["column"], res["reason"],
                             d["out_ratio"], [r["min"], r["max"]]))
    return out


def build_flags(report, thresholds=None):
    """Sinh danh sach co tu bao cao profile_problem. thresholds: ghi de hang trong config.py (THRESHOLD_KEYS).
    Cac nguong cua quan he (JOIN_UNMATCHED_FLAG_RATIO, RELATIONSHIP_MIN_OVERLAP) da dung khi profile_problem
    chay join_check; o day chi doc ket qua."""
    th = resolve_thresholds(thresholds)
    file_flags = {f["table"]: f.get("flags") or [] for f in report.get("files") or []}
    not_in_dict = set((report.get("dictionary") or {}).get("files_without_dictionary") or [])
    flags = _problem_flags(report)
    for name, t in report["tables"].items():
        flags += _table_flags(name, t, file_flags.get(name, []), _dict_key_columns(report, t), th)
        if name in not_in_dict:
            flags.append(_flag("not_in_dictionary", FLAG, name, None, f"{name}: file không có trong dictionary"))
        flags += _dictionary_flags(name, t)
    return flags + _relationship_flags(report)


def summarize_flags(flags, table_names):
    """{flag: n, info: n, by_table: {ten bang du lieu: {flag, info}}}."""
    by_table = {n: {FLAG: 0, INFO: 0} for n in table_names}
    total = {FLAG: 0, INFO: 0}
    for f in flags:
        total[f["severity"]] += 1
        if f["table"] in by_table:
            by_table[f["table"]][f["severity"]] += 1
    return {**total, "by_table": by_table}
