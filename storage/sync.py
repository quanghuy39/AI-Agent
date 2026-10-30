"""Dong bo ho so output/<bai>/ len R2 tai R2_OUTPUT_PREFIX + <bai>/. Chi tai len, khong xoa gi tren R2.

Chay: python -m storage.sync <bai> [--dry-run]

Ghi qua r2.upload_file (token R2_WRITE_*). Manifest {duong dan tuong doi: sha256} o cache/sync_manifest/<bai>.json:
file hash khong doi thi bo qua; manifest chi cap nhat sau khi tung file tai len thanh cong, nen file loi duoc tai lai
lan sau. Tu choi: ho so gia lap output/_fake/ (ke ca goi tay), khoa ngoai R2_OUTPUT_PREFIX, input/, answers/.

sync_task: khong ghi log, loi tung file gom vao ket qua. safe_sync: dung trong vong lap, khong bao gio nem loi;
su kien sync no ghi lam doi run_log.jsonl, nen lan dong bo sau luon co it nhat file nay.
"""

import argparse
import json
import os
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

import config
from runner import run_code as runner
from storage import r2

_TASK_RE = re.compile(r"^[A-Za-z0-9_\-]+$")


@dataclass
class SyncResult:
    uploaded: list = field(default_factory=list)   # duong dan tuong doi da tai (dry_run: se tai)
    skipped: int = 0                               # hash khong doi
    failed: list = field(default_factory=list)     # duong dan tuong doi tai loi
    errors: list = field(default_factory=list)     # ten loi, khong kem thong bao (co the chua bucket, duong dan)
    not_attempted: int = 0                         # chua thu vi lo mat ket noi
    dry_run: bool = False


def _check_task(task):
    if not _TASK_RE.match(task or "") or task.lower() == "answers":
        raise ValueError(f"Tên bài không hợp lệ: {task!r}")
    if config.FAKE_OUTPUT_SUBDIR in (task, runner.OUTPUT_DIR.name):
        raise ValueError(f"Không đồng bộ hồ sơ giả lập output/{config.FAKE_OUTPUT_SUBDIR}/.")


def check_key(key, settings):
    """Khoa ghi hop le: trong R2_OUTPUT_PREFIX, khong dung input/ hay answers/. Sai -> ValueError."""
    out, inp = settings["R2_OUTPUT_PREFIX"], settings["R2_INPUT_PREFIX"]
    if not out.endswith("/") or not key.startswith(out) or key.startswith(inp) or ".." in Path(key).parts \
            or r2._is_answers(key):
        raise ValueError(f"Không được ghi '{key}': chỉ ghi trong '{out}', không ghi vào input/ hay answers/.")


def _manifest_path(task):
    return r2.CACHE_DIR / config.SYNC_MANIFEST_DIR / f"{task}.json"


def _read_manifest(task):
    """Manifest hong thi coi nhu rong: ton them mot lan tai lai, khong sai du lieu."""
    try:
        data = json.loads(_manifest_path(task).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_manifest(task, manifest):
    path = _manifest_path(task)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(manifest, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise


def _local_files(root):
    """Moi file cua ho so, tru file tam ghi an toan cua Ledger."""
    return [p for p in sorted(root.rglob("*"))
            if p.is_file() and not (p.name.startswith(".") and p.name.endswith(".tmp"))]


def sync_task(task, dry_run=False):
    """Tai cac file da doi cua mot bai. Tu choi (ValueError) truoc khi tai neu bai hoac prefix khong hop le;
    loi tung file gom vao SyncResult. Mat ket noi thi dung luot nay, khoi cho timeout tung file."""
    _check_task(task)
    settings = r2.get_r2_settings()
    prefix = f"{settings['R2_OUTPUT_PREFIX']}{task}/"
    check_key(prefix, settings)
    root = runner.OUTPUT_DIR / task
    if not root.is_dir():
        raise ValueError(f"Không có hồ sơ output/{task}/.")

    manifest = _read_manifest(task)
    result = SyncResult(dry_run=dry_run)
    files = _local_files(root)
    for i, path in enumerate(files):
        rel = path.relative_to(root).as_posix()
        digest = runner._sha256(path)
        if manifest.get(rel) == digest:
            result.skipped += 1
            continue
        if dry_run:
            result.uploaded.append(rel)
            continue
        try:
            key = prefix + rel
            check_key(key, settings)
            r2.upload_file(path, key)
        except Exception as e:  # noqa: BLE001  mot file loi khong duoc lam hong ca luot
            result.failed.append(rel)
            result.errors.append(type(e).__name__)
            if isinstance(e, runner._CONNECTION_ERRORS):
                result.not_attempted = len(files) - i - 1
                break
            continue
        manifest[rel] = digest
        _write_manifest(task, manifest)
        result.uploaded.append(rel)
    return result


def safe_sync(led, task, reason):
    """Dong bo trong luc chay: khong bao gio dung agent. Loi -> mot dong canh bao; moi lan ghi su kien sync."""
    try:
        r = sync_task(task)
    except Exception as e:  # noqa: BLE001  chi ghi ten loi
        r = SyncResult(errors=[type(e).__name__])
    errors = sorted(set(r.errors))
    extra = {"reason": reason, "uploaded": len(r.uploaded), "skipped": r.skipped, "failed": len(r.failed),
             "not_attempted": r.not_attempted, "errors": errors}
    led.log_event("sync", "sync", status="error" if errors else "ok", extra=extra)
    if errors:
        print(f"Cảnh báo: đồng bộ R2 chưa xong ({', '.join(errors)}), {len(r.failed)} file lỗi, lần sau thử lại.")
    return r


def main(argv=None):
    p = argparse.ArgumentParser(description="Đồng bộ hồ sơ một bài lên R2 (chỉ tải lên, không xóa).")
    p.add_argument("task", metavar="bai")
    p.add_argument("--dry-run", action="store_true", help="chỉ in số file sẽ tải, không tải")
    args = p.parse_args(argv)
    try:
        r = sync_task(args.task, dry_run=args.dry_run)
    except ValueError as e:
        print(f"Từ chối: {e}")
        return 2
    if args.dry_run:
        print(f"Sẽ tải {len(r.uploaded)} file, bỏ qua {r.skipped} file.")
        return 0
    print(f"Đã tải {len(r.uploaded)} file, bỏ qua {r.skipped} file, lỗi {len(r.failed)} file"
          + (f", chưa thử {r.not_attempted} file" if r.not_attempted else "") + ".")
    if r.errors:
        print(f"Lỗi: {', '.join(sorted(set(r.errors)))}")
    return 1 if r.errors else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
