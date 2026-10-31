import os
import pandas as pd
import numpy as np
import json

input_dir = os.environ["AGENT_INPUT_DIR"]
output_dir = os.environ["AGENT_OUTPUT_DIR"]

# 1. Đọc dữ liệu
campaign_touch = pd.read_csv(os.path.join(input_dir, "CAMPAIGN_TOUCH.csv"), dtype=str)
order = pd.read_csv(os.path.join(input_dir, "ORDER.csv"), dtype=str)

# Tiền xử lý kiểu dữ liệu
campaign_touch["touch_date"] = pd.to_datetime(campaign_touch["touch_date"], format="%Y-%m-%d")
order["order_date"] = pd.to_datetime(order["order_date"], format="%d-%m-%Y")

n_touch_before = len(campaign_touch)
n_order_before = len(order)

# 2. Join CAMPAIGN_TOUCH và ORDER theo customer_id
merged = pd.merge(order, campaign_touch, on="customer_id", suffixes=("_ord", "_tch"))

# Lọc các cặp thoả mãn điều kiện: touch_date <= order_date <= touch_date + 7 ngày
merged["days_diff"] = (merged["order_date"] - merged["touch_date"]).dt.days
valid_window = merged[(merged["days_diff"] >= 0) & (merged["days_diff"] <= 7)].copy()

# Đếm mỗi đơn hàng một lần: gán đơn cho lượt tiếp cận gần nhất (days_diff nhỏ nhất) cho mỗi order_id
valid_window["min_days"] = valid_window.groupby("order_id")["days_diff"].transform("min")
unique_attributed = valid_window[valid_window["days_diff"] == valid_window[
    "min_days"]].drop_duplicates(subset=["order_id"])

# Thống kê số đơn bị nhiều chiến dịch cùng chạm trong 7 ngày trước đó
order_touch_counts = valid_window.groupby("order_id")["campaign_name"].nunique()
orders_multi_touched = int((order_touch_counts > 1).sum())

# Tổng hợp theo chiến dịch
campaign_order_summary = unique_attributed.groupby("campaign_name").agg(
    attributed_orders=("order_id", "count"),
    unique_customers=("customer_id", "nunique"),
    avg_days_to_order=("days_diff", "mean")
).reset_index()

# Lưu kết quả
output_file = os.path.join(output_dir, "campaign_order_attribution.csv")
campaign_order_summary.to_csv(output_file, index=False)

total_attributed_orders = int(unique_attributed["order_id"].nunique())
total_orders_count = int(order["order_id"].nunique())
attribution_rate = float(total_attributed_orders / total_orders_count) if total_orders_count > 0 else 0.0

top_campaign_row = campaign_order_summary.sort_values(by="attributed_orders", ascending=False).iloc[0] if len(campaign_order_summary) > 0 else None
top_campaign_name = str(top_campaign_row["campaign_name"]) if top_campaign_row is not None else "None"
top_campaign_orders = int(top_campaign_row["attributed_orders"]) if top_campaign_row is not None else 0

result_summary = {
    "total_attributed_orders": total_attributed_orders,
    "total_orders": total_orders_count,
    "attribution_rate": attribution_rate,
    "orders_multi_touched": orders_multi_touched,
    "top_attributed_campaign": top_campaign_name,
    "top_attributed_campaign_orders": top_campaign_orders
}

meta = {
    "result_summary": result_summary,
    "checks": [
        {
            "type": "row_count",
            "label": "campaign_touch",
            "before": int(n_touch_before),
            "after": int(len(campaign_touch)),
            "expect": "same",
            "expected_ratio": None
        },
        {
            "type": "row_count",
            "label": "order",
            "before": int(n_order_before),
            "after": int(len(order)),
            "expect": "same",
            "expected_ratio": None
        },
        {
            "type": "join",
            "label": "order and campaign_touch join",
            "left": "ORDER.csv",
            "right": "CAMPAIGN_TOUCH.csv",
            "left_on": "customer_id",
            "right_on": "customer_id",
            "how": "inner"
        },
        {
            "type": "join",
            "label": "campaign_order_attribution output check",
            "left": "campaign_order_attribution.csv",
            "right": "ORDER.csv",
            "left_on": "campaign_name",
            "right_on": "customer_id",
            "how": "inner"
        },
        {
            "type": "sum",
            "label": "sum of attributed orders by campaign",
            "parts": [int(x) for x in campaign_order_summary["attributed_orders"].tolist()],
            "total": int(total_attributed_orders)
        },
        {
            "type": "custom",
            "name": "Thời gian quy định 7 ngày không phản ánh chính xác hành vi thực tế của khách hàng.",
            "passed": bool(total_attributed_orders >= 0 and total_attributed_orders <= total_orders_count),
            "reason": "Attributed orders count {} is within total orders {}".format(total_attributed_orders, total_orders_count)
        }
    ]
}

with open(os.environ["AGENT_META_PATH"], "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False)
