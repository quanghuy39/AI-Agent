# Code contract (mandatory)

Files
- Read input data only from os.environ["AGENT_INPUT_DIR"].
- Files written by earlier steps are in os.environ["AGENT_OUTPUT_DIR"]. Write every output file only there.
- Do not read or write any other location. Use only pandas, numpy and the Python standard library.
  No network, no subprocess or shell, no deleting files.

Reading data
- Read CSV files with dtype=str, then convert types explicitly as the data summary describes.
  Parse dates with the exact format shown in the data summary (e.g. format="%d-%m-%Y"); never guess a format.
- Use only tables and columns listed in the data summary; never invent columns.
- Columns marked [masked] may be used freely in the code and in files written to AGENT_OUTPUT_DIR
  (e.g. as join keys), but their values must never appear in result_summary and must never be printed
  (no print/head/display of rows containing them, no values in error messages you raise).

Numbers
- Every value in result_summary is computed by the code. Never type a result by hand.

Meta file (required; the last thing the script does)
Write one JSON object to os.environ["AGENT_META_PATH"] with:
- result_summary: flat object. Keys are ASCII identifiers (letters, digits, _); values are numbers, strings,
  booleans or null. Reports refer to them as {{R<n>.<key>}}.
- checks: list with at least one item, each one of:
  - {"type": "row_count", "label", "before": int, "after": int,
     "expect": "same"|"not_more"|"not_fewer"|"free", "expected_ratio": [lo, hi] or null}
  - {"type": "sum", "label", "parts": [numbers], "total": number}
  - {"type": "range", "label", "file", "column", "min": number or null, "max": number or null}
  - {"type": "join", "label", "left", "right", "left_on", "right_on", "how": "left"|"inner"}
    (left/right are file names in the input or output directory; *_on is a column name or a list)
  - {"type": "custom", "name", "passed": bool, "reason"}

Required checks
- Each item of can_be_wrong_if has at least one matching custom check.
- Each filter or join has a row_count check; give expected_ratio when the step should drop many rows.
- Each join of two tables has a join check.
- Each group-by aggregation has a sum check when the overall total can be computed.

Example (end of script):
```python
import json, os
meta = {
    "result_summary": {"total_value": float(total), "n_groups": int(len(by_group))},
    "checks": [
        {"type": "row_count", "label": "filter", "before": int(n_before), "after": int(n_after),
         "expect": "not_more", "expected_ratio": None},
        {"type": "sum", "label": "group totals", "parts": [float(x) for x in by_group], "total": float(total)},
        {"type": "custom", "name": "no negative values", "passed": bool((values >= 0).all()), "reason": "..."},
    ],
}
with open(os.environ["AGENT_META_PATH"], "w", encoding="utf-8") as f:
    json.dump(meta, f, ensure_ascii=False)
```
