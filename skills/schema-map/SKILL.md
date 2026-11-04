---
name: schema-map
description: Explore a TiDB or MySQL schema and explain or simulate SQL query plans in an interactive, source-backed workspace. Use for schema onboarding, table relationships, pasted EXPLAIN output, slow-query analysis, index suggestions, or approximate query latency.
---

# Schema map

Build an interactive schema workspace from repository files. Read SQL and code; never connect to or execute against a database. Python 3.9+ standard library is sufficient. `<skill>` means this skill's directory.

The workspace has four tabs: Overview, Schema, Query plan, and Simulator. Present it in the host's inline interactive surface when available. Initial invocation is preview-only: do not create a standalone HTML deliverable in the user's repository. The workspace's **Export HTML** button downloads a standalone copy; use the CLI `export` command only when the user explicitly asks you to save one. Publish to cloud hosting only after an explicit publish request.

## Extract the schema

```bash
python3 <skill>/scripts/schema_map.py extract <repo_root> <schema.sql> [--out <work_dir>]
```

This writes `schema.json` and `annotations.json` in a temporary work directory and prints a compact table summary. Never load all of `schema.json` into context. Review prefilled annotations, inspect only cited repository lines, and edit `annotations.json`:

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

Display that fragment inline. Do not describe it as an exported file or ask the user to open a browser. The preview contains the complete tabbed workspace and its own **Export HTML** button. If the host cannot render interactive fragments, explain that limitation and offer explicit HTML export or cloud publishing instead.

## Export only on request

The user can click **Export HTML** in the preview. If they instead ask you to save the workspace, run:

```bash
python3 <skill>/scripts/schema_map.py export <work_dir> -o <output.html> [--markdown <SCHEMA.md>]
```

Report the saved path. The exported HTML is self-contained and has no network dependencies. If the user explicitly asks to publish, use available hosting tooling and report the resulting location; do not publish merely because preview is unavailable.

## Completion

Report table and relationship counts, parse warnings, unverified purposes, and query-analysis confidence when present. Mention the most consequential query hotspot or recommendation, including the assumptions behind it.
