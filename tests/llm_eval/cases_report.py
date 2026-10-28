"""10 case cho role report. Dau vao: so ket qua gia (ID R1, R2... voi cac truong so) va vai insight.
Model phai viet van ban tieng Viet, moi con so dung placeholder {{ID.truong}}.
"""

from tests.llm_eval.common import Case


def reference_answer(case):
    """Cau tra loi mau dat moi tieu chi (dung de kiem tra case giai duoc)."""
    ids = case.checks.get("required_ids") or list(case.ledger)
    n = max(case.checks.get("min_sections", 1), min(len(ids), case.checks.get("max_sections", 10)))
    groups = [ids[i::n] for i in range(n)]
    sections = []
    for group in groups:
        heading = case.ledger[group[0]].get("label", "Kết quả") if group else "Tổng quan"
        parts = []
        for rid in group:
            fields = [f for f, v in case.ledger[rid].items() if isinstance(v, (int, float)) and not isinstance(v, bool)]
            parts.append(f"{case.ledger[rid].get('label', rid)}: " + ", ".join(f"{f} {{{{{rid}.{f}}}}}" for f in fields))
        sections.append({"heading": heading, "body": ". ".join(parts) or "Không có số liệu."})
    return {"sections": sections}


CASES = [
    Case(
        id="R01_channels",
        role="report",
        user="Viết báo cáo ngắn về doanh thu theo kênh bán hàng.",
        checks={"min_sections": 2, "max_sections": 4, "min_placeholders": 4, "required_ids": ["R1", "R2", "R3"]},
        ledger={
            "R1": {"label": "Kênh web", "total": 14409.04, "pct": 54.44, "n": 230},
            "R2": {"label": "Kênh cửa hàng", "total": 8438.21, "pct": 31.88, "n": 114},
            "R3": {"label": "Kênh điện thoại", "total": 3619.99, "pct": 13.68, "n": 56},
        },
        insights=["Kênh web chiếm tỷ trọng lớn nhất ({{R1.pct}}).", "Kênh điện thoại nhỏ nhất."],
    ),
    Case(
        id="R02_cancellation",
        role="report",
        user="Viết báo cáo về tình trạng hủy và hoàn tiền đơn hàng.",
        checks={"min_sections": 1, "max_sections": 3, "min_placeholders": 2, "required_ids": ["R1", "R2"]},
        ledger={
            "R1": {"label": "Đơn bị hủy", "pct": 13.75, "n": 55},
            "R2": {"label": "Đơn hoàn tiền", "pct": 11.0, "n": 44},
        },
        insights=["Tỷ lệ hủy cao hơn tỷ lệ hoàn tiền.", "Nên xem lại quy trình xác nhận đơn."],
    ),
    Case(
        id="R03_growth",
        role="report",
        user="So sánh doanh thu năm 2025 với năm 2024 và nêu mức tăng trưởng.",
        checks={"min_sections": 2, "max_sections": 3, "min_placeholders": 3, "required_ids": ["R1", "R2", "R3"]},
        ledger={
            "R1": {"label": "Doanh thu năm 2024", "total": 12850.42},
            "R2": {"label": "Doanh thu năm 2025", "total": 13616.82},
            "R3": {"label": "Tăng trưởng so với năm trước", "pct": 5.96},
        },
        insights=["Doanh thu tăng nhẹ ({{R3.pct}})."],
    ),
    Case(
        id="R04_segments",
        role="report",
        user="Viết báo cáo doanh thu đơn hoàn tất theo phân khúc khách hàng.",
        checks={"min_sections": 2, "max_sections": 4, "min_placeholders": 3, "required_ids": ["R1", "R2", "R3"]},
        ledger={
            "R1": {"label": "Phân khúc Retail", "total": 9630.63, "mean": 64.2},
            "R2": {"label": "Phân khúc SMB", "total": 7405.81, "mean": 71.89},
            "R3": {"label": "Phân khúc Enterprise", "total": 2414.23, "mean": 68.98},
        },
        insights=["Retail đóng góp nhiều nhất nhưng giá trị đơn trung bình thấp nhất."],
    ),
    Case(
        id="R05_states",
        role="report",
        user="Viết báo cáo phân bố đơn hàng theo bang.",
        checks={"min_sections": 2, "max_sections": 5, "min_placeholders": 5, "required_ids": ["R1", "R2", "R3", "R4", "R5"]},
        ledger={
            "R1": {"label": "Bang CA", "n": 88, "total": 5934.12},
            "R2": {"label": "Bang NY", "n": 79, "total": 5310.4},
            "R3": {"label": "Bang TX", "n": 83, "total": 5602.77},
            "R4": {"label": "Bang FL", "n": 71, "total": 4719.03},
            "R5": {"label": "Bang WA", "n": 73, "total": 4885.36},
        },
        insights=["CA dẫn đầu cả số đơn lẫn doanh thu.", "Các bang chênh lệch không lớn."],
    ),
    Case(
        id="R06_top_customers",
        role="report",
        user="Viết đoạn báo cáo về ba khách hàng có tổng chi tiêu cao nhất.",
        checks={"min_sections": 1, "max_sections": 2, "min_placeholders": 3, "required_ids": ["R1", "R2", "R3"]},
        ledger={
            "R1": {"label": "Khách hàng thứ nhất", "customer_id": "C0048", "total": 973.89},
            "R2": {"label": "Khách hàng thứ hai", "customer_id": "C0087", "total": 796.74},
            "R3": {"label": "Khách hàng thứ ba", "customer_id": "C0073", "total": 793.08},
        },
        insights=["Khách hàng đứng đầu vượt xa người thứ hai."],
    ),
    Case(
        id="R07_data_quality",
        role="report",
        user="Viết mục 'Chất lượng dữ liệu' nêu các vấn đề phát hiện được và ảnh hưởng tới phân tích.",
        checks={"min_sections": 1, "max_sections": 2, "min_placeholders": 3, "required_ids": ["R1", "R2", "R3"]},
        ledger={
            "R1": {"label": "Đơn thiếu amount", "n": 9},
            "R2": {"label": "Đơn của khách hàng không tồn tại", "n": 6},
            "R3": {"label": "Dòng khách hàng trùng lặp", "n": 4},
        },
        insights=["Các dòng trùng đã được bỏ trước khi join.", "Đơn thiếu amount không được tính vào tổng."],
    ),
    Case(
        id="R08_order_value",
        role="report",
        user="Viết báo cáo về giá trị đơn hàng trung bình, so sánh giữa các kênh.",
        checks={"min_sections": 2, "max_sections": 3, "min_placeholders": 3, "required_ids": ["R1", "R2", "R3"]},
        ledger={
            "R1": {"label": "Toàn bộ đơn hoàn tất", "mean": 67.44, "median": 52.1},
            "R2": {"label": "Kênh web", "mean": 63.37},
            "R3": {"label": "Kênh cửa hàng", "mean": 74.69},
        },
        insights=["Đơn tại cửa hàng có giá trị trung bình cao hơn web."],
    ),
    Case(
        id="R09_quarterly",
        role="report",
        user="Viết báo cáo xu hướng doanh thu theo quý của năm 2025.",
        checks={"min_sections": 2, "max_sections": 4, "min_placeholders": 4, "required_ids": ["R1", "R2", "R3", "R4"]},
        ledger={
            "R1": {"label": "Quý I năm 2025", "total": 4274.18, "n": 53},
            "R2": {"label": "Quý II năm 2025", "total": 3381.55, "n": 50},
            "R3": {"label": "Quý III năm 2025", "total": 3203.6, "n": 50},
            "R4": {"label": "Quý IV năm 2025", "total": 2757.49, "n": 47},
        },
        insights=["Doanh thu giảm dần qua các quý.", "Quý I cao nhất."],
    ),
    Case(
        id="R10_exec_summary",
        role="report",
        user="Viết đúng một mục tóm tắt điều hành, tối đa ba câu.",
        checks={"min_sections": 1, "max_sections": 1, "min_placeholders": 3, "required_ids": ["R1", "R2", "R3"]},
        ledger={
            "R1": {"label": "Tổng doanh thu", "total": 26467.24},
            "R2": {"label": "Tổng số đơn", "n": 400},
            "R3": {"label": "Tỷ lệ đơn hoàn tất", "pct": 75.25},
        },
        insights=["Phần lớn đơn được hoàn tất."],
    ),
]
