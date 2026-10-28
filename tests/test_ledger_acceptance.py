"""Kiem thu nghiem thu cho ledger. Dung tmp_path, khong mang.

Chay: python -m pytest tests/test_ledger_acceptance.py -v
"""

from ledger import Check, Ledger, LogEvent, ResultStatus
from llm.usage import make_record, write_usage

OK = [Check(name="cross_check", status="pass")]


def test_summary_of_50_results_fits_default_limit(tmp_path):
    led = Ledger(tmp_path / "bai1")
    for n in range(1, 51):
        led.add_result(f"Doanh thu của nhóm khách hàng số {n} là bao nhiêu?", "pandas groupby sum",
                       {"total": 1000 + n, "n": n}, OK)

    text = led.get_summary()
    assert len(text) <= 2000
    assert "R50 | Doanh thu của nhóm khách hàng số 50 là bao nhiêu?" in text


def test_mark_stale_mid_analysis_and_reload(tmp_path):
    root = tmp_path / "bai1"
    led = Ledger(root)
    questions = {
        "R1": "Tổng doanh thu đơn hoàn tất?",
        "R2": "Doanh thu theo kênh bán hàng?",
        "R3": "Tỷ trọng doanh thu kênh web?",
        "R4": "Số khách hàng theo bang?",
    }
    led.add_result(questions["R1"], "sum", {"total": 19759.83}, OK)
    led.add_result(questions["R2"], "groupby", {"web": 14409.04}, OK, depends_on=["R1"])
    led.add_result(questions["R3"], "ratio", {"pct": 54.44}, OK, depends_on=["R2"])
    led.add_result(questions["R4"], "nunique", {"CA": 26}, OK)

    assert led.mark_stale("R1") == ["R2", "R3"]

    text = led.get_summary()
    for rid in ("R1", "R2", "R3"):
        assert questions[rid] not in text
    assert questions["R4"] in text

    expected = {"R1": ResultStatus.SUPERSEDED, "R2": ResultStatus.STALE, "R3": ResultStatus.STALE,
                "R4": ResultStatus.VALID}
    assert {r.id: r.status for r in led.results()} == expected
    reopened = Ledger(root)
    assert {r.id: r.status for r in reopened.results()} == expected
    assert reopened.get_summary() == text


def test_llm_call_lands_in_run_log(tmp_path):
    led = Ledger(tmp_path / "bai1")
    led.attach_usage_log()
    try:
        write_usage(make_record(role="analysis", provider="groq", model_key="gpt_oss_120b",
                                prompt_tokens=10, completion_tokens=5))
        events = [e for e in led.read_log() if e.action == "llm_call"]
        assert len(events) == 1
        e = events[0]
        assert isinstance(e, LogEvent)
        assert e.extra["provider"] == "groq"
        assert e.extra["model_key"] == "gpt_oss_120b"
        assert (e.extra["prompt_tokens"], e.extra["completion_tokens"]) == (10, 5)
    finally:
        led.detach_usage_log()
