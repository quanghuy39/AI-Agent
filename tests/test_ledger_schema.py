"""Kiem tra ledger/schema.py.

Chay: python -m pytest tests/test_ledger_schema.py -v
"""

from datetime import datetime

import pytest
from pydantic import ValidationError

from ledger import (
    Check,
    CheckStatus,
    Decision,
    DecisionKind,
    DecisionStatus,
    Finding,
    FindingKind,
    LogEvent,
    OpenItem,
    OpenItemStatus,
    ResultEntry,
    ResultStatus,
)


def result(**kw):
    data = {"id": "R3", "question": "Tổng doanh thu?", "method": "sum(amount)", "result_summary": {"total": 1234}}
    data.update(kw)
    return ResultEntry(**data)


# ---------- Tao duoc khi dung ----------

def test_enum_values():
    assert [s.value for s in ResultStatus] == ["valid", "stale", "superseded"]
    assert [s.value for s in CheckStatus] == ["pass", "flag", "unchecked"]
    assert [s.value for s in DecisionStatus] == ["pending", "confirmed"]
    assert [s.value for s in FindingKind] == ["evidence", "hypothesis"]
    assert [s.value for s in OpenItemStatus] == ["open", "closed"]
    assert ResultStatus.VALID == "valid"  # str Enum


def test_check_defaults():
    c = Check(name="no_nulls")
    assert (c.status, c.reason) == (CheckStatus.UNCHECKED, "")
    assert Check(name="x", status="flag", reason="có null").status is CheckStatus.FLAG


def test_result_entry_defaults_and_summary():
    r = result()
    assert r.result_summary["total"] == 1234
    assert r.status is ResultStatus.VALID
    assert (r.depends_on, r.checks, r.definition_version) == ([], [], 1)
    assert r.output_path is None and r.code_file is None
    datetime.fromisoformat(r.created_at)  # tu dien, dung ISO


def test_result_entry_full_and_roundtrip():
    r = result(
        output_path="output/r3.csv", code_file="code/r3.py", depends_on=["R1", "R2"], definition_version=2,
        checks=[{"name": "no_dup_keys", "status": "pass"}], status="stale",
    )
    assert r.checks[0].status is CheckStatus.PASS and r.status is ResultStatus.STALE
    dumped = r.model_dump(mode="json")
    assert dumped["status"] == "stale" and dumped["checks"][0]["status"] == "pass"
    assert ResultEntry.model_validate(dumped) == r


def test_decision_finding_open_item_log():
    d = Decision(id="D1", kind="metric_definition", text="Doanh thu = tổng amount đơn completed")
    assert (d.kind, d.required, d.status, d.confirmed_at) == (
        DecisionKind.METRIC_DEFINITION, True, DecisionStatus.PENDING, None)
    d2 = Decision(id="D2", kind="assumption", text="x", status="confirmed", confirmed_at="2026-10-28T10:00:00+07:00")
    assert d2.status is DecisionStatus.CONFIRMED

    f = Finding(id="F1", text="Web dẫn đầu ({{R1.pct}})", kind="evidence", result_ids=["R1"])
    assert f.kind is FindingKind.EVIDENCE
    h = Finding(id="F2", text="Có thể do khuyến mãi", kind="hypothesis")
    assert h.result_ids == []

    o = OpenItem(id="O1", text="Hỏi lại định nghĩa khách hàng mới")
    assert o.status is OpenItemStatus.OPEN

    e = LogEvent(step="analysis", action="run_code", status="ok", checks=[Check(name="runs", status="pass")])
    assert (e.inputs, e.extra, e.code_file) == ({}, {}, None)
    datetime.fromisoformat(e.ts)


# ---------- Bao loi khi sai ----------

@pytest.mark.parametrize("model,bad_id", [
    (lambda i: result(id=i), "R"),
    (lambda i: result(id=i), "r3"),
    (lambda i: result(id=i), "R3a"),
    (lambda i: result(id=i), "D3"),
    (lambda i: Decision(id=i, kind="assumption", text="x"), "R1"),
    (lambda i: Finding(id=i, text="x", kind="hypothesis"), "F"),
    (lambda i: OpenItem(id=i, text="x"), "O-1"),
])
def test_bad_id(model, bad_id):
    with pytest.raises(ValidationError):
        model(bad_id)


@pytest.mark.parametrize("make", [
    lambda: result(status="deleted"),
    lambda: result(status="VALID"),
    lambda: Check(name="x", status="ok"),
    lambda: Decision(id="D1", kind="assumption", text="x", status="done"),
    lambda: Decision(id="D1", kind="guess", text="x"),
    lambda: Finding(id="F1", text="x", kind="opinion", result_ids=["R1"]),
    lambda: OpenItem(id="O1", text="x", status="resolved"),
])
def test_bad_status(make):
    with pytest.raises(ValidationError):
        make()


def test_evidence_requires_result_ids():
    with pytest.raises(ValidationError, match="evidence"):
        Finding(id="F1", text="Doanh thu tăng", kind="evidence")
    with pytest.raises(ValidationError, match="evidence"):
        Finding(id="F1", text="Doanh thu tăng", kind="evidence", result_ids=[])


def test_bad_reference_ids():
    with pytest.raises(ValidationError):
        Finding(id="F1", text="x", kind="evidence", result_ids=["total"])
    with pytest.raises(ValidationError):
        result(depends_on=["D1"])


def test_bad_time_and_extra_field():
    with pytest.raises(ValidationError):
        result(created_at="hôm qua")
    with pytest.raises(ValidationError):
        Decision(id="D1", kind="assumption", text="x", confirmed_at="28/10/2026")
    with pytest.raises(ValidationError):
        result(statsu="valid")  # go sai ten truong


def test_confirmed_decision_requires_confirmed_at():
    with pytest.raises(ValidationError, match="confirmed_at"):
        Decision(id="D1", kind="assumption", text="x", status="confirmed")


def test_pending_decision_must_not_have_confirmed_at():
    with pytest.raises(ValidationError, match="confirmed_at"):
        Decision(id="D1", kind="assumption", text="x", status="pending", confirmed_at="2026-10-28T10:00:00+07:00")


def test_confirmed_decision_with_confirmed_at_is_valid():
    d = Decision(id="D1", kind="assumption", text="x", status="confirmed", confirmed_at="2026-10-28T10:00:00+07:00")
    assert d.status is DecisionStatus.CONFIRMED
    assert d.confirmed_at == "2026-10-28T10:00:00+07:00"


def test_confirm_returns_new_confirmed_and_keeps_original():
    d = Decision(id="D1", kind="metric_definition", text="Doanh thu = tổng amount đơn completed")
    c = d.confirm()
    assert c.status is DecisionStatus.CONFIRMED
    assert c.confirmed_at is not None
    datetime.fromisoformat(c.confirmed_at)
    assert len(c.confirmed_at) == len(d.created_at)  # cung dinh dang voi created_at (giay + mui gio)
    assert (c.id, c.kind, c.text, c.created_at) == (d.id, d.kind, d.text, d.created_at)
    assert d.status is DecisionStatus.PENDING and d.confirmed_at is None  # ban goc khong doi


def test_confirm_uses_given_time():
    d = Decision(id="D2", kind="assumption", text="x")
    c = d.confirm(at="2026-10-28T10:00:00+07:00")
    assert c.confirmed_at == "2026-10-28T10:00:00+07:00"


def test_confirm_already_confirmed_raises():
    c = Decision(id="D3", kind="assumption", text="x").confirm()
    with pytest.raises(ValueError, match="đã confirmed"):
        c.confirm()


def test_assignment_is_validated():
    r = result()
    r.status = "superseded"
    assert r.status is ResultStatus.SUPERSEDED
    with pytest.raises(ValidationError):
        r.status = "gone"
