"""Diem vao: dieu phoi mot bai theo pha understand -> explore -> plan -> loop -> synthesize -> done.

Chay: python main.py <bai> [--sensitive BANG.cot ...] [--restart] [--yes] [--fake] [--no-sync]
--sensitive: cot che, chot vao state o lan chay dau; chay tiep dung danh sach trong state, truyen khac thi dung.
--restart: ket qua valid va moi decision cu thanh superseded (hoi lai tu dau), file work/ sang
work/_superseded/<thoi diem>/, insights.json sang _superseded/, dong open_item cua buoc xac nhan, ve pha understand
(run_log giu nguyen; khong truyen --sensitive thi giu danh sach cot che cu).
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
from ledger import Ledger, Phase
from llm import LLMChainExhausted, LLMError
from profiling import flags as fl
from profiling import quality
from profiling.data_summary import SensitiveLeakError
from report.synthesize import print_insights, synthesize
from runner import run_code as runner
from skills.analysis.schemas import AnalysisPlan
from storage import sync as r2_sync

LEAK_OPEN_ITEM = "Bản tóm tắt dữ liệu lộ giá trị cột bị che; cần xem lại khai báo cột nhạy cảm."
CONCLUSION_OPEN_ITEM = "Kết luận chưa được chấp nhận; chạy lại để AI tổng hợp lại."
CONCLUSION_KEY = "accept_conclusion|synthesize"  # co dinh: moi lan tu choi roi tong hop lai dung chung mot decision
CONCLUSION_TEXT = "Chấp nhận kết luận tổng hợp trong insights.json"


# ---------- cac pha ----------

def _phase_plan(ctx, state, yes, ask_user):
    if state.plan is None:
        plan = loop.make_plan(ctx)
        loop.check_plan(ctx, plan)
        ctx.ledger.update_state(plan=plan.model_dump(mode="json"))  # ghi truoc khi hoi chap nhan
    else:
        plan = AnalysisPlan.model_validate(state.plan)
    loop.accept_plan(ctx, plan, yes, ask_user)


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
        _phase_plan(ctx, state, yes, ask_user)
        return Phase.LOOP
    if state.phase == Phase.LOOP:
        _phase_loop(ctx, state, max_retries, yes, ask_user)
        return Phase.SYNTHESIZE
    _phase_synthesize(ctx, yes, ask_user)
    return Phase.DONE


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
    superseded = led.supersede_valid()
    decisions = led.supersede_decisions()
    led.close_open_items_with_text(loop.PLAN_OPEN_ITEM)
    led.close_open_items_with_text(loop.PENDING_OPEN_ITEM)
    led.close_open_items_with_text(CONCLUSION_OPEN_ITEM)
    insights = led.discard_insights()
    # khong truyen --sensitive: giu danh sach cu, khong am tham bo che
    led.set_state(Phase.UNDERSTAND, sensitive=None if sensitive or old is None else old.sensitive)
    led.log_event("analysis", "restart", extra={"superseded": superseded, "superseded_decisions": decisions,
                                                "superseded_insights": insights})
    print(f"Làm lại từ đầu: {len(superseded)} kết quả và {len(decisions)} decision cũ chuyển superseded.")


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
                            f"Muốn đổi cột che thì chạy lại với --restart.")
    return ctx


def _with_sync(ask_user, led, task):
    """Bo ask_user: dong bo truoc moi cau hoi xac nhan (cau go tiep khong phai diem dung moi)."""
    def ask(question):
        if question not in loop.FOLLOW_UP_PROMPTS:
            r2_sync.safe_sync(led, task, "checkpoint")
        return ask_user(question)
    return ask


def run_task(task, sensitive=None, max_retries=None, yes=False, ask_user=input, restart=False, sync=False):
    """Chay mot bai tu pha dang do; tra ve ma thoat. sync: dong bo ho so len R2 (CLI bat, goi truc tiep thi tat)."""
    max_retries = config.ANALYSIS_MAX_RETRIES if max_retries is None else max_retries
    led = Ledger(runner.OUTPUT_DIR / task)
    led.attach_usage_log()  # moi luot goi AI ghi vao run_log.jsonl
    ctx = code = None
    _warn_options(yes)
    if sync:
        ask_user = _with_sync(ask_user, led, task)
    try:
        if restart:
            _restart(led, sensitive)
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
        led.log_event("analysis", "done", extra={"results": list(led.results_by_question().values())})
        code = loop.DONE
        return code
    except (loop.StopLoop, LLMError, SensitiveLeakError, ClientError, BotoCoreError) as e:
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
    p.add_argument("--yes", action="store_true", help="tự trả lời y (chỉ dùng khi test)")
    p.add_argument("--fake", action="store_true", help="dùng FakeLLM kịch bản demo (không gọi AI thật)")
    p.add_argument("--no-sync", action="store_true", help="không đồng bộ hồ sơ lên R2")
    args = p.parse_args(argv)
    try:
        quality._check_problem_id(args.task)
    except ValueError as e:
        p.error(str(e))
    return run_cli(args.task, fake=args.fake, sync=not args.no_sync, sensitive=args.sensitive, restart=args.restart,
                   yes=args.yes)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
