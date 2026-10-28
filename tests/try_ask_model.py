"""Goi that ask_model dung 1 lan.

Chay: python tests/try_ask_model.py <role> "<prompt>" [--start <model_key>] [--no-cache]
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm import client  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role")
    parser.add_argument("prompt")
    parser.add_argument("--start", default=None, help="model_key bắt đầu trong chain")
    parser.add_argument("--no-cache", action="store_true", help="bỏ qua cache cả đọc lẫn ghi")
    args = parser.parse_args()

    messages = [{"role": "user", "content": args.prompt}]
    r = client._ask(messages, args.role, start_model=args.start, use_cache=not args.no_cache)

    print(f"model_key:         {r.model_key}")
    print(f"provider:          {r.provider}")
    print(f"litellm_model:     {r.litellm_model}")
    print(f"prompt_tokens:     {r.prompt_tokens}")
    print(f"completion_tokens: {r.completion_tokens}")
    print(f"finish_reason:     {r.finish_reason}")
    print(f"fallback_count:    {r.fallback_count}")
    for item in r.fallback_reasons:
        print(f"  - {item['model_key']}: {item['reason']} (status={item['status_code']})")
    print(f"latency_s:         {r.latency_s}")
    print(f"cached:            {r.cached}")
    print(f"model_switched:    {r.model_switched}")
    print(f"json_retries:      {r.json_retries}")
    print(f"text[:200]:        {r.text[:200]}")


if __name__ == "__main__":
    main()
