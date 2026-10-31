"""Kiem tra phan table/chart cua ban tong hop va ve bieu do. FakeLLM, du lieu gia, khong goi that.

Chay: python -m pytest tests/test_charts.py -v
"""

import pandas as pd
import pytest
from matplotlib import font_manager
from PIL import Image

import config
import llm
from analysis import loop
from ledger import Ledger, Phase
from report import charts
from report.charts import (
    ChartError,
    _font,
    _frame,
    _limit,
    _number,
    auto_chart,
    draw_chart,
    draw_charts,
    validate_visuals,
    with_auto_charts,
)
from report.synthesize import synthesize
from skills.report import load_prompt
from skills.report.schemas import ChartSpec, SynthesisOutput, TableSpec

TASK = "bai0a"
PASS = [{"name": "c", "status": "pass"}]
CHART = {"type": "bar", "file": "by_group.csv", "x": "group", "y": ["total"], "title": "Doanh thu theo nhóm"}
TABLE = {"file": "by_group.csv", "columns": ["group", "total"]}


@pytest.fixture
def led(tmp_path):
    ledger = Ledger(tmp_path / "output" / TASK)
    ledger.set_state(Phase.SYNTHESIZE, plan={"requirements": ["Doanh thu"]})
    ledger.set_problem({"problem": "Đề."})
    work = ledger.root / "work"
    work.mkdir()
    pd.DataFrame({"group": ["A", "B", "C"], "total": [1200, 3400.5, 90], "rate": [0.1, 0.2, 0.3],
                  "note": ["x", "y", "z"]}).to_csv(work / "by_group.csv", index=False)
    pd.DataFrame({"group": ["A", "A", "B", "B"], "brand": ["X", "Y", "X", "Y"],
                  "n": [1, 2, 3, 4]}).to_csv(work / "by_brand.csv", index=False)
    pd.DataFrame({"month": [f"T{i}" for i in range(30)], "n": range(30), "m": range(30)}).to_csv(work / "month.csv", index=False)
    ledger.add_result("Bảng?", "m", {"n": 3}, PASS, question_key="Q1",
                      output_path="work/by_group.csv; work/by_brand.csv; work/month.csv")
    ledger.add_result("Khác?", "m", {"n": 3}, PASS, question_key="Q2")
    return ledger


def output(chart=None, table=None, result_ids=("R1",), labels=None):
    insight = {"id": "I1", "requirement": 1, "text": "x", "result_ids": list(result_ids), "chart": chart, "table": table}
    return SynthesisOutput.model_validate({"summary": "s", "insights": [insight], "conclusion": "c", "limitations": "l",
                                           "column_labels": labels or {}})


def chart_errors(led, **changes):
    return validate_visuals(output(chart={**CHART, **changes}), led)


# B1: kiem tra
def test_good_table_and_chart_have_no_errors(led):
    assert validate_visuals(output(CHART, TABLE, labels={"group": "Nhóm"}), led) == []


def test_no_table_no_chart_has_no_errors(led):
    assert validate_visuals(output(), led) == []


def test_file_must_belong_to_cited_result(led):
    errors = validate_visuals(output(CHART, TABLE, result_ids=["R2"]), led)
    assert len(errors) == 2 and all("by_group.csv không thuộc bảng của result_ids" in e for e in errors)


def test_unknown_file(led):
    assert "file khac.csv không thuộc" in chart_errors(led, file="khac.csv")[0]


def test_invalid_type(led):
    assert chart_errors(led, type="pie") == ["I1.chart: type pie không hợp lệ; chỉ dùng bar, line, stacked_bar."]


def test_missing_columns(led):
    assert "không có cột khong_co" in chart_errors(led, x="khong_co")[0]
    assert "không có cột khong_co" in chart_errors(led, y=["khong_co"])[0]


def test_y_must_be_numeric(led):
    assert chart_errors(led, y=["note"]) == ["I1.chart: cột note không phải cột số."]


@pytest.mark.parametrize("y", [[], ["total", "rate", "total", "rate", "total"]])
def test_y_count(led, y):
    assert any("y cần từ một tới" in e for e in chart_errors(led, y=y))


def test_series_only_for_stacked_bar(led):
    assert any("series chỉ dùng cho stacked_bar" in e for e in chart_errors(led, series="note"))


def test_series_needs_single_y(led):
    errors = chart_errors(led, type="stacked_bar", series="note", y=["total", "rate"])
    assert "I1.chart: có series thì y chỉ gồm một cột." in errors


@pytest.mark.parametrize("title", ["Năm 2024", "", "{{R1.n}}"])
def test_title_without_digits(led, title):
    assert any("title phải có chữ" in e for e in chart_errors(led, title=title))


def test_duplicate_x_is_rejected_not_summed(led):
    errors = chart_errors(led, file="by_brand.csv", x="group", y=["n"])
    assert any("code không tự cộng gộp" in e for e in errors)
    assert chart_errors(led, file="by_brand.csv", x="group", y=["n"], type="stacked_bar", series="brand") == []


def test_masked_x_counts_as_duplicate(led):
    out = output({**CHART})
    assert any("giá trị trùng" in e for e in validate_visuals(out, led, mask=lambda s: "[đã che]"))


def test_table_columns_exist_and_capped(led):
    bad = validate_visuals(output(table={"file": "by_group.csv", "columns": ["khong_co"]}), led)
    assert bad == ["I1.table: không có cột khong_co trong by_group.csv."]
    many = validate_visuals(output(table={"file": "by_group.csv", "columns": ["group"] * 7}), led)
    assert many == ["I1.table: columns cần từ một tới 6 cột."]


def test_table_sort_valid(led):
    spec = {**TABLE, "sort": {"by": "total", "desc": False}}
    assert validate_visuals(output(table=spec), led) == []


def test_table_sort_default_desc():
    assert TableSpec.model_validate({"file": "a", "columns": ["x"], "sort": {"by": "n"}}).sort.desc is True


def test_table_sort_column_must_exist(led):
    errors = validate_visuals(output(table={**TABLE, "sort": {"by": "khong_co"}}), led)
    assert errors == ["I1.table: sort.by khong_co không có trong by_group.csv."]


def test_table_sort_column_must_be_number(led):
    errors = validate_visuals(output(table={**TABLE, "sort": {"by": "note"}}), led)
    assert errors == ["I1.table: sort.by note không phải cột số."]


def test_label_with_digit_rejected(led):
    assert validate_visuals(output(labels={"total": "Tổng 2024"}), led) == ["column_labels: nhãn của total có chữ số."]


def test_visual_error_goes_back_to_ai(led):
    good = {"summary": "s", "insights": [{"id": "I1", "requirement": 1, "text": "x", "result_ids": ["R1"], "chart": CHART}],
            "conclusion": "c", "limitations": "l"}
    bad = {**good, "insights": [{**good["insights"][0], "chart": {**CHART, "type": "pie"}}]}
    fake = llm.FakeLLM({"report": [bad, good]})
    with llm.use_fake(fake):
        record = synthesize(led, TASK)
    assert len(fake.calls) == 2 and "type pie không hợp lệ" in fake.calls[1]["messages"][0]["content"]
    assert record["insights"][0]["chart"]["type"] == "bar"


def test_visuals_stop_after_three_wrong_answers(led):
    bad = {"summary": "s", "insights": [{"id": "I1", "requirement": 1, "text": "x", "result_ids": ["R1"],
                                         "chart": {**CHART, "type": "pie"}}], "conclusion": "c", "limitations": "l"}
    with llm.use_fake(llm.FakeLLM({"report": [bad] * 3})), pytest.raises(loop.StopLoop):
        synthesize(led, TASK)
    assert led.get_insights() is None


# B2: ve
def png_of(led, spec, name="out.png", labels=None, mask=str):
    out = led.root / "report" / "charts" / name
    draw_chart(ChartSpec.model_validate(spec), led.root / "work" / spec["file"], out, labels, mask)
    return out


@pytest.mark.parametrize("spec", [
    CHART,
    {**CHART, "type": "line"},
    {**CHART, "y": ["total", "rate"]},
    {"type": "stacked_bar", "file": "by_brand.csv", "x": "group", "y": ["n"], "series": "brand", "title": "Theo nhóm"},
    {"type": "stacked_bar", "file": "by_group.csv", "x": "group", "y": ["total", "rate"], "title": "Hai cột"},
    {"type": "line", "file": "month.csv", "x": "month", "y": ["n", "m"], "title": "Theo tháng"},
], ids=["bar", "line", "grouped", "stacked_series", "stacked_columns", "line_long"])
def test_draws_png_200dpi_16_9(led, spec):
    with Image.open(png_of(led, spec)) as image:
        assert image.size == (2000, 1125)
        assert round(image.info["dpi"][0]) == 200


def test_draw_charts_saves_by_insight_id_and_skips_no_chart(led):
    record = {"column_labels": {"group": "Nhóm"},
              "insights": [{"id": "I1", "result_ids": ["R1"], "chart": CHART}, {"id": "I2", "result_ids": [], "chart": None}]}
    drawn = draw_charts(record, led)
    assert drawn == {"I1": led.root / "report" / "charts" / "I1.png"} and drawn["I1"].is_file()


def test_draw_charts_missing_file_raises(led):
    (led.root / "work" / "by_group.csv").unlink()
    record = {"insights": [{"id": "I1", "result_ids": ["R1"], "chart": CHART}]}
    with pytest.raises(ChartError, match="không tìm thấy bảng by_group.csv"):
        draw_charts(record, led)


def test_draw_charts_old_record_without_chart_key(led):
    assert draw_charts({"insights": [{"id": "I1", "result_ids": []}]}, led) == {}


def spec_of(**changes):
    return ChartSpec.model_validate({**CHART, **changes})


def test_number_format_is_vietnamese():
    assert [_number(v) for v in (1250000.0, 3400.5, 0.0513, 0.0)] == ["1.250.000", "3.400,5", "0,0513", "0"]


def test_more_than_twenty_groups_keeps_top_fifteen_with_note():
    df = pd.DataFrame({"group": [f"N{i}" for i in range(25)], "total": range(25)})
    frame, note = _limit(_frame(df, spec_of(), {}, str), spec_of(), "Tổng")
    assert list(frame.index) == [f"N{i}" for i in range(24, 9, -1)] and note == "Hiển thị 15/25 nhóm lớn nhất theo Tổng."


def test_twenty_groups_are_all_kept():
    df = pd.DataFrame({"group": [f"N{i}" for i in range(20)], "total": range(20)})
    frame, note = _limit(_frame(df, spec_of(), {}, str), spec_of(), "Tổng")
    assert len(frame) == 20 and note == ""


def test_line_keeps_every_point():
    df = pd.DataFrame({"group": [f"T{i}" for i in range(37)], "total": range(37)})
    spec = spec_of(type="line")
    frame, note = _limit(_frame(df, spec, {}, str), spec, "Tổng")
    assert len(frame) == 37 and note == ""


def test_series_beyond_palette_fold_into_other():
    rows = [("A", f"S{i}", 10 - i) for i in range(10)]
    df = pd.DataFrame(rows, columns=["group", "brand", "n"])
    spec = spec_of(type="stacked_bar", series="brand", y=["n"])
    frame, note = _limit(_frame(df, spec, {}, str), spec, "n")
    assert frame.shape[1] == len(config.REPORT_CHART_PALETTE) and frame["Khác"].iloc[0] == 3 + 2 + 1
    assert frame.sum(axis=1).iloc[0] == sum(r[2] for r in rows) and "gộp vào Khác" in note


def test_frame_uses_labels_and_keeps_file_order():
    df = pd.DataFrame({"group": ["B", "A"], "total": [1, 2]})
    frame = _frame(df, spec_of(), {"total": "Tổng tiền"}, str)
    assert list(frame.index) == ["B", "A"] and list(frame.columns) == ["Tổng tiền"]


def test_frame_masks_group_names():
    df = pd.DataFrame({"group": ["BIMAT", "A"], "total": [1, 2]})
    assert list(_frame(df, spec_of(), {}, lambda s: s.replace("BIMAT", "[đã che]")).index) == ["[đã che]", "A"]


def test_font_falls_back_without_times(monkeypatch):
    monkeypatch.setattr(font_manager.fontManager, "ttflist", [font_manager.FontEntry(name="DejaVu Sans")])
    assert _font() == "DejaVu Sans"


def test_font_prefers_times(monkeypatch):
    monkeypatch.setattr(font_manager.fontManager, "ttflist", [font_manager.FontEntry(name="Times New Roman")])
    assert _font() == "Times New Roman"


def test_empty_table_raises(led):
    pd.DataFrame({"group": [], "total": []}).to_csv(led.root / "work" / "by_group.csv", index=False)
    with pytest.raises(ChartError):
        png_of(led, CHART)


# bieu do du phong
def sales_table(led, name, frame):
    frame.to_csv(led.root / "work" / name, index=False)
    led.add_result("B", "m", {}, PASS, output_path=f"work/{name}")
    return {"id": "I1", "result_ids": [f"R{len(led.results())}"], "chart": None,
            "table": {"file": name, "columns": list(frame.columns)}}


ONE_DIM = pd.DataFrame({"category_name": ["A", "B", "C"], "n": [1, 3, 2]})
CODE_NAME = pd.DataFrame({"category_id": ["C1", "C2"], "category_name": ["Nước", "Bánh"], "n": [5, 9]})


def test_auto_chart_one_label_one_number(led):
    chart = auto_chart(sales_table(led, "a.csv", ONE_DIM), led, {"n": "Số sản phẩm", "category_name": "Nhóm hàng"})
    assert chart == {"type": "bar", "file": "a.csv", "x": "category_name", "y": ["n"], "series": None,
                     "title": "Số sản phẩm theo Nhóm hàng"}


def test_auto_chart_code_and_name_uses_name(led):
    assert auto_chart(sales_table(led, "a.csv", CODE_NAME), led)["x"] == "category_name"


def test_auto_chart_numeric_code_counts_as_code(led):
    frame = pd.DataFrame({"category_id": [1, 2], "category_name": ["A", "B"], "n": [5, 9]})
    chart = auto_chart(sales_table(led, "a.csv", frame), led)
    assert (chart["x"], chart["y"]) == ("category_name", ["n"])


def test_auto_chart_skips_multi_dimension(led):
    frame = pd.DataFrame({"category_id": ["C1", "C1"], "category_name": ["A", "A"], "brand": ["X", "Y"], "n": [1, 2]})
    assert auto_chart(sales_table(led, "a.csv", frame), led) is None
    two_labels = pd.DataFrame({"category_name": ["A", "A"], "brand": ["X", "Y"], "n": [1, 2]})
    assert auto_chart(sales_table(led, "b.csv", two_labels), led) is None


def test_auto_chart_skips_two_numbers_or_none(led):
    assert auto_chart(sales_table(led, "a.csv", pd.DataFrame({"g": ["A", "B"], "n": [1, 2], "m": [3, 4]})), led) is None
    assert auto_chart(sales_table(led, "b.csv", pd.DataFrame({"g": ["A", "B"]})), led) is None


@pytest.mark.parametrize("rows, drawn", [(1, False), (2, True), (20, True), (21, False)])
def test_auto_chart_row_limit(led, rows, drawn):
    frame = pd.DataFrame({"g": [f"N{i}" for i in range(rows)], "n": range(rows)})
    assert bool(auto_chart(sales_table(led, "a.csv", frame), led)) == drawn


def test_auto_chart_skips_duplicate_labels_and_existing_chart(led):
    assert auto_chart(sales_table(led, "a.csv", pd.DataFrame({"g": ["A", "A"], "n": [1, 2]})), led) is None
    ins = sales_table(led, "b.csv", ONE_DIM)
    assert auto_chart({**ins, "chart": CHART}, led) is None
    assert auto_chart({**ins, "table": None}, led) is None


def test_auto_chart_skips_missing_column(led):
    ins = sales_table(led, "a.csv", ONE_DIM)
    assert auto_chart({**ins, "table": {"file": "a.csv", "columns": ["khong_co", "n"]}}, led) is None


def test_with_auto_charts_logs_and_leaves_record_unchanged(led):
    ins = sales_table(led, "a.csv", ONE_DIM)
    record = {"insights": [ins]}
    done = with_auto_charts(record, led)
    assert done["insights"][0]["chart_auto"] and record["insights"][0]["chart"] is None
    event = [e for e in led.read_log() if e.action == "auto_chart"][-1]
    assert event.extra == {"insight": "I1", "file": "a.csv", "x": "category_name", "y": ["n"]}


def test_no_log_without_auto_chart(led):
    with_auto_charts({"insights": [{"id": "I1", "result_ids": [], "chart": None}]}, led)
    assert not [e for e in led.read_log() if e.action == "auto_chart"]


def test_auto_chart_is_drawn_descending(led, monkeypatch):
    seen = {}
    ins = sales_table(led, "a.csv", ONE_DIM)
    done = with_auto_charts({"insights": [ins]}, led)

    def spy(spec, path, out, labels, mask, descending):
        seen["descending"] = descending

    monkeypatch.setattr(charts, "draw_chart", spy)
    draw_charts(done, led)
    assert seen == {"descending": True}


def test_descending_sorts_groups_and_time_keeps_order(led, monkeypatch):
    orders = []
    monkeypatch.setattr(charts, "_draw_bars", lambda ax, frame, spec: orders.append(list(frame.index)) or False)
    ONE_DIM.to_csv(led.root / "work" / "a.csv", index=False)
    pd.DataFrame({"month": ["T1", "T2", "T3"], "n": [1, 3, 2]}).to_csv(led.root / "work" / "t.csv", index=False)
    out = led.root / "report" / "x.png"
    draw_chart(ChartSpec.model_validate({**CHART, "file": "a.csv", "x": "category_name", "y": ["n"]}),
               led.root / "work" / "a.csv", out, descending=True)
    draw_chart(ChartSpec.model_validate({**CHART, "file": "t.csv", "x": "month", "y": ["n"]}),
               led.root / "work" / "t.csv", out, descending=True)
    assert orders == [["B", "C", "A"], ["T1", "T2", "T3"]]


def test_prompt_suggests_chart_for_short_table():
    assert "Bảng không quá hai mươi dòng nên kèm chart" in load_prompt("synthesize")
