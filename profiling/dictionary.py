"""Doc data dictionary cua mot bai va doi chieu voi ket qua profiling. Chi BAO CAO, khong sua du lieu.

Ho tro dictionary dang Markdown (moi bang mot tieu de "## Ten bang" kem bang Cot | Kieu | Dinh nghia)
va CSV 4 cot (table, column, type, definition). Mau: templates/data_dictionary_template.md.
"""

import re
import unicodedata
from pathlib import PurePosixPath

import config

DICT_EXTS = (".md", ".csv")
PREFERRED_NAMES = ("data_dictionary.md", "data_dictionary.csv")

# Ten tieu de cot (da bo dau, chu thuong, bo khoang trang) -> truong
FIELD_ALIASES = {
    "table": ("table", "bang"),
    "column": ("column", "cot"),
    "type": ("type", "kieu"),
    "definition": ("definition", "dinhnghia"),
    "valid_range": ("khoanghople", "validrange", "valid_range", "range"),  # tuy chon
}
# Khoang hop le: "a..b" (a <= b), ">= a", "<= b"; so nguyen hoac thuc, co the am.
_NUM = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
_RANGE_BETWEEN_RE = re.compile(rf"^({_NUM})\s*\.\.\s*({_NUM})$")
_RANGE_BOUND_RE = re.compile(rf"^(>=|<=)\s*({_NUM})$")
MD_REQUIRED = ("column", "type")
CSV_REQUIRED = ("table", "column", "type")

# Kieu dictionary -> tap kieu suy ra duoc chap nhan. None: chap nhan moi kieu (khong bao gio co).
TYPE_RULES = {
    "int": {"integer"}, "integer": {"integer"},
    "float": {"integer", "float"}, "double": {"integer", "float"},
    "number": {"integer", "float"}, "decimal": {"integer", "float"},
    "date": {"date", "datetime"},
    "datetime": {"datetime", "date"}, "timestamp": {"datetime", "date"},
    "string": None, "text": None, "varchar": None,
}

# The trong ngoac o cot Kieu: (PK), (FK), (SENSITIVE), ket hop duoc: (PK, SENSITIVE)
_TAG = r"(?:pk|fk|sensitive)"
_KEY_RE = re.compile(rf"\(\s*({_TAG}(?:\s*[,/ ]\s*{_TAG})*)\s*\)", re.IGNORECASE)
_SEPARATOR_CELL_RE = re.compile(r"^:?-+:?$")
_HEADING_RE = re.compile(r"^\s*##(?!#)\s*(.*?)\s*#*\s*$")
_TEXT_DATE_RE = re.compile(r"(?<![a-z])(dd|mm|yyyy)([-/.])(dd|mm|yyyy)\2(dd|mm|yyyy)(?![a-z])", re.IGNORECASE)
_TEXT_DATE_PARTS = {"dd": "%d", "mm": "%m", "yyyy": "%Y"}


# ---------- tim file ----------

def find_dictionary(keys):
    """Chon file dictionary trong danh sach key cua mot bai.

    Tra ve dict: key, format ("md"/"csv"/None), status ("ok"/"none"/"unsupported"), warnings,
    dictionary_keys (moi key co chu "dictionary" trong ten: khong profile nhu bang du lieu).
    """
    named = [k for k in keys if "dictionary" in _filename(k).lower()
             and not any(p.lower() == "answers" for p in PurePosixPath(k).parts)]
    preferred = [k for name in PREFERRED_NAMES for k in named if _filename(k).lower() == name]
    others = [k for k in named if k not in preferred and _filename(k).lower().endswith(DICT_EXTS)]
    candidates = preferred + others
    result = {"key": None, "format": None, "status": "none", "warnings": [], "dictionary_keys": named}
    if candidates:
        key = candidates[0]
        result.update(key=key, format=_filename(key).lower().rsplit(".", 1)[-1], status="ok")
        if len(candidates) > 1:
            result["warnings"].append(f"Có {len(candidates)} file dictionary, dùng {key}; bỏ qua: "
                                      + ", ".join(candidates[1:]))
    elif named:
        result.update(key=named[0], status="unsupported")
    return result


def _filename(key):
    return PurePosixPath(key).name


# ---------- parse ----------

def _plain(text):
    """Bo dau tieng Viet, chu thuong, bo khoang trang: "Định nghĩa" -> "dinhnghia"."""
    text = unicodedata.normalize("NFKD", str(text).replace("đ", "d").replace("Đ", "D"))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", "", text).lower()


def _field_of(header):
    plain = _plain(header)
    return next((f for f, aliases in FIELD_ALIASES.items() if plain in aliases), None)


def _split_type(raw):
    """Tach "(PK)"/"(FK)"/"(SENSITIVE)" khoi kieu. Tra ve (type chu thuong, key, sensitive).
    Co ca PK va FK -> "PK"."""
    raw = "" if raw is None else str(raw)
    tags = {t.upper() for m in _KEY_RE.finditer(raw) for t in re.findall(_TAG, m.group(1), re.IGNORECASE)}
    type_ = re.sub(r"\s+", " ", _KEY_RE.sub(" ", raw)).strip().lower()
    key = "PK" if "PK" in tags else "FK" if "FK" in tags else None
    return type_, key, "SENSITIVE" in tags


def _number(text):
    return int(text) if re.fullmatch(r"[+-]?\d+", text) else float(text)


def parse_valid_range(text):
    """Khoang hop le -> ({"min", "max"} | None, loi | None). O trong -> (None, None).
    Nhan "a..b" (a <= b), ">= a", "<= b"; dang khac hoac a > b -> (None, mo ta loi)."""
    s = re.sub(r"\s+", " ", "" if text is None else str(text)).strip()
    if not s:
        return None, None
    m = _RANGE_BETWEEN_RE.match(s)
    if m:
        lo, hi = _number(m.group(1)), _number(m.group(2))
        if lo > hi:
            return None, f"khoảng '{s}' có cận dưới lớn hơn cận trên"
        return {"min": lo, "max": hi}, None
    m = _RANGE_BOUND_RE.match(s)
    if m:
        value = _number(m.group(2))
        return ({"min": value, "max": None} if m.group(1) == ">=" else {"min": None, "max": value}), None
    return None, f"khoảng '{s}' không đúng dạng a..b, >= a hoặc <= b"


def _add_column(tables, table, name, raw_type, definition, warnings, where, raw_range=None):
    cols = tables.setdefault(table, {"columns": []})["columns"]
    if any(c["name"] == name for c in cols):
        warnings.append(f"{where}: cột '{name}' lặp lại trong bảng '{table}', giữ dòng đầu")
        return
    type_, key, sensitive = _split_type(raw_type)
    valid_range, error = parse_valid_range(raw_range)
    if error:
        warnings.append(f"{where}: bảng '{table}', cột '{name}': {error}, bỏ qua khoảng")
    cols.append({"name": name, "type": type_, "key": key, "sensitive": sensitive,
                 "definition": (definition or "").strip(), "valid_range": valid_range})


def _md_cells(line):
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return [c.strip() for c in s.split("|")]


def parse_dictionary_md(text):
    """Markdown -> (tables, parse_warnings).
    tables: {ten bang: {"columns": [{name, type, key, sensitive, definition, valid_range}]}}.
    Cot thu 4 tuy chon "Khoang hop le" / "Valid range" / "Range"."""
    tables, warnings = {}, []
    table, header = None, None  # header: list truong theo vi tri, "bad" neu tieu de khong hop le
    for lineno, line in enumerate(text.splitlines(), start=1):
        heading = _HEADING_RE.match(line)
        if heading:
            table, header = heading.group(1) or None, None
            if table is None:
                warnings.append(f"Dòng {lineno}: tiêu đề ## rỗng")
            continue
        if not line.strip().startswith("|"):
            header = None  # het mot bang markdown
            continue
        cells = _md_cells(line)
        if all(_SEPARATOR_CELL_RE.match(c) for c in cells if c) and any(cells):
            continue
        if not any(cells):
            continue
        if table is None:
            if header is None:
                warnings.append(f"Dòng {lineno}: bảng markdown nằm ngoài tiêu đề ## nào, bỏ qua")
                header = "bad"
            continue
        if header is None:
            fields = [_field_of(c) for c in cells]
            missing = [f for f in MD_REQUIRED if f not in fields]
            if missing:
                warnings.append(f"Dòng {lineno}: bảng '{table}' thiếu tiêu đề {missing}, bỏ qua bảng này")
                header = "bad"
            else:
                header = fields
            continue
        if header == "bad":
            continue
        if len(cells) != len(header):
            warnings.append(f"Dòng {lineno}: bảng '{table}' có {len(cells)} ô, cần {len(header)}, bỏ qua dòng")
            continue
        row = {f: c for f, c in zip(header, cells) if f}
        name = row.get("column", "").strip("`* ")
        if not name:
            warnings.append(f"Dòng {lineno}: bảng '{table}' có dòng thiếu tên cột, bỏ qua dòng")
            continue
        _add_column(tables, table, name, row.get("type"), row.get("definition"), warnings, f"Dòng {lineno}",
                    row.get("valid_range"))
    return tables, warnings


def parse_dictionary_csv(df):
    """DataFrame (doc dang chuoi) -> (tables, parse_warnings). Thieu cot bat buoc -> ValueError."""
    mapping = {}
    for col in df.columns:
        field = _field_of(col)
        if field and field not in mapping:
            mapping[field] = col
    missing = [f for f in CSV_REQUIRED if f not in mapping]
    if missing:
        raise ValueError(f"Dictionary CSV thiếu cột bắt buộc {missing}. Các cột đang có: {list(df.columns)}. "
                         f"Cần: table/bang, column/cot, type/kieu, definition/dinh nghia.")
    tables, warnings = {}, []
    for pos, (_, r) in enumerate(df.iterrows()):
        where = f"Dòng {pos + 2}"  # dong 1 la tieu de

        def get(field):
            v = r[mapping[field]] if field in mapping else None
            return None if v is None or v != v else str(v).strip()  # v != v: NaN

        table, name = get("table"), get("column")
        if not table or not name:
            warnings.append(f"{where}: thiếu tên bảng hoặc tên cột, bỏ qua dòng")
            continue
        _add_column(tables, table, name, get("type"), get("definition"), warnings, where, get("valid_range"))
    return tables, warnings


# ---------- ghep bang ----------

def normalize_table_name(name):
    """Chu thuong, chi giu chu/so, bo mot chu "s" cuoi: "Support Ticket", "SUPPORT_TICKETS" -> "supportticket"."""
    s = "".join(c for c in str(name).lower() if c.isalnum())
    return s[:-1] if len(s) > 1 and s.endswith("s") else s


def match_tables(dict_tables, file_tables):
    """Ghep ten bang dictionary voi ten bang tu file. Tra ve matched [(dict_name, file_name)],
    tables_without_file, files_without_dictionary, warnings (ten trung nhau sau chuan hoa)."""
    warnings = []

    def index(names, label):
        out = {}
        for n in names:
            norm = normalize_table_name(n)
            if norm in out:
                warnings.append(f"{label} '{n}' trùng tên sau chuẩn hóa với '{out[norm]}', không ghép")
                continue
            out[norm] = n
        return out

    d_idx, f_idx = index(dict_tables, "Bảng dictionary"), index(file_tables, "File")
    matched = [(d, f_idx[norm]) for norm, d in d_idx.items() if norm in f_idx]
    used_d = {d for d, _ in matched}
    used_f = {f for _, f in matched}
    return {
        "matched": matched,
        "tables_without_file": [d for d in dict_tables if d not in used_d],
        "files_without_dictionary": [f for f in file_tables if f not in used_f],
        "warnings": warnings,
    }


# ---------- doi chieu ----------

def _col_norm(name):
    return str(name).strip().lower()


def dictionary_key_cols(dict_table, data_columns):
    """PK cua dictionary -> (key_cols theo ten cot that trong du lieu hoac None, PK thieu trong du lieu)."""
    by_norm = {_col_norm(c): c for c in data_columns}
    pk = [c["name"] for c in dict_table["columns"] if c["key"] == "PK"]
    missing = [p for p in pk if _col_norm(p) not in by_norm]
    if not pk or missing:
        return None, missing
    return [by_norm[_col_norm(p)] for p in pk], []


def text_date_format(definition):
    """Tim dinh dang dang chu (dd-mm-yyyy, yyyy/mm/dd...) trong dinh nghia -> strptime, khong thay -> None."""
    for m in _TEXT_DATE_RE.finditer(definition or ""):
        parts = [m.group(1).lower(), m.group(3).lower(), m.group(4).lower()]
        if sorted(parts) == ["dd", "mm", "yyyy"]:
            return m.group(2).join(_TEXT_DATE_PARTS[p] for p in parts)
    return None


def _date_part(fmt):
    """Phan ngay cua dinh dang strptime: "%Y-%m-%d %H:%M:%S" -> "%Y-%m-%d"."""
    return re.split(r"[ T]%H", fmt, maxsplit=1)[0]


def _base_type(type_):
    m = re.match(r"[a-z]+", type_ or "")
    return m.group(0) if m else ""


def compare_table_with_dictionary(table_report, dict_table, dict_table_name=None):
    """Doi chieu ket qua profile_table cua mot bang voi mot bang trong dictionary.

    key_source doc tu table_report["key_source"] (profile_problem gan san).
    """
    columns = table_report["columns"]
    by_norm = {_col_norm(c): c for c in columns}
    dict_norms = {_col_norm(c["name"]) for c in dict_table["columns"]}
    hints = [h.lower() for h in config.DERIVED_HINTS]
    n_rows = table_report["n_rows"]

    missing, type_mismatches, date_mismatches, notes = [], [], [], []
    for c in dict_table["columns"]:
        data_col = by_norm.get(_col_norm(c["name"]))
        if data_col is None:
            definition = c["definition"].lower()
            missing.append({"column": c["name"], "type": c["type"],
                            "derived_hint": any(h in definition for h in hints)})
            continue
        info = columns[data_col]
        base = _base_type(c["type"])
        if base not in TYPE_RULES:
            notes.append(f"{data_col}: unknown_dictionary_type ({c['type'] or 'rỗng'})")
            continue
        if info["null_count"] >= n_rows:
            continue  # cot toan null: khong suy ra duoc kieu
        accepted = TYPE_RULES[base]
        if accepted is not None and info["inferred_type"] not in accepted:
            type_mismatches.append({"column": data_col, "dictionary_type": c["type"],
                                    "inferred_type": info["inferred_type"]})
        if info["inferred_type"] in ("date", "datetime") and info.get("date_format"):
            expected = text_date_format(c["definition"])
            actual = _date_part(info["date_format"])
            if expected and expected != actual:
                date_mismatches.append({"column": data_col, "dictionary_format": expected,
                                        "data_format": info["date_format"]})

    _, pk_missing = dictionary_key_cols(dict_table, columns)
    flags = ["pk_missing_in_data"] if pk_missing else []
    return {
        "dictionary_table": dict_table_name,
        "missing_in_data": missing,
        "undocumented_in_dictionary": [c for c in columns if _col_norm(c) not in dict_norms],
        "type_mismatches": type_mismatches,
        "date_format_mismatches": date_mismatches,
        "pk_cols": [c["name"] for c in dict_table["columns"] if c["key"] == "PK"],
        "pk_missing": pk_missing,
        "key_source": table_report.get("key_source"),
        "notes": notes,
        "flags": flags,
    }
