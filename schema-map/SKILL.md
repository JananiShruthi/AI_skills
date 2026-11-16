---
name: schema-map
description: Explain a TiDB/MySQL repository schema to new developers with a source-backed pipeline story, Mermaid guide, and offline HTML explorer showing table keys and relationships.
---

# Schema map

Generate `SCHEMA.md` and one offline HTML explorer from a SQL schema. Read repository files; do not connect to a database. The script handles parsing, code references, constraints, grouping, diagrams, and validation. Supply only the small human layer: evidence-backed purpose statements, feature categories, a short data-flow story, and any justified inferred relationships. Focus on onboarding, not debugging recipes or hypothetical failure analysis.

`<skill>` means this skill's directory. Python 3.9+ standard library is sufficient.

## Run

```bash
python3 <skill>/scripts/schema_map.py extract <repo_root> <schema.sql|migrations_dir> [more.sql ...] [--out <work_dir>]
```

Pass migration files in application order, or a directory whose SQL files sort in migration order. The command prints the work directory, table count, parse warnings, and a compact table summary. It writes `schema.json` and `annotations.json` in the work directory and refuses to replace an existing extraction without `--force`. Explicit purpose rows in repository Markdown tables are prefilled with citations. Never load the entire JSON into your prompt. Review prefilled statements against the schema, then inspect only the cited lines needed for unfilled tables. Edit `annotations.json`:

```json
{
  "purposes": {
    "documents": {"text": "Stores document metadata used during ingestion and retrieval.", "evidence": ["schema.sql:42", "app/documents.py:18"]}
  },
  "categories": {"documents": "Documents & ingestion"},
  "pipeline": [
    {"title": "Register document", "summary": "Store document metadata before processing chunks.", "tables": ["documents"], "evidence": ["app/ingest.py:30"]}
  ],
  "inferred_edges": [
    {"from": "child_table", "to": "parent_table", "evidence": ["application/db_layer/query.py:42"]}
  ]
}
```

Use one short purpose sentence per table. Evidence must directly support it. A name alone is insufficient. Leave unsupported entries absent; the page will say **“Confirm with the project owner”**. Add an inferred edge only when a SQL join or code query actually connects the tables; a similarly named column is insufficient. Add 3–7 pipeline steps when code shows an end-to-end flow; each step needs a short title, one sentence, table names, and cited code. When no flow can be verified, leave it empty and the guide will say so. Use `categories` to correct unhelpful automatic groups; prefer established feature names from repository documentation. Do not describe hypothetical deletion outcomes.

```bash
python3 <skill>/scripts/schema_map.py render <work_dir> [-o <output.html>] [--markdown <SCHEMA.md>] [--force]
```

Default outputs are `<repo_root>/SCHEMA.md` and `<repo_root>/docs/schema_explorer.html`. The HTML can remain local and ignored by Git. `render` refuses to overwrite either existing output unless `--force` is supplied; inspect the existing files before choosing to replace them. It validates table coverage, relationships, evidence paths and line numbers, and annotations before writing. Missing FK targets and unsupported migration operations become visible warnings. If parsing reports a warning, the guide says the schema may be incomplete; do not claim completeness. The HTML contains no absolute repository path and needs no network access. Report both paths, table and relationship counts, and any parse warnings or unverified purposes. Stop after one extraction, annotation, render, and validation pass unless correcting a concrete failure.
