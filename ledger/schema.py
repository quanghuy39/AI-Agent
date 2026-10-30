"""Schema cua so ket qua (ledger). Khong import litellm.

Moi ket qua so (ResultEntry) co id R1, R2...; van ban cua AI chi tham chieu so qua placeholder
{{R3.total}}, code se dien gia tri tu result_summary.
"""

import re
from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RESULT_ID = r"^R\d+$"
DECISION_ID = r"^D\d+$"
FINDING_ID = r"^F\d+$"
OPEN_ITEM_ID = r"^O\d+$"


def now_iso():
    """Thoi diem hien tai theo gio may, dang ISO co mui gio."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _check_iso(value):
    if value is None:
        return value
    try:
        datetime.fromisoformat(value)
    except (TypeError, ValueError):
        raise ValueError(f"không phải thời gian ISO: {value!r}") from None
    return value


class ResultStatus(StrEnum):
    VALID = "valid"
    STALE = "stale"
    SUPERSEDED = "superseded"


class CheckStatus(StrEnum):
    PASS = "pass"
    FLAG = "flag"
    UNCHECKED = "unchecked"


class DecisionStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"


class DecisionKind(StrEnum):
    METRIC_DEFINITION = "metric_definition"
    ASSUMPTION = "assumption"
    ANOMALY_HANDLING = "anomaly_handling"


class FindingKind(StrEnum):
    EVIDENCE = "evidence"
    HYPOTHESIS = "hypothesis"


class OpenItemStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class Phase(StrEnum):
    UNDERSTAND = "understand"
    EXPLORE = "explore"
    PLAN = "plan"
    LOOP = "loop"
    SYNTHESIZE = "synthesize"
    DONE = "done"


class _Model(BaseModel):
    # extra="forbid": go sai ten truong thi bao loi thay vi lang le bo qua
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class Check(_Model):
    name: str
    status: CheckStatus = CheckStatus.UNCHECKED
    reason: str = ""


class ResultEntry(_Model):
    id: str = Field(pattern=RESULT_ID)
    question: str
    question_key: str | None = None  # key trong ke hoach (Q1, Q2...); ho so cu khong co
    method: str
    result_summary: dict[str, Any]  # so co nhan, vd {"total": 1234} -> {{R3.total}}
    output_path: str | None = None
    code_file: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    definition_version: int = Field(default=1, ge=1)
    checks: list[Check] = Field(default_factory=list)
    status: ResultStatus = ResultStatus.VALID
    created_at: str = Field(default_factory=now_iso)

    @field_validator("created_at")
    @classmethod
    def _created_at_iso(cls, v):
        return _check_iso(v)

    @field_validator("depends_on")
    @classmethod
    def _depends_on_are_result_ids(cls, v):
        bad = [x for x in v if not _matches(RESULT_ID, x)]
        if bad:
            raise ValueError(f"depends_on chỉ chứa id kết quả dạng R<số>, sai: {bad}")
        return v


class Decision(_Model):
    id: str = Field(pattern=DECISION_ID)
    kind: DecisionKind
    text: str
    required: bool = True
    status: DecisionStatus = DecisionStatus.PENDING
    created_at: str = Field(default_factory=now_iso)
    confirmed_at: str | None = None

    @field_validator("created_at", "confirmed_at")
    @classmethod
    def _times_iso(cls, v):
        return _check_iso(v)

    @model_validator(mode="after")
    def _confirmed_at_matches_status(self):
        if self.status == DecisionStatus.CONFIRMED and self.confirmed_at is None:
            raise ValueError("decision confirmed phải có confirmed_at")
        if self.status == DecisionStatus.PENDING and self.confirmed_at is not None:
            raise ValueError("decision pending không được có confirmed_at")
        return self

    def confirm(self, at: str | None = None) -> "Decision":
        """Tra ve ban moi da xac nhan (status=confirmed, confirmed_at=at hoac thoi diem hien tai).
        Khong sua doi tuong goc. Da confirmed roi thi ValueError."""
        if self.status == DecisionStatus.CONFIRMED:
            raise ValueError(f"decision {self.id} đã confirmed lúc {self.confirmed_at}")
        data = self.model_dump()
        data.update(status=DecisionStatus.CONFIRMED, confirmed_at=at if at is not None else now_iso())
        return Decision.model_validate(data)


class Finding(_Model):
    id: str = Field(pattern=FINDING_ID)
    text: str
    kind: FindingKind
    result_ids: list[str] = Field(default_factory=list)

    @field_validator("result_ids")
    @classmethod
    def _result_ids_format(cls, v):
        bad = [x for x in v if not _matches(RESULT_ID, x)]
        if bad:
            raise ValueError(f"result_ids chỉ chứa id kết quả dạng R<số>, sai: {bad}")
        return v

    @model_validator(mode="after")
    def _evidence_needs_results(self):
        if self.kind == FindingKind.EVIDENCE and not self.result_ids:
            raise ValueError("finding loại evidence phải có ít nhất một result_id")
        return self


class OpenItem(_Model):
    id: str = Field(pattern=OPEN_ITEM_ID)
    text: str
    status: OpenItemStatus = OpenItemStatus.OPEN


class RunState(_Model):
    """Tien do cua mot bai (state.json). phase: pha dang do. Cau nao da giai thi hoi Ledger.results_by_question."""
    phase: Phase
    plan: dict[str, Any] | None = None  # AnalysisPlan.model_dump
    sensitive: list[str] | None = None  # cot che "BANG.cot" cua lan chay dau; None: chua chot
    updated_at: str = Field(default_factory=now_iso)

    @field_validator("updated_at")
    @classmethod
    def _updated_at_iso(cls, v):
        return _check_iso(v)


class LogEvent(_Model):
    ts: str = Field(default_factory=now_iso)
    step: str
    action: str
    inputs: dict[str, Any] = Field(default_factory=dict)
    code_file: str | None = None
    checks: list[Check] = Field(default_factory=list)
    status: str
    extra: dict[str, Any] = Field(default_factory=dict)  # cho noi thong tin luot goi AI (muc sau)

    @field_validator("ts")
    @classmethod
    def _ts_iso(cls, v):
        return _check_iso(v)


def _matches(pattern, value):
    return isinstance(value, str) and re.fullmatch(pattern, value) is not None
