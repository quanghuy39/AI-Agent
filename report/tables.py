"""File bang cua mot ket qua. result_tables la cach duy nhat de lay danh sach nay (tong hop, bieu do, Word).

Chi doc ten cot va so dong; load_table doc ca bang, chi dung de ve bieu do va bang trong bao cao, KHONG dua cho AI.
"""

from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq
from pandas.api.types import is_bool_dtype, is_datetime64_any_dtype, is_numeric_dtype

import config
from profiling import quality
from storage import r2

TABLE_SUFFIXES = (".csv", ".parquet")


def name_has(name, hints):
    """Ten cot co mot tu thuoc hints (tach tu theo _ - khoang trang va camelCase)."""
    return bool({w.lower() for w in quality.name_words(str(name))} & set(hints))


def is_measure(name, column):
    """Cot so do: can phai, dinh dang so. Cot so mang nghia ma (nam, id) giu nguyen de khong in 2024 thanh 2.024."""
    return is_numeric_dtype(column) and not is_bool_dtype(column) and not name_has(name, config.REPORT_PLAIN_NUMBER_HINTS)


def is_time_column(name, column):
    return is_datetime64_any_dtype(column) or name_has(name, config.REPORT_TIME_COLUMN_HINTS)


def _read_csv(path, **kwargs):
    fmt = r2.detect_csv_format(path)
    return pd.read_csv(path, sep=fmt["delimiter"], encoding=fmt["encoding"], **kwargs)


def load_table(path):
    """Ca bang, cot so giu kieu so."""
    path = Path(path)
    return pd.read_parquet(path) if path.suffix.lower() == ".parquet" else _read_csv(path)


def _table_info(path):
    """{path, name, columns, rows}; file khong doc duoc (vd. rong) thi columns, rows la None."""
    columns = rows = None
    try:
        if path.suffix.lower() == ".parquet":
            meta = pq.read_metadata(path)
            columns, rows = list(meta.schema.names), meta.num_rows
        else:
            columns = [str(c) for c in _read_csv(path, nrows=0).columns]
            rows = len(_read_csv(path, usecols=[0]))
    except (OSError, ValueError):
        pass
    return {"path": path, "name": path.name, "columns": columns, "rows": rows}


def _result_files(ledger, r):
    """File cua ket qua co tren dia: output_path, cong files_created trong run_log cua moi lan thu cung buoc (<id>_<key>).
    Phan run_log chi de doc ho so cu: truoc khi co files_written, output_path thieu file lan thu hong da ghi y het.
    File da chuyen di (--restart) thi bo."""
    logged = [f for e in ledger.read_log() if e.step == "runner" and e.action == f"{r.id}_{r.question_key}"
              for f in e.extra.get("files_created") or []]
    paths = [ledger.root / p for p in (r.output_path or "").split("; ") if p] + [Path(f) for f in logged]
    return sorted({p.resolve() for p in paths if p.is_file()}, key=lambda p: p.name)


def result_tables(ledger, result_id):
    """[{path, name, columns, rows}] cac file .csv/.parquet cua ket qua, theo ten file. Khong co ket qua -> KeyError."""
    files = _result_files(ledger, ledger.get_result(result_id))
    return [_table_info(p) for p in files if p.suffix.lower() in TABLE_SUFFIXES]
