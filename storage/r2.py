"""Module storage: doc/ghi du lieu tren Cloudflare R2 (tuong thich S3)."""
import csv
import sys
from pathlib import Path

import boto3
import pandas as pd
from botocore.config import Config

from config import ROOT, get_r2_settings

CACHE_DIR = ROOT / "cache"
CSV_ENCODINGS = ("utf-8-sig", "cp1258", "latin-1")
CSV_DELIMITERS = ",;\t|"
CSV_SNIFF_BYTES = 8192


def _check_read(key_or_prefix, grader=False):
    """Agent chi duoc DOC input/ va output/. answers/ chi danh cho buoc cham diem (grader=True)."""
    if grader:
        return
    s = get_r2_settings()
    allowed = (s["R2_INPUT_PREFIX"], s["R2_OUTPUT_PREFIX"])
    if ".." in Path(key_or_prefix).parts or not key_or_prefix.startswith(allowed):
        raise ValueError(
            f"Không được đọc '{key_or_prefix}'. Chỉ được đọc trong {allowed}."
        )


def _make_client(key_id, secret):
    s = get_r2_settings()
    return boto3.client(
        "s3",
        endpoint_url=s["R2_ENDPOINT"],
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def _read_client():
    """Client DOC: dung token chi doc."""
    s = get_r2_settings()
    return _make_client(s["R2_ACCESS_KEY_ID"], s["R2_SECRET_ACCESS_KEY"])


def _write_client():
    """Client GHI: dung token Read & Write."""
    s = get_r2_settings()
    return _make_client(s["R2_WRITE_ACCESS_KEY_ID"], s["R2_WRITE_SECRET_ACCESS_KEY"])


def list_objects(prefix="", grader=False):
    """Liet ke file tren R2 (co phan trang). Tra ve list dict."""
    _check_read(prefix, grader)
    bucket = get_r2_settings()["R2_BUCKET"]
    client = _read_client()
    paginator = client.get_paginator("list_objects_v2")
    result = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            result.append(
                {
                    "key": obj["Key"],
                    "size": obj["Size"],
                    "etag": obj["ETag"].strip('"'),
                    "last_modified": obj["LastModified"],
                }
            )
    return result


def download_with_cache(key, cache_dir=None, grader=False):
    """Tai file ve cache/. Chi tai lai khi etag tren R2 da doi. Tra ve Path."""
    _check_read(key, grader)
    if ".." in Path(key).parts:
        raise ValueError(f"Đường dẫn không hợp lệ: {key}")
    bucket = get_r2_settings()["R2_BUCKET"]
    base = Path(cache_dir) if cache_dir else CACHE_DIR
    local = base / key
    etag_file = local.parent / (local.name + ".etag")
    local.parent.mkdir(parents=True, exist_ok=True)

    client = _read_client()
    remote_etag = client.head_object(Bucket=bucket, Key=key)["ETag"].strip('"')

    if local.exists() and etag_file.exists():
        if etag_file.read_text(encoding="utf-8").strip() == remote_etag:
            return local  # cache con moi, khong tai lai

    client.download_file(bucket, key, str(local))
    etag_file.write_text(remote_etag, encoding="utf-8")
    return local


def get_etag(key, cache_dir=None):
    """Doc etag da ghi khi tai file (de ghi vao ho so truy vet)."""
    base = Path(cache_dir) if cache_dir else CACHE_DIR
    etag_file = (base / key).parent / (Path(key).name + ".etag")
    return etag_file.read_text(encoding="utf-8").strip() if etag_file.exists() else None


def upload_file(local_path, key):
    """Tai file len R2. CHI cho phep ghi vao prefix output/."""
    s = get_r2_settings()
    out_prefix = s["R2_OUTPUT_PREFIX"]
    if not key.startswith(out_prefix) or ".." in Path(key).parts:
        raise ValueError(
            f"Chỉ được ghi vào '{out_prefix}'. Đường dẫn '{key}' không hợp lệ."
        )
    _write_client().upload_file(str(local_path), s["R2_BUCKET"], key)
    return key


def read_table(key, grader=False, dtype=None):
    """Tai (co cache) roi doc thanh dataframe.

    dtype=None: nhu cu (pandas tu doan kieu). dtype=str: moi cot la chuoi, null mac dinh cua pandas
    giu la null (giu nguyen "05042025", "4.0"...). Voi parquet (da co kieu san) dtype=str chi doi
    tung gia tri khac null sang str.
    """
    path = download_with_cache(key, grader=grader)
    if str(path).lower().endswith(".parquet"):
        df = pd.read_parquet(path)
        if dtype is str:
            df = df.astype(object).where(df.notna(), None)
            df = df.apply(lambda col: col.map(lambda v: v if v is None else str(v)))
        elif dtype is not None:
            df = df.astype(dtype)
        return df
    fmt = detect_csv_format(path)
    df = pd.read_csv(path, dtype=dtype, sep=fmt["delimiter"], encoding=fmt["encoding"])
    if df.shape[1] == 1 and any(c in fmt["first_line"] for c in ";\t"):
        fmt["flags"].append("suspect_wrong_delimiter")
    df.attrs["csv_format"] = {"delimiter": fmt["delimiter"], "encoding": fmt["encoding"], "flags": fmt["flags"]}
    return df


def detect_csv_format(path):
    """Nhan dien ma hoa (thu utf-8-sig, cp1258, latin-1) va dau phan cach (csv.Sniffer tren vai KB dau).

    Tra ve dict: encoding, delimiter, first_line, flags. Khong doan duoc dau phan cach thi dung ",".
    """
    raw = Path(path).read_bytes()
    for encoding in CSV_ENCODINGS:
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    sample = text[:CSV_SNIFF_BYTES]
    if len(text) > CSV_SNIFF_BYTES and "\n" in sample:
        sample = sample[:sample.rfind("\n")]  # chi dung cac dong day du
    first_line = sample.splitlines()[0] if sample else ""
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=CSV_DELIMITERS).delimiter
    except csv.Error:
        delimiter = ","
    return {"encoding": encoding, "delimiter": delimiter, "first_line": first_line, "flags": []}


def _fmt_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


if __name__ == "__main__":
    grader = "--grader" in sys.argv[1:]
    args = [a for a in sys.argv[1:] if a != "--grader"]
    if args and args[0] == "list":
        prefix = args[1] if len(args) > 1 else get_r2_settings()["R2_INPUT_PREFIX"]
        for o in list_objects(prefix, grader=grader):
            print(f"{o['key']}  ({_fmt_size(o['size'])})")
    elif len(args) == 2 and args[0] == "head":
        df = read_table(args[1], grader=grader)
        print(df.head())
        print(f"Số dòng: {len(df)}, số cột: {df.shape[1]}")
    else:
        print("Cách dùng:")
        print("  python -m storage.r2 list [prefix] [--grader]")
        print("  python -m storage.r2 head <key> [--grader]")