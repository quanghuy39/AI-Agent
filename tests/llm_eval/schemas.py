"""Schema pydantic cho cau tra loi cua tung role trong bo kiem dinh.

analysis dung dung schema cua skill that (skills.analysis.schemas.AnalysisStep)."""

from pydantic import BaseModel

from skills.analysis.schemas import AnalysisStep


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


SCHEMAS = {"analysis": AnalysisStep, "report": ReportOutput, "slide": SlideOutput}
