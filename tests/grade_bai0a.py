"""Cham bai0a: so ket qua cua agent (output/bai0a/) voi answers/bai0a/ (quyen grader).
KHONG phai pytest. Chay tay: python -m tests.grade_bai0a

- Dap an (answer.csv, answer_notes.md) doc qua r2.grader_download (thu muc TAM ngoai cache/, xoa ngay);
  cuoi script kiem tra cache/ khong co duong dan nao chua "answers" moi xuat hien. Khong dua dap an cho agent,
  khong ghi vao output/ hay Ledger, khong sua file cua agent.
- Muc 1: bang category x brand (so tung dong). Muc 2: tong theo nhom hang va nhom lon nhat (ke ca dong hang)
  so voi result_summary. Muc 3: tung y trong answer_notes.md; y nao kiem duoc bang code (theo tu khoa) thi danh
  gia, con lai ghi "cần người xem".
"""

import json
import re
import sys

import pandas as pd

from analysis.meta_grader import read_local_table
from config import ROOT
from storage import r2

PROBLEM = "bai0a"
ANSWER_CSV = f"answers/{PROBLEM}/answer.csv"
ANSWER_NOTES = f"answers/{PROBLEM}/answer_notes.md"
OUT = ROOT / "output" / PROBLEM
KEYS = ["category_id", "brand"]
COUNT_HINTS = ("so_san_pham", "count", "n_products", "num", "total", "so_luong", "products", "n")
NEED_HUMAN = "cần người xem"
# (ten khoa khop chinh xac, mau chuoi con dung khi khong co khoa do)
TOTAL_PRODUCTS = ("total_products", r"total.*product|product.*total|tong.*san_pham")
TOTAL_CATEGORIES = ("total_categories", r"total.*categor|n_categor|so.*nhom")
MAX_COUNT = ("max_count", r"max.*(count|n_|so|product)|(count|so).*max|top.*count")


def _answers_in_cache():
    return sorted(str(p) for p in r2.CACHE_DIR.rglob("*") if "answers" in p.parts)


def load_answers():
    """(DataFrame answer.csv dang chuoi, noi dung answer_notes.md hoac None). Qua r2.grader_download: thu muc tam
    ngoai cache/, xoa ngay khi doc xong (ke ca khi loi)."""
    with r2.grader_download(ANSWER_CSV) as path:
        table = read_local_table(path)
    try:
        with r2.grader_download(ANSWER_NOTES) as path:
            notes = path.read_text(encoding="utf-8-sig")
    except Exception as e:  # noqa: BLE001  thieu notes thi muc 3 bao ro, cac muc khac van cham
        print(f"Cảnh báo: không đọc được {ANSWER_NOTES} ({type(e).__name__})")
        notes = None
    return table, notes


# ---------- doc ket qua cua agent ----------

def load_results():
    """result_summary gop cua moi ket qua status valid trong output/bai0a/results.json (sau ghi de truoc)."""
    path = OUT / "results.json"
    if not path.is_file():
        return {}, []
    entries = [e for e in json.loads(path.read_text(encoding="utf-8")) if e.get("status", "valid") == "valid"]
    summary = {}
    for e in entries:
        summary.update(e.get("result_summary") or {})
    return summary, entries


def _int_column(df, exclude):
    """Cot (khong thuoc exclude) ma moi gia tri khong rong la so nguyen. Uu tien ten co goi y dem."""
    cands = []
    for c in df.columns:
        if c in exclude:
            continue
        v = pd.to_numeric(df[c], errors="coerce")
        if len(v) and v.notna().all() and (v % 1 == 0).all():
            cands.append(c)
    cands.sort(key=lambda c: next((i for i, h in enumerate(COUNT_HINTS) if h in c.lower()), len(COUNT_HINTS)))
    return cands[0] if cands else None


def find_agent_table(required, optional=("category_name",)):
    """(ten file, DataFrame, cot dem) cua CSV dau tien trong work/ co du cot required, mot cot dem, va khong co
    cot nao khac ngoai optional (de bang category x brand khong bi lay nham lam bang tong theo nhom)."""
    for path in sorted((OUT / "work").glob("*.csv")):
        df = read_local_table(path)
        if not set(required) <= set(df.columns):
            continue
        col = _int_column(df, set(required) | set(optional))
        if col is not None and set(df.columns) <= set(required) | set(optional) | {col}:
            return path.name, df, col
    return None, None, None


def _counts(df, keys, col):
    out = {}
    for _, row in df.iterrows():
        k = tuple(str(row[c]).strip() for c in keys)
        out[k] = out.get(k, 0) + int(float(row[col]))
    return out


# ---------- muc 1 ----------

def grade_table(answer):
    print("\n== Mục 1: bảng category × brand ==")
    ans_col = _int_column(answer, set(KEYS) | {"category_name"})
    name, agent, agent_col = find_agent_table(KEYS)
    if ans_col is None or agent is None:
        print(f"  RỚT: không tìm được bảng ({'đáp án' if ans_col is None else 'agent'} thiếu cột đếm / cột khóa)")
        return False, None
    print(f"  Cột đếm: đáp án '{ans_col}' ↔ agent '{agent_col}' (file work/{name})")
    exp, got = _counts(answer, KEYS, ans_col), _counts(agent, KEYS, agent_col)
    missing = sorted(set(exp) - set(got))
    extra = sorted(set(got) - set(exp))
    wrong = sorted(k for k in set(exp) & set(got) if exp[k] != got[k])
    print(f"  Đáp án {len(exp)} dòng, agent {len(got)} dòng; khớp {len(set(exp) & set(got)) - len(wrong)}")
    for k in missing:
        print(f"  thiếu: {k} (cần {exp[k]})")
    for k in extra:
        print(f"  thừa: {k} (agent {got[k]})")
    for k in wrong:
        print(f"  lệch: {k} cần {exp[k]}, agent {got[k]}")
    ok = not (missing or extra or wrong)
    print(f"  {'ĐẠT' if ok else 'RỚT'}")
    return ok, (answer, ans_col)


# ---------- muc 2 ----------

def numeric_keys(summary, exact, pattern):
    """Khoa so cua result_summary: co khoa dung ten thi chi lay khoa do, khong co moi do theo chuoi con
    (de max_total_products khong bi lay nham cho total_products)."""
    numeric = [k for k, v in summary.items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return [exact] if exact in numeric else [k for k in numeric if re.search(pattern, k, re.I)]


def _summary_ids(summary, pattern):
    """Ma nhom hang (dang CAT...) trong cac khoa result_summary khop pattern; gia tri co the ghep bang , ; |."""
    ids = set()
    for k, v in summary.items():
        if re.search(pattern, k, re.I) and isinstance(v, str):
            ids.update(x.strip() for x in re.split(r"[,;|]", v) if x.strip())
    return ids


def grade_totals(answer, ans_col, summary):
    print("\n== Mục 2: tổng theo nhóm hàng và nhóm lớn nhất ==")
    per_cat = {}
    for (cat, _), n in _counts(answer, KEYS, ans_col).items():
        per_cat[cat] = per_cat.get(cat, 0) + n
    top = max(per_cat.values())
    exp_max = {c for c, n in per_cat.items() if n == top}
    ok = True

    name, agent, col = find_agent_table(["category_id"])
    agent_cat = {}
    if agent is None:
        print("  RỚT: không có CSV tổng theo category_id trong work/")
        ok = False
    else:
        agent_cat = {k[0]: v for k, v in _counts(agent, ["category_id"], col).items()}
        diff = sorted(set(per_cat) ^ set(agent_cat)) + sorted(
            c for c in set(per_cat) & set(agent_cat) if per_cat[c] != agent_cat[c])
        print(f"  work/{name} (cột '{col}'): {len(agent_cat)} nhóm; " + ("khớp mọi nhóm" if not diff else f"lệch {diff}"))
        ok &= not diff

    checks = [(TOTAL_PRODUCTS, sum(per_cat.values())), (TOTAL_CATEGORIES, len(per_cat)), (MAX_COUNT, top)]
    for (label, pattern), exp in checks:
        keys = numeric_keys(summary, label, pattern)
        got = [summary[k] for k in keys]
        good = bool(got) and all(g == exp for g in got)
        print(f"  {label}: cần {exp}, result_summary {dict(zip(keys, got)) or 'không có khóa'} -> {'ĐẠT' if good else 'RỚT'}")
        ok &= good

    got_max = _summary_ids(summary, r"max|top|lon_nhat|largest")
    got_max = {x for x in got_max if x in per_cat} or got_max
    good = got_max == exp_max
    tie = f" (đồng hạng {len(exp_max)} nhóm)" if len(exp_max) > 1 else ""
    print(f"  nhóm lớn nhất: cần {sorted(exp_max)}{tie}, agent {sorted(got_max) or 'không có'} -> {'ĐẠT' if good else 'RỚT'}")
    ok &= good
    print(f"  {'ĐẠT' if ok else 'RỚT'}")
    return ok, agent_cat


# ---------- muc 3 ----------

def note_items(notes):
    """Moi dong bat dau bang -, *, + hoac so thu tu la mot y (bo dong tieu de #)."""
    items = []
    for line in notes.splitlines():
        m = re.match(r"^\s*(?:[-*+]|\d+[.)])\s+(.*\S)", line)
        if m:
            items.append(m.group(1))
    if not items:  # van xuoi: moi dong khong rong, khong phai tieu de, la mot y
        items = [line.strip() for line in notes.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    return items


HEADING = "tiêu đề, bỏ qua"


def _num(summary, key):
    return next((summary[k] for k in numeric_keys(summary, *key)), None)


def judge_note(item, agent_cat, summary, checks, table_ok):
    """(ket qua, cach kiem): so y cua dap an voi KET QUA CUA AGENT (bang trong work/, result_summary, checks).
    Chi kiem cac dang nhan dien duoc; con lai NEED_HUMAN."""
    low = item.lower()
    nums = [int(x) for x in re.findall(r"(?<![\w.])\d+(?![\w.])", re.sub(r"\bCAT\d+\b", " ", item))]
    ids = re.findall(r"\bCAT\d+\b", item)
    if item.rstrip().endswith(":") or (not nums and not ids and re.fullmatch(r"[\w\s]+", item)):
        return HEADING, ""
    max_ids = _summary_ids(summary, r"max|top|lon_nhat|largest")
    max_ids = {x for x in max_ids if x in agent_cat} or max_ids  # bo ten nhom (max_category_name), giu ma
    if re.search(r"đồng hạng|bằng nhau|\btie\b|hòa", low):
        return ("ĐẠT" if set(ids) <= max_ids else "RỚT"), f"agent báo đủ nhóm đồng hạng {ids}"
    if re.search(r"(lớn|nhiều|cao)\b.*\bnhất|max", low) and ids:
        n = _num(summary, MAX_COUNT)
        codes = {x for x in max_ids if re.fullmatch(r"CAT\d+", x)}  # danh sach co the kem ten nhom, chi so ma
        good = set(ids) == codes and (not nums or n == nums[-1])
        return ("ĐẠT" if good else "RỚT"), f"nhóm lớn nhất của agent {sorted(max_ids)}, số {n}"
    if len(ids) == 1 and nums:
        got = agent_cat.get(ids[0])
        return ("ĐẠT" if got == nums[-1] else "RỚT"), f"agent: {ids[0]} = {got}"
    if re.search(r"tổng sản phẩm|tổng số sản phẩm", low) and nums:
        got = _num(summary, TOTAL_PRODUCTS)
        return ("ĐẠT" if got == nums[-1] else "RỚT"), f"agent total_products = {got}"
    if re.search(r"tổng các phần|tổng toàn bộ", low):
        sums = [c for c in checks if re.search(r"total|sum|tổng", c.get("name", ""), re.I)]
        good = bool(sums) and all(c.get("status") == "pass" for c in sums)
        return ("ĐẠT" if good else "RỚT"), f"check tổng của agent: {[(c['name'], c['status']) for c in sums]}"
    if re.search(r"thương hiệu|brand", low):
        return ("ĐẠT" if table_ok else "RỚT"), "bảng category × brand (mục 1)"
    return NEED_HUMAN, ""


def grade_notes(notes, agent_cat, summary, checks, table_ok):
    print("\n== Mục 3: answer_notes.md ==")
    if notes is None:
        print("  RỚT: không có answer_notes.md")
        return False
    items = note_items(notes)
    verdicts = []
    for i, item in enumerate(items, 1):
        verdict, how = judge_note(item, agent_cat, summary, checks, table_ok)
        verdicts.append(verdict)
        print(f"  {i}. {item}\n     -> {verdict}{f' ({how})' if how else ''}")
    n_code = sum(v in ("ĐẠT", "RỚT") for v in verdicts)
    n_human = verdicts.count(NEED_HUMAN)
    if n_code == 0:  # khong co y nao kiem duoc bang code: khong bao ĐẠT
        print(f"  {NEED_HUMAN.upper()} ({n_human} ý, không ý nào kiểm được bằng code)")
        return None
    ok = "RỚT" not in verdicts
    print(f"  {'ĐẠT' if ok else 'RỚT'} ({n_code} ý kiểm bằng code; {n_human} ý {NEED_HUMAN}; "
          f"{verdicts.count(HEADING)} dòng tiêu đề)")
    return ok


def main():
    before = set(_answers_in_cache())
    summary, entries = load_results()
    if not entries:
        print(f"Không có kết quả của agent trong {OUT / 'results.json'}")
        return 1
    print(f"Kết quả agent: {[e['id'] for e in entries]}; khóa result_summary: {sorted(summary)}")
    answer, notes = load_answers()

    table_ok, info = grade_table(answer)
    ans_col = info[1] if info else _int_column(answer, set(KEYS) | {"category_name"})
    if ans_col is None:
        totals_ok, agent_cat = False, {}
    else:
        totals_ok, agent_cat = grade_totals(answer, ans_col, summary)
    checks = [c for e in entries for c in e.get("checks") or []]
    notes_ok = grade_notes(notes, agent_cat, summary, checks, table_ok)

    leftover = sorted(set(_answers_in_cache()) - before)
    if leftover:
        print(f"LỖI: cache/ còn {len(leftover)} đường dẫn chứa 'answers' do script này tạo: {leftover}")
        return 1
    if before:
        print(f"Cảnh báo: cache/ đã có sẵn {len(before)} đường dẫn chứa 'answers' từ trước (không xóa).")
    marks = {"Mục 1": table_ok, "Mục 2": totals_ok, "Mục 3": notes_ok}  # None = cần người xem
    label = {True: "ĐẠT", False: "RỚT", None: NEED_HUMAN.upper()}
    graded = [v for v in marks.values() if v is not None]
    total = "RỚT" if False in graded else "ĐẠT" + (f" (còn mục {NEED_HUMAN})" if None in marks.values() else "")
    print("\nTổng: " + ", ".join(f"{k} {label[v]}" for k, v in marks.items()) + f" -> {total}")
    return 1 if False in graded else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
