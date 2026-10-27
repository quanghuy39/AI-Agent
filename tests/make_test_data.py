"""Tao bo du lieu test (bai 1, bai 2) + de bai + dap an vao thu muc test_data/.
Chay: python -m tests.make_test_data
Khong ghi gi len R2. Ban tu keo tha thu muc test_data/input va test_data/answers len R2.
"""
import bisect
import json
import random
import shutil
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

OUT = Path("test_data")
SEED = 42
DD_SHEETS = {"bai0a": ["Products", "Category"], "bai0b": ["Support Ticket", "Customer"],
             "bai3": ["Order", "OrderItem", "PriceChange", "Channel"]}


def dd_to_md(sheets, names):
    parts = []
    for n in names:
        df = sheets[n]
        parts.append(f"## {n}\n\n| Cột | Kiểu | Định nghĩa |\n| --- | --- | --- |")
        for _, r in df.iterrows():
            d = str(r["Definition"]).strip().replace("\n", " ")
            parts.append(f"| {r['Column Name']} | {r['Data Type']} | {d} |")
        parts.append("")
    return "\n".join(parts)


DE_BAI1 = """# Bài 0a: Cơ cấu sản phẩm theo nhóm hàng và thương hiệu

Bảng dùng: PRODUCT, CATEGORY (xem data_dictionary.md).

Câu hỏi: Mỗi nhóm hàng (category_id, kèm category_name) có bao nhiêu sản phẩm,
và trong mỗi nhóm, mỗi thương hiệu (brand) có bao nhiêu sản phẩm?
Nhóm hàng nào có nhiều sản phẩm nhất?
"""

DE_BAI2 = """# Bài 0b: Ticket hỗ trợ theo khu vực và phân khúc khách hàng

Bảng dùng: SUPPORT_TICKET, CUSTOMER (xem data_dictionary.md). Khóa nối: customer_id.

Câu hỏi: Theo từng cặp (state, segment) của khách hàng, có bao nhiêu ticket
đã giải quyết (status = Resolved) và điểm hài lòng csat_score trung bình là bao nhiêu?
Cặp nào có csat trung bình thấp nhất (chỉ xét cặp có từ 10 ticket trở lên)?

Lưu ý: đề cố ý không nói rõ cách xử lý csat_score thiếu hoặc ngoài thang,
khách hàng không tìm thấy, hay khóa viết lệch định dạng. Agent nên nhận ra và hỏi lại.
"""


def norm_key(s):
    return str(s).strip().upper()


def build(product, category, customer, ticket, dd, outdir=OUT):
    rnd = random.Random(SEED)
    if outdir.exists():
        shutil.rmtree(outdir)
    for p in ("input/bai0a", "input/bai0b", "answers/bai0a", "answers/bai0b"):
        (outdir / p).mkdir(parents=True)

    # ---------- Bai 1 ----------
    i1, a1 = outdir / "input/bai0a", outdir / "answers/bai0a"
    product.to_csv(i1 / "PRODUCT.csv", index=False)
    category.to_csv(i1 / "CATEGORY.csv", index=False)
    (i1 / "de_bai.md").write_text(DE_BAI1, encoding="utf-8")
    (i1 / "data_dictionary.md").write_text(dd_to_md(dd, DD_SHEETS["bai0a"]), encoding="utf-8")

    m = product.merge(category, on="category_id", how="left")
    ans1 = (m.groupby(["category_id", "category_name", "brand"]).size()
            .reset_index(name="so_san_pham"))
    # cach 2 doc lap: Counter thuan Python
    cnt = Counter(zip(product["category_id"], product["brand"]))
    assert sum(cnt.values()) == len(product) == ans1["so_san_pham"].sum()
    assert all(cnt[(r.category_id, r.brand)] == r.so_san_pham for r in ans1.itertuples())
    ans1.to_csv(a1 / "answer.csv", index=False)
    tot = ans1.groupby(["category_id", "category_name"])["so_san_pham"].sum().sort_values(ascending=False)
    (a1 / "answer_notes.md").write_text(
        f"Tổng sản phẩm: {len(product)}\n"
        f"Nhóm nhiều sản phẩm nhất: {tot.index[0][0]} ({tot.index[0][1]}) = {tot.iloc[0]}\n"
        f"Tổng theo nhóm:\n{tot.to_string()}\n"
        "Kiểm tra: tổng các phần = tổng toàn bộ (đã assert trong code).\n", encoding="utf-8")

    # ---------- Bai 2 ----------
    i2, a2 = outdir / "input/bai0b", outdir / "answers/bai0b"
    c = customer.copy()
    t = ticket.copy()
    t["csat_score"] = t["csat_score"].astype("float")
    errors = {}

    # 1) khach hang trung dong (ban sao y het) -> join nhan dong
    dup_ids = rnd.sample(list(c["customer_id"]), 20)
    c = pd.concat([c, c[c["customer_id"].isin(dup_ids)]], ignore_index=True)
    errors["customer_duplicate_rows"] = {"ids": sorted(dup_ids), "extra_rows": 20}

    # chon cac dong ticket bi sua, khong trung nhau
    idx = rnd.sample(list(t.index), 15 + 5 + 10 + 5)
    null_i, bad_i, orph_i, fmt_i = idx[:15], idx[15:20], idx[20:30], idx[30:35]

    # 2) csat null
    t.loc[null_i, "csat_score"] = float("nan")
    errors["csat_null"] = sorted(t.loc[null_i, "ticket_id"])
    # 3) csat ngoai thang 1-5
    t.loc[bad_i, "csat_score"] = [0, 0, 9, 9, 99]
    errors["csat_out_of_range"] = {k: v for k, v in zip(t.loc[bad_i, "ticket_id"], [0, 0, 9, 9, 99])}
    # 4) ticket mo coi: customer_id khong co trong CUSTOMER
    t["customer_id"] = t["customer_id"].astype(object)
    for n, i in enumerate(orph_i):
        t.at[i, "customer_id"] = f"CUST9{n:05d}"
    errors["orphan_tickets"] = sorted(t.loc[orph_i, "ticket_id"])
    # 5) khoa lech dinh dang (chu thuong / khoang trang) - bo qua neu khong chuan hoa se khong khop
    fmt_orig = {}
    for n, i in enumerate(fmt_i):
        orig = t.at[i, "customer_id"]
        fmt_orig[t.at[i, "ticket_id"]] = orig
        t.at[i, "customer_id"] = orig.lower() if n % 2 == 0 else f" {orig} "
    errors["key_format_mismatch"] = fmt_orig

    c.to_csv(i2 / "CUSTOMER.csv", index=False)
    t.to_csv(i2 / "SUPPORT_TICKET.csv", index=False)
    (i2 / "de_bai.md").write_text(DE_BAI2, encoding="utf-8")
    (i2 / "data_dictionary.md").write_text(dd_to_md(dd, DD_SHEETS["bai0b"]), encoding="utf-8")

    # ---- dap an bai 2: dinh nghia duoc chot trong answer_notes ----
    # Dinh nghia: Resolved; chuan hoa khoa (strip+upper); khach hang bo dong trung;
    # ticket mo coi loai khoi bang chinh; csat ngoai 1-5 hoac null bi loai khoi trung binh
    # nhung ticket van duoc dem.
    cu = c.assign(k=c["customer_id"].map(norm_key)).drop_duplicates("k")
    tt = t.assign(k=t["customer_id"].map(norm_key))
    res = tt[tt["status"] == "Resolved"]
    j = res.merge(cu[["k", "state", "segment"]], on="k", how="inner")
    assert len(j) == len(res) - res["k"].isin(set(cu["k"])).eq(False).sum()
    ok = j["csat_score"].between(1, 5)
    ans2 = (j.assign(csat_ok=j["csat_score"].where(ok))
            .groupby(["state", "segment"])
            .agg(so_ticket=("ticket_id", "count"), csat_tb=("csat_ok", "mean"),
                 so_csat_hop_le=("csat_ok", "count")).reset_index())
    ans2["csat_tb"] = ans2["csat_tb"].round(4)
    # cach 2 doc lap: thuan Python, khong dung pandas merge
    seg = {}
    for r in cu.itertuples():
        seg[r.k] = (r.state, r.segment)
    cntt, sums, cnts = Counter(), defaultdict(float), Counter()
    for r in res.itertuples():
        if r.k not in seg:
            continue
        cntt[seg[r.k]] += 1
        if r.csat_score == r.csat_score and 1 <= r.csat_score <= 5:
            sums[seg[r.k]] += r.csat_score
            cnts[seg[r.k]] += 1
    for r in ans2.itertuples():
        key = (r.state, r.segment)
        assert cntt[key] == r.so_ticket, key
        if cnts[key]:
            assert abs(sums[key] / cnts[key] - r.csat_tb) < 1e-3, key
    assert sum(cntt.values()) == ans2["so_ticket"].sum()
    ans2.to_csv(a2 / "answer.csv", index=False)

    big = ans2[ans2["so_ticket"] >= 10].sort_values("csat_tb")
    low = big.iloc[0] if len(big) else None
    n_orph = int((~res["k"].isin(set(cu["k"]))).sum())
    (a2 / "answer_notes.md").write_text(
        "Định nghĩa dùng cho đáp án:\n"
        "- Chỉ ticket status = Resolved.\n"
        "- Chuẩn hóa customer_id (bỏ khoảng trắng, viết hoa) trước khi nối.\n"
        "- CUSTOMER bỏ dòng trùng (bản sao y hệt).\n"
        "- Ticket không tìm thấy khách hàng: loại khỏi bảng kết quả (đếm riêng).\n"
        "- csat null hoặc ngoài 1-5: không tính vào trung bình, ticket vẫn được đếm.\n\n"
        f"Số ticket Resolved: {len(res)}; vào bảng kết quả: {int(ans2['so_ticket'].sum())}; "
        f"ticket Resolved mồ côi: {n_orph}\n"
        + (f"Cặp csat thấp nhất (>=10 ticket): {low['state']} / {low['segment']} "
           f"= {low['csat_tb']} ({int(low['so_ticket'])} ticket)\n" if low is not None else "")
        + "Nếu KHÔNG chuẩn hóa khóa: 5 ticket lệch định dạng bị tính là mồ côi.\n"
        "Nếu KHÔNG bỏ dòng trùng: ticket của 20 khách bị nhân đôi.\n", encoding="utf-8")
    (a2 / "injected_errors.json").write_text(
        json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8")
    return ans1, ans2


DE_BAI3 = """# Bài 3: Doanh thu theo kênh bán và theo quý

Bảng dùng: ORDER, ORDER_ITEM, PRICE_CHANGE, CHANNEL (xem data_dictionary.md).

Câu hỏi: Doanh thu của từng kênh bán (channel_name) theo từng quý là bao nhiêu?
Kênh nào tăng trưởng quý-trên-quý cao nhất ở quý gần nhất, và kết quả đó có đáng tin không?

Lưu ý: đề cố ý không nói doanh thu là gộp hay thuần, đơn vị của item_discount,
cách lấy giá cho từng dòng hàng, hay cách xử lý đơn hàng thiếu dữ liệu.
Agent nên nhận ra các điểm mơ hồ này và hỏi lại.
"""


def build3(order, item, price, channel, dd, outdir=OUT):
    i3, a3 = outdir / "input/bai3", outdir / "answers/bai3"
    i3.mkdir(parents=True, exist_ok=True)
    a3.mkdir(parents=True, exist_ok=True)
    for name, df in (("ORDER", order), ("ORDER_ITEM", item), ("PRICE_CHANGE", price), ("CHANNEL", channel)):
        df.to_csv(i3 / f"{name}.csv", index=False)
    (i3 / "de_bai.md").write_text(DE_BAI3, encoding="utf-8")
    (i3 / "data_dictionary.md").write_text(dd_to_md(dd, DD_SHEETS["bai3"]), encoding="utf-8")

    # ngay dang dd-mm-yyyy
    dt = pd.to_datetime(order["order_date"], format="%d-%m-%Y", errors="coerce")
    bad_date = int(dt.isna().sum())
    o = order.assign(dt=dt)
    o["q"] = o["dt"].dt.to_period("Q").astype(str)
    iso = o["dt"].dt.isocalendar()
    iso_mismatch = int(((iso["year"] != o["year"]) | (iso["week"] != o["week"])).sum())
    cal_vs_iso_year = int((o["dt"].dt.year != o["year"]).sum())

    # don khong co dong hang (bi cat cut)
    no_item = int((~o["order_id"].isin(set(item["order_id"]))).sum())

    l = item.merge(o[["order_id", "channel_id", "q", "year", "week"]], on="order_id", how="inner")
    assert len(l) == len(item) - int((~item["order_id"].isin(set(o["order_id"]))).sum())
    l["yw"] = l["year"] * 100 + l["week"]

    # gia: lay gia gan nhat co (year,week) <= (year,week) cua don
    pc = price.assign(yw=price["year"] * 100 + price["week"]).sort_values("yw")
    l = l.sort_values("yw")
    l = pd.merge_asof(l, pc[["product_id", "yw", "unit_price"]], on="yw", by="product_id",
                      direction="backward")
    # cach 2 doc lap: bisect thuan Python
    series = {}
    for r in pc.sort_values(["product_id", "yw"]).itertuples():
        series.setdefault(r.product_id, ([], []))
        series[r.product_id][0].append(r.yw)
        series[r.product_id][1].append(r.unit_price)
    ref = []
    for pid, yw in zip(l["product_id"], l["yw"]):
        s_ = series.get(pid)
        k = bisect.bisect_right(s_[0], yw) - 1 if s_ else -1
        ref.append(s_[1][k] if k >= 0 else np.nan)
    ref = np.array(ref)
    got = l["unit_price"].to_numpy()
    assert np.array_equal(np.isnan(ref), np.isnan(got))
    assert np.allclose(ref[~np.isnan(ref)], got[~np.isnan(got)])

    n_noprice = int(l["unit_price"].isna().sum())
    p = l.dropna(subset=["unit_price"]).copy()
    p["gross"] = p["quantity"] * p["unit_price"]
    p["net_line"] = p["gross"] - p["item_discount"]                      # discount = tien cho ca dong
    p["net_unit"] = p["gross"] - p["quantity"] * p["item_discount"]      # discount = tien moi don vi

    g = (p.groupby(["channel_id", "q"])
         .agg(so_don=("order_id", "nunique"), so_dong_hang=("order_item_id", "count"),
              doanh_thu_gop=("gross", "sum"), doanh_thu_thuan_dong=("net_line", "sum"),
              doanh_thu_thuan_donvi=("net_unit", "sum")).reset_index())
    g = g.merge(channel, on="channel_id", how="left")
    g = g[["channel_id", "channel_name", "q", "so_don", "so_dong_hang", "doanh_thu_gop",
           "doanh_thu_thuan_dong", "doanh_thu_thuan_donvi"]].rename(columns={"q": "quy"})
    assert abs(g["doanh_thu_gop"].sum() - p["gross"].sum()) < 1e-3 * max(1, p["gross"].sum())
    g.round(2).to_csv(a3 / "answer.csv", index=False)

    # chan doan don vi item_discount
    ratio_line = float((p["item_discount"] > p["gross"]).mean())
    ratio_unit = float((p["quantity"] * p["item_discount"] > p["gross"]).mean())
    med = float((p["item_discount"] / p["gross"]).median())
    disc_no_promo = int(((item["item_discount"] > 0) & item["promo_id"].isna()).sum())

    # tang truong quy-tren-quy o quy gan nhat
    last_q = sorted(g["quy"].unique())[-1]
    prev_q = sorted(g["quy"].unique())[-2] if g["quy"].nunique() > 1 else None
    lines = []
    if prev_q:
        for col in ("doanh_thu_gop", "doanh_thu_thuan_dong", "doanh_thu_thuan_donvi"):
            a = g[g["quy"] == last_q].set_index("channel_name")[col]
            b = g[g["quy"] == prev_q].set_index("channel_name")[col]
            gr = ((a - b) / b).dropna().sort_values(ascending=False)
            lines.append(f"- {col}: cao nhất = {gr.index[0]} ({gr.iloc[0]:.2%})" if len(gr) else f"- {col}: n/a")
    qd = o[o["q"] == last_q]["dt"]
    full_end = (qd.min().to_period("Q").end_time.normalize())
    partial = qd.max() < full_end

    notes = [
        "Định nghĩa dùng cho đáp án (mỗi cột là một cách hiểu, agent phải hỏi để chốt):",
        "- Giá dòng hàng = giá gần nhất trong PRICE_CHANGE có (year, week) <= (year, week) của đơn, theo sản phẩm.",
        "- Dòng không có giá (đơn trước lần đổi giá đầu tiên) bị loại và đếm riêng.",
        "- Quý theo ngày order_date (định dạng dd-mm-yyyy), giá tra theo cột year/week (ISO).",
        "- doanh_thu_gop = quantity x unit_price.",
        "- doanh_thu_thuan_dong = gộp - item_discount (coi là tiền của cả dòng).",
        "- doanh_thu_thuan_donvi = gộp - quantity x item_discount (coi là tiền mỗi đơn vị).",
        "",
        "Bẫy trong dữ liệu (đã đo):",
        f"- ORDER_ITEM bị cắt cụt (giới hạn Excel): đơn không có dòng hàng nào = {no_item} / {len(order)} ({no_item/len(order):.2%}). Doanh thu thấp hơn thực tế khoảng mức này, không có lỗi báo.",
        f"- Dòng hàng không có giá: {n_noprice} / {len(item)}.",
        f"- Dòng có discount > 0 nhưng không có promo_id: {disc_no_promo}.",
        f"- Tỷ lệ dòng có discount > gộp: nếu discount là tiền cả dòng = {ratio_line:.2%}; nếu là tiền mỗi đơn vị = {ratio_unit:.2%}. Trung vị discount/gộp = {med:.4f}. Cách hiểu nào làm tỷ lệ này cao bất hợp lý thì ít khả năng đúng.",
        f"- order_date sai định dạng: {bad_date}; year/week không khớp lịch ISO của order_date: {iso_mismatch}; năm ISO khác năm dương lịch: {cal_vs_iso_year} đơn.",
        f"- Quý gần nhất = {last_q}, dữ liệu {'CHƯA ĐỦ quý (so sánh tăng trưởng không công bằng)' if partial else 'đủ quý'}.",
        "",
        f"Tăng trưởng quý {last_q} so với {prev_q}:",
    ] + lines
    (a3 / "answer_notes.md").write_text("\n".join(notes) + "\n", encoding="utf-8")
    return g


def main():
    from storage.r2 import download_with_cache, read_table
    dd = pd.read_excel(download_with_cache("input/Data Dictionary.xlsx"), sheet_name=None)
    a1, a2 = build(read_table("input/PRODUCT.csv"), read_table("input/CATEGORY.csv"),
                   read_table("input/CUSTOMER.csv"), read_table("input/SUPPORT_TICKET.csv"), dd)
    # Bai 3 (build3) da bi bo khoi bo test; giu ham lai de dung sau, khong chay o day.
    print("Xong. Thu muc:", OUT.resolve())
    print("Bai 1:", len(a1), "dong dap an; Bai 2:", len(a2), "dong dap an")


if __name__ == "__main__":
    main()
