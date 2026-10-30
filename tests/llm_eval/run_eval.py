"""Cham mot model cho mot role tren bo case kiem dinh. GOI API THAT (ton han muc) khi co --yes.

Chay:
  python tests/llm_eval/run_eval.py --list
  python tests/llm_eval/run_eval.py --model <model_key> --role <analysis|report|slide> [--limit N] [--resume] [--yes]

Moi case ghi mot dong JSON vao tests/llm_eval/results/<ngay>_<model>_<role>.jsonl.
--resume bo qua case da co trong moi file ket qua cua cung model va role (ke ca ngay truoc).
"""

import argparse
import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import llm  # noqa: E402
from llm import LLMChainExhausted, LLMRequestError, client  # noqa: E402
from tests.llm_eval import synth_data  # noqa: E402
from tests.llm_eval.common import ROLES, build_messages, load_cases  # noqa: E402
from tests.llm_eval.schemas import SCHEMAS  # noqa: E402
from tests.llm_eval.scoring import passed, score_case  # noqa: E402

RESULTS_DIR = Path(__file__).resolve().parent / "results"
ANSWER_HEAD = 1500
EXIT_OK, EXIT_NOT_CONFIRMED, EXIT_QUOTA, EXIT_REQUEST, EXIT_USAGE = 0, 2, 3, 4, 5
QUOTA_REASONS = ("rate_limit", "cooldown")


def _parse(argv):
    p = argparse.ArgumentParser(description="Kiểm định một model cho một role.")
    p.add_argument("--model")
    p.add_argument("--role", choices=ROLES)
    p.add_argument("--limit", type=int, default=None, help="số case tối đa chạy trong lần này")
    p.add_argument("--resume", action="store_true", help="bỏ qua case đã có kết quả")
    p.add_argument("--yes", action="store_true", help="xác nhận gọi API thật")
    p.add_argument("--list", action="store_true", help="in số case theo role rồi thoát")
    p.add_argument("--results-dir", default=str(RESULTS_DIR))
    p.add_argument("--code-timeout", type=int, default=30, help="giây tối đa để chạy code của model")
    return p.parse_args(argv)


def result_files(results_dir, model, role):
    return sorted(Path(results_dir).glob(f"????-??-??_{model}_{role}.jsonl"))


def done_case_ids(results_dir, model, role):
    done = set()
    for path in result_files(results_dir, model, role):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                done.add(json.loads(line)["case_id"])
    return done


def _append(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()


def _wait_seconds(limits):
    rpm = (limits or {}).get("rpm")
    return 60 / rpm * 1.2 if rpm else 2.0


def run_case(case, model, data_dir, code_timeout):
    """Goi model cho mot case va cham. Tra ve ban ghi. Nem lai LLMChainExhausted (khong phai json_invalid)
    va LLMRequestError de vong ngoai dung."""
    t0 = time.perf_counter()
    text, out, error = None, None, None
    try:
        r = llm.ask_one_model(build_messages(case), case.role, model, json_schema=SCHEMAS[case.role], use_cache=False)
        text, out = r.text, r.data
        usage = {"prompt_tokens": r.prompt_tokens, "completion_tokens": r.completion_tokens,
                 "network_calls": r.network_calls, "json_retries": r.json_retries}
        latency = r.latency_s
    except LLMChainExhausted as e:
        reason = e.attempts[0]["reason"] if e.attempts else None
        if reason != "json_invalid":
            raise
        error = "json_invalid"
        usage = dict(getattr(e, "llm_usage", None) or {})
        latency = round(time.perf_counter() - t0, 3)

    details = {}  # analysis: cau truc meta (khong gia tri du lieu, khong code day du)
    criteria, reasons = score_case(case, out, data_dir, code_timeout, details)
    return {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "model_key": model,
        "role": case.role,
        "case_id": case.id,
        "passed": passed(criteria),
        "criteria": criteria,
        "reasons": reasons,
        "error": error,
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "network_calls": usage.get("network_calls"),
        "json_retries": usage.get("json_retries"),
        "latency_s": latency,
        "answer_head": (text or "")[:ANSWER_HEAD],
        **details,
    }


def main(argv=None, sleep=time.sleep):
    args = _parse(argv)
    if args.list:
        for role in ROLES:
            print(f"{role}: {len(load_cases(role))}")
        return EXIT_OK
    if not args.model or not args.role:
        print("Cần --model và --role (hoặc --list).")
        return EXIT_USAGE

    cfg = client._load_config(client._config_path())  # chi doc yaml, khong can khoa
    chains = client._active_chains(cfg)
    if args.model not in chains.get(args.role, []):
        print(f"Model '{args.model}' không nằm trong chain của role '{args.role}': {chains.get(args.role)}")
        return EXIT_USAGE
    limits = cfg["models"][args.model].get("limits") or {}
    settings = cfg["settings"]

    results_dir = Path(args.results_dir)
    cases = load_cases(args.role)
    done = done_case_ids(results_dir, args.model, args.role) if args.resume else set()
    todo = [c for c in cases if c.id not in done]
    if args.limit is not None:
        todo = todo[:args.limit]

    worst = len(todo) * (1 + settings.get("json_retries", 0)) * (1 + settings["num_retries"])
    wait = _wait_seconds(limits)
    print(f"Model {args.model}, role {args.role}: {len(todo)} case cần chạy "
          f"({len(done)} đã có, tổng {len(cases)}).")
    print(f"Số lần gọi: ít nhất {len(todo)}, tối đa {worst} (tính cả thử lại JSON và 5xx).")
    print(f"Hạn mức trong llm.yaml: rpd={limits.get('rpd')}, rpm={limits.get('rpm')}, "
          f"tpm={limits.get('tpm')}, tpd={limits.get('tpd')}. Giãn cách {wait:.1f} giây giữa các lần gọi.")
    if not todo:
        print("Không còn case nào để chạy.")
        return EXIT_OK
    if not args.yes:
        print("Chưa chạy. Thêm --yes để gọi API thật.")
        return EXIT_NOT_CONFIRMED

    out_path = results_dir / f"{datetime.now():%Y-%m-%d}_{args.model}_{args.role}.jsonl"
    n_done = n_pass = 0
    with tempfile.TemporaryDirectory(prefix="llm_eval_data_") as tmp:
        data_dir = synth_data.write_data(Path(tmp) / "data") if args.role == "analysis" else None
        for i, case in enumerate(todo):
            if i:
                sleep(wait)
            try:
                record = run_case(case, args.model, data_dir, args.code_timeout)
            except LLMChainExhausted as e:
                reason = e.attempts[0]["reason"] if e.attempts else None
                if reason in QUOTA_REASONS:
                    print(f"Dừng ở case {case.id}: model {args.model} {reason}. "
                          f"Đã lưu {n_done} case vào {out_path}.")
                    print("hết hạn mức, chạy tiếp ngày mai với --resume")
                else:
                    print(f"Dừng ở case {case.id}: model {args.model} không trả lời được ({reason}, "
                          f"status={e.attempts[0]['status_code'] if e.attempts else None}). "
                          f"Đã lưu {n_done} case. Chạy lại sau với --resume.")
                return EXIT_QUOTA
            except LLMRequestError as e:
                print(f"Dừng ở case {case.id}: nhà cung cấp từ chối (model_key={e.model_key}, "
                      f"status={e.status_code}). Kiểm tra khóa, tên model hoặc prompt.")
                return EXIT_REQUEST
            _append(out_path, record)
            n_done += 1
            n_pass += record["passed"]
            mark = "ĐẠT" if record["passed"] else "RỚT"
            print(f"  {case.id:22} {mark}  {', '.join(k for k, v in record['criteria'].items() if v is False) or ''}")

    print(f"Xong {n_done} case, đạt toàn bộ tiêu chí {n_pass}/{n_done}. Kết quả: {out_path}")
    return EXIT_OK


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
