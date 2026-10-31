"""Diem vao: dieu phoi mot bai theo pha understand -> explore -> plan -> loop -> synthesize -> done.

Chay: python main.py <bai> [--sensitive BANG.cot ...] [--restart] [--resynthesize] [--build-report]
[--redo RID [--note GOPY]] [--yes] [--fake] [--no-sync]
--sensitive: cot che, chot vao state o lan chay dau; chay tiep dung danh sach trong state, truyen khac thi dung.
--restart: ket qua valid va moi decision cu thanh superseded (hoi lai tu dau), file work/ sang
work/_superseded/<thoi diem>/, insights.json sang _superseded/, dong open_item cua buoc xac nhan, ve pha understand
(run_log giu nguyen; khong truyen --sensitive thi giu danh sach cot che cu).
--resynthesize: chi chay lai pha synthesize, dung khi state la synthesize hoac done: insights.json sang _superseded/,
decision chap nhan ket luan thanh superseded (hoi lai); ket qua va decision khac giu nguyen.
--build-report: dung lai bieu do va Word tu insights.json hien co, khong goi AI, can state done.
--redo: chay lai mot ket qua valid va cac ket qua phu thuoc (thiet ke 8.5), hoi y/n truoc khi doi gi; ket qua khac giu
nguyen, xong thi tong hop va dung bao cao lai. --note: gop y cho cau hoi cua ket qua do. Dung giua chung: chay lai khong co.
--yes tu tra loi "y" (chi dung khi test). --fake dung FakeLLM voi kich ban demo (khong goi AI that), ho so ghi vao
output/_fake/<bai>/ nen khong dung toi ho so that, va khong dong bo len R2.
--no-sync: khong dong bo ho so len R2. Mac dinh dong bo truoc moi cau hoi xac nhan, khi xong va khi thoat vi loi;
dong bo loi chi canh bao, khong dung agent (storage/sync.py).

Sau moi pha ghi state.json (phase = pha dang do). Chay lai thi tiep tu pha do: ke hoach da co trong state khong goi
lai AI, phase da qua plan nghia la ke hoach da duoc chap nhan, cau da co trong results_by_question() khong giai lai.
Ma thoat: xem analysis/loop.py. Day la luong duy nhat; python -m analysis.loop chi goi vao day.
"""

import argparse
import sys
from contextlib import contextmanager
from datetime import datetime

from botocore.exceptions import BotoCoreError, ClientError

import config
import llm
from analysis import loop
from ledger import Ledger, Phase, ReportLocked, ResultStatus
from llm import LLMChainExhausted, LLMError
from profiling import flags as fl
from profiling import quality
from profiling.data_summary import SensitiveLeakError
from report import build as report_build
from report.charts import ChartError
from report.docx_builder import CONCLUSION_KEY, ReportError
from report.synthesize import print_insights, synthesize
from runner import run_code as runner
from skills.analysis.schemas import AnalysisPlan
from storage import sync as r2_sync

LEAK_OPEN_ITEM = "Bản tóm tắt dữ liệu lộ giá trị cột bị che; cần xem lại khai báo cột nhạy cảm."
CONCLUSION_OPEN_ITEM = "Kết luận chưa được chấp nhận; chạy lại để AI tổng hợp lại."
REPORT_OPEN_ITEM = "Chưa dựng được báo cáo; sửa lỗi rồi chạy --build-report."
CONCLUSION_TEXT = "Chấp nhận kết luận tổng hợp trong insights.json"


# ---------- cac pha ----------

def _phase_loop(ctx, state, max_retries, yes, ask_user):
    loop.review_decisions(ctx, yes, ask_user, loop.PENDING_OPEN_ITEM)  # con lai tu lan chay truoc
    loop.require_confirmed(ctx)
    plan = AnalysisPlan.model_validate(state.plan)
    for q in loop.topo_order(plan.sub_questions):
        solved = ctx.ledger.results_by_question()
        if q.key in solved:
            print(f"{q.key}: đã có {solved[q.key]}, bỏ qua.")
            continue
        loop.solve_question(ctx, q, solved, max_retries, yes, ask_user)


def _phase_synthesize(ctx, yes, ask_user):
    """Tong hop roi hoi "Chap nhan ket luan?" (luon dung). insights.json con do thi la ban chua duoc tra loi (vd. ngat
    giua chung): hoi lai, khong goi AI. Tra loi n: bo ban nay, giu pha synthesize, chay lai se tong hop lai."""
    loop.print_summary(ctx)
    led = ctx.ledger
    record = led.get_insights() or synthesize(led, ctx.task, ctx.mask, fl.all_flags(ctx.report))
    led.close_open_items_with_text(loop.CHAIN_OPEN_ITEM)  # da chay tiep duoc: muc nay het y nghia
    print_insights(record)
    d = loop._add_pending(ctx, "assumption", CONCLUSION_TEXT, True, CONCLUSION_KEY)  # pending truoc khi hoi
    if not loop.confirm("Chấp nhận kết luận?", yes, ask_user):
        led.discard_insights()
        led.add_open_item_once(CONCLUSION_OPEN_ITEM)
        led.log_event("report", "confirm_conclusion", status="rejected")
        raise loop.StopLoop("Người dùng chưa chấp nhận kết luận.")
    loop._confirm_decision(ctx, d)
    led.close_open_items_with_text(CONCLUSION_OPEN_ITEM)
    led.log_event("report", "confirm_conclusion", extra={"decision": d.id, "n_insights": len(record["insights"])})


def _run_phase(ctx, state, max_retries, yes, ask_user):
    """Chay pha dang do cua state, tra ve pha ke tiep."""
    if state.phase == Phase.UNDERSTAND:
        loop.understand(ctx, yes, ask_user)
        return Phase.EXPLORE
    if state.phase == Phase.EXPLORE:
        loop.explore(ctx)
        return Phase.PLAN
    if state.phase == Phase.PLAN:
        loop.accept_plan(ctx, loop.load_plan(ctx), yes, ask_user)
        return Phase.LOOP
    if state.phase == Phase.LOOP:
        _phase_loop(ctx, state, max_retries, yes, ask_user)
        return Phase.SYNTHESIZE
    _phase_synthesize(ctx, yes, ask_user)
    return Phase.DONE


def _build_outputs(ctx):
    """Dung bao cao tu insights.json, khong goi AI. Chay sau khi ket luan da duoc chap nhan (state done), nen loi o day
    khong lam mat ket luan: sua roi chay --build-report."""
    led = ctx.ledger
    record = led.get_insights()
    if record is None:
        raise loop.StopLoop("Chưa có insights.json. Chạy --resynthesize để tổng hợp trước.")
    try:
        outputs = report_build.build_report(led, ctx.task, record, ctx.report, ctx.mask)
    except (ChartError, ReportError) as e:
        led.add_open_item_once(REPORT_OPEN_ITEM)
        led.log_event("report", "build", status="error", extra={"error": ctx.mask(str(e))})
        raise loop.StopLoop(f"Không dựng được báo cáo: {ctx.mask(str(e))} Không ghi file Word.") from None
    led.close_open_items_with_text(REPORT_OPEN_ITEM)
    print(f"Báo cáo: {outputs['docx']}")
    print(f"Hồ sơ bài toán: {outputs['case']}")
    print(f"Dàn ý slide: {outputs['outline']}")


# ---------- ma thoat ----------

def _local_time(epoch):
    return "không rõ" if epoch is None else datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


def exit_code_for(e, led, ctx=None):
    """Chuyen exception cua mot lan chay thanh ma thoat: ghi log, ghi open_item (khong trung), in thong bao."""
    if isinstance(e, loop.StopLoop):
        print(f"Dừng: {e}")
        led.log_event("analysis", "stop", status="stopped")
        if ctx is not None:
            loop.print_summary(ctx)
        return e.code
    if isinstance(e, LLMChainExhausted) and e.json_invalid_only:  # khong phai het han muc: chay lai sau cung vay
        led.log_event("analysis", "stop", status="json_invalid")
        print(f"AI trả lời sai schema ở mọi model đã thử, không phải hết hạn mức: {e}")
        return loop.ERROR
    if isinstance(e, LLMChainExhausted):
        state = led.update_state()
        led.add_open_item_once(loop.CHAIN_OPEN_ITEM)
        led.log_event("analysis", "stop", status="chain_exhausted", extra={"next_available_at": e.next_available_at})
        print(f"Hết hạn mức chuỗi model, đã lưu tiến độ ở pha {state.phase.value}. "
              f"Có hạn mức lại lúc: {_local_time(e.next_available_at)} (giờ máy). Chạy lại để tiếp tục.")
        return loop.EXHAUSTED
    if isinstance(e, SensitiveLeakError):
        led.add_open_item_once(LEAK_OPEN_ITEM)
        led.log_event("analysis", "stop", status="sensitive_leak")
        print(f"Dừng: {e}")
        return loop.STOPPED
    if isinstance(e, ReportLocked):
        led.log_event("analysis", "stop", status="report_locked")
        print(e)
        return loop.STOPPED
    if isinstance(e, LLMError):
        led.log_event("analysis", "stop", status="llm_error", extra={"error_type": type(e).__name__})
        print(f"Lỗi gọi AI ({type(e).__name__}): {e}")
        return loop.ERROR
    # con lai la loi R2: chi in ten loi, thong bao cua boto co the chua duong dan bucket
    led.log_event("analysis", "stop", status="r2_error", extra={"error_type": type(e).__name__})
    print(f"Lỗi R2 ({type(e).__name__}): không đọc được dữ liệu. Chạy lại khi R2 hoạt động.")
    return loop.ERROR


# ---------- chay ca bai ----------

def _restart(led, sensitive):
    old = led.get_state()
    insights = led.discard_insights()  # truoc tien: report/ bi khoa thi chua doi gi
    superseded = led.supersede_valid()
    decisions = led.supersede_decisions()
    led.close_open_items_with_text(loop.PLAN_OPEN_ITEM)
    led.close_open_items_with_text(loop.PENDING_OPEN_ITEM)
    led.close_open_items_with_text(CONCLUSION_OPEN_ITEM)
    # khong truyen --sensitive: giu danh sach cu, khong am tham bo che
    led.set_state(Phase.UNDERSTAND, sensitive=None if sensitive or old is None else old.sensitive)
    led.log_event("analysis", "restart", extra={"superseded": superseded, "superseded_decisions": decisions,
                                                "superseded_insights": insights})
    print(f"Làm lại từ đầu: {len(superseded)} kết quả và {len(decisions)} decision cũ chuyển superseded.")


def _resynthesize(led):
    """Ve pha synthesize, chi bo ban tong hop va decision chap nhan ket luan. Pha som hon -> StopLoop."""
    state = led.get_state()
    if state is None or state.phase not in (Phase.SYNTHESIZE, Phase.DONE):
        where = "chưa có tiến độ" if state is None else f"đang ở pha {state.phase.value}"
        raise loop.StopLoop(f"--resynthesize chỉ dùng khi bài ở pha synthesize hoặc done, bài này {where}. "
                            f"Chạy lại không kèm cờ này để tiếp tục.")
    insights = led.discard_insights()
    decisions = led.supersede_decisions(CONCLUSION_KEY)
    led.update_state(phase=Phase.SYNTHESIZE)
    led.log_event("analysis", "resynthesize", extra={"superseded_insights": insights, "superseded_decisions": decisions})
    print(f"Tổng hợp lại: {'đã chuyển insights.json sang _superseded/' if insights else 'không có insights.json cũ'}.")


def _redo_targets(led, result_id):
    """Cac ket qua se chay lai: result_id va ket qua valid phu thuoc no. Khong chay lai duoc -> StopLoop. Chi doc."""
    state = led.get_state()
    if state is None or state.phase not in (Phase.LOOP, Phase.SYNTHESIZE, Phase.DONE):
        where = "chưa có tiến độ" if state is None else f"đang ở pha {state.phase.value}"
        raise loop.StopLoop(f"--redo chỉ dùng từ pha loop trở đi, bài này {where}.")
    by_id = {r.id: r for r in led.results()}
    target = by_id.get(result_id)
    if target is None:
        raise loop.StopLoop(f"Không có kết quả {result_id}.")
    if target.status != ResultStatus.VALID:
        raise loop.StopLoop(f"{result_id} có status={target.status.value}, chỉ chạy lại được kết quả valid.")
    targets = [target, *(by_id[r] for r in led.valid_dependents(result_id))]
    planned = {q["key"] for q in (state.plan or {}).get("sub_questions", [])}
    outside = [r.id for r in targets if r.question_key not in planned]
    if outside:
        raise loop.StopLoop(f"{', '.join(outside)} không thuộc kế hoạch hiện tại, không chạy lại riêng được. Dùng --restart.")
    return targets


def _redo(led, result_id, note, yes, ask_user):
    """Hoi truoc, tra loi n thi chua doi gi. Dong y: bo ban tong hop nhu --resynthesize, ve pha loop, bo cac ket qua;
    phase ghi truoc nen dung giua chung thi python main.py <bai> chay tiep."""
    targets = _redo_targets(led, result_id)
    print("Sẽ chạy lại:")
    for r in targets:
        print(f"  {r.id} ({r.question_key}): {r.question}")
    if not loop.confirm("Chạy lại các kết quả trên?", yes, ask_user):
        raise loop.StopLoop("Không chạy lại, hồ sơ giữ nguyên.")
    insights = led.discard_insights()
    decisions = led.supersede_decisions(CONCLUSION_KEY)
    led.update_state(phase=Phase.LOOP)
    stale = loop.retire_result(led, result_id, note)
    led.log_event("analysis", "redo", inputs={"result_id": result_id}, extra={
        "stale": stale, "has_note": bool(note), "superseded_insights": insights, "superseded_decisions": decisions})


def _check_build_report(led):
    state = led.get_state()
    if state is None or state.phase != Phase.DONE:
        where = "chưa có tiến độ" if state is None else f"đang ở pha {state.phase.value}"
        raise loop.StopLoop(f"--build-report chỉ dùng khi bài đã done, bài này {where}.")
    return state


def _warn_options(yes):
    level = config.get_env("CAUTION_LEVEL", required=False, default=config.CAUTION_DEFAULT)
    if level != config.CAUTION_DEFAULT:
        print(f"Cảnh báo: CAUTION_LEVEL={level} chưa hỗ trợ, dùng mức chuẩn.")
    if yes:
        print("Cảnh báo: --yes tự xác nhận mọi decision.")


def _load_state(led):
    state = led.get_state()
    if state is not None:
        return state
    if led.results():
        print("Hồ sơ cũ chưa có state.json: kết quả cũ không có question_key nên các câu sẽ được giải lại. "
              "Dùng --restart để đánh dấu kết quả cũ là superseded.")
    return led.set_state(Phase.UNDERSTAND)


def _open_context(led, task, state, sensitive):
    """Nap ngu canh voi danh sach cot che: lan dau chot vao state, sau do dung state; truyen khac -> StopLoop."""
    ctx = loop.load_context(task, sensitive or state.sensitive)
    if state.sensitive is None:
        led.update_state(sensitive=ctx.sensitive)
    elif sensitive and set(ctx.sensitive) != set(state.sensitive):
        raise loop.StopLoop(f"--sensitive khác danh sách đã chốt {state.sensitive}. "
                            f"Muốn đổi cột che thì chạy lại với --restart --sensitive ...")
    return ctx


def _with_sync(ask_user, led, task):
    """Bo ask_user: dong bo truoc moi cau hoi xac nhan (cau go tiep khong phai diem dung moi)."""
    def ask(question):
        if question not in loop.FOLLOW_UP_PROMPTS:
            r2_sync.safe_sync(led, task, "checkpoint")
        return ask_user(question)
    return ask


def run_task(task, sensitive=None, max_retries=None, yes=False, ask_user=input, restart=False, sync=False,
             resynthesize=False, build_report=False, redo=None, note=None):
    """Chay mot bai tu pha dang do; tra ve ma thoat. sync: dong bo ho so len R2 (CLI bat, goi truc tiep thi tat)."""
    max_retries = config.ANALYSIS_MAX_RETRIES if max_retries is None else max_retries
    led = Ledger(runner.OUTPUT_DIR / task)
    if restart or resynthesize or build_report or redo:  # kiem truoc moi buoc ghi: bi khoa thi khong doi gi, ke ca run_log
        locked = led.locked_report_files()
        if locked:
            print(ReportLocked(locked))
            return loop.STOPPED
    led.attach_usage_log()  # moi luot goi AI ghi vao run_log.jsonl
    ctx = code = None
    _warn_options(yes)
    if sync:
        ask_user = _with_sync(ask_user, led, task)
    try:
        if restart or resynthesize or redo:  # kiem truoc khi doi ho so: profile cu thi dung, chua bo ket qua nao
            loop.saved_report(led, task)
        if restart:
            _restart(led, sensitive)
        if resynthesize:
            _resynthesize(led)
        if redo:
            _redo(led, redo, note, yes, ask_user)
        if build_report:
            ctx = _open_context(led, task, _check_build_report(led), sensitive)
            _build_outputs(ctx)
            code = loop.DONE
            return code
        state = _load_state(led)
        if state.phase == Phase.DONE:
            print(f"Bài {task} đã xong. Dùng --restart để làm lại.")
            code = loop.DONE
            return code
        llm.set_task_model(task, state.model)  # so doi model voi lan chay truoc, ke ca khac tien trinh
        ctx = _open_context(led, task, state, sensitive)
        while state.phase != Phase.DONE:
            next_phase = _run_phase(ctx, state, max_retries, yes, ask_user)
            state = led.update_state(phase=next_phase)
        _build_outputs(ctx)
        led.log_event("analysis", "done", extra={"results": list(led.results_by_question().values())})
        code = loop.DONE
        return code
    except (loop.StopLoop, ReportLocked, LLMError, SensitiveLeakError, ClientError, BotoCoreError) as e:
        code = exit_code_for(e, led, ctx)
        return code
    except KeyboardInterrupt:
        led.log_event("analysis", "stop", status="interrupted")
        print("\nDừng do người dùng ngắt. Chạy lại để tiếp tục.")
        code = loop.STOPPED
        return code
    finally:
        if sync:  # ca loi ngoai du kien (code van None): ho so toi diem nay van len R2
            r2_sync.safe_sync(led, task, "done" if code == loop.DONE else "exit")
        led.detach_usage_log()


@contextmanager
def fake_output():
    """Tam doi goc ho so sang output/_fake/ (runner va quality la hai noi duy nhat giu goc nay)."""
    saved = runner.OUTPUT_DIR, quality.OUTPUT_DIR
    runner.OUTPUT_DIR = saved[0] / config.FAKE_OUTPUT_SUBDIR
    quality.OUTPUT_DIR = saved[1] / config.FAKE_OUTPUT_SUBDIR
    try:
        yield
    finally:
        runner.OUTPUT_DIR, quality.OUTPUT_DIR = saved


def run_cli(task, fake=False, sync=True, **kwargs):
    if fake:
        print(f"Chế độ giả lập, hồ sơ ở output/{config.FAKE_OUTPUT_SUBDIR}/{task}")
        with fake_output(), llm.use_fake(loop.demo_fake()):
            return run_task(task, sync=False, **kwargs)
    return run_task(task, sync=sync, **kwargs)


def main(argv=None):
    p = argparse.ArgumentParser(description="Chạy agent phân tích cho một bài, tiếp tục từ pha đang dở.")
    p.add_argument("task", metavar="bai")
    p.add_argument("--sensitive", action="append", default=[], metavar="BANG.COT",
                   help="cột che (lặp được); chốt ở lần chạy đầu, đổi thì cần --restart")
    p.add_argument("--restart", action="store_true", help="làm lại từ đầu: kết quả và decision cũ thành superseded")
    p.add_argument("--resynthesize", action="store_true",
                   help="chỉ chạy lại pha synthesize (state synthesize hoặc done), giữ kết quả và decision đã có")
    p.add_argument("--build-report", action="store_true",
                   help="dựng lại biểu đồ và Word từ insights.json, không gọi AI (state done)")
    p.add_argument("--redo", metavar="RID",
                   help="chạy lại một kết quả và các kết quả phụ thuộc, rồi tổng hợp lại; kết quả khác giữ nguyên")
    p.add_argument("--note", metavar="GOPY", help="góp ý cho câu hỏi của --redo")
    p.add_argument("--yes", action="store_true", help="tự trả lời y (chỉ dùng khi test)")
    p.add_argument("--fake", action="store_true", help="dùng FakeLLM kịch bản demo (không gọi AI thật)")
    p.add_argument("--no-sync", action="store_true", help="không đồng bộ hồ sơ lên R2")
    args = p.parse_args(argv)
    if args.resynthesize and (args.restart or args.sensitive):
        p.error("--resynthesize không dùng cùng --restart hoặc --sensitive")
    if args.build_report and (args.restart or args.resynthesize or args.sensitive):
        p.error("--build-report không dùng cùng --restart, --resynthesize hoặc --sensitive")
    if args.redo and (args.restart or args.resynthesize or args.build_report or args.sensitive):
        p.error("--redo không dùng cùng --restart, --resynthesize, --build-report hoặc --sensitive")
    if args.note and not args.redo:
        p.error("--note chỉ dùng cùng --redo")
    try:
        quality._check_problem_id(args.task)
    except ValueError as e:
        p.error(str(e))
    return run_cli(args.task, fake=args.fake, sync=not args.no_sync, sensitive=args.sensitive, restart=args.restart,
                   resynthesize=args.resynthesize, build_report=args.build_report, redo=args.redo, note=args.note,
                   yes=args.yes)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
