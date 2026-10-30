"""Cac pha phan tich: hieu de, tong quan du lieu, lap ke hoach, tung cau hoi (AI viet code, runner chay, cham meta,
ghi so), tong hop. Thu tu pha, luu tien do va ma thoat nam o main.py (run_task); module nay khong co luong rieng.

Chay: python -m analysis.loop <task> [--sensitive BANG.cot ...] [--use-fake] [--max-retries 3] [--yes]
--yes tu tra loi "y" (chi dung khi test). --use-fake dung FakeLLM voi kich ban demo (khong goi AI that).

Ma thoat: 0 xong; 1 loi cau hinh/yeu cau AI hoac loi R2; 2 het han muc chuoi model (chay lai de tiep tuc); 3 dung
giua chung (nguoi dung chon n, het so lan thu, AI bao khong tra loi duoc, ban tom tat lo gia tri che).
Moi chuoi dua lai cho AI deu qua mask_text: gia tri that cua cot che -> [đã che]. Gia tri that chi o bo nho.
"""

import argparse
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import config
import llm
from analysis.meta_grader import grade_meta, read_local_table
from ledger import DecisionStatus, Ledger, ResultStatus
from ledger.ledger import SUPERSEDED_DIR
from ledger.schema import now_iso
from profiling import quality
from profiling.data_summary import build_data_summary
from runner import run_code as runner
from skills.analysis import render_prompt
from skills.analysis.schemas import AnalysisPlan, AnalysisStep, ProblemRestatement
from storage import r2

MASK = "[đã che]"
PROBLEM_FILE = "de_bai.md"
ROLE = "analysis"
DONE, ERROR, EXHAUSTED, STOPPED = 0, 1, 2, 3
CHAIN_OPEN_ITEM = "hết hạn mức chuỗi, chạy tiếp sau (chuỗi model analysis)"


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
    input_limit: int | None = None                  # max_input_tokens nho nhat cua chuoi analysis (nap lan dau)
    sensitive: list = field(default_factory=list)   # "BANG.cot" da dung: --sensitive + mac dinh cua bai
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
    columns = _sensitive(task, report, sensitive)
    try:
        summary = build_data_summary(report, sensitive=columns, max_chars=config.SUMMARY_MAX_CHARS)
    except ValueError as e:  # bang/cot trong --sensitive khong ton tai
        raise StopLoop(f"Cột che không hợp lệ: {e}") from None
    prefix = f"{r2.get_r2_settings()['R2_INPUT_PREFIX']}{task}/"
    ctx = Context(task=task, problem=problem, summary=summary, ledger=led, input_dir=r2.CACHE_DIR / prefix,
                  work_dir=runner.OUTPUT_DIR / task / "work",
                  masked_values=_masked_values(report, summary["masked_columns"]), sensitive=columns,
                  decisions=[_decision_line(d) for d in led.decisions() if d.status == DecisionStatus.CONFIRMED])
    led.log_event("analysis", "load_context", inputs={"task": task},
                  extra={"summary_chars": summary["chars"], "level": summary["level"],
                         "masked_columns": summary["masked_columns"], "n_masked_values": len(ctx.masked_values)})
    return ctx


# ---------- hoi nguoi dung ----------

def confirm(question, yes=False, ask_user=input):
    if yes:
        print(f"{question} [y/n] y (--yes)")
        return True
    return ask_user(f"{question} [y/n] ").strip().lower() in ("y", "yes", "c", "co", "có")


def _render(ctx, prompt_name, **variables):
    """Render prompt; moi bien (tru data summary da che san) deu qua ctx.mask."""
    return render_prompt(prompt_name, problem=ctx.mask(ctx.problem), data_summary=ctx.summary["text"],
                         **{k: ctx.mask(v) for k, v in variables.items()})


def _call(ctx, text, schema):
    return llm.ask_model([{"role": "user", "content": text}], role=ROLE, json_schema=schema, task_id=ctx.task).data


def _ask(ctx, prompt_name, schema, **variables):
    return _call(ctx, _render(ctx, prompt_name, **variables), schema)


# ---------- do dai prompt ----------

def min_input_tokens(role=ROLE):
    """max_input_tokens nho nhat trong chuoi fallback dang dung cua role (doc llm.yaml qua llm.client)."""
    from llm import client  # cau hinh LLM: chi doc file, khong goi mang
    cfg = client._load_config(client._config_path())
    chain = client._active_chains(cfg).get(role) or [i["model"] for i in cfg["roles"][role]["chain"]]
    return min(int(cfg["models"][m]["max_input_tokens"]) for m in chain)


def estimate_tokens(text):
    return math.ceil(len(text) / config.PROMPT_CHARS_PER_TOKEN) + config.PROMPT_SCHEMA_TOKENS


def fit_prompt(render, variables, shortened, limit):
    """Render prompt step va cat dan cho vua ngan sach = limit * (1 - PROMPT_MARGIN).

    render(**variables) -> chuoi. shortened: ban rut gon cua "previous_results" va "confirmed_decisions".
    Thu tu cat: (a) previous_results rut gon, (b) error_feedback con ANALYSIS_FEEDBACK_SHORT_CHARS ky tu cuoi,
    (c) confirmed_decisions rut gon. Tra ve (text hoac None neu van vuot, info chi gom so lieu)."""
    budget = int(limit * (1 - config.PROMPT_MARGIN))
    text = render(**variables)
    info = {"limit": limit, "budget": budget, "chars_before": len(text), "tokens_before": estimate_tokens(text),
            "cut": []}
    steps = [("previous_results", shortened["previous_results"]),
             ("error_feedback", variables["error_feedback"][-config.ANALYSIS_FEEDBACK_SHORT_CHARS:]),
             ("confirmed_decisions", shortened["confirmed_decisions"])]
    current = dict(variables)
    for name, value in steps:
        if estimate_tokens(text) <= budget:
            break
        current[name] = value
        info["cut"].append(name)
        text = render(**current)
    info.update(chars_after=len(text), tokens_after=estimate_tokens(text))
    return (text if info["tokens_after"] <= budget else None), info


def _decision_line(d):
    return f"- {d.id} [{d.kind.value}] {d.text}"


def _add_confirmed(ctx, kind, text, required=True):
    """Ghi va xac nhan quyet dinh; da co quyet dinh confirmed cung noi dung thi khong them (chay lai, --restart)."""
    for d in ctx.ledger.decisions():
        if d.status == DecisionStatus.CONFIRMED and d.kind == kind and d.text == text:
            return d.id
    d = ctx.ledger.add_decision(kind, text, required=required)
    ctx.ledger.confirm_decision(d.id)
    ctx.decisions.append(_decision_line(d))
    return d.id


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


def understand(ctx, yes, ask_user):
    """AI dien giai lai de, nguoi dung xac nhan. Khong dong y: go chinh sua, goi lai AI (toi da
    ANALYSIS_UNDERSTAND_MAX_RETRIES lan). Dong y -> set_problem; khong chot duoc -> open_item + StopLoop."""
    corrections, previous = [], "(none)"
    for attempt in range(config.ANALYSIS_UNDERSTAND_MAX_RETRIES + 1):
        r = _ask(ctx, "understand", ProblemRestatement, previous_restatement=previous,
                 user_corrections="\n".join(f"- {c}" for c in corrections) or "(none)")
        ctx.ledger.log_event("analysis", "understand", extra={"attempt": attempt + 1, "n_goals": len(r.goals),
                                                              "n_ambiguities": len(r.ambiguities)})
        print_restatement(r)
        if confirm("Diễn giải này đúng với đề bài?", yes, ask_user):
            ctx.ledger.set_problem({"problem": ctx.problem, "restatement": r.model_dump(),
                                    "user_corrections": corrections, "confirmed_at": now_iso()})
            return
        if attempt == config.ANALYSIS_UNDERSTAND_MAX_RETRIES:
            break
        text = ask_user("Gõ chỉnh sửa (để trống để dừng): ").strip()
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


def _confirmed_understanding(ctx):
    """Ban dien giai da xac nhan (problem.json) dang van ban cho prompt plan."""
    record = ctx.ledger.get_problem() or {}
    r = record.get("restatement") or {}
    lines = [r.get("restatement") or "(none)"]
    for title, items in (("Goals", r.get("goals")), ("Open points", r.get("ambiguities")),
                         ("User corrections", record.get("user_corrections"))):
        if items:
            lines += [f"{title}:"] + [f"- {x}" for x in items]
    return "\n".join(lines)


def make_plan(ctx):
    plan = _ask(ctx, "plan", AnalysisPlan, confirmed_understanding=_confirmed_understanding(ctx))
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


def accept_plan(ctx, plan, yes, ask_user):
    print_plan(plan)
    if not confirm("Chấp nhận kế hoạch và các giả định?", yes, ask_user):
        ctx.ledger.add_open_item_once("Kế hoạch phân tích chưa được chấp nhận; chạy lại để xem lại, "
                                      "hoặc --restart để làm lại từ đầu.")
        ctx.ledger.log_event("analysis", "confirm_plan", status="rejected")
        raise StopLoop("Người dùng không chấp nhận kế hoạch.")
    for a in plan.assumptions:
        _add_confirmed(ctx, a.kind, a.text, a.required)
    for r in plan.proposed_ranges:
        _add_confirmed(ctx, "assumption", f"Khoảng hợp lệ của {r.column}: {_fmt_bounds(r)} ({r.reason})")
    ctx.ledger.log_event("analysis", "confirm_plan", extra={"n_decisions": len(ctx.decisions)})


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


def _work_files(ctx):
    """Dong liet ke file trong work/ cho AI; bo _superseded/ (runner cung khong chep thu muc nay)."""
    files = sorted(p.relative_to(ctx.work_dir).as_posix() for p in ctx.work_dir.rglob("*")
                   if p.is_file() and SUPERSEDED_DIR not in p.relative_to(ctx.work_dir).parts) \
        if ctx.work_dir.is_dir() else []
    return "Files in output directory: " + (", ".join(files) if files else "(none)")


def _previous_results(ctx, results_by_key):
    lines = [ctx.ledger.get_summary(), _work_files(ctx)]  # get_summary chi gom ket qua valid
    for key, rid in results_by_key.items():
        lines.append(f"{key} -> {rid}")
    return "\n".join(lines)


def _short_previous(ctx, results_by_key):
    """Ban rut gon (chi ket qua valid): id, cau hoi, cac khoa result_summary, ten file trong work/."""
    lines = [f"{r.id}: {r.question} | keys: {', '.join(r.result_summary) or '-'}" for r in ctx.ledger.results()
             if r.status == ResultStatus.VALID]
    lines.append(_work_files(ctx))
    lines += [f"{key} -> {rid}" for key, rid in results_by_key.items()]
    return "\n".join(lines)


def _short_decisions(ctx):
    n = config.ANALYSIS_DECISION_SHORT_CHARS
    texts = [re.sub(r"^- D\d+ \[[^\]]*\] ", "- ", d) for d in ctx.decisions]
    return "\n".join(t if len(t) <= n else t[:n - 1] + "…" for t in texts) or "(none)"


def _step_prompt(ctx, q, results_by_key, feedback):
    """Prompt step da che va cat cho vua model nho nhat; qua dai -> open_item + StopLoop (khong goi AI)."""
    variables = {"confirmed_decisions": "\n".join(ctx.decisions) or "(none)",
                 "previous_results": _previous_results(ctx, results_by_key),
                 "question": f"{q.key}: {q.question}", "error_feedback": feedback}
    shortened = {"previous_results": _short_previous(ctx, results_by_key), "confirmed_decisions": _short_decisions(ctx)}
    if ctx.input_limit is None:
        ctx.input_limit = min_input_tokens()
    text, info = fit_prompt(lambda **v: _render(ctx, "step", **v), variables, shortened, ctx.input_limit)
    ctx.ledger.log_event("analysis", "prompt_fit", inputs={"question": q.key},
                         status="ok" if text is not None else "too_long", extra=info)
    if text is None:
        ctx.ledger.add_open_item(f"{q.key}: prompt quá dài so với model nhỏ nhất "
                                 f"(~{info['tokens_after']} token > {info['budget']}).")
        raise StopLoop(f"{q.key}: prompt quá dài so với model nhỏ nhất.")
    return text


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


def _record(ctx, q, step, run, grade, results_by_key, yes, ask_user):
    if step.new_assumptions:
        print(f"{q.key}: AI đưa thêm giả định:")
        for a in step.new_assumptions:
            print(f"  [{a.kind}{', bắt buộc' if a.required else ''}] {a.text}")
        if not confirm("Chấp nhận các giả định mới?", yes, ask_user):
            ctx.ledger.add_open_item(f"{q.key}: giả định mới chưa được chấp nhận.")
            raise StopLoop(f"{q.key}: người dùng không chấp nhận giả định mới.")
        for a in step.new_assumptions:
            _add_confirmed(ctx, a.kind, a.text, a.required)

    entry = ctx.ledger.add_result(
        q.question, step.method, grade.result_summary, grade.checks,
        depends_on=[results_by_key[d] for d in q.depends_on],
        output_path="; ".join(_rel(ctx, p) for p in run.files_created) or None,
        code_file=_rel(ctx, run.code_path), question_key=q.key)
    ctx.ledger.close_open_items_with_text(CHAIN_OPEN_ITEM)  # da chay tiep duoc: muc nay het y nghia
    ctx.ledger.log_event("analysis", "result", inputs={"question": q.key}, code_file=entry.code_file,
                         status="flag" if grade.flags else "ok", extra={"result_id": entry.id})
    if grade.flags:
        print(f"{q.key} ({entry.id}) có cờ:")
        for f in grade.flags:
            print(f"  - {f}")
        ctx.ledger.add_finding(f"{entry.id} có {len(grade.flags)} check bị cờ: "
                               + "; ".join(c["name"] for c in grade.checks if c["status"] == "flag"),
                               "hypothesis", result_ids=[entry.id])
        if not confirm("Vẫn tiếp tục?", yes, ask_user):
            raise StopLoop(f"Dừng sau {entry.id} theo yêu cầu (có cờ).")
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


def synthesize(ctx, yes, ask_user):
    """Khung pha tong hop. TODO Ngay 7: viet ket luan that thay cho bang ket qua."""
    print_summary(ctx)
    if not confirm("Chấp nhận kết luận?", yes, ask_user):
        raise StopLoop("Người dùng chưa chấp nhận kết luận.")


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
pd.Series(counts, name="rows").to_csv(os.path.join(out, "demo_row_counts.csv"))
meta = {"result_summary": {"n_tables": len(counts), "total_rows": int(sum(counts.values()))},
        "checks": [{"type": "custom", "name": "có bảng", "passed": len(counts) > 0, "reason": f"{len(counts)} bảng"}]}
json.dump(meta, open(os.environ["AGENT_META_PATH"], "w", encoding="utf-8"))
'''
    step = {"explanation": "Đếm dòng từng file CSV.", "method": "pandas read_csv, len", "code": code,
            "can_be_wrong_if": ["Không có file CSV nào"], "output_files": ["demo_row_counts.csv"]}
    restatement = {"restatement": "Demo: đếm số dòng từng bảng.", "goals": ["Số dòng của từng bảng"]}
    return llm.FakeLLM(by_schema={ProblemRestatement: restatement, AnalysisPlan: plan, AnalysisStep: step})


def main(argv=None):
    p = argparse.ArgumentParser(description="Vòng lặp phân tích một bài.")
    p.add_argument("task")
    p.add_argument("--sensitive", action="append", default=[], metavar="BANG.COT")
    p.add_argument("--use-fake", action="store_true", help="dùng FakeLLM kịch bản demo (không gọi AI thật)")
    p.add_argument("--max-retries", type=int, default=config.ANALYSIS_MAX_RETRIES)
    p.add_argument("--yes", action="store_true", help="tự trả lời y (chỉ dùng khi test)")
    args = p.parse_args(argv)
    import main as app  # lazy: main.py import module nay
    return app.run_cli(args.task, fake=args.use_fake, sensitive=args.sensitive, max_retries=args.max_retries,
                       yes=args.yes)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
