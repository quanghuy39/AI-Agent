"""Cache ket qua thanh cong trong sqlite3. Khoa khong chua ten model."""

import hashlib
import json
import logging
import sqlite3
from contextlib import closing
from pathlib import Path

log = logging.getLogger("llm")

ROOT = Path(__file__).resolve().parent.parent

_SCHEMA = "CREATE TABLE IF NOT EXISTS llm_cache (key TEXT PRIMARY KEY, value TEXT NOT NULL, created_at TEXT NOT NULL)"


def cache_key(role, messages, schema_json):
    payload = json.dumps(
        {"role": role, "messages": messages, "schema_json": schema_json},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _resolve(path):
    path = Path(path)
    return path if path.is_absolute() else ROOT / path


def _connect(path):
    path = _resolve(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute(_SCHEMA)
    return con


def get(path, key):
    """Tra ve dict da luu hoac None. Loi doc cache coi nhu miss va canh bao."""
    try:
        with closing(_connect(path)) as con:
            row = con.execute("SELECT value FROM llm_cache WHERE key = ?", (key,)).fetchone()
    except Exception as e:  # noqa: BLE001
        log.warning("Không đọc được cache %s: %s: %s", path, type(e).__name__, e)
        return None
    return json.loads(row[0]) if row else None


def put(path, key, value):
    try:
        with closing(_connect(path)) as con, con:
            con.execute(
                "INSERT OR REPLACE INTO llm_cache (key, value, created_at) VALUES (?, ?, ?)",
                (key, json.dumps(value, ensure_ascii=False), value["cached_at"]),
            )
    except Exception as e:  # noqa: BLE001
        log.warning("Không ghi được cache %s: %s: %s", path, type(e).__name__, e)
