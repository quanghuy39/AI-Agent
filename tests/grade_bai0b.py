"""Cham bai0b: chay profiling nhu binh thuong, roi so voi answers/bai0b/injected_errors.json (quyen grader).
KHONG phai pytest. Chay tay: python -m tests.grade_bai0b

- profile_problem khong nhan gi tu dap an.
- Dap an duoc tai vao mot thu muc TAM ngoai cache/ (download_with_cache ghi file + .etag ra dia), doc xong xoa ngay
  trong finally. Cuoi script kiem tra cache/ khong co duong dan nao chua "answers" moi xuat hien.
- Khong ghi dap an vao output/ hay Ledger.
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

from profiling import quality
from storage import r2

PROBLEM = "bai0b"
ANSWER_KEY = f"answers/{PROBLEM}/injected_errors.json"
TICKET, CUSTOMER, CSAT = "SUPPORT_TICKET", "CUSTOMER", "csat_score"
RANGES = {f"{TICKET}.{CSAT}": (1, 5)}


def _answers_in_cache():
    return sorted(str(p) for p in r2.CACHE_DIR.rglob("*") if "answers" in p.parts)


def load_answers():
    """Tai dap an vao thu muc tam, doc, xoa thu muc tam (ke ca khi loi)."""
    tmp = Path(tempfile.mkdtemp(prefix="grade_"))
    try:
        path = r2.download_with_cache(ANSWER_KEY, cache_dir=tmp, grader=True)
        return json.loads(Path(path).read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def expected_count(value):
    """customer_duplicate_rows: {"ids", "extra_rows"}; cac loai khac: list/dict, so loi = so phan tu."""
    if isinstance(value, dict) and "extra_rows" in value:
        return int(value["extra_rows"])
    return len(value)


def found_counts(report):
    """{loai loi: (so tim thay hoac None, ghi chu)} lay tu report."""
    tables = report["tables"]
    out = {}

    def get(name, fn):
        try:
            out[name] = (fn(), "")
        except (KeyError, StopIteration, TypeError) as e:
            out[name] = (None, f"thiếu trong report: {type(e).__name__} {e}")

    get("customer_duplicate_rows", lambda: tables[CUSTOMER]["duplicate_key_rows"])
    get("csat_null", lambda: tables[TICKET]["columns"][CSAT]["null_count"])

    def out_of_range():
        r = next(x for x in report["range_checks"] if (x["table"], x["column"]) == (TICKET, CSAT))
        return r["result"]["details"]["below"] + r["result"]["details"]["above"]
    get("csat_out_of_range", out_of_range)

    def rel():
        return next(c for c in report["relationship_checks"]
                    if c["child"] == TICKET and c["parent"] == CUSTOMER and c["status"] != "rejected")
    get("orphan_tickets", lambda: rel()["result"]["details"]["left_unmatched_normalized"])
    get("key_format_mismatch", lambda: rel()["result"]["details"]["recoverable"])
    return out


def main():
    before = set(_answers_in_cache())
    report = quality.profile_problem(PROBLEM, ranges=RANGES)  # khong truyen dap an vao
    answers = load_answers()
    found = found_counts(report)

    rows, ok_all = [], True
    for kind, (got, note) in found.items():
        exp = expected_count(answers[kind]) if kind in answers else None
        ok = exp is not None and got == exp
        ok_all &= ok
        rows.append((kind, "-" if exp is None else str(exp), "-" if got is None else str(got),
                     "ĐẠT" if ok else "KHÔNG ĐẠT", note or ("không có trong đáp án" if exp is None else "")))

    head = ("Loại lỗi", "Đáp án", "Agent tìm thấy", "Kết quả")
    widths = [max(len(head[i]), *(len(r[i]) for r in rows)) for i in range(4)]
    line = lambda cells: "| " + " | ".join(c.ljust(w) for c, w in zip(cells, widths)) + " |"  # noqa: E731
    print(line(head))
    print("|" + "|".join("-" * (w + 2) for w in widths) + "|")
    for r in rows:
        print(line(r[:4]) + (f"  ({r[4]})" if r[4] else ""))

    leftover = sorted(set(_answers_in_cache()) - before)
    if leftover:
        print(f"LỖI: cache/ còn {len(leftover)} đường dẫn chứa 'answers' do script này tạo: {leftover}")
        return 1
    if before:
        print(f"Cảnh báo: cache/ đã có sẵn {len(before)} đường dẫn chứa 'answers' từ trước (không xóa).")
    print("Tổng: " + ("ĐẠT hết" if ok_all else "có loại KHÔNG ĐẠT"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
