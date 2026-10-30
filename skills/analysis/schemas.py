"""Schema JSON cho skill phan tich: dau ra cua AI (ProblemRestatement, AnalysisPlan, AnalysisStep) va file meta do
CODE ghi (AgentMeta).

Ten truong cua MetaCheck khop tham so cua validators/ (row_count_check, sum_consistency, range_check, join_check)
de vong lap noi thang. Assumption khop ledger.add_decision(kind, text, required).
"""

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_KEY_RE = re.compile(r"^Q\d+$")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProblemRestatement(_Model):
    restatement: str = Field(min_length=1)
    goals: list[str] = Field(min_length=1)
    ambiguities: list[str] = Field(default_factory=list)


class Assumption(_Model):
    kind: Literal["metric_definition", "assumption", "anomaly_handling"]
    text: str = Field(min_length=1)
    required: bool = True


class ProposedRange(_Model):
    column: str = Field(pattern=r"^[^.\s][^.]*\..+$")  # "BANG.cot"
    min: float | None = None
    max: float | None = None
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def _bounds(self):
        if self.min is None and self.max is None:
            raise ValueError("proposed range cần ít nhất min hoặc max")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("min lớn hơn max")
        return self


class SubQuestion(_Model):
    key: str = Field(pattern=_KEY_RE.pattern)
    question: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)
    covers: list[int] = Field(default_factory=list)  # chi so (tu 1) trong AnalysisPlan.requirements


class AnalysisPlan(_Model):
    understanding: str
    requirements: list[str]  # cac yeu cau cua de (ngan); moi yeu cau phai co sub_question covers
    sub_questions: list[SubQuestion]
    assumptions: list[Assumption]
    proposed_ranges: list[ProposedRange] = Field(default_factory=list)
    cannot_answer_reason: str | None = None

    @model_validator(mode="after")
    def _questions(self):
        if not self.sub_questions and not self.cannot_answer_reason:
            raise ValueError("cần ít nhất một sub_question (hoặc điền cannot_answer_reason)")
        keys = [q.key for q in self.sub_questions]
        if len(keys) != len(set(keys)):
            raise ValueError(f"key của sub_questions bị trùng: {keys}")
        for q in self.sub_questions:
            bad = [d for d in q.depends_on if d not in keys or d == q.key]
            if bad:
                raise ValueError(f"{q.key}.depends_on tham chiếu key không hợp lệ: {bad}")
        if self.cannot_answer_reason:
            return self
        n = len(self.requirements)
        if n == 0 or any(not r.strip() for r in self.requirements):
            raise ValueError("requirements cần ít nhất một yêu cầu không rỗng (hoặc điền cannot_answer_reason)")
        for q in self.sub_questions:
            bad = [i for i in q.covers if not 1 <= i <= n]
            if bad:
                raise ValueError(f"{q.key}.covers có chỉ số ngoài 1..{n}: {bad}")
        covered = {i for q in self.sub_questions for i in q.covers}
        missing = [i for i in range(1, n + 1) if i not in covered]
        if missing:
            raise ValueError(f"yêu cầu {missing} chưa có sub_question nào covers")
        return self


class AnalysisStep(_Model):
    explanation: str
    method: str
    can_be_wrong_if: list[str]
    code: str
    output_files: list[str]
    new_assumptions: list[Assumption] = Field(default_factory=list)
    cannot_answer_reason: str | None = None

    @model_validator(mode="after")
    def _required_unless_cannot_answer(self):
        if self.cannot_answer_reason:
            return self
        if not [x for x in self.can_be_wrong_if if x.strip()]:
            raise ValueError("can_be_wrong_if cần ít nhất một ý (hoặc điền cannot_answer_reason)")
        if not self.code.strip():
            raise ValueError("code rỗng (chỉ được rỗng khi có cannot_answer_reason)")
        return self


# ---------- meta do code ghi ----------

class _Check(_Model):
    label: str = Field(min_length=1)


class RowCountCheck(_Check):
    type: Literal["row_count"]
    before: int = Field(ge=0)
    after: int = Field(ge=0)
    expect: Literal["same", "not_more", "not_fewer", "free"] = "free"
    expected_ratio: tuple[float, float] | None = None

    @field_validator("expected_ratio")
    @classmethod
    def _ratio(cls, v):
        if v is not None and v[0] > v[1]:
            raise ValueError("expected_ratio cần [lo, hi] với lo <= hi")
        return v


class SumCheck(_Check):
    type: Literal["sum"]
    parts: list[float]
    total: float


class RangeCheck(_Check):
    type: Literal["range"]
    file: str = Field(min_length=1)
    column: str = Field(min_length=1)
    min: float | None = None
    max: float | None = None

    @model_validator(mode="after")
    def _bounds(self):
        if self.min is None and self.max is None:
            raise ValueError("range check cần ít nhất min hoặc max")
        return self


class JoinCheck(_Check):
    type: Literal["join"]
    left: str = Field(min_length=1)    # ten file trong input hoac trong work
    right: str = Field(min_length=1)
    left_on: str | list[str]
    right_on: str | list[str]
    how: Literal["left", "inner"] = "left"


class CustomCheck(_Model):
    type: Literal["custom"]
    name: str = Field(min_length=1)
    passed: bool
    reason: str


MetaCheck = Annotated[RowCountCheck | SumCheck | RangeCheck | JoinCheck | CustomCheck, Field(discriminator="type")]
SummaryValue = bool | int | float | str | None


class AgentMeta(_Model):
    """File meta do code AI ghi vao AGENT_META_PATH; vong lap doc de ghi so ket qua."""
    result_summary: dict[str, SummaryValue]
    checks: list[MetaCheck] = Field(min_length=1)

    @field_validator("result_summary")
    @classmethod
    def _ascii_keys(cls, v):
        bad = [k for k in v if not _IDENT_RE.match(k)]
        if bad:
            raise ValueError(f"khóa result_summary phải là định danh ASCII (dùng cho {{{{R3.key}}}}): {bad}")
        return v
