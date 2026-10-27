"""Module storage: doc/ghi du lieu tren Cloudflare R2 (tuong thich S3)."""
import sys
from pathlib import Path

import boto3
import pandas as pd
from botocore.config import Config

from config import ROOT, get_r2_settings

CACHE_DIR = ROOT / "cache"


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


def read_table(key, grader=False):
    """Tai (co cache) roi doc thanh dataframe."""
    path = download_with_cache(key, grader=grader)
    if str(path).lower().endswith(".parquet"):
        return pd.read_parquet(path)
    return pd.read_csv(path)


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