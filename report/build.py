"""Dung bo bao cao tu insights.json vao output/<bai>/report/. Khong goi AI. Tra ve {ten: duong dan}."""

import config
from report.charts import draw_charts
from report.docx_builder import build_document, report_title, save_document
from report.slides_outline import build_outline, save_outline


def build_report(ledger, task, record, profile, mask=str):
    """Ve bieu do, dung Word, ho so bai toan, dan y slide. Loi: ChartError hoac ReportError, khong ghi file nao ngoai
    bieu do: Word va dan y duoc dung va kiem xong moi ghi."""
    folder = ledger.root / config.REPORT_SUBDIR
    charts = draw_charts(record, ledger, mask)
    document = build_document(record, ledger, charts, task, profile, mask)
    slides = build_outline(record, charts, report_title(ledger.get_problem() or {}, task), ledger.root, mask)
    docx_path = folder / f"bao_cao_{task}.docx"
    save_document(document, docx_path)
    case_path = folder / f"ho_so_{task}.md"
    case_path.write_text(mask(ledger.render_markdown()), encoding="utf-8")  # file rieng, khong chen vao Word
    outline_path = folder / "slides_outline.json"
    save_outline(slides, outline_path)
    ledger.log_event("report", "build", extra={"n_charts": len(charts), "docx": docx_path.name, "n_slides": len(slides)})
    return {"docx": docx_path, "case": case_path, "outline": outline_path}
