"""10 case cho role slide. Dau vao: so ket qua gia, insight, danh sach chart_ref hop le.
Gioi han so slide theo case, toi da 5 bullet moi slide, so phai dung placeholder {{ID.truong}}.
"""

from tests.llm_eval.common import Case


def reference_answer(case):
    """Cau tra loi mau dat moi tieu chi (dung de kiem tra case giai duoc)."""
    ids = case.checks.get("required_ids") or list(case.ledger)
    charts = list(case.checks.get("required_charts", ()))
    n = min(len(ids), case.checks["max_slides"])
    slides = []
    for i in range(n):
        group = ids[i::n]
        bullets = []
        for rid in group:
            for f, v in case.ledger[rid].items():
                if isinstance(v, (int, float)) and not isinstance(v, bool) and len(bullets) < 5:
                    bullets.append(f"{case.ledger[rid].get('label', rid)}: {{{{{rid}.{f}}}}}")
        slides.append({
            "title": case.ledger[group[0]].get("label", "Kết quả"),
            "bullets": bullets,
            "chart_ref": charts[i] if i < len(charts) else None,
        })
    return {"slides": slides}


CASES = [
    Case(
        id="S01_channels",
        role="slide",
        user="Soạn slide về doanh thu theo kênh bán hàng.",
        checks={"max_slides": 3, "max_bullets": 5, "min_placeholders": 3, "required_ids": ["R1", "R2", "R3"],
                "required_charts": ["chart_revenue_by_channel"]},
        ledger={
            "R1": {"label": "Kênh web", "total": 14409.04, "pct": 54.44},
            "R2": {"label": "Kênh cửa hàng", "total": 8438.21, "pct": 31.88},
            "R3": {"label": "Kênh điện thoại", "total": 3619.99, "pct": 13.68},
        },
        insights=["Web chiếm hơn một nửa doanh thu ({{R1.pct}})."],
        chart_refs=["chart_revenue_by_channel", "chart_orders_by_month"],
    ),
    Case(
        id="S02_cancellation",
        role="slide",
        user="Soạn một slide về tỷ lệ hủy đơn và đề xuất.",
        checks={"max_slides": 1, "max_bullets": 5, "min_placeholders": 2, "required_ids": ["R1", "R2"]},
        ledger={
            "R1": {"label": "Đơn bị hủy", "pct": 13.75, "n": 55},
            "R2": {"label": "Đơn hoàn tiền", "pct": 11.0, "n": 44},
        },
        insights=["Hủy đơn nhiều hơn hoàn tiền.", "Đề xuất: xác nhận đơn qua điện thoại."],
        chart_refs=["chart_status_share"],
    ),
    Case(
        id="S03_growth",
        role="slide",
        user="Soạn slide so sánh doanh thu năm 2025 với năm 2024.",
        checks={"max_slides": 2, "max_bullets": 5, "min_placeholders": 3, "required_ids": ["R1", "R2", "R3"],
                "required_charts": ["chart_revenue_by_year"]},
        ledger={
            "R1": {"label": "Doanh thu năm 2024", "total": 12850.42},
            "R2": {"label": "Doanh thu năm 2025", "total": 13616.82},
            "R3": {"label": "Tăng trưởng", "pct": 5.96},
        },
        insights=["Tăng trưởng nhẹ."],
        chart_refs=["chart_revenue_by_year"],
    ),
    Case(
        id="S04_segments",
        role="slide",
        user="Soạn slide về doanh thu theo phân khúc khách hàng.",
        checks={"max_slides": 3, "max_bullets": 5, "min_placeholders": 3, "required_ids": ["R1", "R2", "R3"]},
        ledger={
            "R1": {"label": "Retail", "total": 9630.63, "mean": 64.2},
            "R2": {"label": "SMB", "total": 7405.81, "mean": 71.89},
            "R3": {"label": "Enterprise", "total": 2414.23, "mean": 68.98},
        },
        insights=["Retail lớn nhất về tổng, SMB cao nhất về giá trị trung bình."],
        chart_refs=["chart_revenue_by_segment", "chart_mean_by_segment"],
    ),
    Case(
        id="S05_states",
        role="slide",
        user="Soạn slide về phân bố đơn theo bang, nhấn mạnh bang dẫn đầu.",
        checks={"max_slides": 2, "max_bullets": 5, "min_placeholders": 5, "required_ids": ["R1", "R2", "R3", "R4", "R5"],
                "required_charts": ["chart_orders_by_state"]},
        ledger={
            "R1": {"label": "CA", "n": 88},
            "R2": {"label": "NY", "n": 79},
            "R3": {"label": "TX", "n": 83},
            "R4": {"label": "FL", "n": 71},
            "R5": {"label": "WA", "n": 73},
        },
        insights=["CA dẫn đầu."],
        chart_refs=["chart_orders_by_state"],
    ),
    Case(
        id="S06_top_customers",
        role="slide",
        user="Soạn một slide về ba khách hàng chi tiêu nhiều nhất.",
        checks={"max_slides": 1, "max_bullets": 5, "min_placeholders": 3, "required_ids": ["R1", "R2", "R3"]},
        ledger={
            "R1": {"label": "Khách hàng thứ nhất", "customer_id": "C0048", "total": 973.89},
            "R2": {"label": "Khách hàng thứ hai", "customer_id": "C0087", "total": 796.74},
            "R3": {"label": "Khách hàng thứ ba", "customer_id": "C0073", "total": 793.08},
        },
        insights=["Khách hàng đứng đầu vượt xa người thứ hai."],
        chart_refs=["chart_top_customers"],
    ),
    Case(
        id="S07_data_quality",
        role="slide",
        user="Soạn một slide 'Lưu ý về dữ liệu'.",
        checks={"max_slides": 1, "max_bullets": 5, "min_placeholders": 3, "required_ids": ["R1", "R2", "R3"]},
        ledger={
            "R1": {"label": "Đơn thiếu amount", "n": 9},
            "R2": {"label": "Đơn của khách hàng không tồn tại", "n": 6},
            "R3": {"label": "Dòng khách hàng trùng lặp", "n": 4},
        },
        insights=["Đã bỏ dòng trùng trước khi join."],
        chart_refs=[],
    ),
    Case(
        id="S08_order_value",
        role="slide",
        user="Soạn slide về giá trị đơn trung bình theo kênh.",
        checks={"max_slides": 2, "max_bullets": 5, "min_placeholders": 3, "required_ids": ["R1", "R2", "R3"]},
        ledger={
            "R1": {"label": "Toàn bộ", "mean": 67.44},
            "R2": {"label": "Web", "mean": 63.37},
            "R3": {"label": "Cửa hàng", "mean": 74.69},
        },
        insights=["Cửa hàng cao nhất."],
        chart_refs=["chart_mean_by_channel"],
    ),
    Case(
        id="S09_quarterly",
        role="slide",
        user="Soạn slide xu hướng doanh thu theo quý năm 2025.",
        checks={"max_slides": 4, "max_bullets": 5, "min_placeholders": 4, "required_ids": ["R1", "R2", "R3", "R4"],
                "required_charts": ["chart_revenue_by_quarter"]},
        ledger={
            "R1": {"label": "Quý I", "total": 4274.18},
            "R2": {"label": "Quý II", "total": 3381.55},
            "R3": {"label": "Quý III", "total": 3203.6},
            "R4": {"label": "Quý IV", "total": 2757.49},
        },
        insights=["Giảm dần qua các quý."],
        chart_refs=["chart_revenue_by_quarter", "chart_orders_by_month"],
    ),
    Case(
        id="S10_exec_summary",
        role="slide",
        user="Soạn đúng một slide tóm tắt điều hành.",
        checks={"max_slides": 1, "max_bullets": 5, "min_placeholders": 3, "required_ids": ["R1", "R2", "R3"]},
        ledger={
            "R1": {"label": "Tổng doanh thu", "total": 26467.24},
            "R2": {"label": "Tổng số đơn", "n": 400},
            "R3": {"label": "Tỷ lệ hoàn tất", "pct": 75.25},
        },
        insights=["Phần lớn đơn được hoàn tất."],
        chart_refs=["chart_revenue_by_channel"],
    ),
]
