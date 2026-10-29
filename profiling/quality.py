"""Profiling tu dong: chat luong du lieu tung bang cua mot bai. Chi BAO CAO, khong doi kieu du lieu.

Chay: python -m profiling.quality <problem_id> [--no-samples]

Du lieu doc dang chuoi (dtype=str) de giu nguyen gia tri goc. Suy kieu chay tren gia tri da bo khoang
trang va dau nhay ' " o hai dau; n_unique va top_values tinh tren gia tri GOC.
Viec suy kieu lam tren cac gia tri khac nhau (kem so lan xuat hien) nen nhanh voi bang lon it gia tri khac nhau.
"""

import argparse
import re
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

import config
from config import ROOT
from ledger import Ledger
from ledger.schema import now_iso
from profiling import dictionary, flags
from storage import r2

OUTPUT_DIR = ROOT / "output"
REPORT_KEY = "data_quality_report"
TABLE_EXTS = (".csv", ".parquet")
YEAR_MIN, YEAR_MAX = 1900, 2100
STRIP_CHARS = " \t\r\n\f\v'\""
DATE_INVALID_EXAMPLES = 3
DAY_FIRST, MONTH_FIRST = "%d/%m/%Y", "%m/%d/%Y"
PATTERN_TOP_N = 3
PATTERN_MAX_DISTINCT = 50
CASE_VARIANT_EXAMPLES = 3
# Cau truc gia tri: chu hoa -> A, chu thuong -> a, chu so -> 9, ky tu khac giu nguyen
_ASCII_PATTERN = str.maketrans(
    {**{c: "A" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"},
     **{c: "a" for c in "abcdefghijklmnopqrstuvwxyz"},
     **{c: "9" for c in "0123456789"}}
)

_INT_RE = r"^[+-]?\d+$"
_INT_AS_FLOAT_RE = r"^[+-]?\d+\.0+$"
_FLOAT_RE = r"^[+-]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?$"
# Loc nhanh truoc khi thu tung dinh dang ngay: chi chu so va dau cua ngay gio (- / . : T khoang trang)
_DATE_SHAPE_RE = r"^\d[\d\-/.:T ]*\d$"
DATE_LIKE_SAMPLE = 200  # thu pd.to_datetime(format="mixed") tren mau truoc, du moi chay ca cot
_SEPARATORS = "-/.: T"
_FORMAT_PARTS = {"%Y": r"\d{4}", "%H": r"\d{1,2}", "%M": r"\d{2}", "%S": r"\d{2}"}


def _has_time(fmt):
    return "%H" in fmt


def _format_regex(fmt):
    """Dinh dang strptime -> regex nghiem. Co dau phan cach: ngay/thang 1-2 chu so; lien: dung 2 chu so."""
    compact = not any(sep in fmt.replace("%", "") for sep in _SEPARATORS)
    day_month = r"\d{2}" if compact else r"\d{1,2}"
    parts = {**_FORMAT_PARTS, "%m": day_month, "%d": day_month}
    out, i = "", 0
    while i < len(fmt):
        token = fmt[i:i + 2]
        if token in parts:
            out += parts[token]
            i += 2
        else:
            out += re.escape(fmt[i])
            i += 1
    return f"^{out}$"


def _clean(values):
    """Bo khoang trang va nhay o hai dau; chuoi rong sau khi bo bi loai khoi suy kieu."""
    cleaned = values.str.strip(STRIP_CHARS)
    return cleaned[cleaned != ""]


def _top_values(original):
    counts = original.value_counts()
    items = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))
    if len(items) > config.TOP_VALUES_MAX_UNIQUE:
        items = items[:config.TOP_VALUES_N]
    return [{"value": v, "count": int(c)} for v, c in items]


def _date_analysis(uniq, weights, n):
    """uniq: Series gia tri khac nhau (da lam sach), weights: so lan xuat hien. Tra ve dict phan tich ngay."""
    matches, parsed = {}, {}
    for fmt in config.DATE_FORMATS:
        shape = uniq.str.fullmatch(_format_regex(fmt))
        dt = pd.Series(pd.NaT, index=uniq.index, dtype="datetime64[ns]")
        if shape.any():
            dt[shape] = pd.to_datetime(uniq[shape], format=fmt, errors="coerce")
        ok = dt.notna() & dt.dt.year.between(YEAR_MIN, YEAR_MAX)
        matches[fmt], parsed[fmt] = ok, dt.where(ok)

    match_frame = pd.DataFrame(matches)
    n_formats = match_frame.sum(axis=1)
    union = n_formats > 0

    def ratio(mask):
        return float(weights[mask].sum() / n) if n else 0.0

    ratios = {fmt: ratio(m) for fmt, m in matches.items()}
    exclusive = {fmt: ratio(m & (n_formats == 1)) for fmt, m in matches.items()}
    return {
        "union_ratio": ratio(union),
        "union": union,
        "ratios": ratios,
        "exclusive": exclusive,
        "parsed": parsed,
    }


def infer_column(series, include_samples=True):
    """Suy kieu cho mot cot doc dang chuoi. Tra ve dict cac truong cua cot (khong gom null_count, n_unique).
    include_samples=False: khong ghi vi du chua gia tri that (case_variant_examples)."""
    original = series.dropna().astype(str)
    cleaned = _clean(original)
    out = {"inferred_type": "string", "type_match_ratio": None, "min": None, "max": None, "notes": [], "flags": []}
    if cleaned.empty:
        _string_extras(out, original, include_samples)
        return out

    vc = cleaned.value_counts()
    uniq = pd.Series(vc.index.astype(str), index=range(len(vc)))
    weights = pd.Series(vc.to_numpy(), index=uniq.index)
    n = int(weights.sum())
    min_ratio = config.TYPE_INFER_MIN_RATIO

    def ratio(mask):
        return float(weights[mask].sum() / n)

    # 1. Ngay truoc (chuoi 8 chu so co the la ngay), chi khi hinh dang du giong ngay
    if ratio(uniq.str.fullmatch(_DATE_SHAPE_RE)) >= min_ratio:
        d = _date_analysis(uniq, weights, n)
        if d["union_ratio"] >= min_ratio:
            return _date_result(out, d, uniq, weights, original, min_ratio)

    # 2. So
    is_int = uniq.str.fullmatch(_INT_RE)
    is_int_float = uniq.str.fullmatch(_INT_AS_FLOAT_RE)
    is_float = uniq.str.fullmatch(_FLOAT_RE)
    if ratio(is_int | is_int_float) >= min_ratio:
        out["inferred_type"] = "integer"
        out["type_match_ratio"] = round(ratio(is_int | is_int_float), 4)
        if is_int_float.any():
            out["notes"].append("integer_stored_as_float")
        _numeric_stats(out, uniq[is_int | is_int_float], weights[is_int | is_int_float], integer=True)
        return out
    if ratio(is_float | is_int) >= min_ratio:
        out["inferred_type"] = "float"
        out["type_match_ratio"] = round(ratio(is_float | is_int), 4)
        _numeric_stats(out, uniq[is_float | is_int], weights[is_float | is_int], integer=False)
        return out

    # 3. Chuoi. Giong ngay nhung khong dinh dang nao trong DATE_FORMATS khop -> chi gan co.
    if _date_like(uniq, weights, n, min_ratio):
        out["flags"].append("date_like_unknown_format")
    _string_extras(out, original, include_samples)
    return out


def _parse_mixed_ok(values):
    """True khi gia tri co it nhat 2 nhom chu so tach nhau (vd. "15 Apr 2025") va pd.to_datetime(format="mixed")
    doc ra nam trong YEAR_MIN..YEAR_MAX. Dieu kien 2 nhom so chan ma kieu "ST0049" bi dateutil doc thanh nam 2049."""
    ok = pd.Series(False, index=values.index)
    shaped = values.str.contains(r"\d+\D+\d+")
    if shaped.any():
        dt = pd.to_datetime(values[shaped], format="mixed", errors="coerce")
        ok[shaped] = (dt.notna() & dt.dt.year.between(YEAR_MIN, YEAR_MAX)).to_numpy()
    return ok


def _date_like(uniq, weights, n, min_ratio):
    """Gia tri doc duoc nhu ngay (dinh dang khong co trong DATE_FORMATS) >= min_ratio? Thu tren mau truoc cho nhanh."""
    sample = uniq.head(DATE_LIKE_SAMPLE)
    w = weights[sample.index]
    if float(w[_parse_mixed_ok(sample)].sum() / w.sum()) < min_ratio:
        return False
    if len(uniq) <= DATE_LIKE_SAMPLE:
        return True
    return float(weights[_parse_mixed_ok(uniq)].sum() / n) >= min_ratio


def _numeric_stats(out, values, weights, integer):
    """values: gia tri khac nhau (chuoi so), weights: so lan xuat hien. Phan vi theo noi suy tuyen tinh."""
    nums = pd.to_numeric(values, errors="coerce")
    keep = nums.notna()
    nums, weights = nums[keep].astype(float), weights[keep]
    if nums.empty:
        return
    lo, hi = nums.min(), nums.max()
    if integer:
        out["min"], out["max"] = int(lo), int(hi)
    else:
        out["min"], out["max"] = float(lo), float(hi)
    full = np.repeat(nums.to_numpy(), weights.to_numpy())
    out["median"] = float(np.median(full))
    out["p01"] = float(np.percentile(full, 1))
    out["p99"] = float(np.percentile(full, 99))
    out["zero_count"] = int(weights[nums == 0].sum())
    out["negative_count"] = int(weights[nums < 0].sum())


def _pattern(value):
    """Cau truc khong phai ASCII (vd. chu co dau): xu ly tung ky tu."""
    return "".join(
        "A" if c.isalpha() and c.isupper() else "a" if c.isalpha() else "9" if c.isdigit() else c
        for c in value
    )


def _string_extras(out, original, include_samples):
    """Cac truong rieng cua cot chu, tinh tren gia tri GOC."""
    out["whitespace_count"] = int((original != original.str.strip()).sum())
    vc = original.value_counts()
    total = int(vc.sum())
    uniq = pd.Series(vc.index.astype(str), index=range(len(vc)))
    counts = pd.Series(vc.to_numpy(), index=uniq.index)

    # pattern_top
    patterns = uniq.str.translate(_ASCII_PATTERN)
    non_ascii = ~uniq.str.isascii()
    if non_ascii.any():
        patterns[non_ascii] = uniq[non_ascii].map(_pattern)
    by_pattern = counts.groupby(patterns).sum()
    out["pattern_n_distinct"] = int(len(by_pattern))
    out["pattern_dominant"] = out["pattern_dominant_ratio"] = out["pattern_other_count"] = None
    if len(by_pattern) > PATTERN_MAX_DISTINCT:
        out["pattern_top"] = None
        out["notes"].append("too_many_patterns")
    else:
        ranked = sorted(by_pattern.items(), key=lambda kv: (-kv[1], kv[0]))
        out["pattern_top"] = [
            {"pattern": pat, "count": int(c), "ratio": round(c / total, 4) if total else 0.0}
            for pat, c in ranked[:PATTERN_TOP_N]
        ]
        if ranked:  # tinh tren toan bo cot, khong chi pattern_top
            pat, c = ranked[0]
            out["pattern_dominant"] = pat
            out["pattern_dominant_ratio"] = round(c / total, 4)
            out["pattern_other_count"] = int(total - c)

    # case_variant_groups: giong nhau sau lower()+strip() nhung khac dang goc
    normalized = uniq.str.strip().str.lower()
    sizes = normalized.groupby(normalized).size()
    variant_keys = sizes[sizes > 1].index
    out["case_variant_groups"] = int(len(variant_keys))
    if include_samples:
        examples = []
        for key in sorted(variant_keys)[:CASE_VARIANT_EXAMPLES]:
            examples.append(sorted(uniq[normalized == key].tolist()))
        out["case_variant_examples"] = examples


def _date_result(out, d, uniq, weights, original, min_ratio):
    used = [f for f, r in d["ratios"].items() if r > 0]
    out["type_match_ratio"] = round(d["union_ratio"], 4)
    out["date_format_ratios"] = {f: round(r, 4) for f, r in d["ratios"].items() if r > 0}

    strong = [f for f, r in d["ratios"].items() if r >= min_ratio]
    if DAY_FIRST in strong and MONTH_FIRST in strong:
        out["flags"].append("ambiguous_day_month")
    if sum(1 for r in d["exclusive"].values() if r >= config.DATE_MIXED_MIN_RATIO) >= 2:
        out["flags"].append("mixed_date_formats")

    fmt = strong[0] if len(strong) == 1 and not out["flags"] else None
    out["date_format"] = fmt
    # datetime khi dinh dang da chon co gio; chua chon duoc thi khi moi dinh dang khop deu co gio
    is_datetime = _has_time(fmt) if fmt is not None else all(_has_time(f) for f in used)
    out["inferred_type"] = "datetime" if is_datetime else "date"
    if fmt is not None:
        dates = d["parsed"][fmt].dropna()
        if is_datetime:
            out["min"], out["max"] = dates.min().isoformat(), dates.max().isoformat()
        else:
            out["min"], out["max"] = dates.min().date().isoformat(), dates.max().date().isoformat()

    invalid = ~d["union"]
    out["date_invalid_count"] = int(weights[invalid].sum())
    bad_values = set(uniq[invalid])
    examples = original[original.str.strip(STRIP_CHARS).isin(bad_values)].drop_duplicates().head(DATE_INVALID_EXAMPLES)
    out["date_invalid_examples"] = examples.tolist()
    return out


def name_words(name):
    """Tach ten cot thanh tu (viet thuong, bo dau tieng Viet).

    1. Bo dau: NFKD roi bo dau ket hop; "đ"/"Đ" -> "d".
    2. Cat theo moi ky tu khong phai chu/so (_ - khoang trang . ...).
    3. Cat camelCase va chuyen chu-so: "MaKH" -> ma, kh; "customerID" -> customer, id; "OrderId2" -> order, id, 2.
    Chuoi viet hoa lien khong co ranh gioi ("CUSTOMERID") la mot tu.
    """
    text = unicodedata.normalize("NFKD", str(name).replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    words = []
    for chunk in re.split(r"[^0-9A-Za-z]+", text):
        words += re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", chunk)
    return [w.lower() for w in words]


def _guess_key(df, columns):
    """Ung vien: ten co tu thuoc KEY_NAME_HINTS, ty le duy nhat >= KEY_MIN_UNIQUE_RATIO, kieu khac float.
    Chon theo (a) thu tu uu tien cua tu goi y khop duoc, (b) ty le duy nhat cao hon, (c) cot dung truoc.
    columns: ket qua profile tung cot (de biet inferred_type). Tra ve (key_cols hoac None, candidates)."""
    priority = {h.lower(): i for i, h in enumerate(config.KEY_NAME_HINTS)}
    n_rows = len(df)
    candidates = []
    if n_rows:
        for pos, col in enumerate(df.columns):
            matched = [w for w in name_words(col) if w in priority]
            if not matched or columns[str(col)]["inferred_type"] == "float":
                continue
            ratio = df[col].nunique(dropna=True) / n_rows
            if ratio >= config.KEY_MIN_UNIQUE_RATIO:
                hint = min(matched, key=priority.get)
                candidates.append((priority[hint], -ratio, pos, col, hint))
    candidates.sort(key=lambda c: c[:3])
    listed = [{"column": col, "unique_ratio": round(-neg, 4), "hint": hint} for _, neg, _, col, hint in candidates]
    return ([candidates[0][3]] if candidates else None), listed


def _key_stats(df, key_cols):
    keys = df[key_cols]
    has_key = keys.notna().all(axis=1)
    with_key = df[has_key]
    counts = with_key.groupby(key_cols, dropna=False).size()
    distinct_rows = with_key.drop_duplicates().groupby(key_cols, dropna=False).size()
    return {
        "null_key_rows": int((~has_key).sum()),
        "duplicate_key_count": int((counts > 1).sum()),
        "duplicate_key_rows": int((counts[counts > 1] - 1).sum()),
        "key_conflict_count": int((distinct_rows > 1).sum()),
    }


def profile_table(df, table_name, key_cols=None, sample_n=5, seed=42, include_samples=True):
    """Profile mot bang (nen doc dang chuoi). include_samples=False tat sample_records va top_values."""
    key_guessed, key_candidates, notes = False, None, []
    if isinstance(key_cols, str):
        key_cols = [key_cols]
    if key_cols:
        missing = [c for c in key_cols if c not in df.columns]
        if missing:
            raise ValueError(f"Bảng {table_name}: không có cột khóa {missing}")

    n_rows = len(df)
    columns, flags = {}, []
    for col in df.columns:
        s = df[col]
        info = {
            "null_count": int(s.isna().sum()),
            "null_ratio": round(float(s.isna().mean()), 4) if n_rows else 0.0,
            "n_unique": int(s.nunique(dropna=True)),
        }
        info.update(infer_column(s, include_samples=include_samples))
        if include_samples:
            info["top_values"] = _top_values(s.dropna())
        columns[str(col)] = info
        flags += [f"{col}: {f}" for f in info["flags"]]

    if key_cols is None:  # doan sau khi profile cot: can inferred_type
        key_cols, key_candidates = _guess_key(df, columns)
        key_guessed = key_cols is not None
        if key_cols is None:
            notes.append("no_key_found")

    report = {
        "table": table_name,
        "n_rows": n_rows,
        "n_cols": int(df.shape[1]),
        "key_cols": key_cols,
        "key_guessed": key_guessed,
        "key_candidates": key_candidates,
        "exact_duplicate_rows": int(df.duplicated().sum()),
    }
    if key_cols:
        report.update(_key_stats(df, key_cols))
    else:
        report.update({"null_key_rows": None, "duplicate_key_count": None, "duplicate_key_rows": None,
                       "key_conflict_count": None})
    report["columns"] = columns
    report["notes"] = notes
    report["flags"] = flags
    if include_samples:
        sample = df.sample(n=min(sample_n, n_rows), random_state=seed) if n_rows else df
        report["sample_records"] = sample.astype(object).where(sample.notna(), None).to_dict("records")
    return report


def _check_problem_id(problem_id):
    if not problem_id or any(c in problem_id for c in "/\\") or ".." in problem_id \
            or problem_id.lower() == "answers":
        raise ValueError(f"problem_id không hợp lệ: {problem_id!r}")


def _is_answers(key):
    return any(part.lower() == "answers" for part in Path(key).parts)


def _read_text(key):
    raw = Path(r2.download_with_cache(key)).read_bytes()
    for encoding in r2.CSV_ENCODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1", errors="replace")


def _load_dictionary(objects):
    """Tim va doc dictionary. Tra ve (thong tin cap bai, cac key khong profile nhu bang du lieu)."""
    found = dictionary.find_dictionary([o["key"] for o in objects])
    info = {"file": found["key"], "format": found["format"], "status": found["status"],
            "tables_without_file": [], "files_without_dictionary": [],
            "parse_warnings": list(found["warnings"]), "tables": {}}
    if found["status"] == "ok":
        try:
            if found["format"] == "md":
                tables, warnings = dictionary.parse_dictionary_md(_read_text(found["key"]))
            else:
                tables, warnings = dictionary.parse_dictionary_csv(r2.read_table(found["key"], dtype=str))
            info["tables"] = tables
            info["parse_warnings"] += warnings
        except Exception as e:  # noqa: BLE001  dictionary loi khong dung ca bai
            info["status"] = "error"
            info["parse_warnings"].append(f"{type(e).__name__}: {e}")
    return info, set(found["dictionary_keys"])


def profile_problem(problem_id, include_samples=True, thresholds=None):
    """Profile moi bang .csv/.parquet trong input/<problem_id>/, ghi vao output/<problem_id>/data_profile.json.
    Co data dictionary thi doc truoc, dung PK cua no lam khoa va doi chieu tung bang (khoa "dictionary_check").
    Sau cung sinh co theo nguong (profiling.flags; thresholds ghi de hang trong config.py)."""
    _check_problem_id(problem_id)
    used_thresholds = flags.resolve_thresholds(thresholds)  # khoa sai thi bao loi truoc khi doc R2
    prefix = f"{r2.get_r2_settings()['R2_INPUT_PREFIX']}{problem_id}/"
    objects = r2.list_objects(prefix)
    dict_info, dict_keys = _load_dictionary(objects)
    dict_tables = dict_info["tables"]
    table_names = {}  # key -> ten bang; tinh truoc khi profile de ghep voi dictionary
    for obj in objects:
        key = obj["key"]
        if key not in dict_keys and not _is_answers(key) and key.lower().endswith(TABLE_EXTS):
            name = Path(key).stem
            table_names[key] = Path(key).name if name in table_names.values() else name
    matching = dictionary.match_tables(list(dict_tables), list(table_names.values()))
    dict_of_file = {f: d for d, f in matching["matched"]}
    dict_info["parse_warnings"] += matching["warnings"]
    if dict_tables:
        dict_info["tables_without_file"] = matching["tables_without_file"]
        dict_info["files_without_dictionary"] = matching["files_without_dictionary"]

    files, skipped, errors, tables = [], [], [], {}
    for obj in objects:
        key = obj["key"]
        if key in dict_keys:
            continue  # file dictionary: ghi o report["dictionary"], khong bao "bo qua"
        if _is_answers(key):
            skipped.append({"key": key, "reason": "answers/ bị cấm đọc"})
            continue
        if not key.lower().endswith(TABLE_EXTS):
            skipped.append({"key": key, "reason": "không phải .csv/.parquet"})
            continue
        name = table_names[key]
        try:
            df = r2.read_table(key, dtype=str)
        except Exception as e:  # noqa: BLE001  ghi loi tung file, profile tiep cac file khac
            errors.append({"key": key, "error": f"{type(e).__name__}: {e}"})
            continue
        etag = r2.get_etag(key) or obj.get("etag")
        fmt = df.attrs.get("csv_format") or {}
        files.append({"key": key, "etag": etag, "table": name, "delimiter": fmt.get("delimiter"),
                      "encoding": fmt.get("encoding"), "flags": list(fmt.get("flags", []))})
        dict_name = dict_of_file.get(name)
        key_cols = None
        if dict_name is not None:
            key_cols, _ = dictionary.dictionary_key_cols(dict_tables[dict_name], [str(c) for c in df.columns])
        t = profile_table(df, name, key_cols=key_cols, include_samples=include_samples)
        t["key_source"] = "dictionary" if key_cols else "guessed" if t["key_guessed"] else None
        t["flags"] += [f"file: {f}" for f in fmt.get("flags", [])]
        if dict_name is not None:
            t["dictionary_check"] = dictionary.compare_table_with_dictionary(t, dict_tables[dict_name], dict_name)
            t["flags"] += [f"dictionary: {f}" for f in t["dictionary_check"]["flags"]]
        elif name in dict_info["files_without_dictionary"]:
            t["flags"].append("dictionary: not_in_dictionary")
        tables[name] = t

    report = {
        "problem_id": problem_id,
        "generated_at": now_iso(),
        "dictionary": dict_info,
        "files": files,
        "skipped": skipped,
        "errors": errors,
        "tables": tables,
    }
    all_flags = flags.build_flags(report, used_thresholds)
    for name, t in tables.items():
        t["quality_flags"] = [f for f in all_flags if f["table"] == name]
    report["quality_flags"] = [f for f in all_flags if f["table"] not in tables]  # cap bai
    report["quality_flags_summary"] = flags.summarize_flags(all_flags, list(tables))

    led = Ledger(OUTPUT_DIR / problem_id)
    profile = led.get_data_profile()
    profile = profile if isinstance(profile, dict) else {}
    profile[REPORT_KEY] = report
    led.set_data_profile(profile)
    # chi so dem, khong kem gia tri du lieu
    led.log_event("profiling", "quality_flags", inputs={"problem_id": problem_id, "thresholds": used_thresholds},
                  extra=report["quality_flags_summary"])
    return report


def _fmt_table(t):
    lines = [f"== {t['table']}: {t['n_rows']:,} dòng, {t['n_cols']} cột =="]
    if t["key_cols"]:
        guessed = " (đoán)" if t["key_guessed"] else ""
        lines.append(
            f"  Khóa {', '.join(t['key_cols'])}{guessed}: {t['duplicate_key_count']} khóa trùng "
            f"({t['duplicate_key_rows']} dòng thừa), {t['key_conflict_count']} khóa khác nội dung, "
            f"{t['null_key_rows']} dòng thiếu khóa"
        )
    else:
        lines.append("  Khóa: không xác định (no_key_found)")
    lines.append(f"  Dòng trùng y hệt: {t['exact_duplicate_rows']}")
    nulls = [f"{c} {i['null_count']} ({i['null_ratio']:.1%})" for c, i in t["columns"].items() if i["null_count"]]
    lines.append(f"  Cột có null: {', '.join(nulls) or 'không'}")
    dates = [f"{c} {i.get('date_format') or 'không xác định'}"
             + (f", từ {i['min']} đến {i['max']}" if i.get("min") and i.get("max") else "")
             for c, i in t["columns"].items() if i["inferred_type"] in ("date", "datetime")]
    lines.append(f"  Cột ngày: {', '.join(dates) or 'không'}")
    cols = t["columns"].items()

    numeric = []
    for c, i in cols:
        if i["inferred_type"] not in ("integer", "float"):
            continue
        parts = [f"min {_num(i.get('min'))}", f"max {_num(i.get('max'))}",
                 f"p01 {_num(i.get('p01'))}", f"p99 {_num(i.get('p99'))}"]
        parts += [f"{k} {i[k]}" for k in ("zero_count", "negative_count") if i.get(k)]
        numeric.append(f"{c} ({', '.join(parts)})")
    _add(lines, "Cột số", numeric, sep="; ")

    minority = []
    for c, i in cols:
        ratio = i.get("pattern_dominant_ratio")
        if i["inferred_type"] != "string" or ratio is None or not i.get("pattern_other_count"):
            continue
        if ratio < 1 - config.MINORITY_PATTERN_RATIO:
            continue  # cot von da dang, khong co cau truc chinh ro rang
        others = [p for p in (i.get("pattern_top") or []) if p["pattern"] != i["pattern_dominant"]]
        top = [f"{p['pattern']} ({p['count']})" for p in others]
        if i["pattern_n_distinct"] - 1 > len(others):
            top.append("...")
        minority.append(f"{c}: {i['pattern_other_count']} giá trị khác cấu trúc chính {i['pattern_dominant']} "
                        f"({i['pattern_n_distinct']} cấu trúc; top: {', '.join(top)})")
    _add(lines, "Cấu trúc thiểu số", minority)
    _add(lines, "Khoảng trắng thừa", [f"{c} {i['whitespace_count']}" for c, i in cols if i.get("whitespace_count")])
    _add(lines, "Biến thể hoa/thường",
         [f"{c} {i['case_variant_groups']} nhóm" for c, i in cols if i.get("case_variant_groups")])
    _add(lines, "Ngày không đọc được", [f"{c} {i['date_invalid_count']}" for c, i in cols
                                         if i["inferred_type"] in ("date", "datetime") and i.get("date_invalid_count")])

    notes = [f"{c}: {n}" for c, i in t["columns"].items() for n in i["notes"]]
    if notes:
        lines.append(f"  Ghi chú: {', '.join(notes)}")
    if t.get("dictionary_check"):
        lines += _fmt_dictionary_check(t)
    lines += _fmt_quality_flags(t.get("quality_flags") or [])
    return "\n".join(lines)


def _fmt_quality_flags(items, indent="  "):
    lines = []
    for label, sev in (("Cờ (cần xem)", "flag"), ("Lưu ý (info)", "info")):
        msgs = [f["message"] for f in items if f["severity"] == sev]
        if msgs:
            lines.append(f"{indent}{label}: {'; '.join(msgs)}")
    return lines or [f"{indent}Cờ: không"]


def _fmt_dictionary_check(t):
    d = t["dictionary_check"]
    lines = [f"  Đối chiếu dictionary (bảng '{d['dictionary_table']}'):"]
    sub = []
    _add(sub, "Thiếu trong dữ liệu", [m["column"] + (" (dictionary nói là cột phải tính)" if m["derived_hint"] else "")
                                      for m in d["missing_in_data"]])
    _add(sub, "Thiếu trong dictionary", d["undocumented_in_dictionary"])
    _add(sub, "Kiểu không khớp", [f"{m['column']} (dictionary {m['dictionary_type']}, dữ liệu {m['inferred_type']})"
                                  for m in d["type_mismatches"]])
    _add(sub, "Định dạng ngày lệch", [f"{m['column']} (dictionary {m['dictionary_format']}, "
                                      f"dữ liệu {m['data_format']})" for m in d["date_format_mismatches"]])
    _add(sub, "Kiểu dictionary không nhận ra", d["notes"])
    if t["key_source"] == "dictionary":
        source = f"dictionary ({', '.join(t['key_cols'])})"
    else:
        source = "đoán" if t["key_source"] == "guessed" else "không có"
        if d["pk_missing"]:
            source += f"; PK trong dictionary thiếu trong dữ liệu: {', '.join(d['pk_missing'])}"
        elif not d["pk_cols"]:
            source += "; dictionary không ghi PK"
    sub.append(f"  Khóa lấy từ: {source}")
    return lines + ["  " + s for s in sub]


def _fmt_dictionary(d):
    if d is None:
        return []
    if d["status"] == "none":
        lines = ["Dictionary: không có"]
    elif d["status"] == "unsupported":
        lines = [f"Dictionary: không hỗ trợ định dạng file {d['file']} (chỉ đọc .md/.csv)"]
    elif d["status"] == "error":
        lines = [f"Dictionary: LỖI đọc {d['file']}"]
    else:
        lines = [f"Dictionary: {d['file']} ({d['format']}, {len(d['tables'])} bảng)"]
    _add(lines, "Bảng trong dictionary không có file", d["tables_without_file"])
    _add(lines, "File không có trong dictionary", d["files_without_dictionary"])
    if d["parse_warnings"]:
        lines.append(f"  Cảnh báo khi đọc dictionary ({len(d['parse_warnings'])}):")
        lines += [f"    - {w}" for w in d["parse_warnings"]]
    return lines


def _num(v):
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return f"{v:g}" if isinstance(v, float) else str(v)


def _add(lines, label, items, sep=", "):
    """Chi in dong khi co du lieu."""
    if items:
        lines.append(f"  {label}: {sep.join(items)}")


def format_summary(report):
    lines = [f"Bài {report['problem_id']} ({report['generated_at']}): {len(report['tables'])} bảng"]
    lines += _fmt_dictionary(report.get("dictionary"))
    if report.get("quality_flags"):
        lines += _fmt_quality_flags(report["quality_flags"], indent="")
    lines += [_fmt_table(t) for t in report["tables"].values()]
    for s in report["skipped"]:
        lines.append(f"Bỏ qua {s['key']}: {s['reason']}")
    for e in report["errors"]:
        lines.append(f"LỖI {e['key']}: {e['error']}")
    all_flags = list(report.get("quality_flags") or []) + [f for t in report["tables"].values()
                                                            for f in t.get("quality_flags") or []]
    n_flag = sum(f["severity"] == "flag" for f in all_flags)
    lines.append(f"Tổng: {n_flag} cờ, {len(all_flags) - n_flag} lưu ý")
    return "\n".join(lines)


def _parse_set(items):
    """["TEN=GIATRI", ...] -> dict; gia tri doi sang int, khong duoc thi float."""
    out = {}
    for item in items or []:
        name, sep, raw = item.partition("=")
        if not sep or not name.strip():
            raise ValueError(f"--set cần dạng TEN=GIATRI, nhận được {item!r}")
        try:
            value = int(raw)
        except ValueError:
            try:
                value = float(raw)
            except ValueError:
                raise ValueError(f"--set {name}: giá trị {raw!r} không phải số") from None
        out[name.strip()] = value
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="Profiling chất lượng dữ liệu của một bài.")
    p.add_argument("problem_id")
    p.add_argument("--no-samples", action="store_true", help="không ghi sample_records và top_values")
    p.add_argument("--set", action="append", default=[], metavar="TEN=GIATRI",
                   help="ghi đè ngưỡng trong config.py, vd. --set NULL_RATIO_FLAG=0.01 (dùng nhiều lần được)")
    args = p.parse_args(argv)
    try:
        thresholds = _parse_set(args.set)
        flags.resolve_thresholds(thresholds)
    except ValueError as e:
        p.error(str(e))
    report = profile_problem(args.problem_id, include_samples=not args.no_samples, thresholds=thresholds)
    print(format_summary(report))
    print(f"\nĐã ghi {OUTPUT_DIR / args.problem_id / 'data_profile.json'} (khóa {REPORT_KEY}).")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
