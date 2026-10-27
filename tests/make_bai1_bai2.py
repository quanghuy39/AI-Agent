"""Tao bo test Bai 1 (khach hang) va Bai 2 (marketing): de bai, dictionary, ban sao du lieu, dap an (moc so + bay + cau hoi).
Chay SAU tests.make_test_data (file do xoa lai thu muc test_data):
    python -m tests.make_test_data
    python -m tests.make_bai1_bai2
Can: pip install scipy
"""
import json
import shutil
from collections import Counter
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import binomtest, chi2_contingency, kruskal

from tests.make_test_data import OUT, dd_to_md

DE_BAI1 = """# Bài 1: Hiểu cơ sở khách hàng

Bối cảnh: Ban quản lý muốn hiểu cơ sở khách hàng hiện có để quyết định nhóm khách nào nên ưu tiên giữ chân.

Yêu cầu:
1. Mô tả khách hàng theo các đặc điểm có trong dữ liệu.
2. Cho biết tỷ lệ rời bỏ (churn) chung và theo từng nhóm khách.
3. Mô tả hành vi mua: mua bao nhiêu lần, qua kênh nào, ở đâu, thanh toán bằng gì, thay đổi theo thời gian ra sao.
4. Nhóm khách nào nên ưu tiên giữ chân? Nêu lý do và mức độ chắc chắn.

Phạm vi: dữ liệu không có giá trị đơn hàng nên chỉ phân tích số lần và thời điểm mua, không tính doanh thu.
Bảng dùng: CUSTOMER, ORDER, STORE, CHANNEL (xem data_dictionary.md).
"""

DE_BAI2 = """# Bài 2: Đánh giá hiệu quả chiến dịch marketing

Bối cảnh: Công ty đã chạy nhiều chiến dịch trên nhiều kênh. Ban quản lý cần biết kênh và chiến dịch nào hiệu quả, và có nên chuyển ngân sách giữa các kênh không.

Yêu cầu:
1. Tổng quan các lượt tiếp cận: số lượng, kênh, chiến dịch, thời gian.
2. So sánh hiệu quả giữa các kênh và giữa các chiến dịch.
3. Hiệu quả có phụ thuộc mức chi phí của kênh không?
4. Các chiến dịch có đem lại đơn hàng không?
5. Đề xuất: nên giữ, tăng hay giảm kênh hoặc chiến dịch nào? Nêu lý do và mức độ chắc chắn.

Bảng dùng: CAMPAIGN_TOUCH, MARKETING_CHANNEL, CUSTOMER, ORDER (xem data_dictionary.md).
"""


def chi_p(a, b):
    return float(chi2_contingency(pd.crosstab(a, b))[1])


def bh(pvals):
    """Hieu chinh Benjamini-Hochberg."""
    p = np.asarray(pvals)
    order = np.argsort(p)
    ranked = p[order] * len(p) / (np.arange(len(p)) + 1)
    adj = np.minimum.accumulate(ranked[::-1])[::-1]
    out = np.empty_like(adj)
    out[order] = np.minimum(adj, 1)
    return out


def r4(x):
    return None if x is None else round(float(x), 4)


def copy_data(paths, names, dest):
    dest.mkdir(parents=True, exist_ok=True)
    for n in names:
        shutil.copyfile(paths[n], dest / f"{n}.csv")


def build_bai1(paths, dd, outdir=OUT):
    i, a = outdir / "input/bai1", outdir / "answers/bai1"
    for d in (i, a):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    copy_data(paths, ["CUSTOMER", "ORDER", "STORE", "CHANNEL"], i)
    (i / "de_bai.md").write_text(DE_BAI1, encoding="utf-8")
    (i / "data_dictionary.md").write_text(dd_to_md(dd, ["Customer", "Order", "Store", "Channel"]), encoding="utf-8")

    c = pd.read_csv(paths["CUSTOMER"])
    o = pd.read_csv(paths["ORDER"])
    st = pd.read_csv(paths["STORE"])
    ch = pd.read_csv(paths["CHANNEL"])
    o["dt"] = pd.to_datetime(o["order_date"], format="%d-%m-%Y")
    c["sd"] = pd.to_datetime(c["signup_date"])
    end = o["dt"].max()
    n = o.groupby("customer_id").size()
    # kiem tra doc lap bang Counter thuan Python
    cnt = Counter(o["customer_id"])
    assert all(cnt[k] == v for k, v in n.items()) and sum(cnt.values()) == len(o)
    c["n_orders"] = c["customer_id"].map(n).fillna(0).astype(int)
    c["churned"] = c["status"].eq("churned")
    last = o.groupby("customer_id")["dt"].max()
    c["days_since_last"] = (end - c["customer_id"].map(last)).dt.days
    J = {"ngay_cuoi_du_lieu": str(end.date()), "ngay_dau_du_lieu": str(o["dt"].min().date()),
         "so_khach": len(c), "so_don": len(o), "so_khach_churned": int(c["churned"].sum()),
         "ty_le_churn": r4(c["churned"].mean()), "khach_khong_co_don": int((c["n_orders"] == 0).sum()),
         "khach_khong_co_don_theo_status": c[c["n_orders"] == 0]["status"].value_counts().to_dict()}

    # churn theo nhom
    for col in ("segment", "state"):
        t = c.groupby(col).agg(khach=("customer_id", "size"), churned=("churned", "sum"))
        t["ty_le_churn"] = (t["churned"] / t["khach"]).round(4)
        t.to_csv(a / f"churn_theo_{col}.csv")
        J[f"churn_theo_{col}_p"] = r4(chi_p(c[col], c["status"]))
        J[f"churn_theo_{col}_min_max"] = [r4(t["ty_le_churn"].min()), r4(t["ty_le_churn"].max())]
    c["signup_q"] = c["sd"].dt.to_period("Q").astype(str)
    J["churn_theo_quy_dang_ky_p"] = r4(chi_p(c["signup_q"], c["status"]))

    # so don theo nhom
    seg = c.groupby("segment").agg(khach=("customer_id", "size"), don_tb_tat_ca=("n_orders", "mean"),
                                   don_trung_vi_tat_ca=("n_orders", "median"))
    seg["don_tb_nguoi_co_mua"] = c[c["n_orders"] > 0].groupby("segment")["n_orders"].mean()
    seg.round(2).to_csv(a / "so_don_theo_segment.csv")
    J["so_don_theo_segment_kruskal_p"] = float(f"{kruskal(*[g['n_orders'] for _, g in c.groupby('segment')])[1]:.3g}")
    J["so_don_theo_state_kruskal_p"] = r4(kruskal(*[g["n_orders"] for _, g in c.groupby("state")])[1])
    J["so_don_theo_status_kruskal_p"] = r4(kruskal(*[g["n_orders"] for _, g in c.groupby("status")])[1])
    J["so_don_moi_khach"] = {"tb": round(float(n.mean()), 2), "trung_vi": float(n.median()), "max": int(n.max()),
                            "khach_max": str(n.idxmax())}

    # nhan churned vs hanh vi mua gan day
    rows = []
    for th in (30, 60, 90, 180):
        rows.append({"nguong_ngay": th,
                     "churned_van_co_don_gan_day": int((c["churned"] & (c["days_since_last"] <= th)).sum()),
                     "churned_tong": int(c["churned"].sum()),
                     "active_khong_co_don_trong_nguong": int((~c["churned"] & ((c["days_since_last"] > th) | c["days_since_last"].isna())).sum()),
                     "active_tong": int((~c["churned"]).sum())})
    pd.DataFrame(rows).to_csv(a / "nhan_churn_vs_mua_gan_day.csv", index=False)
    J["nhan_churn_vs_mua_gan_day"] = rows

    # kenh, thanh toan, noi mua
    om = o.merge(c[["customer_id", "segment", "state", "sd"]], on="customer_id").merge(
        st, on="store_id").merge(ch, on="channel_id")
    J["ty_trong_kenh"] = om["channel_name"].value_counts(normalize=True).round(4).to_dict()
    J["ty_trong_thanh_toan"] = om["payment_type"].value_counts(normalize=True).round(4).to_dict()
    J["kenh_theo_segment_p"] = r4(chi_p(om["segment"], om["channel_name"]))
    cs = pd.crosstab(om["segment"], om["channel_name"])
    cs.div(cs.sum(axis=1), axis=0).round(4).to_csv(a / "kenh_theo_segment.csv")
    J["kenh_theo_segment_cramers_v"] = r4((chi2_contingency(cs)[0] / cs.values.sum() / (min(cs.shape) - 1)) ** 0.5)
    J["thanh_toan_theo_segment_p"] = r4(chi_p(om["segment"], om["payment_type"]))
    J["ty_le_don_khac_bang_voi_khach"] = r4((om["state"] != om["store_state"]).mean())
    J["don_truoc_ngay_dang_ky"] = int((om["dt"] < om["sd"]).sum())
    J["dong_don_trung_khach_ngay"] = int(o.duplicated(["customer_id", "dt"]).sum())
    J["top1pct_khach_chiem_ty_le_don"] = r4(n.nlargest(100).sum() / n.sum())
    mo = o.groupby(o["dt"].dt.to_period("M").astype(str)).size().rename("so_don")
    mo.to_csv(a / "don_theo_thang.csv")
    J["thang_cuoi_chua_du"] = {"thang": mo.index[-1], "den_ngay": str(end.date())}
    J["khach_dang_ky_tu_den"] = [str(c["sd"].min().date()), str(c["sd"].max().date())]
    (a / "checkpoints.json").write_text(json.dumps(J, ensure_ascii=False, indent=2), encoding="utf-8")

    (a / "bay_va_cau_hoi.md").write_text(
        "# Bài 1: bẫy agent phải nêu và câu agent nên hỏi lại\n\n"
        "## Bẫy trong dữ liệu (số đo ở checkpoints.json)\n"
        "- Nhãn status=churned KHÔNG khớp hành vi mua: đa số khách churned vẫn có đơn gần đây, nhiều khách active không có đơn lâu (số active không có đơn trong ngưỡng tính cả 445 khách active chưa từng mua). Phải nêu và đề xuất định nghĩa churn theo thời gian không mua.\n"
        "- Churn không khác biệt có ý nghĩa theo segment, state hay quý đăng ký. Không được kết luận 'nhóm X rời bỏ nhiều hơn' chỉ vì tỷ lệ lệch nhẹ.\n"
        "- Số đơn khác biệt thật theo segment (Premium cao nhất, Value thấp nhất), không khác biệt theo state.\n"
        "- Kênh mua khác biệt thật theo segment (kenh_theo_segment.csv): Premium và Health-focused mua chủ yếu qua Online và Mobile App (khoảng 70%), Mainstream và Value chủ yếu qua Modern Trade và General Trade (khoảng 58%). Thanh toán thì không khác biệt theo segment.\n"
        "- Số đơn phụ thuộc thời gian khách đã đăng ký và khách nhiều đơn bất thường (một khách có hàng trăm đơn).\n"
        "- 612 khách chưa mua lần nào; một số đơn có ngày trước ngày đăng ký; nhiều đơn cùng khách cùng ngày.\n"
        "- Khoảng 94% đơn ở cửa hàng khác bang với khách; kênh Online và Mobile App vẫn có store_id: nơi mua không phản ánh nơi ở.\n"
        "- Ngày dữ liệu kết thúc 21/02/2026, tháng cuối chưa đủ; năm 2023 tăng dần vì khách đăng ký dần.\n"
        "- Không có giá trị đơn hàng: chỉ nói về tần suất, không nói doanh thu.\n\n"
        "## Câu agent nên hỏi lại hoặc ghi thành giả định chờ xác nhận\n"
        "- Churn định nghĩa theo nhãn status hay theo số ngày không mua? Mốc bao nhiêu ngày? Mốc thời gian tính từ ngày nào?\n"
        "- Khách chưa có đơn nào tính thế nào? Khách có số đơn cực lớn có loại không?\n"
        "- Tần suất tính theo tổng số đơn hay số đơn trên thời gian kể từ đăng ký?\n"
        "- 'Ưu tiên giữ chân' dựa trên tiêu chí gì khi dữ liệu không có giá trị đơn?\n"
        "- Nơi mua tính theo cửa hàng (store) hay theo kênh bán?\n\n"
        "## Kết luận chấp nhận được cho câu 4\n"
        "Phải nêu rõ dữ liệu không cho thấy nhóm nào rời bỏ nhiều hơn một cách có ý nghĩa, nhãn churn không đáng tin để làm cơ sở, và mọi đề xuất ưu tiên (ví dụ Premium vì tần suất mua cao) chỉ là gợi ý có điều kiện.\n",
        encoding="utf-8")
    return J


def build_bai2(paths, dd, outdir=OUT):
    i, a = outdir / "input/bai2", outdir / "answers/bai2"
    for d in (i, a):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    copy_data(paths, ["CAMPAIGN_TOUCH", "MARKETING_CHANNEL", "CUSTOMER", "ORDER"], i)
    (i / "de_bai.md").write_text(DE_BAI2, encoding="utf-8")
    (i / "data_dictionary.md").write_text(
        dd_to_md(dd, ["Campaign_Touch", "Marketing_Channel", "Customer", "Order"]), encoding="utf-8")

    t = pd.read_csv(paths["CAMPAIGN_TOUCH"])
    mc = pd.read_csv(paths["MARKETING_CHANNEL"])
    c = pd.read_csv(paths["CUSTOMER"])
    o = pd.read_csv(paths["ORDER"])
    t["dt"] = pd.to_datetime(t["touch_date"])
    o["dt"] = pd.to_datetime(o["order_date"], format="%d-%m-%Y")
    c["sd"] = pd.to_datetime(c["signup_date"])
    t = t.merge(mc[["channel_name", "cost_tier"]], left_on="channel", right_on="channel_name", how="left")
    assert t["cost_tier"].notna().all()
    t["conv"] = t["outcome"].eq("Converted")
    cnt = Counter(t["outcome"])
    assert sum(cnt.values()) == len(t)

    J = {"so_luot": len(t), "so_khach_duoc_tiep_can": int(t["customer_id"].nunique()),
         "khoang_ngay": [str(t["dt"].min().date()), str(t["dt"].max().date())],
         "outcome": {k: r4(v) for k, v in t["outcome"].value_counts(normalize=True).items()},
         "kenh_co_du_lieu": sorted(t["channel"].unique()),
         "kenh_khong_co_du_lieu": sorted(set(mc["channel_name"]) - set(t["channel"])),
         "so_chien_dich": int(t["campaign_name"].nunique())}
    for col in ("channel", "campaign_name", "cost_tier"):
        tab = t.groupby(col).agg(luot=("conv", "size"), ty_le_converted=("conv", "mean"),
                                 ty_le_clicked=("outcome", lambda s: (s == "Clicked").mean())).round(4)
        tab.to_csv(a / f"hieu_qua_theo_{col}.csv")
        J[f"{col}_chi2_p"] = r4(chi_p(t[col], t["outcome"]))
    # 48 cap chien dich x kenh
    base = t["conv"].mean()
    g = t.groupby(["campaign_name", "channel"]).agg(luot=("conv", "size"), converted=("conv", "sum")).reset_index()
    g["ty_le"] = (g["converted"] / g["luot"]).round(4)
    g["p_tho"] = [binomtest(int(k), int(n), base).pvalue for k, n in zip(g["converted"], g["luot"])]
    g["p_BH"] = bh(g["p_tho"])
    g.round(4).sort_values("p_tho").to_csv(a / "48_cap_chien_dich_kenh.csv", index=False)
    J["cap_chien_dich_kenh"] = {"so_cap": len(g), "p_tho_duoi_0.05": int((g["p_tho"] < 0.05).sum()),
                                "p_BH_duoi_0.05": int((g["p_BH"] < 0.05).sum()),
                                "ty_le_min_max": [r4(g["ty_le"].min()), r4(g["ty_le"].max())]}
    # outcome co noi voi don hang khong
    ts = t.sort_values("dt")
    os_ = o[["customer_id", "dt"]].rename(columns={"dt": "odt"}).sort_values("odt")
    m = pd.merge_asof(ts, os_, left_on="dt", right_on="odt", by="customer_id", direction="forward")
    gap = (m["odt"] - m["dt"]).dt.days
    rows = {}
    for d in (7, 30):
        m[f"co_don_{d}"] = gap.le(d).fillna(False)
        rows[f"{d}_ngay"] = {k: r4(v) for k, v in m.groupby("outcome")[f"co_don_{d}"].mean().items()}
        J[f"outcome_vs_co_don_{d}_ngay_chi2_p"] = r4(chi_p(m["outcome"], m[f"co_don_{d}"]))
    J["ty_le_co_don_sau_tiep_can"] = rows
    # tuong quan va moc thoi gian
    tp = t.groupby("customer_id").size()
    on = o.groupby("customer_id").size()
    cc = pd.DataFrame({"t": tp, "o": on}).fillna(0)
    J["tuong_quan_so_luot_va_so_don_moi_khach"] = r4(cc["t"].corr(cc["o"]))
    J["luot_truoc_ngay_dang_ky"] = int((t.merge(c[["customer_id", "sd"]], on="customer_id")
                                       .eval("dt < sd")).sum())
    J["ty_le_luot_truoc_ngay_dang_ky"] = r4(J["luot_truoc_ngay_dang_ky"] / len(t))
    yr = t.groupby(t["dt"].dt.year).agg(luot=("conv", "size"), ty_le_converted=("conv", "mean")).round(4)
    yr.to_csv(a / "theo_nam.csv")
    J["theo_nam"] = {int(k): {"luot": int(v["luot"]), "ty_le_converted": float(v["ty_le_converted"])} for k, v in yr.iterrows()}
    # chien dich 'Reactivation Offer' co nham vao khach churned khong
    tc = t.merge(c[["customer_id", "status"]], on="customer_id")
    J["reactivation_ty_le_gui_cho_churned"] = r4(tc[tc["campaign_name"] == "Reactivation Offer"]["status"].eq("churned").mean())
    J["tat_ca_luot_ty_le_gui_cho_churned"] = r4(tc["status"].eq("churned").mean())
    (a / "checkpoints.json").write_text(json.dumps(J, ensure_ascii=False, indent=2), encoding="utf-8")

    (a / "bay_va_cau_hoi.md").write_text(
        "# Bài 2: bẫy agent phải nêu và câu agent nên hỏi lại\n\n"
        "## Bẫy trong dữ liệu (số đo ở checkpoints.json)\n"
        "- Tỷ lệ Converted gần như bằng nhau ở mọi kênh và chiến dịch; kiểm định không có ý nghĩa. Không được xếp hạng kênh tốt nhất chỉ vì chênh lệch nhỏ.\n"
        "- So sánh 48 cặp chiến dịch x kênh: có đúng 1 cặp 'có ý nghĩa' ở mức thô, nhưng sau hiệu chỉnh nhiều phép so sánh thì không còn. Agent phải nhận ra nhiễu ngẫu nhiên.\n"
        "- outcome=Converted không nối được với đơn hàng trong ORDER: tỷ lệ có đơn sau khi nhận chiến dịch giống nhau ở Converted, Clicked, Ignored. Phải nêu mâu thuẫn này, không suy diễn Converted là có đơn.\n"
        "- Khoảng 36% lượt tiếp cận xảy ra trước ngày đăng ký của khách: dữ liệu mâu thuẫn, cần hỏi cách xử lý.\n"
        "- Chỉ có cost_tier, không có chi phí thật: không tính được ROI; không nên kết luận chuyển ngân sách theo hiệu quả trên chi phí.\n"
        "- Chỉ 6 trên 12 kênh trong MARKETING_CHANNEL có dữ liệu.\n"
        "- Số lượt giảm mạnh theo năm trong khi tỷ lệ Converted đứng yên; năm 2026 chưa đủ.\n"
        "- Tên chiến dịch (Reactivation Offer, Cart Abandon Reminder...) gợi ý nhắm đối tượng riêng nhưng dữ liệu cho thấy lượt gửi như ngẫu nhiên.\n\n"
        "## Câu agent nên hỏi lại hoặc ghi thành giả định chờ xác nhận\n"
        "- 'Hiệu quả' định nghĩa là gì: tỷ lệ Converted, Clicked, hay có đơn hàng sau tiếp cận? Cửa sổ thời gian bao nhiêu ngày?\n"
        "- Có loại các lượt tiếp cận trước ngày đăng ký không?\n"
        "- Ngưỡng ý nghĩa thống kê và cách xử lý nhiều phép so sánh?\n"
        "- Không có chi phí thật: có chấp nhận đánh giá theo cost_tier không?\n\n"
        "## Kết luận chấp nhận được cho câu 5\n"
        "Phải nêu rõ dữ liệu không chứng minh kênh hay chiến dịch nào hiệu quả hơn, nên chưa có cơ sở chuyển ngân sách; đề xuất đúng là kiểm chứng thêm (thử nghiệm có đối chứng, có chi phí thật) trước khi quyết định.\n",
        encoding="utf-8")
    return J


def main():
    from storage.r2 import download_with_cache
    dd = pd.read_excel(download_with_cache("input/Data Dictionary.xlsx"), sheet_name=None)
    paths = {n: download_with_cache(f"input/{n}.csv") for n in
             ("CUSTOMER", "ORDER", "STORE", "CHANNEL", "CAMPAIGN_TOUCH", "MARKETING_CHANNEL")}
    build_bai1(paths, dd)
    build_bai2(paths, dd)
    print("Xong. Thu muc:", OUT.resolve())


if __name__ == "__main__":
    main()
