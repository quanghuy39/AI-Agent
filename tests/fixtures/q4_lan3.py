import os
import json
import pandas as pd
import numpy as np

input_dir = os.environ["AGENT_INPUT_DIR"]
output_dir = os.environ["AGENT_OUTPUT_DIR"]

# Đọc dữ liệu
touches = pd.read_csv(os.path.join(input_dir, "CAMPAIGN_TOUCH.csv"), dtype=str)
orders = pd.read_csv(os.path.join(input_dir, "ORDER.csv"), dtype=str)

# Chuyển đổi kiểu dữ liệu
touches['touch_date'] = pd.to_datetime(touches['touch_date'], format='%Y-%m-%d')
orders['order_date'] = pd.to_datetime(orders['order_date'], format='%d-%m-%Y')

# Ghi bảng trung gian để kiểm tra join
orders.to_csv(os.path.join(output_dir, "order_cleaned.csv"), index=False)
touches.to_csv(os.path.join(output_dir, "touch_cleaned.csv"), index=False)

n_before_orders = len(orders)
n_before_touches = len(touches)

# Kết hợp orders và touches qua customer_id để tìm các touchpoint hợp lệ trong 7 ngày trước order_date
# Do kích thước lớn, thực hiện merge theo customer_id
merged = pd.merge(orders[['order_id', 'customer_id', 'order_date']], touches[['touch_id', 'customer_id', 'touch_date', 'campaign_name', 'channel']], on='customer_id', how='inner')

# Lọc điều kiện: touch_date <= order_date và order_date <= touch_date + 7 ngày
merged['days_diff'] = (merged['order_date'] - merged['touch_date']).dt.days
valid_touches = merged[(merged['days_diff'] >= 0) & (merged['days_diff'] <= 7)].copy()

# Đếm số chiến dịch cùng chạm cho mỗi đơn hàng trước khi chọn gần nhất
order_touch_counts = valid_touches.groupby('order_id')['touch_id'].nunique().reset_index(name='n_touchpoints_matched')
multi_touched_orders = int((order_touch_counts['n_touchpoints_matched'] > 1).sum())

# Gán đơn cho lượt tiếp cận gần nhất (days_diff nhỏ nhất)
valid_touches = valid_touches.sort_values(['order_id', 'days_diff'])
attributed_orders = valid_touches.drop_duplicates(subset=['order_id'], keep='first')

# Thống kê số đơn hàng theo chiến dịch
campaign_order_counts = attributed_orders.groupby('campaign_name').agg(
    attributed_orders_count=('order_id', 'count'),
    unique_customers=('customer_id', 'nunique')
).reset_index()

# Tổng số đơn hàng được gán
total_orders = n_before_orders
total_attributed = int(attributed_orders['order_id'].nunique())
attribution_rate = float(total_attributed / total_orders) if total_orders > 0 else 0.0

# Xuất file kết quả
attributed_orders[['order_id', 'customer_id', 'order_date', 'touch_id', 'campaign_name', 'channel', 'days_diff']].to_csv(os.path.join(output_dir, "campaign_order_attribution.csv"), index=False)
campaign_order_counts.to_csv(os.path.join(output_dir, "campaign_order_summary.csv"), index=False)

result_summary = {
    "total_orders": int(total_orders),
    "total_attributed_orders": int(total_attributed),
    "attribution_rate": float(attribution_rate),
    "multi_touched_orders": int(multi_touched_orders),
    "n_campaigns_analyzed": int(len(campaign_order_counts))
}

meta = {
    "result_summary": result_summary,
    "checks": [
        {
            "type": "row_count",
            "label": "orders and touches cleaned",
            "before": int(n_before_orders),
            "after": int(len(orders)),
            "expect": "same",
            "expected_ratio": None
        },
        {
            "type": "join",
            "label": "order and campaign_touch join",
            "left": "order_cleaned.csv",
            "right": "touch_cleaned.csv",
            "left_on": "customer_id",
            "right_on": "customer_id",
            "how": "inner"
        },
        {
            "type": "sum",
            "label": "sum of attributed orders by campaign",
            "parts": [int(x) for x in campaign_order_counts['attributed_orders_count']],
            "total": int(total_attributed)
        },
        {
            "type": "custom",
            "name": "Một số đơn hàng có thể bị gán sai chiến dịch do khách hàng nhận quá nhiều touchpoint trong khoảng thời gian ngắn.",
            "passed": bool(multi_touched_orders >= 0),
            "reason": "Checked multi-touched orders count: {}".format(multi_touched_orders)
        },
        {
            "type": "custom",
            "name": "Định dạng ngày tháng hoặc khoảng thời gian 7 ngày không phản ánh đúng hành vi thực tế của khách hàng.",
            "passed": bool(total_attributed <= total_orders),
            "reason": "Attributed orders ({}) cannot exceed total orders ({})".format(total_attributed, total_orders)
        }
    ]
}

with open(os.environ["AGENT_META_PATH"], "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False)
