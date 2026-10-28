"""An danh DataFrame truoc khi gui cho LLM. Khong import litellm."""

import pandas as pd


def anonymize(df, drop=None, mask=None):
    """Tra ve DataFrame moi, khong sua df goc.

    drop: danh sach cot bi bo.
    mask: danh sach cot bi thay gia tri bang ma "<ten cot>_0001", on dinh trong mot lan goi
          (cung gia tri cung ma, danh so theo thu tu xuat hien). Null giu nguyen null.
    """
    drop = list(drop or [])
    mask = list(mask or [])

    missing = [c for c in dict.fromkeys(drop + mask) if c not in df.columns]
    if missing:
        raise ValueError(f"Cột không có trong DataFrame: {missing}")
    both = [c for c in mask if c in drop]
    if both:
        raise ValueError(f"Cột vừa nằm trong drop vừa nằm trong mask: {both}")

    out = df.drop(columns=drop).copy()
    for col in mask:
        codes = {}
        values = []
        for v in out[col]:
            if pd.isna(v):
                values.append(v)
                continue
            if v not in codes:
                codes[v] = f"{col}_{len(codes) + 1:04d}"
            values.append(codes[v])
        out[col] = pd.Series(values, index=out.index, dtype=object)
    return out
