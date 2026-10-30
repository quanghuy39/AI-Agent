"""Cham bai1: python -m tests.grade_bai1, giong python -m tests.grade bai1."""

import sys

from tests.grade import grade


def main():
    return grade("bai1")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
