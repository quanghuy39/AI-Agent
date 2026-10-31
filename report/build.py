"""Dung bo bao cao tu insights.json vao output/<bai>/report/. Khong goi AI. Tra ve {ten: duong dan}."""

import config
from report.charts import draw_charts
from report.docx_builder import build_document, save_document


def build_report(ledger, task, record, profile, mask=str):
    """Ve bieu do roi dung Word. Loi: ChartError hoac ReportError, khong ghi file Word."""
    folder = ledger.root / config.REPORT_SUBDIR
    charts = draw_charts(record, ledger, mask)
    docx_path = folder / f"bao_cao_{task}.docx"
    save_document(build_document(record, ledger, charts, task, profile, mask), docx_path)
    case_path = folder / f"ho_so_{task}.md"
    case_path.write_text(mask(ledger.render_markdown()), encoding="utf-8")  # file rieng, khong chen vao Word
    ledger.log_event("report", "build", extra={"n_charts": len(charts), "docx": docx_path.name})
    return {"docx": docx_path, "case": case_path}
