"""Du lieu GIA cho bo kiem dinh. Co dinh seed; KHONG dung du lieu that (R2, cache/).

Bay co chu y:
- customers co vai dong trung lap HOAN TOAN (cung customer_id) -> join nhan ban dong.
- orders co vai amount null.
- orders co vai don cua customer_id khong ton tai trong customers.
Ngay o dang chuoi dd-mm-yyyy.
"""

import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20261028
N_CUSTOMERS = 120
N_DUP_CUSTOMERS = 4
N_ORDERS = 400
N_NULL_AMOUNT = 9
N_ORPHAN_ORDERS = 6

SEGMENTS = ["Retail", "SMB", "Enterprise"]
STATES = ["CA", "NY", "TX", "FL", "WA"]
CHANNELS = ["web", "store", "phone"]
STATUSES = ["completed", "cancelled", "refunded"]

COLUMNS = {
    "customers": ["customer_id", "segment", "state", "signup_date"],
    "orders": ["order_id", "customer_id", "order_date", "amount", "channel", "status"],
}
KNOWN_COLUMNS = frozenset(c for cols in COLUMNS.values() for c in cols)


def _dates(rng, start, end, n):
    days = (pd.Timestamp(end) - pd.Timestamp(start)).days
    offsets = rng.integers(0, days + 1, size=n)
    return [(pd.Timestamp(start) + pd.Timedelta(days=int(d))).strftime("%d-%m-%Y") for d in offsets]


def make_tables(seed=SEED):
    rng = np.random.default_rng(seed)

    ids = [f"C{i:04d}" for i in range(1, N_CUSTOMERS + 1)]
    customers = pd.DataFrame({
        "customer_id": ids,
        "segment": rng.choice(SEGMENTS, size=N_CUSTOMERS, p=[0.5, 0.35, 0.15]),
        "state": rng.choice(STATES, size=N_CUSTOMERS),
        "signup_date": _dates(rng, "2023-01-01", "2024-12-31", N_CUSTOMERS),
    })
    dup_idx = rng.choice(N_CUSTOMERS, size=N_DUP_CUSTOMERS, replace=False)
    customers = pd.concat([customers, customers.iloc[dup_idx]], ignore_index=True)
    customers = customers.sample(frac=1, random_state=seed).reset_index(drop=True)

    n_regular = N_ORDERS - N_ORPHAN_ORDERS
    order_customers = list(rng.choice(ids, size=n_regular)) + [f"C9{i:03d}" for i in range(1, N_ORPHAN_ORDERS + 1)]
    amounts = np.round(rng.lognormal(mean=4.0, sigma=0.8, size=N_ORDERS), 2)
    orders = pd.DataFrame({
        "order_id": [f"O{i:05d}" for i in range(1, N_ORDERS + 1)],
        "customer_id": order_customers,
        "order_date": _dates(rng, "2024-01-01", "2025-12-31", N_ORDERS),
        "amount": amounts,
        "channel": rng.choice(CHANNELS, size=N_ORDERS, p=[0.55, 0.3, 0.15]),
        "status": rng.choice(STATUSES, size=N_ORDERS, p=[0.75, 0.15, 0.10]),
    })
    orders = orders.sample(frac=1, random_state=seed + 1).reset_index(drop=True)
    null_idx = rng.choice(N_ORDERS, size=N_NULL_AMOUNT, replace=False)
    orders.loc[null_idx, "amount"] = np.nan
    return customers, orders


def write_data(data_dir, seed=SEED):
    """Ghi customers.csv, orders.csv vao data_dir. Tra ve Path cua thu muc."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    customers, orders = make_tables(seed)
    customers.to_csv(data_dir / "customers.csv", index=False)
    orders.to_csv(data_dir / "orders.csv", index=False)
    return data_dir


def local_report(data_dir):
    """data_quality_report cho cac CSV trong data_dir (khong R2, khong ledger): cung cac buoc cua
    quality.profile_problem nhung khong co dictionary va ranges."""
    from analysis.meta_grader import read_local_table
    from profiling import flags, relationships
    from profiling.quality import profile_table

    frames = {p.stem: read_local_table(p) for p in sorted(Path(data_dir).glob("*.csv"))}
    tables = {name: profile_table(df, name) for name, df in frames.items()}
    for t in tables.values():
        t["key_source"] = "guessed" if t["key_guessed"] else None
    rel = relationships.check_relationships(frames, {n: t["key_cols"] for n, t in tables.items()})
    report = {"problem_id": "llm_eval", "dictionary": {"tables": {}}, "files": [], "skipped": [], "errors": [],
              "tables": tables, "relationship_checks": rel["checks"], "relationship_notes": rel["notes"],
              "range_checks": []}
    all_flags = flags.build_flags(report)
    for name, t in tables.items():
        t["quality_flags"] = [f for f in all_flags if f["table"] == name]
    report["quality_flags"] = [f for f in all_flags if f["table"] not in tables]
    return report


@lru_cache(maxsize=4)
def data_summary_text(seed=SEED):
    """Ban tom tat du lieu (profiling.data_summary) cua du lieu gia, nhu vong lap phan tich dua vao prompt."""
    import config
    from profiling.data_summary import build_data_summary

    with tempfile.TemporaryDirectory(prefix="llm_eval_summary_") as tmp:
        report = local_report(write_data(Path(tmp) / "data", seed))
    return build_data_summary(report, max_chars=config.SUMMARY_MAX_CHARS)["text"]
