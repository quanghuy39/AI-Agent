"""Cham mot bai: so ket qua cua agent (output/<bai>/) voi answers/<bai>/ (quyen grader).
KHONG phai pytest. Chay tay: python -m tests.grade <bai>

- Moi file trong answers/<bai>/ doc qua r2.grader_download (thu muc tam ngoai cache/, xoa ngay); cuoi script kiem
  tra cache/ khong co duong dan nao chua "answers". Khong dua dap an cho agent, khong ghi vao output/ hay Ledger,
  khong sua file cua agent.
- Moc so: moi dong (md/txt), moi o so CSV, moi la JSON co so. So cua agent lay tu result_summary (moi ket qua
  valid) va moi o so trong work/*.csv; chi so cung loai (ty le voi ty le, so dem voi so nguyen, p-value/kiem dinh
  voi kiem dinh). KHỚP: bang gia tri (ty le: lech <= 0,1 diem phan tram; so co n chu so thap phan: bang khi lam
  tron) VA nhan co tu chung. CẦN NGƯỜI XEM: chi trung gia tri, nhan khong chung (co the trung ngau nhien).
  LỆCH: khong bang, nhung co so cung loai cung nhan. Con lai KHÔNG TÌM THẤY.
- Bay va "cac cau agent phai hoi" (muc co tieu de chua bẫy/trap/phải hỏi/cần hỏi): so tu voi decisions.json,
  findings.json, open_items.json. ĐÃ NÊU / KHÔNG NÊU / CẦN NGƯỜI XEM theo ty le tu chung (chi la goi y; nguoi
  cham xem lai). Moi muc tach thanh cac y (theo ; ? va cau); ĐÃ NÊU chi khi moi y deu dat nguong.
"""

import argparse
import json
import math
import re
import sys
import unicodedata

from analysis.meta_grader import read_local_table
from config import ROOT
from storage import r2

OUTPUT_ROOT = ROOT / "output"
TASK_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
PCT_TOL = 0.1          # diem phan tram
MENTIONED, PARTIAL = 0.5, 0.2  # ty le tu chung de coi la ĐÃ NÊU / CẦN NGƯỜI XEM
LIST_SECTION_RE = re.compile(r"bẫy|trap|phải hỏi|cần hỏi|nên hỏi|hỏi lại|câu hỏi", re.I)
STOP = set("""và của cho các những một được trong với theo khi thì là có không này đó nếu hoặc từ đến trên dưới
bao nhiêu tổng tính số khách hàng đơn nhóm lần the and for with from that this what which are not total count
rate ratio csv""".split())
STAT_RE = re.compile(r"(^|[_\W])(p|pvalue|kruskal|cramers|chi2|anova)([_\W]|$)", re.I)
NUM_RE = re.compile(r"(?<![\w.,])[-+]?\d+(?:[.,]\d+)*(?:\s*%)?")


def _answers_in_cache():
    return sorted(str(p) for p in r2.CACHE_DIR.rglob("*") if "answers" in (x.lower() for x in p.parts))


def tokens(text):
    """Tu (chu thuong, >= 3 ky tu, bo tu dung); tach theo moi ky tu khong phai chu/so (ke ca _ va -)."""
    text = unicodedata.normalize("NFC", str(text)).lower()
    return {t for t in re.split(r"[^\w]+|_", text) if len(t) >= 3 and t not in STOP and not t.isdigit()}


def parse_number(tok):
    """'356.242' -> (356242, 0, False); '28,4%' -> (28.4, 1, True); '0.284' -> (0.284, 3, False)."""
    pct = tok.rstrip().endswith("%")
    t = tok.replace("%", "").strip().lstrip("+")
    parts = re.split(r"[.,]", t.lstrip("-"))
    sign = -1 if t.startswith("-") else 1
    if len(parts) > 1 and all(len(p) == 3 for p in parts[1:]) and not pct and parts[0] != "0":  # dau phan nghin
        return sign * float("".join(parts)), 0, pct
    if len(parts) == 1:
        return sign * float(parts[0]), 0, pct
    return sign * float("".join(parts[:-1]) + "." + parts[-1]), len(parts[-1]), pct


def _skip(value, decimals, pct, text, start):
    if not pct and decimals == 0 and 1990 <= value <= 2100:
        return True  # nam
    before = text[:start].rstrip()
    rest = text[start:].lstrip("0123456789")
    return bool(re.search(r"(^|\s)(Q|R|D|F|O|top|mục|bước|câu)$", before, re.I)) or bool(
        re.fullmatch(r"\s*", text[:start]) and rest[:1] in (".", ")") and rest[1:2] in (" ", ""))


def numbers_in(text):
    out = []
    for m in NUM_RE.finditer(text):
        value, decimals, pct = parse_number(m.group(0))
        if not _skip(value, decimals, pct, text, m.start()):
            out.append((m.group(0).strip(), value, decimals, pct))
    return out


# ---------- doc dap an ----------

def load_answer_files(task):
    """[(ten file, noi dung | DataFrame | object JSON)]; moi file qua grader_download."""
    prefix = f"answers/{task}/"
    files = []
    for obj in r2.list_objects(prefix, grader=True):
        key = obj["key"]
        name = key[len(prefix):]
        if not name or name.endswith("/"):
            continue
        suffix = name.lower().rsplit(".", 1)[-1]
        with r2.grader_download(key) as path:
            if suffix in ("md", "txt"):
                files.append((name, path.read_text(encoding="utf-8-sig")))
            elif suffix == "csv":
                files.append((name, read_local_table(path)))
            elif suffix == "json":
                files.append((name, json.loads(path.read_text(encoding="utf-8-sig"))))
            else:
                print(f"Bỏ qua {name}: không đọc định dạng .{suffix}")
    return files


def _flatten(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flatten(v, f"{path}.{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _flatten(v, f"{path}[{i}]")
    else:
        yield path, obj


def split_answer(files):
    """(moc so [(nguon, nhan, token, value, decimals, pct)], muc danh sach [(muc, y)])."""
    milestones, list_items = [], []
    for name, content in files:
        if isinstance(content, str):
            section = ""
            for line in content.splitlines():
                s = line.strip()
                if not s or re.fullmatch(r"\|?[\s:|-]+\|?", s):
                    continue
                if s.startswith("#"):
                    section = s.lstrip("#").strip()
                    continue
                if LIST_SECTION_RE.search(section):
                    item = re.sub(r"^([-*+]|\d+[.)])\s+", "", s)
                    list_items.append((section, item))
                    continue
                for tok, value, dec, pct in numbers_in(s):
                    milestones.append((f"{name} [{section}]" if section else name, s, tok, value, dec, pct))
        elif hasattr(content, "iterrows"):  # CSV
            text_cols = [c for c in content.columns if pd_numeric(content[c]) is None]
            for _, row in content.iterrows():
                ident = " ".join(f"{c}={row[c]}" for c in text_cols)  # nhan = o chu dinh danh dong + ten cot
                for c in content.columns:
                    cell = str(row[c]) if row[c] is not None else ""
                    for tok, value, dec, pct in numbers_in(cell):
                        if c not in text_cols and re.fullmatch(r"\s*" + re.escape(tok) + r"\s*", cell):
                            milestones.append((name, f"{ident} | {c}", tok, value, dec, pct))
        else:  # JSON
            for path, v in _flatten(content):
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    dec = len(repr(v).split(".")[1]) if isinstance(v, float) and "." in repr(v) else 0
                    milestones.append((name, path, repr(v), float(v), dec, False))
                elif isinstance(v, str) and LIST_SECTION_RE.search(path):
                    list_items.append((path, v))
    return milestones, list_items


# ---------- so cua agent ----------

def load_agent(out):
    """(so [(vi tri, token nhan, value)], tap tu cua gia tri chuoi trong work/, ket qua valid)."""
    nums, value_words = [], set()
    results = json.loads((out / "results.json").read_text(encoding="utf-8")) if (out / "results.json").is_file() else []
    results = [r for r in results if r.get("status", "valid") == "valid"]
    for r in results:
        for k, v in (r.get("result_summary") or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                nums.append((f"{r['id']}.{k}", tokens(k), float(v)))
            elif isinstance(v, str):
                value_words |= tokens(v)
    for path in sorted((out / "work").glob("*.csv")):
        df = read_local_table(path)
        text_cols = [c for c in df.columns if pd_numeric(df[c]) is None]
        for _, row in df.iterrows():
            row_label = " ".join(str(row[c]) for c in text_cols if row[c] is not None)
            value_words |= tokens(row_label)
            for c in df.columns:
                if c in text_cols or row[c] is None:
                    continue
                try:
                    v = float(row[c])
                except ValueError:
                    continue
                where = f"work/{path.name}: {row_label or '-'} | {c}"
                nums.append((where, tokens(row_label) | tokens(c), v))  # khong tinh ten file (qua chung)
    return nums, value_words, results


def pd_numeric(series):
    vals = [v for v in series if v is not None]
    try:
        return [float(v) for v in vals] if vals else None
    except ValueError:
        return None


def value_matches(value, decimals, pct, v):
    if pct:
        return abs(v * 100 - value) <= PCT_TOL + 1e-9 or (abs(v) > 1 and abs(v - value) <= PCT_TOL + 1e-9)
    if 0 < abs(value) < 1 and abs(v) < 1 and decimals >= 2:
        return abs(v - value) <= PCT_TOL / 100 + 1e-9  # ty le viet dang 0,284
    if decimals == 0:
        return v == value
    return math.isclose(round(v, decimals), value, abs_tol=10 ** -decimals / 2 + 1e-12)


def grade_milestones(milestones, agent_nums, value_words):
    print("\n== Mốc số ==")
    counts = {"KHỚP": 0, "LỆCH": 0, "KHÔNG TÌM THẤY": 0, "CẦN NGƯỜI XEM": 0}
    for i, (src, label, tok, value, dec, pct) in enumerate(milestones, 1):
        lt = tokens(re.sub(NUM_RE, " ", label))
        stat = bool(STAT_RE.search(label))  # p-value / he so kiem dinh: chi so voi so cung la kiem dinh
        ratio = pct or (0 < abs(value) < 1 and dec > 0)

        def same_kind(w, v):
            if stat != bool(STAT_RE.search(w)):
                return False
            return abs(v) <= 1 if ratio else (v == int(v) if dec == 0 else True)

        hits = [(w, t, v) for w, t, v in agent_nums if value_matches(value, dec, pct, v) and same_kind(w, v)]
        labelled = [h for h in hits if lt & h[1]]
        related = [(len(lt & t), w, v) for w, t, v in agent_nums
                   if ((lt & t & value_words) or len(lt & t) >= 2) and same_kind(w, v)]
        if labelled:
            best = max(labelled, key=lambda h: len(lt & h[1]))
            status, detail = "KHỚP", f"{best[0]} = {best[2]:g}"
        elif hits and abs(value) >= 1:  # trung gia tri nhung khong chung nhan: co the ngau nhien
            status, detail = "CẦN NGƯỜI XEM", "chỉ trùng giá trị: " + "; ".join(f"{w} = {v:g}" for w, _, v in hits[:3])
        elif related:
            top = max(o for o, _, _ in related)
            cands = [(w, v) for o, w, v in related if o == top]
            target = value / 100 if pct else value
            w, v = min(cands, key=lambda c: min(abs(c[1] - target), abs(c[1] - value)))
            status, detail = "LỆCH", f"gần nhất cùng nhãn: {w} = {v:g}"
        else:
            status, detail = "KHÔNG TÌM THẤY", ""
        counts[status] += 1
        print(f"  {i}. [{src}] {label}\n     số {tok} -> {status}{f' ({detail})' if detail else ''}")
    return counts


def grade_list_items(items, results, out):
    print("\n== Bẫy và câu agent phải hỏi ==")
    counts = {"ĐÃ NÊU": 0, "KHÔNG NÊU": 0, "CẦN NGƯỜI XEM": 0}
    if not items:
        print("  Không tìm thấy mục bẫy / câu phải hỏi trong đáp án -> CẦN NGƯỜI XEM")
        return counts
    docs = []
    for name in ("decisions", "findings", "open_items"):
        path = out / f"{name}.json"
        for d in json.loads(path.read_text(encoding="utf-8")) if path.is_file() else []:
            if d.get("status") != "superseded":  # decision cua lan lam truoc khong tinh la da neu
                docs.append((f"{name}:{d.get('id', '?')}", d.get("text", "")))
    for r in results:  # ten check (y can_be_wrong_if) cung la cho agent da neu rui ro
        for c in r.get("checks") or []:
            docs.append((f"results:{r['id']} check", c.get("name", "")))
    for i, (section, item) in enumerate(items, 1):
        parts = [x for x in re.split(r"[;?]|\.(?:\s|$)|:\s", item) if tokens(x)]  # tach thanh tung y
        scores = [max(((len(tokens(x) & tokens(text)) / len(tokens(x)), where) for where, text in docs),
                      default=(0, "-")) for x in parts]
        if scores and all(sc >= MENTIONED for sc, _ in scores):
            status = "ĐÃ NÊU"
        elif not scores or all(sc < PARTIAL for sc, _ in scores):
            status = "KHÔNG NÊU"
        else:
            status = "CẦN NGƯỜI XEM"
        counts[status] += 1
        detail = "; ".join(f"ý {j} {sc:.0%} ({where})" for j, (sc, where) in enumerate(scores, 1))
        print(f"  {i}. [{section}] {item}\n     -> {status} (từ chung theo ý: {detail})")
    return counts


def grade(task):
    """Cham mot bai; tra ve ma thoat (0 da cham, 1 khong cham duoc hoac cache/ co answers, 2 ten bai sai)."""
    if not TASK_RE.match(task):
        print(f"Tên bài không hợp lệ: {task!r}")
        return 2
    out = OUTPUT_ROOT / task
    before = set(_answers_in_cache())
    agent_nums, value_words, results = load_agent(out)
    if not results:
        print(f"Không có kết quả của agent trong {out / 'results.json'}")
        return 1
    print(f"Kết quả agent: {[r['id'] for r in results]}; {len(agent_nums)} số (result_summary + work/*.csv)")
    files = load_answer_files(task)
    print(f"File đáp án: {[n for n, _ in files]}")
    if not files:
        print(f"Không có đáp án trong answers/{task}/")
        return 1
    milestones, items = split_answer(files)

    m = grade_milestones(milestones, agent_nums, value_words)
    t = grade_list_items(items, results, out)

    leftover = sorted(set(_answers_in_cache()) - before)
    if leftover or before:
        print(f"LỖI: cache/ có {len(set(leftover) | before)} đường dẫn chứa 'answers': {sorted(set(leftover) | before)}")
        return 1
    print("\n== Tổng kết ==")
    print(f"  Mốc số ({len(milestones)}): " + ", ".join(f"{k} {v}" for k, v in m.items()))
    print(f"  Bẫy / câu phải hỏi ({len(items)}): " + ", ".join(f"{k} {v}" for k, v in t.items()))
    print("  cache/: không có đường dẫn chứa 'answers'")
    print("  Ghi chú: LỆCH, CẦN NGƯỜI XEM và mọi đánh giá bẫy là đối chiếu tự động theo từ khóa, cần người xem lại.")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Chấm kết quả của agent bằng answers/ (quyền grader).")
    p.add_argument("task", metavar="bai")
    return grade(p.parse_args(argv).task)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
