"""Static MySQL/TiDB query-plan explanation and bounded latency simulation."""
import json
import math
import re


IDENT = r"(?:`[^`]+`|[A-Za-z_][\w$]*)"
SCHEMA = rf"(?:{IDENT}|<[A-Za-z_][\w$]*>)"
TABLE_REF = re.compile(
    rf"\b(?:FROM|JOIN|UPDATE)\s+(?P<table>{SCHEMA}(?:\s*\.\s*{IDENT})?)"
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
    "use", "force", "ignore", "index", "key", "where", "set", "using",
}
SCOPE_COLUMNS = ("account_id", "tenant_id", "namespace_id", "namespace_sk", "organization_id", "org_id")


def clean_name(value):
    return value.split(".")[-1].replace("`", "").strip().lower()


def _number(value, default):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else default
    except (TypeError, ValueError):
        return default


def mask_sql(query):
    """Hide comments and quoted literals without changing SQL offsets."""
    chars = list(query)
    i = 0
    while i < len(query):
        if query[i] == "`":
            i += 1
            while i < len(query):
                if query[i] == "`":
                    if i + 1 < len(query) and query[i + 1] == "`":
                        i += 2
                    else:
                        i += 1
                        break
                else:
                    i += 1
            continue
        if query.startswith("--", i) or query[i] == "#":
            end = query.find("\n", i)
            end = len(query) if end < 0 else end
        elif query.startswith("/*", i):
            close = query.find("*/", i + 2)
            end = len(query) if close < 0 else close + 2
        elif query[i] in ("'", '"'):
            quote = query[i]
            end = i + 1
            while end < len(query):
                if query[end] == "\\":
                    end += 2
                elif query[end] == quote:
                    if end + 1 < len(query) and query[end + 1] == quote:
                        end += 2
                    else:
                        end += 1
                        break
                else:
                    end += 1
        else:
            i += 1
            continue
        for pos in range(i, min(end, len(query))):
            if chars[pos] != "\n":
                chars[pos] = " "
        i = end
    return "".join(chars)


def parse_sql(query, tables):
    """Return table aliases, predicates, joins, and coarse query operators."""
    by_name = {t["name"]: t for t in tables}
    sql = mask_sql(query)
    refs = []
    aliases = {}
    unknown = []

    def add_ref(table, alias):
        if table not in by_name:
            if table not in unknown:
                unknown.append(table)
            return
        if alias in aliases:
            return
        refs.append({"table": table, "alias": alias})
        aliases[alias] = table
        aliases.setdefault(table, table)

    for match in TABLE_REF.finditer(sql):
        table = clean_name(match.group("table"))
        alias = (match.group("alias") or table).replace("`", "").lower()
        if alias in SQL_WORDS:
            alias = table
        add_ref(table, alias)
    for match in re.finditer(r"\bFROM\b(?P<body>.*?)(?=\b(?:WHERE|JOIN|ON|GROUP|ORDER|LIMIT|HAVING|UNION)\b|;|$)", sql, re.I | re.S):
        for item in match.group("body").split(",")[1:]:
            comma_ref = re.match(rf"\s*(?P<table>{SCHEMA}(?:\s*\.\s*{IDENT})?)(?:\s+(?:AS\s+)?(?P<alias>{IDENT}))?", item, re.I)
            if comma_ref:
                table = clean_name(comma_ref.group("table"))
                alias = clean_name(comma_ref.group("alias") or table)
                add_ref(table, alias if alias not in SQL_WORDS else table)

    joins = []
    column_comparisons = []
    for match in JOIN_EQUALITY.finditer(sql):
        column_comparisons.append((match.start(), match.end()))
        left_alias = clean_name(match.group("a"))
        right_alias = clean_name(match.group("b"))
        left = aliases.get(left_alias)
        right = aliases.get(right_alias)
        if left and right:
            joins.append({
                "left_table": left,
                "left_alias": left_alias,
                "left_column": clean_name(match.group("ac")),
                "right_table": right,
                "right_alias": right_alias,
                "right_column": clean_name(match.group("bc")),
            })

    predicates = {ref["alias"]: [] for ref in refs}
    columns_by_table = {t["name"]: {c["name"] for c in t["columns"]} for t in tables}
    for match in PREDICATE.finditer(sql):
        if not re.search(r"\b(?:WHERE|ON)\b", sql[:match.start()], re.I):
            continue
        if any(start <= match.start() < end for start, end in column_comparisons):
            continue
        column = clean_name(match.group("column"))
        alias = clean_name(match.group("alias") or "")
        operator = match.group("operator").upper()
        candidates = []
        if alias and alias in aliases:
            candidates = [alias if alias in predicates else next((r["alias"] for r in refs if r["table"] == aliases[alias]), alias)]
        elif not alias:
            candidates = [ref["alias"] for ref in refs if column in columns_by_table.get(ref["table"], set())]
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
        "unknown_tables": unknown,
        "has_order": bool(re.search(r"\bORDER\s+BY\b", sql, re.I)),
        "order_column": clean_name(re.search(rf"\bORDER\s+BY\s+(?:{IDENT}\s*\.\s*)?({IDENT})", sql, re.I).group(1)) if re.search(rf"\bORDER\s+BY\s+(?:{IDENT}\s*\.\s*)?({IDENT})", sql, re.I) else None,
        "has_group": bool(re.search(r"\bGROUP\s+BY\b|\bDISTINCT\b", sql, re.I)),
        "limit": int(re.search(r"\bLIMIT\s+(\d+)", sql, re.I).group(1)) if re.search(r"\bLIMIT\s+(\d+)", sql, re.I) else None,
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
        if "id" in lowered and "estrows" in lowered and "task" in lowered:
            header = lowered
            continue
        if header and len(cells) == len(header) and not set("".join(cells)) <= {"-", "+", "="}:
            row = dict(zip(header, cells))
            if "estrows" in header:
                operator = re.sub(r"^[^A-Za-z]+", "", row["id"]).split("_")[0]
                access_object = row.get("access object", "")
                table = re.search(r"\btable\s*:\s*([A-Za-z_][\w$]*)", access_object, re.I)
                key = re.search(r"\bindex\s*:\s*([A-Za-z_][\w$]*)", access_object, re.I)
                timing = re.search(r"\btime\s*:\s*([\d.]+)\s*(ms|s|µs|us)?", row.get("execution info", ""), re.I)
                actual_ms = _number(timing.group(1), 0) * {"s": 1000, "µs": 0.001, "us": 0.001}.get((timing.group(2) or "ms").lower(), 1) if timing else None
                operations.append({"table": clean_name(table.group(1)) if table else "", "access": operator,
                                   "key": key.group(1) if key else None, "estimated_rows": _number(row["estrows"], 0),
                                   "produced_rows": _number(row.get("actrows"), 0), "cost": 0,
                                   "actual_end_ms": actual_ms, "source": "tidb explain", "label": row["id"] + " · " + access_object})
            else:
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


def _leading_index(indexes, column):
    return next((idx for idx in indexes if idx["columns"][0] == column), None)


def _starting_scan_rows(ref, by_name, table_stats, default_rows, predicates):
    """Estimate the first access for a candidate inner-join starting table."""
    table = by_name[ref["table"]]
    configured = table_stats.get(ref["table"], {})
    rows = int(max(1, _number(configured.get("rows"), default_rows)))
    filters = predicates.get(ref["alias"], [])
    if not _matching_index(_indexes(table), filters):
        return rows
    selectivity = 1.0
    for predicate in filters:
        selectivity *= _selectivity(predicate, rows, configured=configured.get("selectivity", {}).get(predicate["column"]))
    return max(1, int(rows * selectivity))


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

    refs = parsed["tables"]
    if (not explain and len(refs) == 2 and parsed["joins"]
            and not re.search(r"\b(?:LEFT|RIGHT|FULL|STRAIGHT_JOIN)\b", mask_sql(query), re.I)):
        refs = sorted(refs, key=lambda ref: _starting_scan_rows(
            ref, by_name, table_stats, stats.get("default_rows", 100_000), parsed["predicates"]
        ))
    prior_aliases = set()
    prior_output = 1
    for ref in refs:
        table_name = ref["table"]
        table = by_name[table_name]
        configured = table_stats.get(table_name, {})
        rows = int(max(1, _number(configured.get("rows"), stats.get("default_rows", 100_000))))
        if "rows" not in configured:
            used_default_rows = True
        predicates = parsed["predicates"].get(ref["alias"], [])
        indexes = _indexes(table)
        match = _matching_index(indexes, predicates)
        order_index = _leading_index(indexes, parsed["order_column"]) if parsed["order_column"] and len(parsed["tables"]) == 1 else None
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
        if any(all(any(p["column"] == col and p["operator"] in {"=", "<=>"} for p in predicates) for col in unique) for unique in unique_columns):
            output_rows = 1
        scan_rows = output_rows if match else rows
        join_index = None
        if prior_aliases:
            for join in parsed["joins"]:
                if join["left_alias"] == ref["alias"] and join["right_alias"] in prior_aliases:
                    join_index = _leading_index(indexes, join["left_column"])
                elif join["right_alias"] == ref["alias"] and join["left_alias"] in prior_aliases:
                    join_index = _leading_index(indexes, join["right_column"])
                if join_index:
                    break
        if join_index and not match:
            per_lookup = 1 if join_index["kind"] in {"PRIMARY KEY", "UNIQUE"} and len(join_index["columns"]) == 1 else max(1, int(rows * 0.01))
            scan_rows = prior_output * per_lookup
        if parsed["limit"] is not None and len(parsed["tables"]) == 1 and not parsed["has_group"] and (not parsed["has_order"] or order_index) and (not predicates or match):
            scan_rows = min(scan_rows, parsed["limit"] or 1)
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
            if (parsed["has_order"] and not order_index) or parsed["has_group"]:
                high_ms *= 1.35
            if prior_aliases and any(ref["alias"] in (join["left_alias"], join["right_alias"]) for join in parsed["joins"]):
                if join_index:
                    low_ms += prior_output * _number(environment.get("best_index_probe_ms"), 0.002)
                    high_ms += prior_output * _number(environment.get("conservative_index_probe_ms"), 0.05)
                else:
                    low_ms += (prior_output + rows) / best_rate * 1000
                    high_ms += (prior_output + rows) / slow_rate * 1000
        total_low += low_ms
        total_high += high_ms
        access = {
            "table": table_name,
            "alias": ref["alias"],
            "table_rows": rows,
            "predicates": predicates,
            "access": operation.get("access") if operation else ("index lookup" if match else "ordered index scan" if order_index else "join index lookup" if join_index else "full scan"),
            "index": operation.get("key") if operation and operation.get("key") else (match["columns"] if match else order_index["columns"] if order_index else join_index["columns"] if join_index else None),
            "estimated_scan_rows": scan_rows,
            "estimated_output_rows": output_rows,
            "latency_low_ms": round(low_ms, 2),
            "latency_high_ms": round(high_ms, 2),
            "actual_ms": round(actual_ms, 3) if actual_ms is not None else None,
        }
        accesses.append(access)
        prior_aliases.add(ref["alias"])
        prior_output = min(1_000_000_000, max(1, prior_output * (per_lookup if join_index and not match else output_rows)))

        predicate_columns = [p["column"] for p in predicates]
        equality = [p["column"] for p in predicates if p["operator"] in {"=", "<=>", "IN"}]
        range_columns = [p["column"] for p in predicates if p["operator"] not in {"=", "<=>", "IN"}]
        candidate = list(dict.fromkeys(equality + range_columns))[:4]
        if predicate_columns and (not match or len(match["matched"]) < min(len(candidate), 2)):
            existing = [idx["columns"] for idx in indexes]
            if candidate and not any(cols[:len(candidate)] == candidate for cols in existing):
                column_types = {col["name"]: col.get("type", "") for col in table["columns"]}
                prefix_required = [col for col in candidate if re.match(r"^(?:TINYTEXT|TEXT|MEDIUMTEXT|LONGTEXT|TINYBLOB|BLOB|MEDIUMBLOB|LONGBLOB)\b", column_types.get(col, ""), re.I)]
                sql = None if prefix_required else f"CREATE INDEX idx_{table_name}_{'_'.join(candidate)} ON {table_name} ({', '.join(candidate)});"
                recommendations.append({
                    "severity": "high" if not match else "medium",
                    "title": f"Review an index for {table_name}",
                    "detail": ("TEXT/BLOB columns " + ", ".join(prefix_required) + " need a prefix length before MySQL/TiDB can index them. Choose a length from data distribution and engine limits before writing DDL."
                               if prefix_required else "Equality filters lead the candidate index; range filters follow. Confirm selectivity and write cost before applying it."),
                    "sql": sql,
                })

        table_columns = {c["name"] for c in table["columns"]}
        filtered = set(predicate_columns)
        for scope in dict.fromkeys((*SCOPE_COLUMNS, *stats.get("scope_columns", []))):
            if scope in table_columns and scope not in filtered:
                recommendations.append({
                    "severity": "medium",
                    "title": f"Confirm {scope} scoping on {table_name}",
                    "detail": f"The table contains {scope}, but the query does not filter it. Add the predicate when it is part of the data-access contract; omitting it can increase work and may cross a tenant boundary.",
                })
                break

    if not parsed["tables"]:
        assumptions.append("No referenced table could be matched to the parsed schema; simulation is unavailable.")
    if parsed["unknown_tables"]:
        assumptions.append("Tables absent from the parsed schema: " + ", ".join(parsed["unknown_tables"]) + ". Estimates omit their cost.")
    if used_default_rows:
        assumptions.append(f"Tables without statistics are modeled as {int(stats.get('default_rows', 100_000)):,} rows.")
    if not explain:
        assumptions.append("No EXPLAIN plan was supplied; access paths are inferred from SQL predicates and declared indexes.")
    if parsed["joins"] and not explain:
        assumptions.append("Join order and fanout are heuristic; a real plan is needed to validate index use and row counts.")
    if parsed["joins"] and parsed["limit"] is not None and not explain:
        assumptions.append("LIMIT does not reduce the estimated join scan because matching rows may appear late.")
    if re.search(r"\b(?:USE|FORCE|IGNORE)\s+(?:INDEX|KEY)\b", mask_sql(query), re.I) and not explain:
        assumptions.append("Index hints were not validated against a real plan.")
    if not stats:
        assumptions.append("No production statistics or hardware profile was supplied; latency is a broad heuristic range.")
    assumptions.append("Cache warmth, concurrency, locks, network latency, skew, partitions, and optimizer decisions can materially change runtime.")

    actual_ops = [op for op in explain if op.get("actual_end_ms") is not None]
    confidence = "high" if actual_ops else "medium" if explain and stats else "low"
    if actual_ops:
        observed = max(op["actual_end_ms"] * max(1, op.get("loops", 1)) for op in actual_ops)
        total_low, total_high = observed * 0.9, observed * 1.1
    if parsed["unknown_tables"] or not parsed["tables"]:
        total_low = total_high = None

    return {
        "query": query.strip(),
        "confidence": confidence,
        "latency": {"low_ms": round(total_low, 2) if total_low is not None else None, "high_ms": round(max(total_low, total_high), 2) if total_high is not None else None},
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
