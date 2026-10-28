"""Gom file ket qua kiem dinh, in bang model x role va so voi thresholds.yaml. Khong goi mang.

Chay: python tests/llm_eval/summary.py [--results-dir DIR] [--thresholds FILE]
Neu mot case duoc chay nhieu lan, lay ket qua MOI NHAT (theo ten file roi thu tu dong).
"""

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
_NAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_(.+)_(analysis|report|slide)\.jsonl$")


def load_results(results_dir):
    """(model, role) -> {case_id: record}."""
    groups = {}
    for path in sorted(Path(results_dir).glob("*.jsonl")):
        m = _NAME_RE.match(path.name)
        if not m:
            continue
        _, model, role = m.groups()
        bucket = groups.setdefault((model, role), {})
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                bucket[rec["case_id"]] = rec
    return groups


def load_thresholds(path):
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    return {role: {**(raw.get("all") or {}), **(raw.get(role) or {})} for role in ("analysis", "report", "slide")}


def _avg(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return sum(values) / len(values) if values else None


def summarize(results_dir, thresholds_path):
    thresholds = load_thresholds(thresholds_path)
    rows = []
    for (model, role), cases in sorted(load_results(results_dir).items()):
        records = list(cases.values())
        names = sorted({k for r in records for k in r["criteria"]})
        rates = {}
        for name in names:
            vals = [r["criteria"].get(name) for r in records if r["criteria"].get(name) is not None]
            rates[name] = (sum(1 for v in vals if v), len(vals))
        verdict = {}
        for name, need in thresholds.get(role, {}).items():
            ok, n = rates.get(name, (0, 0))
            verdict[name] = n > 0 and ok / n >= need
        rows.append({
            "model": model,
            "role": role,
            "n_cases": len(records),
            "rates": rates,
            "avg_tokens": _avg([(r.get("prompt_tokens") or 0) + (r.get("completion_tokens") or 0) for r in records]),
            "avg_latency_s": _avg([r.get("latency_s") for r in records]),
            "verdict": verdict,
            "meets_thresholds": bool(verdict) and all(verdict.values()),
        })
    return rows, thresholds


def format_rows(rows, thresholds):
    lines = []
    for role in ("analysis", "report", "slide"):
        role_rows = [r for r in rows if r["role"] == role]
        if not role_rows:
            continue
        names = sorted({n for r in role_rows for n in r["rates"]})
        lines.append(f"\n=== {role} ===")
        need = thresholds.get(role, {})
        lines.append("ngưỡng: " + ", ".join(f"{k}>={v}" for k, v in need.items()))
        header = ["model", "case"] + names + ["token TB", "latency TB", "đạt ngưỡng"]
        table = [header]
        for r in role_rows:
            cells = [r["model"], str(r["n_cases"])]
            for n in names:
                ok, total = r["rates"].get(n, (0, 0))
                if total == 0:
                    cells.append("-")
                    continue
                mark = ""
                if n in r["verdict"]:
                    mark = " ✓" if r["verdict"][n] else " ✗"
                cells.append(f"{ok / total:.0%} ({ok}/{total}){mark}")
            cells.append(f"{r['avg_tokens']:.0f}" if r["avg_tokens"] is not None else "-")
            cells.append(f"{r['avg_latency_s']:.1f}s" if r["avg_latency_s"] is not None else "-")
            failed = [k for k, v in r["verdict"].items() if not v]
            cells.append("CÓ" if r["meets_thresholds"] else f"KHÔNG ({', '.join(failed)})")
            table.append(cells)
        widths = [max(len(row[i]) for row in table) for i in range(len(header))]
        for row in table:
            lines.append("  ".join(c.ljust(w) for c, w in zip(row, widths)))
    return "\n".join(lines) if lines else "Chưa có file kết quả."


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--results-dir", default=str(HERE / "results"))
    p.add_argument("--thresholds", default=str(HERE / "thresholds.yaml"))
    args = p.parse_args(argv)
    rows, thresholds = summarize(args.results_dir, args.thresholds)
    print(format_rows(rows, thresholds))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
