"""Kiem tra bao cao Word: thu tu muc, font, bang, anh, kiem cuoi. Du lieu gia, khong goi AI, khong goi R2.

Chay: python -m pytest tests/test_docx_builder.py -v
"""

import json
from datetime import date

import docx
import pandas as pd
import pytest
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn

import config
from ledger import Ledger, Phase
from report.build import build_report
from report.charts import ChartError
from report.docx_builder import HEADINGS, ReportError, _shorten, build_document, report_title, save_document
from report.slides_outline import build_outline

TASK = "bai0a"
PASS = [{"name": "c", "status": "pass"}]
FONT = "Times New Roman"
PROBLEM = "# Bài 0a: Cơ cấu sản phẩm theo nhóm\n\nCâu hỏi: mỗi nhóm có bao nhiêu sản phẩm?"
CHART = {"type": "bar", "file": "by_group.csv", "x": "group", "y": ["total"], "series": None, "title": "Tổng theo nhóm"}
TABLE = {"file": "by_group.csv", "columns": ["group", "year", "total"]}
PROFILE = {"tables": {"PRODUCT": {"n_rows": 1250, "n_cols": 5, "flags": []}},
           "quality_flags": [{"severity": "info", "code": "x", "message": "Cột A thiếu một ít giá trị", "source_key": "k"}]}


def insight(i, requirement, **extra):
    return {"id": f"I{i}", "requirement": requirement, "text": "t", "text_filled": f"Nhận định {i} là 1.250.",
            "result_ids": ["R1"], "label": "verified", "table": None, "chart": None, **extra}


RECORD = {"summary": "s", "summary_filled": "Tóm tắt đạt 1.250.", "conclusion": "c", "conclusion_filled": "Kết luận rõ.",
          "limitations": "l", "limitations_filled": "Hạn chế rõ.", "requirements": ["Số sản phẩm", "Tổng giá"],
          "column_labels": {"group": "Nhóm hàng", "total": "Tổng tiền"}, "model": "fake",
          "insights": [insight(1, 1, table=TABLE, chart=CHART), insight(2, 2, label="hypothesis")]}


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path / "output" / TASK)
    ledger.set_state(Phase.DONE, plan={"requirements": RECORD["requirements"]})
    ledger.set_problem({"problem": PROBLEM, "restatement": {"restatement": "Đếm sản phẩm theo nhóm."}})
    work = ledger.root / "work"
    work.mkdir()
    pd.DataFrame({"group": [f"N{i}" for i in range(20)], "year": [2024] * 20,
                  "total": [1250.5 + i for i in range(20)]}).to_csv(work / "by_group.csv", index=False)
    ledger.add_result("Tổng theo nhóm?", "pandas groupby", {"n": 20}, PASS, question_key="Q1", output_path="work/by_group.csv")
    ledger.confirm_decision(ledger.add_decision("metric_definition", "Doanh thu gồm thuế").id)
    ledger.confirm_decision(ledger.add_decision("anomaly_handling", "Bỏ dòng trùng khóa").id)
    ledger.confirm_decision(ledger.add_decision("assumption", "Chấp nhận kết luận", source_key="accept_conclusion|synthesize").id)
    return ledger


def built(led, record=RECORD, mask=str):
    return docx.Document(build_report(led, TASK, record, PROFILE, mask)["docx"])


def styled(doc, name):
    return [p.text for p in doc.paragraphs if p.style.name == name]


def fonts(element):
    r = element.find(qn("w:rPr")).find(qn("w:rFonts"))
    return {k.split("}")[1]: v for k, v in r.attrib.items()}


# thu tu va noi dung
def test_seven_headings_in_order(led):
    assert styled(built(led), "Heading 1") == list(HEADINGS)


def test_requirements_are_subsections_in_order(led):
    h2 = styled(built(led), "Heading 2")
    assert h2[:2] == ["3.1. Số sản phẩm", "3.2. Tổng giá"]
    assert h2[2:] == ["5.1. Nguồn dữ liệu", "5.2. Chất lượng dữ liệu", "6.1. Phương pháp", "6.2. Giả định đã xác nhận"]


def test_cover_has_title_date_author_then_toc(led, monkeypatch):
    monkeypatch.setenv("REPORT_AUTHOR", "Nguyễn Văn A")
    doc = built(led)
    texts = [p.text for p in doc.paragraphs]
    assert styled(doc, "Title") == ["Cơ cấu sản phẩm theo nhóm"]
    assert any(t.startswith("Ngày lập: ") for t in texts)
    assert texts.index("Người lập: Nguyễn Văn A") < texts.index("Mục lục") < texts.index(HEADINGS[0])


def test_cover_has_no_task_name(led):
    assert not any("Tên bài" in p.text or TASK in p.text for p in built(led).paragraphs)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_cover_without_author_has_no_line(led, monkeypatch, value):
    monkeypatch.delenv("REPORT_AUTHOR", raising=False)
    if value is not None:
        monkeypatch.setenv("REPORT_AUTHOR", value)
    assert not any("Người lập" in p.text for p in built(led).paragraphs)


def test_report_author_is_optional_setting():
    assert "REPORT_AUTHOR" not in config.R2_VARS + config.LLM_KEY_VARS + config.OTHER_VARS
    assert "REPORT_AUTHOR=" in (config.ROOT / ".env.example").read_text(encoding="utf-8")


def test_accept_flag_decision_only_in_section_six(led):
    d = led.add_decision("anomaly_handling", "chấp nhận R1 dù có cờ join", source_key="accept_flags|R1")
    led.confirm_decision(d.id)
    doc = built(led)
    texts = [(p.style.name, p.text) for p in doc.paragraphs]
    assert ("List Bullet", d.text) in texts
    order = [t for _, t in texts]
    assert order.index(d.text) > order.index("6.2. Giả định đã xác nhận")


def test_internal_codes_kept_for_tracing(led):
    doc = built(led)
    assert "(kết quả R1)" in styled(doc, "Caption")[0]
    assert any(t.startswith("R1: ") for t in styled(doc, "List Bullet"))


def test_toc_field_and_update_on_open(led):
    doc = built(led)
    assert any("TOC" in (i.text or "") for i in doc.element.body.iter(qn("w:instrText")))
    update = doc.settings.element.find(qn("w:updateFields"))
    assert update is not None and update.get(qn("w:val")) == "true"


def test_body_text_and_labels(led):
    doc = built(led)
    texts = [p.text for p in doc.paragraphs]
    assert "Nhận định 1 là 1.250." in texts and "Đã kiểm chứng" in texts and "Phỏng đoán" in texts
    assert "Tóm tắt đạt 1.250." in texts and "Kết luận rõ." in texts and "Hạn chế rõ." in texts
    run = next(p for p in doc.paragraphs if p.text == "Phỏng đoán").runs[0]
    assert run.font.size.pt == config.REPORT_LABEL_PT and run.font.color.rgb is not None


def test_captions_numbered(led):
    captions = styled(built(led), "Caption")
    assert captions == ["Bảng 1: Nhóm hàng, year, Tổng tiền (kết quả R1)", "Hình 1: Tổng theo nhóm"]


def test_sections_five_and_six_use_ledger(led):
    doc = built(led)
    bullets = styled(doc, "List Bullet")
    assert "Bỏ dòng trùng khóa" in bullets and "Cột A thiếu một ít giá trị" in bullets
    assert "Doanh thu gồm thuế" in bullets and "R1: Tổng theo nhóm? Phương pháp: pandas groupby" in bullets
    assert "Chấp nhận kết luận" not in bullets
    source = doc.tables[1]
    assert [c.text for c in source.rows[1].cells] == ["PRODUCT", "1.250", "5"]


def test_superseded_and_pending_decisions_left_out(led):
    led.add_decision("assumption", "CHUAXACNHAN")
    assert "CHUAXACNHAN" not in "\n".join(p.text for p in built(led).paragraphs)


def test_no_data_issue_says_so(led):
    led.supersede_decisions()
    doc = docx.Document(build_report(led, TASK, RECORD, {"tables": {}, "quality_flags": []})["docx"])
    texts = [p.text for p in doc.paragraphs]
    assert "Không phát hiện bất thường trong dữ liệu." in texts and "Không có giả định nào ngoài đề bài." in texts
    assert "Không có thông tin bảng nguồn." in texts


# dinh dang
@pytest.mark.parametrize("name", ["Normal", "Heading 1", "Heading 2", "Heading 3", "Caption", "List Bullet", "Title", "Table Grid"])
def test_times_new_roman_every_style_with_east_asian(led, name):
    got = fonts(built(led).styles[name].element)
    assert got == {"ascii": FONT, "hAnsi": FONT, "eastAsia": FONT, "cs": FONT}


def test_doc_defaults_font_and_body_size(led):
    doc = built(led)
    defaults = doc.styles.element.find(qn("w:docDefaults")).find(qn("w:rPrDefault"))
    assert fonts(defaults)["ascii"] == FONT and doc.styles["Normal"].font.size.pt == 12


def test_headings_keep_word_styles_black(led):
    style = built(led).styles["Heading 1"]
    color = style.element.find(qn("w:rPr")).find(qn("w:color"))
    assert color.get(qn("w:val")) == "000000" and color.get(qn("w:themeColor")) is None


def test_bullets_use_style_without_typed_character(led):
    doc = built(led)
    bullets = styled(doc, "List Bullet")
    assert bullets and not any(p.text.lstrip().startswith(("•", "-", "*")) for p in doc.paragraphs)


def test_page_setup_header_footer(led):
    section = built(led).sections[0]
    assert round(section.left_margin.cm, 1) == round(section.top_margin.cm, 1) == 2.5
    assert section.different_first_page_header_footer
    assert section.header.paragraphs[0].text == "Cơ cấu sản phẩm theo nhóm"
    assert section.first_page_header.paragraphs[0].text == ""
    codes = [i.text for i in section.footer._element.iter(qn("w:instrText"))]
    assert any("PAGE" in c for c in codes)


# bang
def test_table_header_style_and_repeat(led):
    table = built(led).tables[0]
    head = table.rows[0]
    assert head._tr.trPr.find(qn("w:tblHeader")) is not None
    assert all(c._tc.tcPr.find(qn("w:shd")) is not None for c in head.cells)
    assert all(c.paragraphs[0].runs[0].bold for c in head.cells)
    assert [c.text for c in head.cells] == ["Nhóm hàng", "year", "Tổng tiền"]
    assert table.style.name == "Table Grid"


def test_number_columns_right_aligned_vietnamese_year_unchanged(led):
    row = built(led).tables[0].rows[1]
    assert [c.text for c in row.cells] == ["N0", "2024", "1.250,5"]
    assert row.cells[2].paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.RIGHT
    assert row.cells[0].paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.LEFT
    assert row.cells[1].paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.LEFT


def test_long_table_cut_with_note(led):
    doc = built(led)
    assert len(doc.tables[0].rows) == 16
    assert any(p.text == "Hiển thị 15/20 dòng, đầy đủ trong file by_group.csv" for p in doc.paragraphs)


def test_short_table_no_note(led):
    pd.DataFrame({"group": ["A"], "year": [2024], "total": [1]}).to_csv(led.root / "work" / "by_group.csv", index=False)
    assert not any("Hiển thị" in p.text for p in built(led).paragraphs)


# anh va kiem cuoi
def test_picture_embedded(led):
    assert len(built(led).inline_shapes) == 1


def test_no_placeholder_left_in_good_report(led):
    doc = built(led)
    assert "{{" not in "\n".join(p.text for p in doc.paragraphs)


@pytest.mark.parametrize("field", ["summary_filled", "conclusion_filled", "limitations_filled"])
def test_placeholder_left_stops_without_file(led, field):
    with pytest.raises(ReportError, match="placeholder"):
        build_report(led, TASK, {**RECORD, field: "còn {{R1.n}}"}, PROFILE)
    assert not list((led.root / "report").glob("*.docx"))


def test_missing_table_stops_without_file(led):
    record = {**RECORD, "insights": [insight(1, 1, table={"file": "khong_co.csv", "columns": ["a"]}), insight(2, 2)]}
    with pytest.raises(ReportError, match="không có bảng khong_co.csv"):
        build_report(led, TASK, record, PROFILE)
    assert not list((led.root / "report").glob("*.docx"))


def test_missing_column_stops(led):
    record = {**RECORD, "insights": [insight(1, 1, table={"file": "by_group.csv", "columns": ["khong_co"]}), insight(2, 2)]}
    with pytest.raises(ReportError, match="không có cột khong_co"):
        build_report(led, TASK, record, PROFILE)


def test_cover_date_format(led):
    doc = build_document({**RECORD, "insights": []}, led, {}, TASK, PROFILE, today=date(2026, 10, 31))
    assert "Ngày lập: 31/10/2026" in [p.text for p in doc.paragraphs]


def test_missing_image_stops(led):
    with pytest.raises(ReportError, match="không có ảnh biểu đồ"):
        build_document(RECORD, led, {}, TASK, PROFILE)


def test_missing_chart_file_stops_before_word(led):
    (led.root / "work" / "by_group.csv").unlink()
    with pytest.raises(ChartError):
        build_report(led, TASK, RECORD, PROFILE)


def test_failed_rebuild_keeps_previous_file(led):
    path = build_report(led, TASK, RECORD, PROFILE)["docx"]
    before = path.read_bytes()
    with pytest.raises(ReportError):
        build_report(led, TASK, {**RECORD, "summary_filled": "{{R1.n}}"}, PROFILE)
    assert path.read_bytes() == before


def test_open_file_gives_clear_error(led, monkeypatch):
    doc = built(led)

    def denied(*a):
        raise PermissionError

    monkeypatch.setattr("os.replace", denied)
    with pytest.raises(ReportError, match="đang mở trong Word"):
        save_document(doc, led.root / "report" / "x.docx")
    assert not list((led.root / "report").glob(".*.tmp"))


def test_masked_value_never_in_word(led):
    pd.DataFrame({"group": ["BIMAT", "A"], "year": [2024, 2024], "total": [1, 2]}).to_csv(
        led.root / "work" / "by_group.csv", index=False)
    record = {**RECORD, "conclusion_filled": "Khách BIMAT mua nhiều."}
    doc = built(led, record, mask=lambda s: s.replace("BIMAT", "[đã che]"))
    text = "\n".join(p.text for p in doc.paragraphs) + "\n".join(c.text for t in doc.tables for r in t.rows for c in r.cells)
    assert "BIMAT" not in text and "[đã che]" in text


def test_masked_numeric_id_not_shown_as_number(led):
    pd.DataFrame({"group": ["A"], "year": [2024], "total": [778899]}).to_csv(led.root / "work" / "by_group.csv", index=False)
    doc = built(led, {**RECORD, "insights": [insight(1, 1, table=TABLE), insight(2, 2)]},
                mask=lambda s: s.replace("778899", "[đã che]"))
    assert doc.tables[0].rows[1].cells[2].text == "[đã che]"


def test_old_record_without_new_keys(led):
    old = {k: v for k, v in RECORD.items() if k != "column_labels"}
    old["insights"] = [{k: v for k, v in insight(i, i).items() if k not in ("table", "chart")} for i in (1, 2)]
    assert styled(built(led, old), "Heading 1") == list(HEADINGS)


# ho so bai toan
def test_case_file_is_ledger_markdown(led):
    outputs = build_report(led, TASK, RECORD, PROFILE)
    assert outputs["case"] == led.root / "report" / f"ho_so_{TASK}.md"
    assert outputs["case"].read_text(encoding="utf-8") == led.render_markdown()


def test_case_file_masks_values_and_stays_out_of_word(led):
    led.add_result("Khách nào?", "m", {"top": "BIMAT"}, PASS)
    outputs = build_report(led, TASK, RECORD, PROFILE, lambda s: s.replace("BIMAT", "[đã che]"))
    text = outputs["case"].read_text(encoding="utf-8")
    assert "BIMAT" not in text and "[đã che]" in text
    assert "Hồ sơ phân tích" not in "\n".join(p.text for p in docx.Document(outputs["docx"]).paragraphs)


def test_case_file_not_written_when_word_fails(led):
    with pytest.raises(ReportError):
        build_report(led, TASK, {**RECORD, "summary_filled": "{{R1.n}}"}, PROFILE)
    assert not (led.root / "report" / f"ho_so_{TASK}.md").exists()


# dan y slide
def outline_of(led, record=RECORD, mask=str):
    return json.loads(build_report(led, TASK, record, PROFILE, mask)["outline"].read_text(encoding="utf-8"))["slides"]


def test_outline_order_and_fields(led):
    slides = outline_of(led)
    assert [s["type"] for s in slides] == ["title", "summary", "insight", "insight", "conclusion"]
    assert slides[0]["title"] == "Cơ cấu sản phẩm theo nhóm" and slides[1]["bullets"] == ["Tóm tắt đạt 1.250."]
    first, second = slides[2], slides[3]
    assert first == {"type": "insight", "title": "Số sản phẩm", "bullets": ["Nhận định 1 là 1.250."],
                     "chart": "report/charts/I1.png", "result_ids": ["R1"], "label": "verified"}
    assert second["chart"] is None and second["label"] == "hypothesis"
    assert slides[-1]["bullets"] == ["Kết luận rõ."]


def test_outline_chart_path_exists(led):
    assert (led.root / outline_of(led)[2]["chart"]).is_file()


def test_outline_splits_sentences_not_numbers(led):
    record = {**RECORD, "conclusion_filled": "Đạt 1.234,5 đơn. Tăng rõ! Còn gì?"}
    assert outline_of(led, record)[-1]["bullets"] == ["Đạt 1.234,5 đơn.", "Tăng rõ!", "Còn gì?"]


def test_outline_placeholder_stops(led):
    charts = {}
    with pytest.raises(ReportError, match="Dàn ý slide còn placeholder"):
        build_outline({**RECORD, "conclusion_filled": "{{R1.n}}"}, charts, "T", led.root)


def test_outline_masks_values(led):
    record = {**RECORD, "conclusion_filled": "Khách BIMAT."}
    assert "BIMAT" not in json.dumps(outline_of(led, record, lambda s: s.replace("BIMAT", "[đã che]")))


def test_nothing_written_when_outline_fails(led, monkeypatch):
    def fail(*a, **k):
        raise ReportError("x")

    monkeypatch.setattr("report.build.build_outline", fail)
    with pytest.raises(ReportError):
        build_report(led, TASK, RECORD, PROFILE)
    assert not list((led.root / "report").glob("bao_cao_*")) and not (led.root / "report" / "slides_outline.json").exists()


# bieu do du phong trong bao cao
def test_auto_chart_appears_in_word_and_outline(led):
    pd.DataFrame({"group": ["A", "B"], "total": [1, 2]}).to_csv(led.root / "work" / "by_group.csv", index=False)
    table = {"file": "by_group.csv", "columns": ["group", "total"]}
    record = {**RECORD, "insights": [insight(1, 1, table=table), insight(2, 2)]}
    outputs = build_report(led, TASK, record, PROFILE)
    doc = docx.Document(outputs["docx"])
    assert len(doc.inline_shapes) == 1 and "Hình 1: Tổng tiền theo Nhóm hàng" in styled(doc, "Caption")
    slides = json.loads(outputs["outline"].read_text(encoding="utf-8"))["slides"]
    assert slides[2]["chart"] == "report/charts/I1.png"
    assert "chart_auto" not in json.dumps(led.get_insights() or {})


def test_multi_dimension_table_gets_no_auto_chart(led):
    assert len(built(led, {**RECORD, "insights": [insight(1, 1, table=TABLE), insight(2, 2)]}).inline_shapes) == 0


# phu tro
@pytest.mark.parametrize("problem, title", [("# Bài 2: Đánh giá hiệu quả\n\nx", "Đánh giá hiệu quả"), ("\n# Đề khác\n", "Đề khác"),
                                            ("", "bai0a")])
def test_report_title(problem, title):
    assert report_title({"problem": problem}, "bai0a") == title


def test_summary_cut_at_sentence():
    text = "Câu một. " * 400
    short = _shorten(text, config.REPORT_SUMMARY_MAX_CHARS)
    assert len(short) <= config.REPORT_SUMMARY_MAX_CHARS and short.endswith(".")
    assert _shorten("Ngắn.", 100) == "Ngắn."
