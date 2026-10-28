"""Schema pydantic cho cau tra loi cua tung role trong bo kiem dinh."""

from pydantic import BaseModel


class AnalysisOutput(BaseModel):
    explanation: str
    code: str
    assumptions: list[str]
    cannot_answer_reason: str | None = None


class Section(BaseModel):
    heading: str
    body: str


class ReportOutput(BaseModel):
    sections: list[Section]


class Slide(BaseModel):
    title: str
    bullets: list[str]
    chart_ref: str | None = None


class SlideOutput(BaseModel):
    slides: list[Slide]


SCHEMAS = {"analysis": AnalysisOutput, "report": ReportOutput, "slide": SlideOutput}
