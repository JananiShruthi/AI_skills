---
name: schema-map
description: Explore a TiDB or MySQL schema and explain or simulate SQL query plans in an interactive, source-backed workspace. Use for schema onboarding, table relationships, pasted EXPLAIN output, slow-query analysis, index suggestions, or approximate query latency.
---

# Schema map

Build an interactive schema workspace from repository files. Read SQL and code; never connect to or execute against a database. Python 3.9+ standard library is sufficient. `<skill>` means this skill's directory.

The workspace has four tabs: Overview, Schema, Query plan, and Simulator. Present it inline when the host supports interactive HTML. Otherwise, export a standalone HTML file to a task-owned temporary directory and give the user its path on the first run. Do not write into the user's repository unless requested. The workspace's **Export HTML** button downloads a standalone copy. Publish to cloud hosting only after an explicit publish request.

## Extract the schema

```bash
python3 <skill>/scripts/schema_map.py extract <repo_root> <schema.sql|migrations_dir> [more.sql ...] [--out <work_dir>] [--force]
```

Pass migration files in application order, or a directory whose SQL files sort in migration order. This writes `schema.json` and `annotations.json` in a temporary work directory, refuses to replace an existing extraction without `--force`, and prints a compact table summary. Missing FK targets and unsupported migration operations become visible warnings. Never load all of `schema.json` into context. Review prefilled annotations, inspect only cited repository lines, and edit `annotations.json`:

```json
{
  "purposes": {
    "documents": {"text": "Stores document metadata used during ingestion.", "evidence": ["schema.sql:42", "app/documents.py:18"]}
  },
  "categories": {"documents": "Documents & ingestion"},
  "pipeline": [
    {"title": "Register document", "summary": "Store metadata before processing chunks.", "tables": ["documents"], "evidence": ["app/ingest.py:30"]}
  ],
  "inferred_edges": [
    {"from": "child_table", "to": "parent_table", "evidence": ["application/db_layer/query.py:42"]}
  ]
}
```

Purpose statements, categories, pipeline steps, and inferred edges need direct evidence. A name alone is insufficient. Leave unsupported purposes absent so the workspace says **Confirm with the project owner**. Add inferred edges only for SQL joins or code queries that connect the tables.

## Analyze a query

When the user supplies SQL, asks about performance, or pastes an execution plan, read [references/query-analysis.md](references/query-analysis.md), then run `analyze-query`. The analysis is static unless supplied `EXPLAIN ANALYZE` contains observed timings. State the confidence and assumptions; never present an estimate as a production SLA.

## Preview inline

Write the preview fragment to the durable, task-owned path required by the host's inline visualization mechanism—not to the target repository:

```bash
python3 <skill>/scripts/schema_map.py preview <work_dir> -o <host_preview_fragment.html>
```

Display that fragment inline. The preview contains the complete tabbed workspace and its own **Export HTML** button. If the host cannot render interactive fragments, run `export` to a new temporary HTML path and link the file in your response. This gives Claude Code users a usable first result.

Keep preview fragments task-private: they contain local repository and work-directory paths for source links and the in-host export action. Both the CLI export and the page's downloaded HTML remove those paths.

## Export

The user can click **Export HTML** in the preview. If they instead ask you to save the workspace, run:

```bash
python3 <skill>/scripts/schema_map.py export <work_dir> -o <output.html> [--markdown <SCHEMA.md>] [--force]
```

Export refuses to overwrite existing files unless `--force` is supplied; inspect them before replacing. Report the saved path. The exported HTML contains no absolute repository path, is self-contained and has no network dependencies. If the user explicitly asks to publish, use available hosting tooling and report the resulting location.

## Completion

Report table and relationship counts, parse warnings, unverified purposes, and query-analysis confidence when present. Mention the most consequential query hotspot or recommendation, including the assumptions behind it.
