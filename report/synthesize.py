"""Pha tong hop: AI viet nhan dinh bang placeholder, code kiem tra, dien so, gan nhan, ghi insights.json.

Goi role "report" voi task_id rieng <bai>/report: task_id cua bai dang gan model analysis, model do co the khong co
trong chuoi report, se bi tinh nham la doi model. use_cache=False: prompt khong doi thi cache tra lai dung cau tra loi
cu, nen tu choi ket luan hoac sai het so lan thu roi chay lai se khong doi duoc gi.
Moi chuoi dua cho AI deu qua mask (che gia tri cot nhay cam). Sai het so lan thu -> open_item + StopLoop, khong ghi file.
"""

import json

import config
import context_builder
import llm
from analysis import loop
from ledger import DecisionStatus, ResultStatus
from profiling import flags as fl
from report.charts import validate_visuals
from report.placeholders import assign_label, fill, requirements_of, validate_synthesis
from report.tables import result_tables
from skills.report import render_prompt
from skills.report.schemas import InsightRecord, SynthesisOutput, SynthesisRecord

ROLE = "report"
PROMPT = "synthesize"
INVALID_OPEN_ITEM = "AI chưa viết được bản tổng hợp đúng quy tắc; chạy lại để thử lại."
TOO_LONG_OPEN_ITEM = "Prompt tổng hợp quá dài so với model nhỏ nhất của chuỗi report."


# ---------- phan du lieu cua prompt ----------

def _requirements_text(requirements):
    return "\n".join(f"[{i}] {req}" for i, req in enumerate(requirements, 1))


def _table_text(table, short):
    name, columns = table["name"], table["columns"]
    if short or columns is None:
        return name
    shown = columns[:config.REPORT_TABLE_MAX_COLUMNS]
    more = f" (+{len(columns) - len(shown)} more columns)" if len(columns) > len(shown) else ""
    return f"{name} [{', '.join(map(str, shown))}]{more}"


def _result_line(r, tables, short=False):
    n_pass = sum(c.status.value == "pass" for c in r.checks)
    n_other = len(r.checks) - n_pass
    parts = [r.id, r.question, *([] if short else [f"method: {r.method}"]),
             f"result_summary: {json.dumps(r.result_summary, ensure_ascii=False)}",
             f"checks: {n_pass} pass, {n_other} not pass"]
    if tables:
        parts.append("tables: " + "; ".join(_table_text(t, short) for t in tables))
    return " | ".join(parts)


def _results_text(valid, tables, short=False):
    """short: bo method va ten cot; result_summary va ten file giu nguyen."""
    return "\n".join(_result_line(r, tables.get(r.id), short) for r in valid) or "(none)"


def _check_lines(valid):
    return [f"- {r.id}, check {c.name}: {c.status.value}{f' ({c.reason})' if c.reason else ''}"
            for r in valid for c in r.checks if c.status.value != "pass"]


def _flags_text(flags, valid, short=False):
    """Co chat luong con lai: co profiling va check chua pass cua ket qua. short: bo co muc info (check giu nguyen)."""
    kept = [f for f in flags if not short or f["severity"] == fl.FLAG]
    lines = [f"- [{f['severity']}] {f['code']}: {f['message']}" for f in kept]
    if len(kept) < len(flags):
        lines.append(f"({len(flags) - len(kept)} info flags omitted)")
    return "\n".join(lines + _check_lines(valid)) or "(none)"


def _problem_view(ledger):
    """problem.json kem requirements cua ke hoach: covers cua cau hoi danh so theo danh sach nay, con goals trong
    problem.json la cua pha hieu de, co the khac so luong."""
    state = ledger.get_state()
    plan = (state.plan if state else None) or {}
    return {**(ledger.get_problem() or {}), "requirements": plan.get("requirements") or []}


def _feedback(errors):
    shown = errors[:config.REPORT_FEEDBACK_MAX_ERRORS]
    omitted = len(errors) - len(shown)
    return "\n".join([f"- {e}" for e in shown] + ([f"(+{omitted} more errors)"] if omitted else [])) or "(none)"


def build_variables(ledger, flags=()):
    """Bien cua prompt lan goi dau va cac ban rut gon, chua che."""
    valid = [r for r in ledger.results() if r.status == ResultStatus.VALID]
    problem = _problem_view(ledger)
    decisions = [d for d in ledger.decisions() if d.status == DecisionStatus.CONFIRMED]
    tables = {r.id: result_tables(ledger, r.id) for r in valid}
    variables = {"problem": problem.get("problem", ""), "requirements": _requirements_text(requirements_of(problem)),
                 "results": _results_text(valid, tables),
                 "confirmed_decisions": "\n".join(map(loop._decision_line, decisions)) or "(none)",
                 "quality_flags": _flags_text(flags, valid), "previous_answer": "(none)", "error_feedback": "(none)"}
    shortened = {"previous_answer": "(none)", "quality_flags": _flags_text(flags, valid, short=True),
                 "confirmed_decisions": loop.short_decisions(ledger), "results": _results_text(valid, tables, short=True)}
    return variables, shortened


def _build_prompt(ledger, variables, shortened, mask):
    def render(**v):
        return render_prompt(PROMPT, **{k: mask(x) for k, x in v.items()})

    text, info = context_builder.build_prompt(ROLE, PROMPT, render, variables, shortened)
    ledger.log_event("report", "prompt_fit", status="ok" if text is not None else "too_long", extra=info)
    if text is None:
        ledger.add_open_item_once(TOO_LONG_OPEN_ITEM)
        raise loop.StopLoop(f"Prompt {PROMPT} quá dài so với model nhỏ nhất.")
    return text


# ---------- hop lai ----------

def build_record(output, problem, results, model):
    valid = [r for r in results if r.status == ResultStatus.VALID]
    insights = [InsightRecord(**i.model_dump(), label=assign_label(i.result_ids, valid), text_filled=fill(i.text, valid))
                for i in output.insights]
    return SynthesisRecord(summary=output.summary, summary_filled=fill(output.summary, valid), insights=insights,
                           conclusion=output.conclusion, conclusion_filled=fill(output.conclusion, valid),
                           limitations=output.limitations, limitations_filled=fill(output.limitations, valid),
                           column_labels=output.column_labels, requirements=requirements_of(problem), model=model)


def synthesize(ledger, task, mask=str, flags=()):
    """Goi AI, kiem tra, thu lai toi da REPORT_SYNTH_MAX_RETRIES lan kem danh sach loi; dat thi ghi insights.json va
    tra ve dict cua file do. Het chuoi model: LLMChainExhausted di len main.py (ma thoat 2)."""
    results = ledger.results()
    problem = _problem_view(ledger)
    if not requirements_of(problem):
        ledger.add_open_item_once(INVALID_OPEN_ITEM)
        raise loop.StopLoop("Không có danh sách yêu cầu của đề để tổng hợp.")
    variables, shortened = build_variables(ledger, flags)
    errors = []
    for attempt in range(1, config.REPORT_SYNTH_MAX_RETRIES + 2):
        text = _build_prompt(ledger, variables, shortened, mask)
        result = llm.ask_model([{"role": "user", "content": text}], role=ROLE, json_schema=SynthesisOutput,
                               task_id=f"{task}/{ROLE}", use_cache=False)
        errors = validate_synthesis(result.data, problem, results) + validate_visuals(result.data, ledger, mask)
        ledger.log_event("report", "synthesize_attempt", inputs={"attempt": attempt},
                         status="retry" if errors else "ok",
                         extra={"model": result.model_key, "n_errors": len(errors), "errors": [mask(e) for e in errors]})
        if not errors:
            record = build_record(result.data, problem, results, result.model_key).model_dump(mode="json")
            ledger.set_insights(record)
            ledger.close_open_items_with_text(INVALID_OPEN_ITEM)
            return record
        print(f"Tổng hợp: lần {attempt} có {len(errors)} lỗi, gửi lại cho AI.")
        variables = {**variables, "previous_answer": result.data.model_dump_json(),
                     "error_feedback": _feedback(errors)}
    ledger.add_open_item_once(INVALID_OPEN_ITEM)
    ledger.log_event("report", "synthesize", status="invalid", extra={"n_errors": len(errors)})
    raise loop.StopLoop(f"Bản tổng hợp vẫn sai sau {config.REPORT_SYNTH_MAX_RETRIES + 1} lần: {mask(errors[0])}")


def print_insights(record):
    """In ban tong hop da dien so, nhom theo tung yeu cau cua de."""
    print(f"\nTóm tắt: {record['summary_filled']}")
    for i, requirement in enumerate(record["requirements"], 1):
        print(f"\nYêu cầu {i}: {requirement}")
        for ins in (x for x in record["insights"] if x["requirement"] == i):
            cited = ", ".join(ins["result_ids"]) or "không có kết quả"
            print(f"  {ins['id']} [{ins['label']}] {ins['text_filled']} ({cited})")
    print(f"\nKết luận: {record['conclusion_filled']}")
    print(f"Hạn chế: {record['limitations_filled']}")
