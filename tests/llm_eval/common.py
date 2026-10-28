"""Case, nap prompt va dung messages cho bo kiem dinh."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from tests.llm_eval import synth_data

HERE = Path(__file__).resolve().parent
PROMPTS_DIR = HERE / "prompts"
ROLES = ("analysis", "report", "slide")


@dataclass
class Case:
    id: str
    role: str
    user: str  # yeu cau chinh (cho report/slide: nhiem vu; so ket qua va insight ghep them)
    checks: dict = field(default_factory=dict)
    # analysis
    reference: dict | None = None  # cau tra loi mau dung (dang AnalysisOutput)
    expected: object = None
    # report / slide
    ledger: dict = field(default_factory=dict)
    insights: list = field(default_factory=list)
    chart_refs: list = field(default_factory=list)


def load_prompt(role):
    """Doc prompt he thong, bo ghi chu <!-- --> o dau file."""
    text = (PROMPTS_DIR / f"{role}.md").read_text(encoding="utf-8")
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL).strip()
    if role == "analysis":
        text = text.replace("<<SAMPLE_ROWS>>", synth_data.sample_text())
    return text


def user_content(case):
    if case.role == "analysis":
        return case.user
    lines = [f"Nhiệm vụ: {case.user}"]
    if case.role == "report":
        lo, hi = case.checks.get("min_sections", 1), case.checks.get("max_sections", 10)
        lines.append(f"Số mục: từ {lo} đến {hi}.")
    else:
        lines.append(f"Tối đa {case.checks['max_slides']} slide, mỗi slide tối đa {case.checks.get('max_bullets', 5)} bullet.")
        lines.append(f"chart_ref hợp lệ: {', '.join(case.chart_refs)}")
    lines.append("Sổ kết quả:")
    lines.append(json.dumps(case.ledger, ensure_ascii=False))
    if case.insights:
        lines.append("Insight:")
        lines += [f"- {i}" for i in case.insights]
    return "\n".join(lines)


def build_messages(case):
    return [
        {"role": "system", "content": load_prompt(case.role)},
        {"role": "user", "content": user_content(case)},
    ]


def load_cases(role):
    if role == "analysis":
        from tests.llm_eval.cases_analysis import CASES
    elif role == "report":
        from tests.llm_eval.cases_report import CASES
    elif role == "slide":
        from tests.llm_eval.cases_slide import CASES
    else:
        raise ValueError(f"role không hỗ trợ: {role}")
    return CASES
