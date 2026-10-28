"""Goi thu tung model trong config/llm.yaml, moi model dung 1 lan, qua duong chinh client._ask.

CANH BAO: moi lan chay ton 1 luot han muc cua TUNG model (gemini_3_8_flash chi 20 luot/ngay,
openrouter chi 50 luot/ngay dung chung cho moi model :free). Neu model yeu cau loi va chuoi
chuyen sang model ke, model ke cung ton them 1 luot.

Chay: python tests/probe_all_models.py
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import LLMChainExhausted, client  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

PROMPT = "Reply with exactly one word: hello"
PAUSE_S = 2


def _short_reasons(reasons):
    if not reasons:
        return "-"
    return ", ".join(f"{r['model_key']}:{r['reason']}({r['status_code']})" for r in reasons)


def _reset_runtime_state():
    # Moi model duoc thu doc lap: cooldown do model truoc gay ra (vd. 429 openrouter
    # lam ca hang vao cooldown) khong duoc lam model sau bi bo qua ma khong goi.
    client._cooldown_until.clear()
    client._rate_limit_streak.clear()
    client._fail_counts.clear()


def main():
    state = client._get_state()
    messages = [{"role": "user", "content": PROMPT}]
    ok = fail = 0

    model_keys = list(state.models)
    for i, model_key in enumerate(model_keys):
        role = next((r for r, chain in state.chains.items() if model_key in chain), None)
        if role is None:
            print(f"{model_key:28} -> (không có role nào chứa model này)  FAIL")
            fail += 1
            continue

        _reset_runtime_state()
        try:
            r = client._ask(messages, role, start_model=model_key, use_cache=False)  # phai goi mang that
        except Exception as e:
            status = getattr(e, "status_code", None)
            err_model = getattr(e, "model_key", None)
            print(
                f"{model_key:28} -> LỖI {type(e).__name__} status={status} model_key={err_model} "
                f"role={role}  FAIL"
            )
            if isinstance(e, LLMChainExhausted):
                print(f"{'':28}    attempts: {_short_reasons(e.attempts)}")
            fail += 1
        else:
            verdict = "OK" if r.model_key == model_key else "FAIL"
            ok += verdict == "OK"
            fail += verdict == "FAIL"
            print(
                f"{model_key:28} -> {r.model_key:28} {verdict:4} "
                f"fallback=[{_short_reasons(r.fallback_reasons)}] "
                f"latency_s={r.latency_s} prompt_tokens={r.prompt_tokens} "
                f"completion_tokens={r.completion_tokens}"
            )

        if i < len(model_keys) - 1:
            time.sleep(PAUSE_S)

    print(f"\nTóm tắt: OK={ok}  FAIL={fail}  (tổng {len(model_keys)} model)")


if __name__ == "__main__":
    main()
