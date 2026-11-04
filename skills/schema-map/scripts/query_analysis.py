"""Static MySQL/TiDB query-plan explanation and bounded latency simulation."""
import json
import math
import re


IDENT = r"(?:`[^`]+`|[A-Za-z_][\w$]*)"
TABLE_REF = re.compile(
    rf"\b(?:FROM|JOIN)\s+(?P<table>{IDENT}(?:\s*\.\s*{IDENT})?)"
    rf"(?:\s+(?:AS\s+)?(?P<alias>[A-Za-z_][\w$]*))?",
    re.I,
)
PREDICATE = re.compile(
    rf"(?:(?P<alias>{IDENT})\s*\.\s*)?(?P<column>{IDENT})\s*"
    r"(?P<operator><=>|>=|<=|<>|!=|=|>|<|\bIN\b|\bBETWEEN\b|\bLIKE\b)",
    re.I,
)
JOIN_EQUALITY = re.compile(
    rf"(?P<a>{IDENT})\s*\.\s*(?P<ac>{IDENT})\s*=\s*"
    rf"(?P<b>{IDENT})\s*\.\s*(?P<bc>{IDENT})",
    re.I,
)
SQL_WORDS = {
    "where", "join", "left", "right", "inner", "outer", "cross", "full",
    "on", "group", "order", "limit", "having", "union", "straight_join",
}
SCOPE_COLUMNS = ("account_id", "tenant_id", "namespace_id", "organization_id", "org_id")


def clean_name(value):
    return value.split(".")[-1].replace("`", "").strip().lower()


def _number(value, default):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else default
    except (TypeError, ValueError):
        return default


def parse_sql(query, tables):
    """Return table aliases, predicates, joins, and coarse query operators."""
    by_name = {t["name"]: t for t in tables}
    refs = []
    aliases = {}
    for match in TABLE_REF.finditer(query):
        table = clean_name(match.group("table"))
        alias = (match.group("alias") or table).replace("`", "").lower()
        if alias in SQL_WORDS:
            alias = table
        if table in by_name and table not in [x["table"] for x in refs]:
            refs.append({"table": table, "alias": alias})
            aliases[alias] = table
            aliases[table] = table

    joins = []
    for match in JOIN_EQUALITY.finditer(query):
        left_alias = clean_name(match.group("a"))
        right_alias = clean_name(match.group("b"))
        left = aliases.get(left_alias)
        right = aliases.get(right_alias)
        if left and right and left != right:
            joins.append({
                "left_table": left,
                "left_column": clean_name(match.group("ac")),
                "right_table": right,
                "right_column": clean_name(match.group("bc")),
            })

    predicates = {ref["table"]: [] for ref in refs}
    columns_by_table = {t["name"]: {c["name"] for c in t["columns"]} for t in tables}
    for match in PREDICATE.finditer(query):
        column = clean_name(match.group("column"))
        alias = clean_name(match.group("alias") or "")
        operator = match.group("operator").upper()
        candidates = []
        if alias and alias in aliases:
            candidates = [aliases[alias]]
        elif not alias:
            candidates = [ref["table"] for ref in refs if column in columns_by_table.get(ref["table"], set())]
            if len(candidates) != 1:
                candidates = []
        for table in candidates:
            item = {"column": column, "operator": operator}
            if item not in predicates[table]:
                predicates[table].append(item)

    return {
        "tables": refs,
        "predicates": predicates,
        "joins": joins,
        "has_order": bool(re.search(r"\bORDER\s+BY\b", query, re.I)),
        "has_group": bool(re.search(r"\bGROUP\s+BY\b|\bDISTINCT\b", query, re.I)),
        "has_limit": bool(re.search(r"\bLIMIT\s+\d+", query, re.I)),
    }


def parse_explain(text):
    """Parse useful facts from MySQL JSON, tabular EXPLAIN, or EXPLAIN ANALYZE text."""
    if not text or not text.strip():
        return []
    raw = text.strip()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = None
    operations = []

    if value is not None:
        def walk(node):
            if isinstance(node, dict):
                if "table_name" in node or "access_type" in node:
                    cost = node.get("cost_info") or {}
                    operations.append({
                        "table": clean_name(str(node.get("table_name", ""))),
                        "access": str(node.get("access_type", "unknown")),
                        "key": node.get("key"),
                        "estimated_rows": _number(node.get("rows_examined_per_scan", node.get("rows", 0)), 0),
                        "produced_rows": _number(node.get("rows_produced_per_join", 0), 0),
                        "cost": _number(cost.get("query_cost", cost.get("read_cost", 0)), 0),
                        "source": "json explain",
                    })
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)
        walk(value)
        return operations

    header = None
    for line in raw.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        lowered = [c.lower() for c in cells]
        if "table" in lowered and ("type" in lowered or "access_type" in lowered):
            header = lowered
            continue
        if header and len(cells) == len(header) and not set("".join(cells)) <= {"-", "+", "="}:
            row = dict(zip(header, cells))
            operations.append({
                "table": clean_name(row.get("table", "")),
                "access": row.get("type", row.get("access_type", "unknown")),
                "key": row.get("key") if row.get("key") not in (None, "NULL") else None,
                "estimated_rows": _number(row.get("rows", 0), 0),
                "produced_rows": 0,
                "cost": 0,
                "source": "tabular explain",
            })

    for line in raw.splitlines():
        actual = re.search(
            r"actual\s+time\s*=\s*([\d.]+)\.\.([\d.]+)\s+rows\s*=\s*([\d.]+)\s+loops\s*=\s*(\d+)",
            line,
            re.I,
        )
        if not actual:
            continue
        estimate = re.search(r"cost\s*=\s*[\d.]+\.\.([\d.]+)\s+rows\s*=\s*([\d.]+)", line, re.I)
        table_match = re.search(r"\bon\s+(`?[A-Za-z_][\w$]*`?)", line, re.I)
        access_match = re.search(r"\b(index lookup|index range scan|table scan|covering index scan|hash join|nested loop)\b", line, re.I)
        operations.append({
            "table": clean_name(table_match.group(1)) if table_match else "",
            "access": access_match.group(1).lower() if access_match else "plan operation",
            "key": None,
            "estimated_rows": _number(estimate.group(2), 0) if estimate else 0,
            "produced_rows": _number(actual.group(3), 0),
            "cost": _number(estimate.group(1), 0) if estimate else 0,
            "actual_start_ms": _number(actual.group(1), 0),
            "actual_end_ms": _number(actual.group(2), 0),
            "loops": int(actual.group(4)),
            "source": "explain analyze",
            "label": re.sub(r"^\s*->\s*", "", line.strip()),
        })
    return operations


def _indexes(table):
    return [
        {"kind": item["kind"], "columns": item["columns"]}
        for item in table.get("constraints", [])
        if item.get("kind") in {"PRIMARY KEY", "UNIQUE", "INDEX"} and item.get("columns")
    ]


def _selectivity(predicate, table_rows, unique=False, configured=None):
    if configured is not None:
        return max(1 / max(table_rows, 1), min(1.0, _number(configured, 1.0)))
    operator = predicate["operator"]
    if unique and operator in {"=", "<=>"}:
        return 1 / max(table_rows, 1)
    if operator in {"=", "<=>"}:
        return 0.01
    if operator == "IN":
        return 0.05
    if operator in {">", "<", ">=", "<=", "BETWEEN"}:
        return 0.20
    if operator == "LIKE":
        return 0.25
    return 0.50


def _matching_index(indexes, predicates):
    by_column = {p["column"]: p for p in predicates}
    best = None
    for index in indexes:
        matched = []
        for column in index["columns"]:
            pred = by_column.get(column)
            if not pred:
                break
            matched.append(column)
            if pred["operator"] not in {"=", "<=>", "IN"}:
                break
        if matched and (best is None or len(matched) > len(best["matched"])):
            best = {**index, "matched": matched}
    return best


def analyze_query(query, schema, stats=None, explain_text=None):
    """Explain access paths and estimate latency without executing the SQL."""
    stats = stats or {}
    table_stats = stats.get("tables", {})
    environment = stats.get("environment", {})
    parsed = parse_sql(query, schema["tables"])
    explain = parse_explain(explain_text or "")
    by_name = {t["name"]: t for t in schema["tables"]}
    accesses = []
    recommendations = []
    assumptions = []
    used_default_rows = False
    total_low = 0.0
    total_high = 0.0

    best_rate = max(1, _number(environment.get("best_rows_per_second"), 2_000_000))
    slow_rate = max(1, _number(environment.get("conservative_rows_per_second"), 50_000))
    if best_rate < slow_rate:
        best_rate, slow_rate = slow_rate, best_rate

    explain_by_table = {}
    for operation in explain:
        if operation.get("table"):
            explain_by_table.setdefault(operation["table"], operation)

    for ref in parsed["tables"]:
        table_name = ref["table"]
        table = by_name[table_name]
        configured = table_stats.get(table_name, {})
        rows = int(max(1, _number(configured.get("rows"), stats.get("default_rows", 100_000))))
        if "rows" not in configured:
            used_default_rows = True
        predicates = parsed["predicates"].get(table_name, [])
        indexes = _indexes(table)
        match = _matching_index(indexes, predicates)
        unique_columns = {
            tuple(index["columns"])
            for index in indexes
            if index["kind"] in {"PRIMARY KEY", "UNIQUE"}
        }
        selectivity = 1.0
        configured_selectivity = configured.get("selectivity", {})
        for predicate in predicates:
            unique = (predicate["column"],) in unique_columns
            selectivity *= _selectivity(
                predicate,
                rows,
                unique=unique,
                configured=configured_selectivity.get(predicate["column"]),
            )
        output_rows = max(1, int(rows * max(1 / rows, selectivity))) if predicates else rows
        scan_rows = output_rows if match else rows
        operation = explain_by_table.get(table_name)
        if operation and operation.get("estimated_rows"):
            scan_rows = max(1, int(operation["estimated_rows"]))
        actual_ms = None
        if operation and operation.get("actual_end_ms") is not None:
            actual_ms = operation["actual_end_ms"] * max(1, operation.get("loops", 1))
            low_ms = max(0.01, actual_ms * 0.9)
            high_ms = max(low_ms, actual_ms * 1.1)
        else:
            low_ms = 0.5 + scan_rows / best_rate * 1000
            high_ms = 4.0 + scan_rows / slow_rate * 1000
            if parsed["has_order"] or parsed["has_group"]:
                high_ms *= 1.35
        total_low += low_ms
        total_high += high_ms
        access = {
            "table": table_name,
            "alias": ref["alias"],
            "table_rows": rows,
            "predicates": predicates,
            "access": operation.get("access") if operation else ("index lookup" if match else "full scan"),
            "index": operation.get("key") if operation and operation.get("key") else (match["columns"] if match else None),
            "estimated_scan_rows": scan_rows,
            "estimated_output_rows": output_rows,
            "latency_low_ms": round(low_ms, 2),
            "latency_high_ms": round(high_ms, 2),
            "actual_ms": round(actual_ms, 3) if actual_ms is not None else None,
        }
        accesses.append(access)

        predicate_columns = [p["column"] for p in predicates]
        equality = [p["column"] for p in predicates if p["operator"] in {"=", "<=>", "IN"}]
        range_columns = [p["column"] for p in predicates if p["operator"] not in {"=", "<=>", "IN"}]
        candidate = list(dict.fromkeys(equality + range_columns))[:4]
        if predicate_columns and (not match or len(match["matched"]) < min(len(candidate), 2)):
            existing = [idx["columns"] for idx in indexes]
            if candidate and candidate not in existing:
                sql = f"CREATE INDEX idx_{table_name}_{'_'.join(candidate)} ON {table_name} ({', '.join(candidate)});"
                recommendations.append({
                    "severity": "high" if not match else "medium",
                    "title": f"Review an index for {table_name}",
                    "detail": "Equality filters lead the candidate index; range filters follow. Confirm selectivity and write cost before applying it.",
                    "sql": sql,
                })

        table_columns = {c["name"] for c in table["columns"]}
        filtered = set(predicate_columns)
        for scope in SCOPE_COLUMNS:
            if scope in table_columns and scope not in filtered:
                recommendations.append({
                    "severity": "medium",
                    "title": f"Confirm {scope} scoping on {table_name}",
                    "detail": f"The table contains {scope}, but the query does not filter it. Add the predicate when it is part of the data-access contract; omitting it can increase work and may cross a tenant boundary.",
                })
                break

    if not parsed["tables"]:
        assumptions.append("No referenced table could be matched to the parsed schema; simulation is unavailable.")
    if used_default_rows:
        assumptions.append(f"Tables without statistics are modeled as {int(stats.get('default_rows', 100_000)):,} rows.")
    if not explain:
        assumptions.append("No EXPLAIN plan was supplied; access paths are inferred from SQL predicates and declared indexes.")
    if not stats:
        assumptions.append("No production statistics or hardware profile was supplied; latency is a broad heuristic range.")
    assumptions.append("Cache warmth, concurrency, locks, network latency, skew, partitions, and optimizer decisions can materially change runtime.")

    actual_ops = [op for op in explain if op.get("actual_end_ms") is not None]
    confidence = "high" if actual_ops else "medium" if explain and stats else "low"
    if actual_ops:
        observed = max(op["actual_end_ms"] * max(1, op.get("loops", 1)) for op in actual_ops)
        total_low, total_high = observed * 0.9, observed * 1.1
    if parsed["has_limit"] and not parsed["has_order"]:
        total_high *= 0.75

    return {
        "query": query.strip(),
        "confidence": confidence,
        "latency": {"low_ms": round(total_low, 2), "high_ms": round(max(total_low, total_high), 2)},
        "accesses": accesses,
        "plan": explain,
        "joins": parsed["joins"],
        "recommendations": recommendations,
        "assumptions": assumptions,
        "simulator": {
            "best_rows_per_second": int(best_rate),
            "conservative_rows_per_second": int(slow_rate),
            "has_order_or_group": parsed["has_order"] or parsed["has_group"],
        },
    }
