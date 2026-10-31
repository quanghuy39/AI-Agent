"""Dan y slide tu insights.json: slides_outline.json cho buoc lam slide. Chi code, khong AI."""

import json
import re

from report.docx_builder import ReportError


def _bullets(text, mask):
    """Moi cau mot y; cat o dau cau co khoang trang sau, nen 1.234,5 khong bi tach."""
    return [s for s in re.split(r"(?<=[.!?])\s+", mask(text).strip()) if s]


def build_outline(record, charts, title, root, mask=str):
    """Danh sach slide: tieu de, tom tat, moi insight mot slide, ket luan. Con placeholder -> ReportError.
    charts: {insight id: duong dan PNG}; duong dan trong outline tinh tu root (goc ho so)."""
    slides = [{"type": "title", "title": mask(title)},
              {"type": "summary", "title": "Tóm tắt", "bullets": _bullets(record["summary_filled"], mask)}]
    for ins in record["insights"]:
        path = charts.get(ins["id"])
        slides.append({"type": "insight", "title": mask(record["requirements"][ins["requirement"] - 1]),
                       "bullets": _bullets(ins["text_filled"], mask),
                       "chart": path.relative_to(root).as_posix() if path else None,
                       "result_ids": ins["result_ids"], "label": ins["label"]})
    slides.append({"type": "conclusion", "title": "Kết luận", "bullets": _bullets(record["conclusion_filled"], mask)})
    if "{{" in json.dumps(slides, ensure_ascii=False):
        raise ReportError("Dàn ý slide còn placeholder chưa điền.")
    return slides


def save_outline(slides, path):
    path.write_text(json.dumps({"slides": slides}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
