"""Do quan he giua cac bang theo ten cot khoa, roi kiem tung quan he bang validators.join_check.

Bang cha co khoa DON K; bang khac co cot ten K (khong phan biet hoa thuong, strip) -> quan he con.cot -> cha.K.
Khoa ghep bi bo qua. Gioi han: khoa ten chung chung (vd. "id") o nhieu bang co the tao quan he gia;
neu gia tri la so 1..n thi overlap cao nen bo loc overlap khong loai duoc.
"""

import config
from validators import join_check, normalize_keys
from validators.result import DEFAULT_MAX_EXAMPLES, pct


def _norm(name):
    return str(name).strip().lower()


def infer_relationships(tables, keys):
    """tables: {ten bang: DataFrame}; keys: {ten bang: key_cols hoac None}.
    Tra ve {"relationships": [{child, child_col, parent, parent_col}], "notes": [ambiguous_direction...]}."""
    relationships, notes, seen_pairs = [], [], set()
    for parent, pk in keys.items():
        if parent not in tables or not pk or len(pk) != 1:
            continue
        key = pk[0]
        for child, df in tables.items():
            if child == parent:
                continue
            col = next((c for c in df.columns if _norm(c) == _norm(key)), None)
            if col is None:
                continue
            child_key = keys.get(child) or []
            if len(child_key) == 1 and _norm(child_key[0]) == _norm(key):
                pair = frozenset((child, parent))
                if pair not in seen_pairs:  # cung khoa o hai bang: khong ro huong, ghi mot lan
                    seen_pairs.add(pair)
                    notes.append({"code": "ambiguous_direction", "table": child, "column": str(col),
                                  "other_table": parent, "other_column": key})
                continue
            relationships.append({"child": child, "child_col": str(col), "parent": parent, "parent_col": key})
    return {"relationships": relationships, "notes": notes}


def overlap_ratio(child_values, parent_values):
    """Ty le gia tri khac null cua cot con (da chuan hoa nhu join_check) co mat ben cha."""
    child = normalize_keys(child_values.dropna())
    if child.empty:
        return 0.0
    parent = set(normalize_keys(parent_values.dropna()))
    return float(child.isin(parent).mean())


def check_relationships(tables, keys, fk_columns=None, min_overlap=None, unmatched_threshold=None,
                        max_examples=DEFAULT_MAX_EXAMPLES):
    """Do quan he va kiem tung cai. Tra ve {"checks": [...], "notes": [...]}.

    checks: {child, child_col, parent, parent_col, status ("ok" | "flag" | "rejected"), result}.
    fk_columns: {ten bang: [cot FK trong dictionary]}; cot FK khong co quan he ok/flag nao -> note fk_without_reference.
    """
    min_overlap = config.RELATIONSHIP_MIN_OVERLAP if min_overlap is None else min_overlap
    inferred = infer_relationships(tables, keys)
    checks = []
    for r in inferred["relationships"]:
        child_s = tables[r["child"]][r["child_col"]]
        parent_s = tables[r["parent"]][r["parent_col"]]
        overlap = overlap_ratio(child_s, parent_s)
        if overlap < min_overlap:
            result = {"overlap": round(overlap, 6), "min_overlap": min_overlap,
                      "reason": f"tên cột trùng nhưng giá trị không giao nhau (overlap {pct(overlap)} "
                                f"< {pct(min_overlap)})"}
            checks.append({**r, "status": "rejected", "result": result})
            continue
        label = f"{r['child']}.{r['child_col']} -> {r['parent']}.{r['parent_col']}"
        result = join_check(tables[r["child"]], tables[r["parent"]], r["child_col"], r["parent_col"], how="left",
                            unmatched_threshold=unmatched_threshold, max_examples=max_examples, label=label)
        result["details"]["overlap"] = round(overlap, 6)
        checks.append({**r, "status": "ok" if result["status"] == "pass" else "flag", "result": result})

    notes = list(inferred["notes"])
    linked = {(c["child"], _norm(c["child_col"])) for c in checks if c["status"] != "rejected"}
    for table, cols in (fk_columns or {}).items():
        for col in cols:
            if (table, _norm(col)) not in linked:
                notes.append({"code": "fk_without_reference", "table": table, "column": col})
    return {"checks": checks, "notes": notes}
