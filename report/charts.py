"""Bieu do cho bao cao: kiem tra phan table/chart cua ban tong hop va ve PNG. Chi code, moi so lay tu file bang.

x (cung series neu co) phai duy nhat tung dong: code khong tu cong gop, vi cot ty le cong lai se ra so sai.
Insight co table ma AI khong dat chart: with_auto_charts tu them bar khi bang chi co mot chieu (xem auto_chart).
Mau theo thu tu co dinh config.REPORT_CHART_PALETTE; them nhom thi gop vao "Khác", khong sinh mau moi.
"""

import re

import pandas as pd
from matplotlib import font_manager, rc_context
from matplotlib.figure import Figure
from matplotlib.ticker import FuncFormatter, MaxNLocator
from pandas.api.types import is_bool_dtype, is_numeric_dtype

import config
from ledger import ResultStatus
from report.placeholders import format_value
from report.tables import is_measure, is_time_column, label_column, load_table, result_tables
from skills.report.schemas import ChartSpec

CHART_TYPES = ("bar", "line", "stacked_bar")
OTHER = "Khác"
CHARTS_DIR = "charts"
INK, MUTED, GRID, OTHER_COLOR = "#0b0b0b", "#52514e", "#e4e3df", "#a8a7a1"


class ChartError(Exception):
    """Khong ve duoc bieu do tu insights.json (file thieu, cot khong con...)."""


# ---------- kiem tra ----------

def tables_of(ledger, result_ids):
    """{ten file: bang} cua cac ket qua valid trong result_ids."""
    valid = {r.id for r in ledger.results() if r.status == ResultStatus.VALID}
    return {t["name"]: t for rid in result_ids if rid in valid for t in result_tables(ledger, rid)}


def _label(value):
    """Gia tri nhom thanh chu; 2024.0 thanh 2024, khong ngan nghin vi day la nhan, khong phai so do."""
    return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)


def _keys(column, mask):
    return column.map(lambda v: mask(_label(v)))


def _has_digit(text):
    return re.search(r"\d", text) is not None


def _is_number(column):
    return is_numeric_dtype(column) and not is_bool_dtype(column)


def _find_table(where, file, available):
    """(bang, loi). Bang dung duoc thi loi la None."""
    table = available.get(file)
    if table is None:
        return None, f"{where}: file {file} không thuộc bảng của result_ids; có: {', '.join(available) or '(không có)'}."
    if table["columns"] is None:
        return None, f"{where}: không đọc được file {file}."
    return table, None


def _table_errors(where, spec, available):
    table, error = _find_table(where, spec.file, available)
    if error:
        return [error]
    errors = [f"{where}: không có cột {c} trong {spec.file}." for c in spec.columns if c not in table["columns"]]
    if not 1 <= len(spec.columns) <= config.REPORT_TABLE_SHOW_COLUMNS:
        errors.append(f"{where}: columns cần từ một tới {config.REPORT_TABLE_SHOW_COLUMNS} cột.")
    if spec.sort:
        by = spec.sort.by
        if by not in table["columns"]:
            errors.append(f"{where}: sort.by {by} không có trong {spec.file}.")
        elif not _is_number(load_table(table["path"])[by]):
            errors.append(f"{where}: sort.by {by} không phải cột số.")
    return errors


def _chart_shape_errors(where, spec):
    errors = []
    if spec.type not in CHART_TYPES:
        errors.append(f"{where}: type {spec.type} không hợp lệ; chỉ dùng {', '.join(CHART_TYPES)}.")
    if not 1 <= len(spec.y) <= config.REPORT_CHART_MAX_Y:
        errors.append(f"{where}: y cần từ một tới {config.REPORT_CHART_MAX_Y} cột số.")
    if spec.series and spec.type != "stacked_bar":
        errors.append(f"{where}: series chỉ dùng cho stacked_bar.")
    if spec.series and len(spec.y) != 1:
        errors.append(f"{where}: có series thì y chỉ gồm một cột.")
    return errors


def _chart_data_errors(where, spec, table, mask):
    used = [spec.x, *spec.y, *([spec.series] if spec.series else [])]
    missing = [c for c in used if c not in table["columns"]]
    if missing:
        return [f"{where}: không có cột {', '.join(missing)} trong {spec.file}."]
    df = load_table(table["path"])
    errors = [f"{where}: cột {c} không phải cột số." for c in spec.y
              if not _is_number(df[c])]
    keys = pd.DataFrame({"x": _keys(df[spec.x], mask)})
    if spec.series:
        keys["series"] = _keys(df[spec.series], mask)
    if keys.duplicated().any():
        errors.append(f"{where}: {spec.x} có giá trị trùng trong {spec.file}, code không tự cộng gộp; "
                      f"chọn cột khác hoặc dùng stacked_bar với series.")
    return errors


def _chart_errors(where, spec, available, mask):
    errors = _chart_shape_errors(where, spec)
    table, error = _find_table(where, spec.file, available)
    if error:
        return errors + [error]
    return errors + _chart_data_errors(where, spec, table, mask)


def validate_visuals(output, ledger, mask=str):
    """Loi cua table, chart, column_labels (cau ngan, gui lai duoc cho AI); rong la dat."""
    errors = []
    for ins in output.insights:
        if ins.table is None and ins.chart is None:
            continue
        available = tables_of(ledger, ins.result_ids)
        if ins.table:
            errors += _table_errors(f"{ins.id}.table", ins.table, available)
        if ins.chart:
            errors += _chart_errors(f"{ins.id}.chart", ins.chart, available, mask)
    errors += [f"column_labels: nhãn của {c} có chữ số." for c, text in output.column_labels.items() if _has_digit(text)]
    return errors


# ---------- chuan bi du lieu ----------

def _frame(df, spec, labels, mask):
    """Bang rong de ve: index la nhom x, moi cot la mot dai. Giu thu tu xuat hien trong file."""
    data = df.assign(_x=_keys(df[spec.x], mask))
    if spec.series:
        data = data.assign(_s=_keys(df[spec.series], mask))
        frame = data.pivot(index="_x", columns="_s", values=spec.y[0])
        return frame.reindex(index=data["_x"].unique(), columns=data["_s"].unique())
    frame = data.set_index("_x")[spec.y]
    if frame.index.has_duplicates:
        raise ChartError(f"{spec.x} có giá trị trùng trong {spec.file}.")
    return frame.rename(columns=lambda c: labels.get(c, c))


def _limit(frame, spec, y_label):
    """Gioi han so nhom, so dai; tra ve (bang, ghi chu). Duong theo thoi gian giu du diem: lay top se pha thu tu."""
    notes = []
    if spec.type != "line":
        frame = frame.fillna(0)
        if len(frame) > config.REPORT_CHART_MAX_GROUPS:
            top = frame.sum(axis=1).nlargest(config.REPORT_CHART_TOP_GROUPS).index
            notes.append(f"Hiển thị {len(top)}/{len(frame)} nhóm lớn nhất theo {y_label}.")
            frame = frame.loc[top]
    limit = len(config.REPORT_CHART_PALETTE)
    if frame.shape[1] > limit and spec.type == "stacked_bar":
        order = frame.sum().sort_values(ascending=False).index
        keep, rest = list(order[:limit - 1]), list(order[limit - 1:])
        frame = frame[keep].assign(**{OTHER: frame[rest].sum(axis=1)})
        notes.append(f"{len(rest)} nhóm nhỏ gộp vào {OTHER}.")
    return frame, " ".join(notes)


# ---------- bieu do du phong ----------

def auto_chart(ins, ledger, mask=str):
    """Dac ta bar cho insight co table ma khong co chart; None neu bang khong du dieu kien: dung mot cot nhan (hoac ma +
    ten) va mot cot so, tu hai toi REPORT_AUTO_CHART_MAX_ROWS dong, nhan khong trung."""
    spec = ins.get("table")
    table = tables_of(ledger, ins["result_ids"]).get(spec["file"]) if spec and not ins.get("chart") else None
    if table is None or table["columns"] is None or not set(spec["columns"]) <= set(table["columns"]):
        return None
    df = load_table(table["path"])
    measures = [c for c in spec["columns"] if is_measure(c, df[c])]
    x = label_column([c for c in spec["columns"] if c not in measures])
    if x is None or len(measures) != 1 or not 2 <= len(df) <= config.REPORT_AUTO_CHART_MAX_ROWS:
        return None
    if _keys(df[x], mask).duplicated().any():
        return None
    return {"type": "bar", "file": spec["file"], "x": x, "y": measures, "series": None}


def with_auto_charts(record, ledger, mask=str):
    """Ban sao record, them chart du phong cho insight du dieu kien, danh dau chart_auto. insights.json khong doi: day
    la cach trinh bay do code chon, chay lai cho ket qua nhu nhau."""
    insights = []
    for ins in record["insights"]:
        chart = auto_chart(ins, ledger, mask)
        if chart:
            ledger.log_event("report", "auto_chart", extra={"insight": ins["id"], "file": chart["file"], "x": chart["x"],
                                                            "y": chart["y"]})
            ins = {**ins, "chart": chart, "chart_auto": True}
        insights.append(ins)
    return {**record, "insights": insights}


# ---------- ve ----------

def chart_title(spec, labels=None):
    """Tieu de trong anh va chu thich Hinh n: sinh tu column_labels, khong dung title cua AI."""
    labels = labels or {}
    text = f"{', '.join(labels.get(c, c) for c in spec.y)} theo {labels.get(spec.x, spec.x)}"
    if spec.series:
        text += f" và {labels.get(spec.series, spec.series)}"
    return text[0].upper() + text[1:]


def _font():
    names = {f.name for f in font_manager.fontManager.ttflist}
    return config.REPORT_FONT if config.REPORT_FONT in names else config.REPORT_CHART_FONT_FALLBACK


def _number(value, _position=None):
    value = float(value)
    return format_value(int(value) if value.is_integer() else value)


def _style(ax, value_axis):
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
    ax.tick_params(colors=MUTED, length=0)
    ax.grid(axis=value_axis, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    getattr(ax, f"{value_axis}axis").set_major_formatter(FuncFormatter(_number))


def _draw_bars(ax, frame, spec):
    stacked = spec.type == "stacked_bar"
    horizontal = len(frame) > config.REPORT_CHART_VERTICAL_MAX_GROUPS \
        or max(len(k) for k in frame.index) > config.REPORT_CHART_VERTICAL_MAX_CHARS
    draw = ax.barh if horizontal else ax.bar
    positions = range(len(frame))
    n = frame.shape[1]
    width = 0.72 if stacked else 0.72 / n
    base = [0.0] * len(frame)
    containers = []
    for i, (name, color) in enumerate(zip(frame.columns, config.REPORT_CHART_PALETTE)):
        color = OTHER_COLOR if name == OTHER else color  # nhom gop khong duoc giong mot nhom that
        values = frame[name].tolist()
        places = list(positions) if stacked else [p + (i - (n - 1) / 2) * width for p in positions]
        size = {"left" if horizontal else "bottom": base} if stacked else {}
        containers.append(draw(places, values, width, label=name, color=color, edgecolor="white", linewidth=1, **size))
        base = [a + b for a, b in zip(base, values)] if stacked else base
    set_ticks, value_axis = (ax.set_yticks, "x") if horizontal else (ax.set_xticks, "y")
    set_ticks(list(positions), list(frame.index))
    _style(ax, value_axis)
    if horizontal:
        ax.invert_yaxis()
    if spec.type == "bar" and frame.size <= config.REPORT_CHART_LABEL_MAX_BARS:
        for container in containers:
            ax.bar_label(container, labels=[_number(v) for v in container.datavalues], padding=3, fontsize=9, color=INK)
    return horizontal


def _draw_lines(ax, frame):
    positions = list(range(len(frame)))
    for name, color in zip(frame.columns, config.REPORT_CHART_PALETTE):
        ax.plot(positions, frame[name].tolist(), label=name, color=color, linewidth=2, marker="o", markersize=4)
    labels = list(frame.index)
    ax.xaxis.set_major_locator(MaxNLocator(nbins=config.REPORT_CHART_LABEL_MAX_BARS, integer=True))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: labels[int(v)] if 0 <= int(v) < len(labels) else ""))
    _style(ax, "y")
    ax.tick_params(axis="x", labelrotation=30)


def draw_chart(spec, path, out_path, labels=None, mask=str, descending=False):
    """Ve mot bieu do ra PNG out_path. descending: xep nhom theo cot so dau tien, giam dan."""
    labels = labels or {}
    y_label = labels.get(spec.y[0], spec.y[0]) if len(spec.y) == 1 else ""
    df = load_table(path)
    frame, note = _limit(_frame(df, spec, labels, mask), spec, y_label or "tổng")
    if descending and not is_time_column(spec.x, df[spec.x]):
        frame = frame.sort_values(by=frame.columns[0], ascending=False, kind="stable")
    if frame.empty:
        raise ChartError(f"{spec.file} không có dòng nào để vẽ.")
    with rc_context({"font.family": _font(), "font.size": 11, "axes.unicode_minus": False}):
        fig = Figure(figsize=config.REPORT_CHART_SIZE, dpi=config.REPORT_CHART_DPI, facecolor="white", layout="constrained")
        ax = fig.add_subplot()
        x_label = labels.get(spec.x, spec.x)
        if spec.type == "line":
            _draw_lines(ax, frame)
            ax.set_xlabel(x_label, color=MUTED)
            ax.set_ylabel(y_label, color=MUTED)
        else:
            horizontal = _draw_bars(ax, frame, spec)
            ax.set_xlabel(y_label if horizontal else x_label, color=MUTED)
            ax.set_ylabel(x_label if horizontal else y_label, color=MUTED)
        ax.set_title(mask(chart_title(spec, labels)), loc="left", color=INK, fontsize=14, fontweight="bold")
        if frame.shape[1] > 1:
            fig.legend(loc="outside right upper", frameon=False, labelcolor=MUTED, fontsize=9)
        if note:
            fig.supxlabel(note, x=0.01, ha="left", fontsize=9, color=MUTED)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out_path)


def draw_charts(record, ledger, mask=str):
    """Ve moi bieu do cua insights.json vao report/charts/<insight_id>.png. Tra ve {insight id: duong dan}.
    File hoac cot khong con -> ChartError."""
    drawn = {}
    for ins in record["insights"]:
        if not ins.get("chart"):
            continue
        spec = ChartSpec.model_validate(ins["chart"])
        table = tables_of(ledger, ins["result_ids"]).get(spec.file)
        if table is None:
            raise ChartError(f"{ins['id']}: không tìm thấy bảng {spec.file}.")
        out_path = ledger.root / config.REPORT_SUBDIR / CHARTS_DIR / f"{ins['id']}.png"
        try:
            draw_chart(spec, table["path"], out_path, record.get("column_labels"), mask, bool(ins.get("chart_auto")))
        except (KeyError, ValueError) as e:
            raise ChartError(f"{ins['id']}: không vẽ được từ {spec.file} ({type(e).__name__}: {e}).") from None
        drawn[ins["id"]] = out_path
    return drawn
