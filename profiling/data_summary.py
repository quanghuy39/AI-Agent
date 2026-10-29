"""Tom tat du lieu gui cho AI, dung tu data_quality_report (KHONG doc R2, KHONG doc du lieu tho).

Chay: python -m profiling.data_summary <problem_id> [--sensitive BANG.cot ...] [--max-chars N] [--no-samples] [--refresh]

Van ban markdown gon, nhan tieng Anh, giu nguyen ten bang/cot/dinh nghia goc; cung report thi cung van ban.
Cot nhay cam (dictionary "(SENSITIVE)", tham so sensitive, tu dong email/phone) chi giu ten, kieu, null, unique,
cau truc, co; gia tri trong ban ghi mau thay bang llm.anonymize. Khong bao gio dua vao: examples cua validators,
date_invalid_examples, case_variant_examples. Luoi an toan cuoi: gia tri goc cua cot bi che lot vao van ban
-> SensitiveLeakError (tru khi gia tri do cung la gia tri cua cot khong bi che: chi canh bao).
"""

import argparse
import math
import re
import sys

import pandas as pd

import config
from ledger import Ledger
from profiling import flags as fl
from profiling import quality

SUMMARY_KEY = "ai_summary"
MIN_LEAK_CHARS = 3
# Cac buoc cat khi vuot ngan sach: (muc, thay doi so voi buoc truoc). Muc 5 khong bao gio bo them gi.
_STEPS = [
    (0, {}),
    (1, {"samples": 1}), (1, {"samples": 0}),
    (2, {"top": 5}), (2, {"top": 0}),
    (3, {"defs": 40}), (3, {"defs": 0}),
    (4, {"detail": False}),
    (5, {"minimal": True}),
]


class SensitiveLeakError(Exception):
    """Gia tri goc cua cot bi che xuat hien trong ban tom tat (thong bao khong chua gia tri)."""


# ---------- cot bi che ----------

def resolve_masked_columns(report, sensitive=None):
    """Gop 3 nguon: dictionary (SENSITIVE), tham so sensitive ["BANG.cot"], tu dong (pii_like), roi lan theo
    relationship_checks khong bi loai (ly do "relationship"). Tra ve ({(bang, cot): [ly do]}, canh bao). Bang/cot trong sensitive khong ton tai -> ValueError."""
    tables = report["tables"]
    dict_tables = (report.get("dictionary") or {}).get("tables") or {}
    reasons = {}

    def add(table, col, why):
        reasons.setdefault((table, col), [])
        if why not in reasons[(table, col)]:
            reasons[(table, col)].append(why)

    for name, t in tables.items():
        check = t.get("dictionary_check")
        if check and check.get("dictionary_table") in dict_tables:
            by_norm = {c.strip().lower(): c for c in t["columns"]}
            for c in dict_tables[check["dictionary_table"]]["columns"]:
                if c.get("sensitive") and c["name"].strip().lower() in by_norm:
                    add(name, by_norm[c["name"].strip().lower()], "dictionary")
    for spec in sensitive or []:
        add(*quality.resolve_table_column(spec, {n: list(t["columns"]) for n, t in tables.items()}, "sensitive"),
            "argument")

    warnings = []
    limit = config.SENSITIVE_AUTO_RATIO
    for name, t in tables.items():
        dict_keys = fl.dict_key_columns(report, t)
        for col, info in t["columns"].items():
            pii = info.get("pii_like") or {}
            kinds = []
            if (pii.get("email_ratio") or 0) >= limit:
                kinds.append("email")
            if pii.get("phone_ratio") is not None and pii["phone_ratio"] >= limit \
                    and not fl.is_key_column(t, col, dict_keys, config.KEY_NAME_HINTS):
                kinds.append("phone")
            for kind in kinds:
                add(name, col, f"auto_{kind}")
                warnings.append(f"{name}.{col}: nghi ngờ nhạy cảm ({kind}), đã che tự động; "
                                f"xác nhận bằng (SENSITIVE) trong dictionary hoặc --sensitive")

    # Lan theo quan he (tru quan he bi loai): mot dau bi che thi dau kia cung bi che; lap toi khi het doi (bac cau).
    links = [((c["child"], c["child_col"]), (c["parent"], c["parent_col"]))
             for c in report.get("relationship_checks") or [] if c["status"] != "rejected"]
    changed = True
    while changed:
        changed = False
        for a, b in links:
            for src, dst in ((a, b), (b, a)):
                if src in reasons and dst not in reasons:
                    add(*dst, "relationship")
                    changed = True
    order = {(n, c): i for i, (n, c) in enumerate((n, c) for n, t in tables.items() for c in t["columns"])}
    masked = dict(sorted(reasons.items(), key=lambda kv: order[kv[0]]))
    return masked, warnings


# ---------- dinh dang ----------

def _cell(value, limit=None):
    limit = config.SUMMARY_CELL_MAX_CHARS if limit is None else limit
    text = "" if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)
    text = re.sub(r"\s+", " ", text).replace("|", "\\|")
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _val(v):
    if isinstance(v, float):
        return f"{v:.4g}"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join("-" if x is None else _val(x) for x in v) + "]"
    return str(v)


def _pct(x):
    return f"{x:.1%}"


_JOIN_FLAG_CODES = ("join_right_unmatched", "join_unmatched", "join_right_duplicate_keys")


def _join_flag_sources(report):
    """{(code, bang, cot): [quan he]} theo thu tu relationship_checks (cung thu tu build_flags sinh co),
    de dong co quan he ghi ro chieu cha/con."""
    out = {}
    for c in report.get("relationship_checks") or []:
        if c["status"] == "rejected":
            continue
        d = c["result"]["details"]
        if d["right_unmatched"]:
            out.setdefault(("join_right_unmatched", c["parent"], c["parent_col"]), []).append(c)
        if "unmatched" in d["flags"]:
            out.setdefault(("join_unmatched", c["child"], c["child_col"]), []).append(c)
        if "right_duplicate_keys" in d["flags"]:
            out.setdefault(("join_right_duplicate_keys", c["parent"], c["parent_col"]), []).append(c)
    return out


def _join_flag_text(code, c):
    d = c["result"]["details"]
    child, parent = f"{c['child']}.{c['child_col']}", f"{c['parent']}.{c['parent_col']}"
    if code == "join_right_unmatched":
        return f"{parent}: {d['right_unmatched']} rows not referenced by {child}"
    if code == "join_unmatched":
        return (f"{child}: {d['left_unmatched_raw']} rows ({_pct(d['unmatched_ratio'])}) with no match in {parent}, "
                f"{d['recoverable']} recoverable by normalizing keys")
    return f"{parent}: {d['right_duplicate_keys']} duplicate keys, join from {child} adds {d['extra_rows']} rows"


def _derived_missing(report):
    """{(bang, cot): derived_hint} cua cac cot dictionary thieu trong du lieu."""
    return {(name, m["column"]): m["derived_hint"] for name, t in report["tables"].items()
            for m in (t.get("dictionary_check") or {}).get("missing_in_data", [])}


def _flag_line(f, join_sources=None, derived=None):
    """Dung lai dong co tu truong co cau truc (khong dung message tieng Viet).
    join_sources: _join_flag_sources (lay ra dan, 1 co ung 1 quan he); derived: _derived_missing."""
    key = (f["code"], f.get("table"), f.get("column"))
    if f["code"] in _JOIN_FLAG_CODES and (join_sources or {}).get(key):
        return f"- {f['code']} " + _join_flag_text(f["code"], join_sources[key].pop(0))
    loc = ".".join(p for p in (f.get("table"), f.get("column")) if p)
    text = f"- {f['code']}" + (f" {loc}" if loc else "")
    if f["code"] == "dict_missing_column" and (derived or {}).get(key[1:]):
        text += " (derived)"
    extras = []
    if f.get("value") is not None:
        extras.append(f"value {_val(f['value'])}")
    if f.get("threshold") is not None:
        extras.append(f"threshold {_val(f['threshold'])}")
    return text + (f": {', '.join(extras)}" if extras else "")


def _definitions(report, t):
    """{cot du lieu: dinh nghia trong dictionary}."""
    check = t.get("dictionary_check")
    dict_tables = (report.get("dictionary") or {}).get("tables") or {}
    if not check or check.get("dictionary_table") not in dict_tables:
        return {}
    by_norm = {c.strip().lower(): c for c in t["columns"]}
    return {by_norm[c["name"].strip().lower()]: c["definition"]
            for c in dict_tables[check["dictionary_table"]]["columns"]
            if c["name"].strip().lower() in by_norm and c.get("definition")}


def _column_line(col, info, opts, masked, is_key, definition, key_like=False):
    """key_like: cot khoa/noi bang (is_key_column). Phan pattern chi in khi cau truc chinh ro rang,
    hoac cot khoa/noi bang, hoac cot bi che."""
    head = f"- {col}" + (" [masked]" if masked else "") + (" [key]" if is_key else "")
    kind = info["inferred_type"]
    if info.get("date_format"):
        kind += f" {info['date_format']}"
    if "integer_stored_as_float" in info.get("notes", []):
        kind += " (integer_stored_as_float)"
    if opts["minimal"] or not opts["detail_col"]:
        return f"{head}: {kind}"
    parts = [kind, f"nulls {info['null_count']}" + (f" ({_pct(info['null_ratio'])})" if info["null_count"] else ""),
             f"unique {info['n_unique']}"]
    if not masked:
        if kind.startswith(("integer", "float")) and info.get("min") is not None:
            parts.append(", ".join(f"{k} {quality._num(info.get(k))}" for k in ("min", "max", "p01", "p99")))
        elif info["inferred_type"] in ("date", "datetime") and info.get("min"):
            parts.append(f"range {info['min']}..{info['max']}")
        top = info.get("top_values")
        if opts["include_samples"] and opts["top"] != 0 and top \
                and info["n_unique"] <= config.TOP_VALUES_MAX_UNIQUE:
            shown = top if opts["top"] is None else top[:opts["top"]]
            parts.append("top " + ", ".join(f"{_cell(v['value'])} ({v['count']})" for v in shown))
    clear = (info.get("pattern_dominant_ratio") or 0) >= 1 - config.MINORITY_PATTERN_RATIO
    if info.get("pattern_dominant") and (clear or key_like or masked):
        parts.append(f"pattern {info['pattern_dominant']} ({_pct(info['pattern_dominant_ratio'])}, "
                     f"other {info['pattern_other_count']}, {info['pattern_n_distinct']} patterns)")
    if definition and opts["defs"] != 0:
        limit = config.SUMMARY_DEFINITION_MAX_CHARS if opts["defs"] is None else opts["defs"]
        parts.append(f'def "{_cell(definition, limit)}"')
    return f"{head}: " + "; ".join(parts)


def _mask_groups(report, masked):
    """Nhom cac cot bi che noi voi nhau qua relationship_checks khong rejected (ke ca bac cau), nhom >= 2 cot.
    Tra ve [{"members": [(bang, cot)] theo thu tu report, "prefix": ten cot cua bang cha dau tien trong nhom}]."""
    parent = {m: m for m in masked}

    def find(x):
        while parent[x] != x:
            x = parent[x]
        return x

    parent_ends = set()
    for c in report.get("relationship_checks") or []:
        a, b = (c["child"], c["child_col"]), (c["parent"], c["parent_col"])
        if c["status"] != "rejected" and a in parent and b in parent:
            parent[find(a)] = find(b)
            parent_ends.add(b)
    groups = {}
    for m in masked:  # masked da theo thu tu bang/cot cua report
        groups.setdefault(find(m), []).append(m)
    out = []
    for members in groups.values():
        if len(members) > 1:
            prefix = next((col for (t, col) in members if (t, col) in parent_ends), members[0][1])
            out.append({"members": members, "prefix": prefix})
    return out


def _group_codes(report, groups, n):
    """Mot bang ma chung cho moi nhom: gom gia tri mau cua cac cot trong nhom (bang theo thu tu report, roi theo
    dong), goi anonymize mot lan. Tra ve {(bang, cot): [ma theo dong mau]}."""
    if not groups:
        return {}
    from llm.anonymize import anonymize  # lazy: llm keo theo litellm, chi tai khi can
    out = {}
    for g in groups:
        values, slots = [], []
        for name, t in report["tables"].items():
            cols = [c for (tn, c) in g["members"] if tn == name]
            for i, r in enumerate((t.get("sample_records") or [])[:n]):
                for c in cols:
                    values.append(r.get(c))
                    slots.append((name, c, i))
        if not values:
            continue
        coded = anonymize(pd.DataFrame({g["prefix"]: pd.Series(values, dtype=object)}), mask=[g["prefix"]])
        for (name, c, i), code in zip(slots, coded[g["prefix"]].tolist()):
            out.setdefault((name, c), {})[i] = code
    return out


def _samples(t, masked_cols, n, group_codes=None):
    """group_codes: {cot: {dong: ma}} cua cac cot thuoc nhom quan he (bang ma chung); cot bi che con lai
    duoc anonymize rieng trong bang nay."""
    group_codes = group_codes or {}
    records = (t.get("sample_records") or [])[:n]
    if not records:
        return []
    cols = list(t["columns"])
    df = pd.DataFrame(records).reindex(columns=cols).astype(object)
    mask = [c for c in cols if c in masked_cols and c not in group_codes]
    if mask:
        from llm.anonymize import anonymize  # lazy: llm keo theo litellm, chi tai khi can
        df = anonymize(df, mask=mask)
    for c, codes in group_codes.items():
        df[c] = pd.Series([codes.get(i) for i in range(len(df))], index=df.index, dtype=object)
    lines = [f"sample ({len(records)}):", "| " + " | ".join(_cell(c) for c in cols) + " |",
             "|" + "---|" * len(cols)]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join(_cell(v) for v in row) + " |")
    return lines


def _relationship_lines(report, minimal):
    lines = []
    for c in report.get("relationship_checks") or []:
        head = f"- {c['child']}.{c['child_col']} -> {c['parent']}.{c['parent_col']}: "
        if c["status"] == "rejected":
            lines.append(head + f"rejected (overlap {_pct(c['result']['overlap'])})")
            continue
        d = c["result"]["details"]
        parts = []
        if d["left_unmatched_raw"]:
            parts.append(f"unmatched {d['left_unmatched_raw']} ({_pct(d['unmatched_ratio'])}), "
                         f"recoverable {d['recoverable']}")
        if d["right_duplicate_keys"]:
            parts.append(f"parent duplicate keys {d['right_duplicate_keys']} (+{d['extra_rows']} rows)")
        if d["key_type_mismatch"]:
            parts.append("key type mismatch")
        if d["left_null_keys"]:
            parts.append(f"null keys {d['left_null_keys']}")
        lines.append(head + ("; ".join(parts) if parts else "all matched"))
    if not minimal:
        for n in report.get("relationship_notes") or []:
            if n["code"] == "ambiguous_direction":
                lines.append(f"- {n['table']}.{n['column']} <-> {n['other_table']}.{n['other_column']}: "
                             f"ambiguous direction (skipped)")
            elif n["code"] == "fk_without_reference":
                lines.append(f"- {n['table']}.{n['column']}: FK without reference")
    return lines


def _render(report, masked, opts):
    tables = report["tables"]
    d = report.get("dictionary") or {}
    status = d.get("status", "none")
    dict_text = status + (f" ({d['file'].rsplit('/', 1)[-1]})" if d.get("file") else "")
    lines = [f"# Data summary: {report['problem_id']}", f"dictionary: {dict_text}; tables: {len(tables)}"]
    all_flags = list(report.get("quality_flags") or [])
    groups = _mask_groups(report, masked)
    codes_by_n = {}

    for name, t in tables.items():
        all_flags += t.get("quality_flags") or []
        key = ", ".join(t["key_cols"]) if t.get("key_cols") else "none"
        source = f" [{t['key_source']}]" if t.get("key_source") else ""
        lines += ["", f"## {name} (rows {t['n_rows']}; key {key}{source})"]
        flagged = {f["column"] for f in t.get("quality_flags") or [] if f.get("column")}
        defs = _definitions(report, t)
        masked_cols = {c for (tn, c) in masked if tn == name}
        dict_keys = fl.dict_key_columns(report, t)
        for col, info in t["columns"].items():
            is_key = col in (t.get("key_cols") or [])
            detail = opts["detail"] or is_key or col in flagged or info["inferred_type"] in ("date", "datetime")
            key_like = fl.is_key_column(t, col, dict_keys, config.KEY_NAME_HINTS)
            lines.append(_column_line(col, info, {**opts, "detail_col": detail}, col in masked_cols, is_key,
                                      defs.get(col), key_like=key_like))
        check = t.get("dictionary_check")
        if check and check["missing_in_data"] and not opts["minimal"]:
            lines.append("missing in data: " + ", ".join(
                m["column"] + (" (derived)" if m["derived_hint"] else "") for m in check["missing_in_data"]))
        if opts["include_samples"] and not opts["minimal"]:
            n = opts["sample_n"] if opts["samples"] is None else min(opts["samples"], opts["sample_n"])
            if n not in codes_by_n:
                codes_by_n[n] = _group_codes(report, groups, n)
            table_codes = {c: v for (tn, c), v in codes_by_n[n].items() if tn == name}
            lines += _samples(t, masked_cols, n, table_codes)

    rel = _relationship_lines(report, opts["minimal"])
    if rel:
        lines += ["", "## Relationships"] + rel
    for title, sev in (("Flags", "flag"), ("Notes", "info")):
        items = [f for f in all_flags if f["severity"] == sev]
        if items and not (opts["minimal"] and sev == "info"):
            join_sources = _join_flag_sources(report)
            derived = _derived_missing(report)
            lines += ["", f"## {title} ({len(items)})"] + [_flag_line(f, join_sources, derived) for f in items]
    return "\n".join(lines) + "\n"


# ---------- luoi an toan ----------

def _column_values(t, col):
    values = [r.get(col) for r in t.get("sample_records") or []]
    values += [v["value"] for v in (t["columns"][col].get("top_values") or [])]
    return {str(v) for v in values if v is not None and not (isinstance(v, float) and math.isnan(v))}


def _safety_net(report, masked, text):
    """Gia tri goc (>= 3 ky tu) cua cot bi che co trong van ban -> SensitiveLeakError, tru khi no cung la gia tri
    cua cot khong bi che (khi do chi canh bao, khong kem gia tri)."""
    unmasked = {}
    for name, t in report["tables"].items():
        for col in t["columns"]:
            if (name, col) not in masked:
                for v in _column_values(t, col):
                    unmasked.setdefault(v.lower(), set()).add(f"{name}.{col}")
    warnings = {}
    for (name, col) in masked:
        for v in sorted(_column_values(report["tables"][name], col)):
            if len(v) < MIN_LEAK_CHARS or not re.search(rf"(?<!\w){re.escape(v)}(?!\w)", text, re.IGNORECASE):
                continue
            others = unmasked.get(v.lower())
            if not others:
                raise SensitiveLeakError(f"Giá trị của cột bị che {name}.{col} lọt vào bản tóm tắt")
            for other in sorted(others):
                warnings[(f"{name}.{col}", other)] = warnings.get((f"{name}.{col}", other), 0) + 1
    return [f"{n} giá trị của {src} cũng xuất hiện ở cột không bị che {other}"
            for (src, other), n in warnings.items()]


# ---------- ham chinh ----------

def build_data_summary(report, sensitive=None, max_chars=None, sample_n=None, include_samples=True):
    """Ban tom tat cho AI tu data_quality_report. Tra ve dict: text, chars, est_tokens, level, truncated,
    masked_columns, warnings, settings. include_samples=False: khong ban ghi mau, khong top values."""
    max_chars = config.SUMMARY_MAX_CHARS if max_chars is None else max_chars
    sample_n = config.SUMMARY_SAMPLE_N if sample_n is None else sample_n
    masked, warnings = resolve_masked_columns(report, sensitive)

    opts = {"samples": None, "top": None, "defs": None, "detail": True, "minimal": False,
            "include_samples": include_samples, "sample_n": sample_n}
    truncated = False
    for level, change in _STEPS:
        opts.update(change)
        text = _render(report, masked, opts)
        if len(text) <= max_chars:
            break
    else:
        truncated = True
        warnings.append(f"Vượt ngân sách: {len(text)} > {max_chars} ký tự ngay cả ở mức cắt 5")

    warnings += _safety_net(report, masked, text)
    return {
        "text": text,
        "chars": len(text),
        "est_tokens": math.ceil(len(text) / 4),
        "level": level,
        "truncated": truncated,
        "masked_columns": [{"table": t, "column": c, "reason": ", ".join(r)} for (t, c), r in masked.items()],
        "warnings": warnings,
        "settings": {"max_chars": max_chars, "sample_n": sample_n, "include_samples": include_samples},
    }


def main(argv=None):
    p = argparse.ArgumentParser(description="Tóm tắt dữ liệu của một bài để gửi cho AI.")
    p.add_argument("problem_id")
    p.add_argument("--sensitive", action="append", default=[], metavar="BANG.COT", help="cột nhạy cảm (lặp được)")
    p.add_argument("--max-chars", type=int, default=None, help=f"ngân sách ký tự (mặc định {config.SUMMARY_MAX_CHARS})")
    p.add_argument("--no-samples", action="store_true", help="không có bản ghi mẫu và top values")
    p.add_argument("--refresh", action="store_true", help="chạy lại profile_problem trước")
    args = p.parse_args(argv)
    try:
        quality._check_problem_id(args.problem_id)
    except ValueError as e:
        p.error(str(e))

    led = Ledger(quality.OUTPUT_DIR / args.problem_id)
    profile = led.get_data_profile()
    report = profile.get(quality.REPORT_KEY) if isinstance(profile, dict) else None
    if report is None or args.refresh:
        report = quality.profile_problem(args.problem_id, include_samples=not args.no_samples)
    try:
        summary = build_data_summary(report, sensitive=args.sensitive, max_chars=args.max_chars,
                                     include_samples=not args.no_samples)
    except ValueError as e:
        p.error(str(e))
    except SensitiveLeakError as e:
        print(f"LỖI: {e}. Không ghi bản tóm tắt.", file=sys.stderr)
        return 2

    profile = led.get_data_profile()
    profile = profile if isinstance(profile, dict) else {}
    profile[SUMMARY_KEY] = summary
    led.set_data_profile(profile)

    print(summary["text"])
    masked = ", ".join(f"{m['table']}.{m['column']} ({m['reason']})" for m in summary["masked_columns"]) or "không"
    stats = f"{summary['chars']} ký tự, ~{summary['est_tokens']} token, level {summary['level']}" \
            + (" (truncated)" if summary["truncated"] else "") + f"; che: {masked}"
    print(stats)
    for w in summary["warnings"]:
        print(f"Cảnh báo: {w}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
