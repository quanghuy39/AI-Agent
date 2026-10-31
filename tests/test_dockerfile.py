"""Dockerfile chep du ma nguon va khong chep du lieu, bi mat. Chi doc file, khong build.

Chay: python -m pytest tests/test_dockerfile.py -v
"""

import re

import config

DOCKERFILE = (config.ROOT / "Dockerfile").read_text(encoding="utf-8")
NOT_IN_IMAGE = {"tests", "output", "cache", "test_data"}


def copied():
    """Moi nguon trong cac lenh COPY (bo dich o cuoi), khong co dau / cuoi."""
    return {src.rstrip("/") for line in re.findall(r"^COPY\s+(.+)$", DOCKERFILE, re.M) for src in line.split()[:-1]}


def source_dirs():
    return {p.name for p in config.ROOT.iterdir()
            if p.is_dir() and not p.name.startswith((".", "_")) and p.name not in NOT_IN_IMAGE}


def test_every_source_directory_is_copied():
    assert source_dirs() - copied() == set()


def test_expected_directories_named():
    assert {"analysis", "report", "skills", "templates", "config"} <= copied()


def test_every_copied_path_exists():
    assert all((config.ROOT / src).exists() for src in copied())


def test_data_and_secrets_not_copied():
    assert not copied() & (NOT_IN_IMAGE | {".env", ".venv"})
