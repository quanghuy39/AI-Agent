"""Dung prompt vua ngan sach token cua mot vai tro (analysis/report/slide). Khong goi AI, khong ghi so.

Uoc luong: ky tu / PROMPT_CHARS_PER_TOKEN + PROMPT_SCHEMA_TOKENS. Ngan sach: max_input_tokens nho nhat trong chuoi
model cua role, chua bien PROMPT_MARGIN, vi model nao trong chuoi cung co the phai nhan prompt nay.

PROMPT_CUTS khai bao cua moi prompt: bien nao cat duoc, theo thu tu nao. De goc, cau hoi hien tai, contract
(NEVER_CUT) khong bao gio cat. Bac cuoi la data_summary: goi lai build_data_summary voi max_chars nho dan, de van qua
cac muc cat va luoi an toan che gia tri, khong cat chuoi tho.
"""

import math

import config
from profiling.data_summary import build_data_summary

NEVER_CUT = ("problem", "question", "contract", "requirements")
SUMMARY = "data_summary"
PROMPT_CUTS = {
    "understand": ("previous_restatement", "user_corrections"),
    "plan": ("confirmed_understanding", SUMMARY),
    "step": ("previous_results", "error_feedback", "confirmed_decisions", "confirmed_understanding", SUMMARY),
    # ban rut gon cua results chi bo method va ten cot, result_summary van du khoa va gia tri; error_feedback khong cat
    "synthesize": ("previous_answer", "quality_flags", "confirmed_decisions", "results"),
}


def min_input_tokens(role):
    """max_input_tokens nho nhat trong chuoi fallback dang dung cua role (doc llm.yaml qua llm.client)."""
    from llm import client  # lazy: llm keo theo litellm; o day chi doc file cau hinh, khong goi mang
    cfg = client._load_config(client._config_path())
    chain = client._active_chains(cfg).get(role) or [i["model"] for i in cfg["roles"][role]["chain"]]
    return min(int(cfg["models"][m]["max_input_tokens"]) for m in chain)


def budget_tokens(limit):
    return int(limit * (1 - config.PROMPT_MARGIN))


def estimate_tokens(text):
    return math.ceil(len(text) / config.PROMPT_CHARS_PER_TOKEN) + config.PROMPT_SCHEMA_TOKENS


def summary_shrinker(report, sensitive):
    """Ham max_chars -> ban tom tat da che; dung cho shortened["data_summary"]."""
    return lambda max_chars: build_data_summary(report, sensitive=sensitive, max_chars=max_chars)["text"]


def _cut_steps(prompt, shortened):
    """[(nhan, bien, gia tri hoac ham tra gia tri)] theo PROMPT_CUTS; bien khong co ban rut gon thi bo qua."""
    steps = []
    for name in PROMPT_CUTS[prompt]:
        if name not in shortened:
            continue
        if name == SUMMARY:
            steps += [(f"{SUMMARY}:{n}", SUMMARY, lambda n=n: shortened[SUMMARY](n))
                      for n in config.CONTEXT_SUMMARY_MAX_CHARS]
        else:
            steps.append((name, name, shortened[name]))
    return steps


def fit_prompt(prompt, render, variables, shortened, limit):
    """Render prompt va cat dan theo PROMPT_CUTS[prompt] cho vua ngan sach; dung o bac du.

    render(**variables) -> chuoi. shortened: {bien: ban rut gon}; rieng data_summary la ham nhan max_chars.
    Bac khong doi noi dung thi khong tinh. Tra ve (text hoac None neu van vuot, info chi gom so lieu)."""
    budget = budget_tokens(limit)
    text = render(**variables)
    info = {"prompt": prompt, "limit": limit, "budget": budget, "chars_before": len(text),
            "tokens_before": estimate_tokens(text), "cut": []}
    current = dict(variables)
    for label, name, value in _cut_steps(prompt, shortened):
        if estimate_tokens(text) <= budget:
            break
        value = value() if callable(value) else value
        if value == current.get(name):
            continue
        current[name] = value
        info["cut"].append(label)
        text = render(**current)
    info.update(chars_after=len(text), tokens_after=estimate_tokens(text))
    return (text if info["tokens_after"] <= budget else None), info


def build_prompt(role, prompt, render, variables, shortened):
    """Prompt vua ngan sach cua role; (text hoac None, info) nhu fit_prompt, info them role."""
    text, info = fit_prompt(prompt, render, variables, shortened, min_input_tokens(role))
    info["role"] = role
    return text, info
