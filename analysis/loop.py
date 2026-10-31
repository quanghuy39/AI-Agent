"""Cac pha phan tich: hieu de, tong quan du lieu, lap ke hoach, tung cau hoi (AI viet code, runner chay, cham meta,
ghi so), tong hop. Thu tu pha, luu tien do va ma thoat nam o main.py (run_task); module nay khong co luong rieng.

Chay: python -m analysis.loop <task> [--sensitive BANG.cot ...] [--use-fake] [--max-retries 3] [--yes] [--no-sync]
--yes tu tra loi "y" (chi dung khi test). --use-fake dung FakeLLM voi kich ban demo (khong goi AI that).

Ma thoat: 0 xong; 1 loi cau hinh/yeu cau AI hoac loi R2; 2 het han muc chuoi model (chay lai de tiep tuc); 3 dung
giua chung (nguoi dung chon n, het so lan thu, AI bao khong tra loi duoc, ban tom tat lo gia tri che).
Moi chuoi dua lai cho AI deu qua mask_text: gia tri that cua cot che -> [đã che]. Gia tri that chi o bo nho.
"""

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import config
import context_builder
import llm
from analysis.meta_grader import grade_meta, read_local_table
from ledger import DecisionStatus, Ledger, ResultStatus
from ledger.ledger import SUPERSEDED_DIR
from ledger.schema import now_iso
from profiling import flags as fl
from profiling import quality
from profiling.data_summary import build_data_summary
from runner import run_code as runner
from skills.analysis import render_prompt
from skills.analysis.schemas import AnalysisPlan, AnalysisStep, ProblemRestatement
from skills.report.schemas import SynthesisOutput
from storage import r2

MASK = "[đã che]"
PROBLEM_FILE = "de_bai.md"
ROLE = "analysis"
DONE, ERROR, EXHAUSTED, STOPPED = 0, 1, 2, 3
CHAIN_OPEN_ITEM = "hết hạn mức chuỗi, chạy tiếp sau (chuỗi model analysis)"
PLAN_OPEN_ITEM = "Kế hoạch phân tích chưa được chấp nhận; chạy lại để xem lại, hoặc --restart để làm lại từ đầu."
PENDING_OPEN_ITEM = "Còn decision chờ xác nhận; chạy lại để trả lời tiếp."
YES = ("y", "yes", "c", "co", "có")
NO = ("n", "no", "k", "khong", "không")
NO_HANDLING = "(chưa có đề xuất của AI) giữ nguyên, không xử lý riêng"  # cach xu ly mac dinh cua co muc flag
# Diem dung xu ly bat thuong khi chi co co info: hoi bang QUESTION, luu TEXT (dua cho AI nen phai tu du nghia)
KEEP_DATA_KEY = "keep_data|info_flags"
KEEP_DATA_QUESTION = "Chấp nhận giữ nguyên dữ liệu với các lưu ý trên?"
KEEP_DATA_TEXT = "Giữ nguyên dữ liệu, không xử lý riêng các lưu ý về chất lượng dữ liệu"
ACCEPT_FLAGS_KEY = "accept_flags|"  # decision "chap nhan R.. du co co", sinh sau khi giai, khong thuoc diem dung nay
EDIT_PROMPT = "  Nội dung mới: "
CORRECTION_PROMPT = "Gõ chỉnh sửa (để trống để dừng): "
PLAN_CORRECTION_PROMPT = "Gõ góp ý (để trống để quay lại câu hỏi): "
# go tiep sau mot cau tra loi, khong phai diem dung moi
FOLLOW_UP_PROMPTS = (EDIT_PROMPT, CORRECTION_PROMPT, PLAN_CORRECTION_PROMPT)
PLAN_QUESTION = "Chấp nhận danh sách câu hỏi nhỏ của kế hoạch?"
PLAN_CORRECTION_PREFIX = "Góp ý kế hoạch: "  # phan biet voi chinh sua cach hieu de trong user_corrections


class StopLoop(Exception):
    """Dung vong lap co chu dich (da ghi open_item neu can). code: ma thoat."""

    def __init__(self, message, code=STOPPED):
        super().__init__(message)
        self.code = code


@dataclass
class Context:
    task: str
    problem: str
    summary: dict                     # ket qua build_data_summary
    ledger: Ledger
    input_dir: Path                   # cache/input/<task>/ (runner da dong bo voi R2)
    work_dir: Path                    # output/<task>/work/
    masked_values: set = field(default_factory=set, repr=False)
    decisions: list = field(default_factory=list)   # dong "- D1 [kind] text" da xac nhan
    sensitive: list = field(default_factory=list)   # "BANG.cot" da dung: --sensitive + mac dinh cua bai
    report: dict = field(default_factory=dict, repr=False)  # data_quality_report
    model: str | None = None                        # model cua lan goi AI gan nhat (khong tinh trung cache)
    switched_from: str | None = None                # model cu khi da doi model ma chua gan co vao ket qua
    _mask_re: object = field(default=None, repr=False)

    def mask(self, text):
        return mask_text(text, self.masked_values, self)


# ---------- che gia tri ----------

def mask_text(text, values, cache=None):
    """Thay moi gia tri trong values (chuoi >= ANALYSIS_MASK_MIN_CHARS ky tu) bang [đã che]; uu tien chuoi dai."""
    if not text or not values:
        return text or ""
    pattern = getattr(cache, "_mask_re", None)
    if pattern is None:
        keep = sorted((v for v in values if len(v) >= config.ANALYSIS_MASK_MIN_CHARS), key=len, reverse=True)
        if not keep:
            return text
        pattern = re.compile("|".join(re.escape(v) for v in keep))
        if cache is not None:
            cache._mask_re = pattern
    return pattern.sub(MASK, text)


def _masked_values(report, masked_columns):
    """Tap gia tri that (chuoi) cua cac cot che, doc tu file input trong cache."""
    files = {f["table"]: f["key"] for f in report.get("files") or []}
    values = set()
    for m in masked_columns:
        key = files.get(m["table"])
        if key is None:
            continue
        df = read_local_table(Path(r2.download_with_cache(key)))  # so etag; cache con moi thi khong tai lai
        if m["column"] in df.columns:
            values |= {str(v).strip() for v in df[m["column"]].dropna().unique()}
    return {v for v in values if len(v) >= config.ANALYSIS_MASK_MIN_CHARS}


# ---------- nap ngu canh ----------

def _read_problem(task):
    prefix = f"{r2.get_r2_settings()['R2_INPUT_PREFIX']}{task}/"
    keys = [o["key"] for o in r2.list_objects(prefix) if "answers" not in Path(o["key"]).parts]
    key = next((k for k in keys if k.rsplit("/", 1)[-1].lower() == PROBLEM_FILE), None)
    if key is None:
        raise StopLoop(f"Không có {PROBLEM_FILE} trong {prefix}")
    return Path(r2.download_with_cache(key)).read_text(encoding="utf-8-sig")


def _sensitive(task, report, sensitive):
    """--sensitive + mac dinh cua bai (chi cot co that); (SENSITIVE) cua dictionary do build_data_summary tu gop."""
    out = list(sensitive or [])
    cols = {n: list(t["columns"]) for n, t in report["tables"].items()}
    for spec in config.ANALYSIS_DEFAULT_SENSITIVE.get(task, []):
        try:
            quality.resolve_table_column(spec, cols, "sensitive")
        except ValueError:
            continue
        if spec not in out:
            out.append(spec)
    return out


def load_context(task, sensitive=None):
    led = Ledger(runner.OUTPUT_DIR / task)
    problem = _read_problem(task)
    profile = led.get_data_profile()
    report = profile.get(quality.REPORT_KEY) if isinstance(profile, dict) else None
    if report is None:
        report = quality.profile_problem(task)
    quality.ensure_flag_keys(report)
    columns = _sensitive(task, report, sensitive)
    try:
        summary = build_data_summary(report, sensitive=columns, max_chars=config.SUMMARY_MAX_CHARS)
    except ValueError as e:  # bang/cot trong --sensitive khong ton tai
        raise StopLoop(f"Cột che không hợp lệ: {e}") from None
    prefix = f"{r2.get_r2_settings()['R2_INPUT_PREFIX']}{task}/"
    ctx = Context(task=task, problem=problem, summary=summary, ledger=led, input_dir=r2.CACHE_DIR / prefix,
                  work_dir=runner.OUTPUT_DIR / task / "work",
                  masked_values=_masked_values(report, summary["masked_columns"]), sensitive=columns, report=report,
                  decisions=[_decision_line(d) for d in led.decisions() if d.status == DecisionStatus.CONFIRMED])
    led.log_event("analysis", "load_context", inputs={"task": task},
                  extra={"summary_chars": summary["chars"], "level": summary["level"],
                         "masked_columns": summary["masked_columns"], "n_masked_values": len(ctx.masked_values)})
    return ctx


# ---------- hoi nguoi dung ----------

def confirm(question, yes=False, ask_user=input):
    """Hoi y/n; nhap khac thi hoi lai, chi n moi la khong dong y."""
    if yes:
        print(f"{question} [y/n] y (--yes)")
        return True
    while True:
        answer = ask_user(f"{question} [y/n] ").strip().lower()
        if answer in YES or answer in NO:
            return answer in YES


def _render(ctx, prompt_name, **variables):
    """Render prompt; moi bien (tru data summary da che san) deu qua ctx.mask."""
    summary = variables.pop(context_builder.SUMMARY, ctx.summary["text"])
    return render_prompt(prompt_name, problem=ctx.mask(ctx.problem), data_summary=summary,
                         **{k: ctx.mask(v) for k, v in variables.items()})


def _build_prompt(ctx, prompt, variables, shortened, question=None):
    """Prompt da che va cat cho vua model nho nhat; moi lan ghi run_log. Qua dai -> open_item + StopLoop."""
    text, info = context_builder.build_prompt(ROLE, prompt, lambda **v: _render(ctx, prompt, **v), variables, shortened)
    ctx.ledger.log_event("analysis", "prompt_fit", inputs={"question": question} if question else {},
                         status="ok" if text is not None else "too_long", extra=info)
    if text is None:
        where = f"{question}: " if question else ""
        ctx.ledger.add_open_item_once(f"{where}prompt quá dài so với model nhỏ nhất (prompt {prompt}).")
        raise StopLoop(f"{where}prompt {prompt} quá dài so với model nhỏ nhất.")
    return text


def _note_model(ctx, result):
    """Theo doi model da dung. Con co doi model chua gan vao ket qua thi state giu model cu, de dung giua chung
    roi chay tiep van phat hien duoc. Trung cache khong tinh."""
    if result.cached:
        return
    if result.model_switched and ctx.switched_from is None:
        ctx.switched_from = result.previous_model
    ctx.model = result.model_key
    if ctx.switched_from is None and ctx.ledger.get_state().model != ctx.model:
        ctx.ledger.update_state(model=ctx.model)


def _call(ctx, text, schema):
    result = llm.ask_model([{"role": "user", "content": text}], role=ROLE, json_schema=schema, task_id=ctx.task)
    _note_model(ctx, result)
    return result.data


def _decision_line(d):
    return f"- {d.id} [{d.kind.value}] {d.text}"


# ---------- decision cho xac nhan ----------

def _text_key(kind, text):
    """source_key cua gia dinh do AI viet: tu noi dung goc, nen sua noi dung (chon s) roi chay lai khong tao trung."""
    return f"ai|{kind}|{hashlib.sha1(text.encode('utf-8')).hexdigest()[:12]}"


def _add_pending(ctx, kind, text, required=True, source_key=None):
    """Ghi decision pending; da co (cung source_key, hoac cung kind va noi dung) thi tra ve decision do."""
    for d in ctx.ledger.active_decisions():
        if (source_key and d.source_key == source_key) or (d.kind == kind and d.text == text):
            return d
    return ctx.ledger.add_decision(kind, text, required=required, source_key=source_key)


def _confirm_decision(ctx, d, text=None):
    """Xac nhan decision; co text thi thay bang noi dung nguoi dung go."""
    if text:
        ctx.ledger.revise_decision(d.id, text)
    else:
        ctx.ledger.confirm_decision(d.id)
    ctx.decisions = [_decision_line(x) for x in ctx.ledger.decisions() if x.status == DecisionStatus.CONFIRMED]


def _pending(ctx, kind):
    return [d for d in ctx.ledger.decisions() if d.status == DecisionStatus.PENDING and d.kind == kind]


def _stop_pending(ctx, open_item):
    ctx.ledger.add_open_item_once(open_item)
    ctx.ledger.log_event("analysis", "confirm_decisions", status="stopped")
    raise StopLoop("Dừng ở bước xác nhận; chạy lại để trả lời tiếp các decision còn chờ.")


def _review_each(ctx, kind, title, yes, ask_user, open_item):
    """Hoi tung decision pending cua kind: y dong y, s go noi dung khac, n dung."""
    items = _pending(ctx, kind)
    if items:
        print(f"\n{title}:")
    for d in items:
        print(f"  {d.id}: {KEEP_DATA_QUESTION if d.source_key == KEEP_DATA_KEY else d.text}")
        text = None
        while not yes:
            answer = ask_user("  y = đồng ý, s = gõ nội dung khác, n = dừng: ").strip().lower()
            if answer in YES:
                break
            if answer == "n":
                _stop_pending(ctx, open_item)
            text = ask_user(EDIT_PROMPT).strip() if answer == "s" else None
            if text:
                break
        _confirm_decision(ctx, d, text)


def _review_list(ctx, title, yes, ask_user, open_item):
    """Hoi gop cac gia dinh con lai: in danh sach danh so; y chap nhan tat ca, go so de sua tung cai, n dung."""
    items = _pending(ctx, "assumption")
    while items:
        print(f"\n{title}:")
        for i, d in enumerate(items, 1):
            print(f"  {i}. {d.text}")
        answer = "y" if yes else ask_user("  y = chấp nhận tất cả, số = sửa mục đó, n = dừng: ").strip().lower()
        if answer in YES:
            for d in items:
                _confirm_decision(ctx, d)
        elif answer == "n":
            _stop_pending(ctx, open_item)
        elif answer.isdigit() and 1 <= int(answer) <= len(items):
            text = ask_user(EDIT_PROMPT).strip()
            if text:
                _confirm_decision(ctx, items[int(answer) - 1], text)
        items = _pending(ctx, "assumption")


def _info_flags(ctx):
    return [f for f in fl.all_flags(ctx.report) if f["severity"] == fl.INFO]


def _has_anomaly_decision(ctx):
    return any(d.kind == "anomaly_handling" and not (d.source_key or "").startswith(ACCEPT_FLAGS_KEY)
               for d in ctx.ledger.active_decisions())


def _print_info_flags(ctx):
    items = _info_flags(ctx)
    if items:
        print("\nLưu ý về dữ liệu:")
        for f in items:
            print(f"  [{f['code']}] {f['message']}")
    elif not _has_anomaly_decision(ctx):  # co muc flag luon co decision: khong co decision nghia la khong co co nao
        print("\nKhông có bất thường.")


def review_decisions(ctx, yes, ask_user, open_item, show_info=False):
    """Hoi moi decision pending theo thu tu: dinh nghia chi so, xu ly bat thuong, gia dinh con lai."""
    _review_each(ctx, "metric_definition", "Định nghĩa chỉ số", yes, ask_user, open_item)
    if show_info:
        _print_info_flags(ctx)
    _review_each(ctx, "anomaly_handling", "Xử lý dữ liệu bất thường", yes, ask_user, open_item)
    _review_list(ctx, "Giả định khác và khoảng hợp lệ", yes, ask_user, open_item)


def require_confirmed(ctx):
    """Con decision bat buoc pending thi khong di tiep; het thi dong cac muc mo cua buoc xac nhan."""
    pending = ctx.ledger.pending_required()
    if pending:
        ctx.ledger.add_open_item_once(PENDING_OPEN_ITEM)
        raise StopLoop(f"Còn decision bắt buộc chờ xác nhận: {', '.join(d.id for d in pending)}.")
    ctx.ledger.close_open_items_with_text(PLAN_OPEN_ITEM)
    ctx.ledger.close_open_items_with_text(PENDING_OPEN_ITEM)


# ---------- hieu de ----------

def print_restatement(r):
    print(f"\nDiễn giải đề: {r.restatement}")
    print("Mục tiêu:")
    for g in r.goals:
        print(f"  - {g}")
    if r.ambiguities:
        print("Điểm chưa rõ:")
        for a in r.ambiguities:
            print(f"  - {a}")


def _corrections_text(corrections, max_chars):
    """Chinh sua cua nguoi dung cho prompt, toi da max_chars, uu tien chinh sua moi nhat."""
    kept, used = [], 0
    for c in reversed(corrections):
        line = f"- {c}"
        if used + len(line) > max_chars:
            break
        kept.insert(0, line)
        used += len(line) + 1
    if corrections and not kept:  # chinh sua moi nhat tu no da qua dai
        kept = [f"- {corrections[-1]}"[:max_chars - 1] + "…"]
    omitted = len(corrections) - len(kept)
    return "\n".join(([f"({omitted} older corrections omitted)"] if omitted else []) + kept) or "(none)"


def understand(ctx, yes, ask_user):
    """AI dien giai lai de, nguoi dung xac nhan. Khong dong y: go chinh sua, goi lai AI (toi da
    ANALYSIS_UNDERSTAND_MAX_RETRIES lan). Dong y -> set_problem; khong chot duoc -> open_item + StopLoop."""
    corrections, previous = [], "(none)"
    for attempt in range(config.ANALYSIS_UNDERSTAND_MAX_RETRIES + 1):
        text = _build_prompt(ctx, "understand", {
            "previous_restatement": previous,
            "user_corrections": _corrections_text(corrections, config.CONTEXT_CORRECTIONS_MAX_CHARS),
        }, {"previous_restatement": "(none)",
            "user_corrections": _corrections_text(corrections, config.CONTEXT_CORRECTIONS_SHORT_CHARS)})
        r = _call(ctx, text, ProblemRestatement)
        ctx.ledger.log_event("analysis", "understand", extra={"attempt": attempt + 1, "n_goals": len(r.goals),
                                                              "n_ambiguities": len(r.ambiguities)})
        print_restatement(r)
        if confirm("Diễn giải này đúng với đề bài?", yes, ask_user):
            ctx.ledger.set_problem({"problem": ctx.problem, "restatement": r.model_dump(),
                                    "user_corrections": corrections, "confirmed_at": now_iso()})
            return
        if attempt == config.ANALYSIS_UNDERSTAND_MAX_RETRIES:
            break
        text = ask_user(CORRECTION_PROMPT).strip()
        if not text:
            break
        corrections.append(text)
        previous = r.restatement
    ctx.ledger.add_open_item_once("Chưa chốt được cách hiểu đề; chạy lại để AI diễn giải lại.")
    ctx.ledger.log_event("analysis", "confirm_problem", status="rejected")
    raise StopLoop("Người dùng chưa xác nhận cách hiểu đề.")


def explore(ctx):
    """Tong quan du lieu. Profile va ban tom tat da co tu load_context: phai co truoc understand vi de gui AI can
    tap gia tri che lay tu profile."""
    s = ctx.summary
    masked = ", ".join(f"{m['table']}.{m['column']}" for m in s["masked_columns"]) or "không"
    print(f"\nDữ liệu: tóm tắt {s['chars']} ký tự, mức cắt {s['level']}; cột che: {masked}")
    for w in s["warnings"]:
        print(f"Cảnh báo: {w}")
    ctx.ledger.log_event("analysis", "explore", extra={"level": s["level"], "n_warnings": len(s["warnings"])})


# ---------- ke hoach ----------

def _fmt_bounds(r):
    if r.min is not None and r.max is not None:
        return f"{r.min:g}..{r.max:g}"
    return f">= {r.min:g}" if r.min is not None else f"<= {r.max:g}"


def _confirmed_understanding(ctx, short=False):
    """Ban dien giai da xac nhan (problem.json) dang van ban cho prompt plan, step. short: chi con dien giai va
    chinh sua cua nguoi dung."""
    record = ctx.ledger.get_problem() or {}
    r = record.get("restatement") or {}
    lines = [r.get("restatement") or "(none)"]
    for title, items in (() if short else (("Goals", r.get("goals")), ("Open points", r.get("ambiguities")))):
        if items:
            lines += [f"{title}:"] + [f"- {x}" for x in items]
    corrections = record.get("user_corrections")
    if corrections:
        limit = config.CONTEXT_CORRECTIONS_SHORT_CHARS if short else config.CONTEXT_CORRECTIONS_MAX_CHARS
        lines += ["User corrections:", _corrections_text(corrections, limit)]
    return "\n".join(lines)


def _summary_cut(ctx):
    return context_builder.summary_shrinker(ctx.report, ctx.sensitive)


def _rejected_plan(ctx):
    """Cau hoi cua ke hoach bi gop y, de AI biet gop y noi ve cai gi. Di qua phan du lieu cua prompt."""
    state = ctx.ledger.get_state()
    old = state.replaced_plan if state else None
    if not old:
        return ""
    lines = [f"- {q['key']}: {q['question']}" for q in old["sub_questions"]]
    return "\n" + "\n".join(["Previous plan, to revise as the user corrections say:", *lines])


def make_plan(ctx):
    text = _build_prompt(ctx, "plan", {"confirmed_understanding": _confirmed_understanding(ctx) + _rejected_plan(ctx)},
                         {"confirmed_understanding": _confirmed_understanding(ctx, short=True),
                          context_builder.SUMMARY: _summary_cut(ctx)})
    plan = _call(ctx, text, AnalysisPlan)
    ctx.ledger.log_event("analysis", "plan", extra={"n_questions": len(plan.sub_questions),
                                                     "n_assumptions": len(plan.assumptions),
                                                     "cannot_answer": bool(plan.cannot_answer_reason)})
    return plan


def print_plan(plan):
    print(f"\nHiểu đề: {plan.understanding}")
    print("Yêu cầu của đề:")
    for i, req in enumerate(plan.requirements, 1):
        keys = [q.key for q in plan.sub_questions if i in q.covers]
        print(f"  {i}. {req} -> {', '.join(keys) if keys else '(chưa có câu hỏi)'}")
    print("Câu hỏi nhỏ:")
    for q in plan.sub_questions:
        dep = f" (cần {', '.join(q.depends_on)})" if q.depends_on else ""
        print(f"  {q.key}: {q.question}{dep}")
    print("Giả định:")
    for a in plan.assumptions:
        print(f"  [{a.kind}{', bắt buộc' if a.required else ''}] {a.text}")
    for r in plan.proposed_ranges:
        print(f"  Khoảng đề xuất {r.column}: {_fmt_bounds(r)} ({r.reason})")


def check_plan(ctx, plan):
    """Ke hoach khong dung duoc -> open_item + StopLoop. Goi truoc khi luu ke hoach vao state."""
    if plan.cannot_answer_reason:
        ctx.ledger.add_open_item_once(f"AI báo không trả lời được: {plan.cannot_answer_reason}")
        raise StopLoop(f"AI báo không trả lời được: {plan.cannot_answer_reason}")
    try:
        topo_order(plan.sub_questions)
    except ValueError as e:
        ctx.ledger.add_open_item_once(f"Kế hoạch có {e}; cần lập lại kế hoạch.")
        raise StopLoop(str(e)) from None


def _plan_items(ctx, plan):
    """[(kind, text, required, source_key)] ke hoach sinh ra: gia dinh, khoang hop le, co muc flag. De xuat cua AI cho
    dung mot co (flag_key) nam trong decision cua co do, khong thanh decision rieng."""
    flagged = [f for f in fl.all_flags(ctx.report) if f["severity"] == fl.FLAG]
    keys = {f["source_key"] for f in flagged}
    proposals = {}
    for a in plan.assumptions:
        if a.kind == "anomaly_handling" and a.flag_key in keys:
            proposals.setdefault(a.flag_key, a)
    items = []
    for a in plan.assumptions:
        if proposals.get(a.flag_key) is not a:
            items.append((a.kind, a.text, a.required, _text_key(a.kind, a.text)))
    for r in plan.proposed_ranges:
        text = f"Khoảng hợp lệ của {r.column}: {_fmt_bounds(r)} ({r.reason})"
        items.append(("assumption", text, True, _text_key("assumption", text)))
    for f in flagged:
        proposal = proposals.get(f["source_key"])
        items.append(("anomaly_handling", f"{f['message']} → Xử lý: {proposal.text if proposal else NO_HANDLING}",
                      True, f["source_key"]))
    return items


def register_plan_decisions(ctx, plan):
    """Ghi pending (chua hoi) moi decision cua ke hoach. Chay lai khong tao trung."""
    for kind, text, required, key in _plan_items(ctx, plan):
        _add_pending(ctx, kind, text, required, key)
    if _info_flags(ctx) and not _has_anomaly_decision(ctx):  # diem dung nay luon dung khi co luu y
        _add_pending(ctx, "anomaly_handling", KEEP_DATA_TEXT, True, KEEP_DATA_KEY)


def _drop_replaced_decisions(ctx, old, new):
    """Decision do ke hoach cu sinh ma ke hoach moi khong sinh y het thi superseded. So noi dung ke hoach sinh ra,
    khong so d.text, nen decision nguoi dung da sua van giu khi ke hoach moi khong doi muc do."""
    current = {key: text for _, text, _, key in _plan_items(ctx, new)}
    for _, text, _, key in _plan_items(ctx, old):
        if current.get(key) != text:
            ctx.ledger.supersede_decisions(key)
    ctx.decisions = [_decision_line(x) for x in ctx.ledger.decisions() if x.status == DecisionStatus.CONFIRMED]


def load_plan(ctx):
    """Ke hoach trong state; chua co thi lap, luu truoc khi hoi. Dang lap lai theo gop y thi bo decision cua ke hoach cu."""
    state = ctx.ledger.get_state()
    if state.plan is not None:
        return AnalysisPlan.model_validate(state.plan)
    plan = make_plan(ctx)
    check_plan(ctx, plan)
    if state.replaced_plan:
        _drop_replaced_decisions(ctx, AnalysisPlan.model_validate(state.replaced_plan), plan)
    ctx.ledger.update_state(plan=plan.model_dump(mode="json"), replaced_plan=None)
    return plan


def _save_plan_correction(ctx, text):
    """Luu gop y vao user_corrections cua problem.json (di theo _confirmed_understanding, tinh vao tran hien co) va bo
    ke hoach trong state: ngat giua chung roi chay lai van lap ke hoach moi."""
    record = ctx.ledger.get_problem() or {}
    corrections = [*(record.get("user_corrections") or []), f"{PLAN_CORRECTION_PREFIX}{text}"]
    ctx.ledger.set_problem({**record, "user_corrections": corrections})
    ctx.ledger.update_state(plan=None, replaced_plan=ctx.ledger.get_state().plan)


def _plan_answer(yes, ask_user, can_correct):
    """Tra ve "y", "n" hoac noi dung gop y. Nhap khong hop le hoac gop y rong thi hoi lai, chi n moi dung.
    can_correct=False: da het luot gop y, chi con y/n."""
    if yes:
        print(f"{PLAN_QUESTION} [y/s/n] y (--yes)")
        return "y"
    options = "y = chấp nhận, s = gõ góp ý, n = dừng" if can_correct else "y = chấp nhận, n = dừng"
    while True:
        answer = ask_user(f"{PLAN_QUESTION} [{options}] ").strip().lower()
        if answer in YES:
            return "y"
        if answer in NO:
            return "n"
        if answer == "s" and can_correct:
            text = ask_user(PLAN_CORRECTION_PROMPT).strip()
            if text:
                return text


def accept_plan(ctx, plan, yes, ask_user):
    """Hoi cac decision con pending, roi danh sach cau hoi nho. Chay lai chi hoi phan chua xong. Tra loi s: gop y gui
    lai AI lap ke hoach moi, toi da ANALYSIS_PLAN_MAX_RETRIES lan; het luot thi chi con y/n."""
    for attempt in range(config.ANALYSIS_PLAN_MAX_RETRIES + 1):
        register_plan_decisions(ctx, plan)
        print_plan(plan)
        review_decisions(ctx, yes, ask_user, PLAN_OPEN_ITEM, show_info=True)
        can_correct = attempt < config.ANALYSIS_PLAN_MAX_RETRIES
        if not can_correct:
            print("Đã hết lượt góp ý cho kế hoạch.")
        answer = _plan_answer(yes, ask_user, can_correct)
        if answer == "y":
            require_confirmed(ctx)
            ctx.ledger.log_event("analysis", "confirm_plan", extra={"n_decisions": len(ctx.decisions)})
            return
        if answer == "n":
            break
        _save_plan_correction(ctx, answer)
        ctx.ledger.log_event("analysis", "confirm_plan", status="correction", extra={"attempt": attempt + 1})
        plan = load_plan(ctx)
    ctx.ledger.add_open_item_once(PLAN_OPEN_ITEM)
    ctx.ledger.log_event("analysis", "confirm_plan", status="rejected")
    raise StopLoop("Người dùng không chấp nhận kế hoạch.")


def topo_order(sub_questions):
    """Thu tu theo phu thuoc, giu thu tu goc khi khong rang buoc. Vong phu thuoc -> ValueError."""
    by_key = {q.key: q for q in sub_questions}
    done, order = set(), []
    pending = list(sub_questions)
    while pending:
        ready = [q for q in pending if all(d in done for d in q.depends_on)]
        if not ready:
            raise ValueError(f"vòng phụ thuộc giữa các câu hỏi: {sorted(q.key for q in pending)}")
        for q in ready:
            order.append(by_key[q.key])
            done.add(q.key)
        pending = [q for q in pending if q.key not in done]
    return order


# ---------- tung cau hoi ----------

def build_feedback(run, grade=None):
    """Phan hoi ngan (tieng Viet) cho lan goi lai; chua che (goi ctx.mask truoc khi gui)."""
    parts = []
    if run.blocked_reason:
        parts.append(f"Code bị chặn, không chạy: {run.blocked_reason}")
    elif run.timed_out:
        parts.append("Code chạy quá thời gian cho phép và đã bị dừng.")
    elif not run.ok:
        tail = run.stderr[-config.ANALYSIS_FEEDBACK_STDERR_CHARS:]
        parts.append(f"Code lỗi (mã thoát {run.returncode}). Phần cuối stderr:\n{tail}")
    elif run.meta is None:
        parts.append("Code chạy xong nhưng không có file meta hợp lệ tại AGENT_META_PATH"
                     + (f": {run.meta_error}" if run.meta_error else "."))
    if grade is not None and grade.structural_errors:
        parts.append("Lỗi cấu trúc cần sửa:\n" + "\n".join(f"- {e}" for e in grade.structural_errors))
    return "\n\n".join(parts)


def _work_files(ctx, max_files=None):
    """Dong liet ke file trong work/ cho AI; bo _superseded/ (runner cung khong chep thu muc nay).
    max_files: chi giu file moi nhat."""
    paths = [p for p in ctx.work_dir.rglob("*")
             if p.is_file() and SUPERSEDED_DIR not in p.relative_to(ctx.work_dir).parts] \
        if ctx.work_dir.is_dir() else []
    omitted = max(len(paths) - max_files, 0) if max_files else 0
    if omitted:
        paths = sorted(paths, key=lambda p: p.stat().st_mtime)[omitted:]
    files = sorted(p.relative_to(ctx.work_dir).as_posix() for p in paths)
    return "Files in output directory: " + (", ".join(files) if files else "(none)") \
        + (f" (+{omitted} older files)" if omitted else "")


def _previous_results(ctx, results_by_key):
    lines = [ctx.ledger.get_summary(), _work_files(ctx)]  # get_summary chi gom ket qua valid
    for key, rid in results_by_key.items():
        lines.append(f"{key} -> {rid}")
    return "\n".join(lines)


def _short_previous(ctx, results_by_key, keep_ids=()):
    """Ban rut gon (chi ket qua valid): id, cau hoi, cac khoa result_summary, ten file trong work/. Toi da
    CONTEXT_PREVIOUS_MAX_RESULTS ket qua moi nhat, cong keep_ids (ket qua cau hien tai phu thuoc)."""
    valid = [r for r in ctx.ledger.results() if r.status == ResultStatus.VALID]
    newest = {r.id for r in valid[-config.CONTEXT_PREVIOUS_MAX_RESULTS:]}
    kept = [r for r in valid if r.id in newest or r.id in keep_ids]
    lines = [f"({len(valid) - len(kept)} older results omitted)"] if len(kept) < len(valid) else []
    lines += [f"{r.id}: {r.question} | keys: {', '.join(r.result_summary) or '-'}" for r in kept]
    lines.append(_work_files(ctx, config.CONTEXT_PREVIOUS_MAX_FILES))
    kept_ids = {r.id for r in kept}
    lines += [f"{key} -> {rid}" for key, rid in results_by_key.items() if rid in kept_ids]
    return "\n".join(lines)


def _decision_rank(d):
    """Thu tu giu lai khi phai cat: dinh nghia chi so, xu ly bat thuong, gia dinh; "chap nhan R.." bo truoc."""
    if (d.source_key or "").startswith(ACCEPT_FLAGS_KEY):
        return 3
    return {"metric_definition": 0, "anomaly_handling": 1}.get(d.kind, 2)


def short_decisions(ledger):
    """Ban rut gon: toi da CONTEXT_DECISIONS_MAX decision, moi cai ANALYSIS_DECISION_SHORT_CHARS ky tu."""
    n = config.ANALYSIS_DECISION_SHORT_CHARS
    confirmed = [d for d in ledger.decisions() if d.status == DecisionStatus.CONFIRMED]
    kept = sorted(confirmed, key=_decision_rank)[:config.CONTEXT_DECISIONS_MAX]
    texts = [f"- {d.text}" for d in kept]
    lines = [t if len(t) <= n else t[:n - 1] + "…" for t in texts]
    if len(kept) < len(confirmed):
        lines.append(f"({len(confirmed) - len(kept)} less important decisions omitted)")
    return "\n".join(lines) or "(none)"


def _short_decisions(ctx):
    return short_decisions(ctx.ledger)


def _step_prompt(ctx, q, results_by_key, feedback):
    variables = {"confirmed_decisions": "\n".join(ctx.decisions) or "(none)",
                 "confirmed_understanding": _confirmed_understanding(ctx),
                 "previous_results": _previous_results(ctx, results_by_key),
                 "question": f"{q.key}: {q.question}", "error_feedback": feedback}
    keep = {results_by_key[d] for d in q.depends_on if d in results_by_key}
    shortened = {"previous_results": _short_previous(ctx, results_by_key, keep),
                 "error_feedback": feedback[-config.ANALYSIS_FEEDBACK_SHORT_CHARS:],
                 "confirmed_decisions": _short_decisions(ctx),
                 "confirmed_understanding": _confirmed_understanding(ctx, short=True),
                 context_builder.SUMMARY: _summary_cut(ctx)}
    return _build_prompt(ctx, "step", variables, shortened, question=q.key)


def _next_result_id(ledger):
    return f"R{max((int(r.id[1:]) for r in ledger.results()), default=0) + 1}"


def _rel(ctx, path):
    try:
        return Path(path).relative_to(runner.OUTPUT_DIR / ctx.task).as_posix()
    except ValueError:
        return str(path)


def solve_question(ctx, q, results_by_key, max_retries, yes, ask_user):
    """Giai mot cau hoi; tra ve id result. Het so lan thu / nguoi dung chon n -> StopLoop."""
    step_name = f"{_next_result_id(ctx.ledger)}_{q.key}"
    feedback = ""
    for attempt in range(1, max_retries + 2):
        step = _call(ctx, _step_prompt(ctx, q, results_by_key, feedback), AnalysisStep)
        if step.cannot_answer_reason:
            ctx.ledger.add_open_item(f"{q.key}: AI báo không trả lời được: {step.cannot_answer_reason}")
            raise StopLoop(f"{q.key}: AI báo không trả lời được.")
        run = runner.run_code(step.code, ctx.task, step_name)
        grade = grade_meta(run.meta, step, ctx.input_dir, ctx.work_dir) if run.ok and run.meta is not None else None
        ok = grade is not None and grade.ok
        ctx.ledger.log_event("analysis", "step_attempt", inputs={"question": q.key, "attempt": attempt},
                             code_file=_rel(ctx, run.code_path), status="ok" if ok else "retry",
                             extra={"run_ok": run.ok, "has_meta": run.meta is not None,
                                    "n_structural_errors": len(grade.structural_errors) if grade else None})
        if ok:
            return _record(ctx, q, step, run, grade, results_by_key, yes, ask_user)
        feedback = ctx.mask(build_feedback(run, grade))
        print(f"{q.key}: lần {attempt} chưa đạt, gửi lại lỗi cho AI.")
    ctx.ledger.add_open_item(f"{q.key}: không giải được sau {max_retries + 1} lần thử. Lỗi cuối: "
                             f"{feedback[:300]}")
    raise StopLoop(f"{q.key}: hết {max_retries} lần thử lại.")


def _flag_model_switch(ctx, grade):
    """Doi model tu ket qua truoc: them check flag de di qua diem dung co co."""
    if ctx.switched_from and ctx.switched_from != ctx.model:
        reason = f"{ctx.switched_from} → {ctx.model}"
        grade.checks.append({"name": "model_switched", "status": "flag", "reason": reason})
        grade.flags.append(f"model_switched: {reason}")


def _record(ctx, q, step, run, grade, results_by_key, yes, ask_user):
    if step.new_assumptions:
        print(f"{q.key}: AI đưa thêm giả định.")
        for a in step.new_assumptions:
            _add_pending(ctx, a.kind, a.text, a.required, _text_key(a.kind, a.text))
        review_decisions(ctx, yes, ask_user, PENDING_OPEN_ITEM)

    _flag_model_switch(ctx, grade)
    entry = ctx.ledger.add_result(
        q.question, step.method, grade.result_summary, grade.checks,
        depends_on=[results_by_key[d] for d in q.depends_on],
        output_path="; ".join(_rel(ctx, p) for p in run.files_written) or None,
        code_file=_rel(ctx, run.code_path), question_key=q.key)
    ctx.ledger.close_open_items_with_text(CHAIN_OPEN_ITEM)  # da chay tiep duoc: muc nay het y nghia
    if ctx.model:
        ctx.ledger.update_state(model=ctx.model)
    ctx.switched_from = None
    ctx.ledger.log_event("analysis", "result", inputs={"question": q.key}, code_file=entry.code_file,
                         status="flag" if grade.flags else "ok", extra={"result_id": entry.id})
    if grade.flags:
        print(f"{q.key} ({entry.id}) có cờ:")
        for f in grade.flags:
            print(f"  - {f}")
        names = [c["name"] for c in grade.checks if c["status"] == "flag"]
        ctx.ledger.add_finding(f"{entry.id} có {len(grade.flags)} check bị cờ: " + "; ".join(names),
                               "hypothesis", result_ids=[entry.id])
        # pending truoc khi hoi: tra loi n thi chay lai se hoi lai, khong lang le di tiep
        d = _add_pending(ctx, "anomaly_handling", f"chấp nhận {entry.id} dù có cờ {', '.join(names)}",
                         source_key=f"{ACCEPT_FLAGS_KEY}{entry.id}")
        if not confirm("Vẫn tiếp tục?", yes, ask_user):
            ctx.ledger.add_open_item_once(PENDING_OPEN_ITEM)
            raise StopLoop(f"Dừng sau {entry.id} theo yêu cầu (có cờ).")
        _confirm_decision(ctx, d)
    return entry.id


# ---------- tong hop ----------

def print_summary(ctx):
    print("\nKết quả:")
    for r in ctx.ledger.results():
        if r.status != ResultStatus.VALID:
            continue
        n_pass = sum(c.status.value == "pass" for c in r.checks)
        n_flag = sum(c.status.value == "flag" for c in r.checks)
        print(f"  {r.id} | {r.question} | {n_pass} pass, {n_flag} flag")
    print(f"Sổ kết quả và file: {ctx.ledger.root}")


# ---------- CLI ----------

def run_loop(task, **kwargs):
    """Lop mong: luong that la main.run_task."""
    import main as app  # lazy: main.py import module nay
    return app.run_task(task, **kwargs)


def demo_fake():
    """FakeLLM voi kich ban demo tong quat: dem dong tung file CSV trong input (khong goi AI that).
    Tra loi theo loai prompt (schema), khong theo thu tu luot goi, de chay tiep o pha nao cung dung."""
    plan = {"understanding": "Demo: đếm số dòng từng bảng.", "assumptions": [],
            "requirements": ["Số dòng của từng bảng"],
            "sub_questions": [{"key": "Q1", "question": "Mỗi bảng có bao nhiêu dòng?", "covers": [1]}]}
    code = '''import json, os
import pandas as pd
inp, out = os.environ["AGENT_INPUT_DIR"], os.environ["AGENT_OUTPUT_DIR"]
counts = {}
for name in sorted(os.listdir(inp)):
    if name.lower().endswith(".csv"):
        counts[name.rsplit(".", 1)[0]] = len(pd.read_csv(os.path.join(inp, name), dtype=str))
pd.Series(counts, name="rows").rename_axis("table").to_csv(os.path.join(out, "demo_row_counts.csv"))
meta = {"result_summary": {"n_tables": len(counts), "total_rows": int(sum(counts.values()))},
        "checks": [{"type": "custom", "name": "có bảng", "passed": len(counts) > 0, "reason": f"{len(counts)} bảng"}]}
json.dump(meta, open(os.environ["AGENT_META_PATH"], "w", encoding="utf-8"))
'''
    step = {"explanation": "Đếm dòng từng file CSV.", "method": "pandas read_csv, len", "code": code,
            "can_be_wrong_if": ["Không có file CSV nào"], "output_files": ["demo_row_counts.csv"]}
    restatement = {"restatement": "Demo: đếm số dòng từng bảng.", "goals": ["Số dòng của từng bảng"]}
    return llm.FakeLLM(by_schema={ProblemRestatement: restatement, AnalysisPlan: plan, AnalysisStep: step,
                                  SynthesisOutput: demo_synthesis})


DEMO_TABLE = "demo_row_counts.csv"


def demo_synthesis(messages):
    """Tra loi gia cho prompt tong hop: doc id ket qua va so yeu cau tu chinh prompt, vi id doi sau --restart.
    Moi yeu cau mot insight, trich ket qua dau tien va khoa dau tien cua no."""
    prompt = messages[0]["content"]
    first = re.search(r"^(R\d+) \|.*\| result_summary: (\{.*\}) \|", prompt, re.M)
    rid = first[1] if first else None
    keys = list(json.loads(first[2])) if first else []
    text = f"Demo: kết quả {rid} ghi nhận {{{{{rid}.{keys[0]}}}}}." if keys else "Demo: chưa trả lời được, không có số."
    insights = [{"id": f"I{i}", "requirement": int(n), "text": text, "result_ids": [rid] if rid else []}
                for i, n in enumerate(re.findall(r"^\[(\d+)\] ", prompt, re.M), 1)]
    if DEMO_TABLE in prompt and insights:  # bang cua demo_fake: co cot so that de ve va dua vao bao cao
        insights[0]["table"] = {"file": DEMO_TABLE, "columns": ["table", "rows"]}
        insights[0]["chart"] = {"type": "bar", "file": DEMO_TABLE, "x": "table", "y": ["rows"], "title": "Số dòng theo bảng"}
    return {"summary": "Demo: đếm số dòng từng bảng.", "insights": insights, "conclusion": "Demo: không có khuyến nghị.",
            "limitations": "Demo: kịch bản giả lập, không phải phân tích thật.",
            "column_labels": {"table": "Bảng", "rows": "Số dòng"}}


def main(argv=None):
    p = argparse.ArgumentParser(description="Vòng lặp phân tích một bài.")
    p.add_argument("task")
    p.add_argument("--sensitive", action="append", default=[], metavar="BANG.COT")
    p.add_argument("--use-fake", action="store_true", help="dùng FakeLLM kịch bản demo (không gọi AI thật)")
    p.add_argument("--max-retries", type=int, default=config.ANALYSIS_MAX_RETRIES)
    p.add_argument("--yes", action="store_true", help="tự trả lời y (chỉ dùng khi test)")
    p.add_argument("--no-sync", action="store_true", help="không đồng bộ hồ sơ lên R2")
    args = p.parse_args(argv)
    import main as app  # lazy: main.py import module nay
    return app.run_cli(args.task, fake=args.use_fake, sync=not args.no_sync, sensitive=args.sensitive,
                       max_retries=args.max_retries, yes=args.yes)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
