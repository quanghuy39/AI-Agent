"""Schema JSON cho skill bao cao: dau ra cua AI (SynthesisOutput, Insight) va noi dung insights.json do CODE ghi
(InsightRecord, SynthesisRecord).

requirement la so thu tu yeu cau cua de, tinh tu 1; khoang hop le do validate_synthesis kiem de loi gui lai duoc cho AI.
Cung ly do, TableSpec va ChartSpec de truong rong rai (type la chuoi, so cot khong gioi han): validate_visuals kiem.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ledger.schema import now_iso


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TableSpec(_Model):
    file: str
    columns: list[str]


class ChartSpec(_Model):
    type: str
    file: str
    x: str
    y: list[str]
    series: str | None = None
    title: str


class Insight(_Model):
    id: str = Field(pattern=r"^I\d+$")
    requirement: int
    text: str = Field(min_length=1)
    result_ids: list[str] = Field(default_factory=list)
    table: TableSpec | None = None
    chart: ChartSpec | None = None


class SynthesisOutput(_Model):
    summary: str = Field(min_length=1)
    insights: list[Insight] = Field(min_length=1)
    conclusion: str = Field(min_length=1)
    limitations: str = Field(min_length=1)
    column_labels: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _unique_ids(self):
        ids = [i.id for i in self.insights]
        if len(ids) != len(set(ids)):
            raise ValueError(f"id của insights bị trùng: {ids}")
        return self


class InsightRecord(Insight):
    label: Literal["verified", "hypothesis"]  # CODE gan, xem report.placeholders.assign_label
    text_filled: str


class SynthesisRecord(_Model):
    summary: str
    summary_filled: str
    insights: list[InsightRecord]
    conclusion: str
    conclusion_filled: str
    limitations: str
    limitations_filled: str
    column_labels: dict[str, str] = Field(default_factory=dict)
    requirements: list[str]
    model: str
    created_at: str = Field(default_factory=now_iso)
