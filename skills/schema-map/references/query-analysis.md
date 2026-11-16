# Query analysis

Use this branch when the user provides SQL, pasted `EXPLAIN` output, or asks how long a query may take.

## Inputs

The SQL query is required. Accept it inline or in a file. The analyzer supports MySQL JSON plans, MySQL and TiDB tabular `EXPLAIN`, TiDB tabular `EXPLAIN ANALYZE`, and MySQL textual `EXPLAIN ANALYZE` trees.

```bash
python3 <skill>/scripts/schema_map.py analyze-query <work_dir> --query-file <query.sql> [--explain <explain.txt>] [--stats <stats.json>]
```

For a short query, `--query '<sql>'` is also valid. Avoid shell interpolation hazards; prefer `--query-file` for user-provided SQL containing quotes, backticks, or `$`.

The optional statistics file makes simulation more meaningful:

```json
{
  "default_rows": 100000,
  "scope_columns": ["namespace_sk"],
  "tables": {
    "emails": {
      "rows": 1000000,
      "selectivity": {"user_id": 0.0001, "account_id": 0.01}
    }
  },
  "environment": {
    "best_rows_per_second": 2000000,
    "conservative_rows_per_second": 50000,
    "best_index_probe_ms": 0.002,
    "conservative_index_probe_ms": 0.05
  }
}
```

Use actual table cardinalities and measured throughput when the user has them. Do not invent production statistics. Without them, the analyzer uses visible defaults and marks confidence low.

`analyze-query` writes `query-analysis.json` inside the work directory, then numbered files for later queries. Run `preview` again to include them in the Query plan and Simulator tabs. Earlier results remain available through the query selector. `--force` explicitly replaces the first result.

## Interpretation

- `high` confidence means pasted `EXPLAIN ANALYZE` supplied observed timing. It still describes that run, not future load.
- `medium` confidence requires both a supplied plan and table/environment statistics.
- `low` confidence means the access path, cardinality, or throughput includes heuristics.

The range models rows examined and coarse throughput. Cache warmth, locks, concurrency, network latency, skew, partitions, stale statistics, and optimizer choices remain outside the model. Keep those assumptions visible.

Treat generated index DDL as a candidate for review. Check selectivity, existing equivalent indexes, write amplification, storage, and the real plan before applying it. A warning about `account_id`, `tenant_id`, `namespace_sk`, or configured `scope_columns` is conditional: recommend the filter only when it is part of the application's access-control or tenancy contract.

When the user asks for a better query, use the workspace's access rows and recommendations as evidence. Explain the dominant cost, propose the smallest query or index change, and recommend validating it with `EXPLAIN ANALYZE` in a safe non-production environment.
