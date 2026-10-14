# Invariants to Check After Every Test

An invariant is a condition that must hold no matter what the system was just asked to do. Write each as a query or script, run the full set after every test (and after every concurrency or fault test), and treat any violation as a finding even if the test's own expectation passed.

Adapt table and column names to the system under test. The patterns below use generic names.

## Contents
- How to use
- Structural invariants
- Tenant isolation invariants
- Sensitive-data invariants
- Versioning invariants
- Index consistency invariants
- Graph invariants
- Response invariants
- Writing a runner

## How to use

1. Map the schema: tables for documents, chunks, vectors, lexical index, entities, aliases, edges, edge evidence.
2. For each pattern below, write the concrete query for this schema. Skip patterns that do not apply; add ones the schema implies.
3. Put them in one script that prints each invariant with its violation count and sample offending rows.
4. Run it before the test run (baseline), after every test, and at the end. A violation present at baseline is a pre-existing bug; one that appears after a test is caused by that test.

## Structural invariants

- Every chunk belongs to an existing document.
- Every document marked complete has at least one chunk (unless it is legitimately empty and flagged so).
- No document is in a "started but never finished" state older than the longest legitimate ingest time. These are orphans from partial writes.
- No duplicate chunk for the same (document, chunk position).
- Chunk positions for a document are contiguous, with no gaps.
- No empty or whitespace-only chunk text.
- Foreign keys resolve; there are no dangling references in any table.

```sql
-- Pattern: orphans
SELECT c.id FROM chunks c LEFT JOIN documents d ON d.id = c.document_id WHERE d.id IS NULL;

-- Pattern: documents with no chunks
SELECT d.id FROM documents d LEFT JOIN chunks c ON c.document_id = d.id
WHERE c.id IS NULL AND d.status = 'complete';

-- Pattern: duplicate positions
SELECT document_id, position, COUNT(*) FROM chunks GROUP BY document_id, position HAVING COUNT(*) > 1;
```

## Tenant isolation invariants

- Every row in every table carries a tenant/namespace value, and it is never null or empty.
- A chunk's tenant equals its document's tenant.
- An edge's tenant equals the tenant of both endpoint entities and of its evidence chunk.
- An alias's tenant equals its entity's tenant.
- Behavioral check (not just SQL): populate two tenants with distinct, recognizable content; query each; assert no result, evidence, entity, or graph path from the other tenant.

```sql
-- Pattern: chunk tenant mismatch
SELECT c.id FROM chunks c JOIN documents d ON d.id = c.document_id WHERE c.tenant <> d.tenant;

-- Pattern: cross-tenant edge
SELECT e.id FROM edges e
JOIN entities s ON s.id = e.source_id
JOIN entities t ON t.id = e.target_id
WHERE s.tenant <> t.tenant OR s.tenant <> e.tenant;
```

## Sensitive-data invariants

Seed documents with canary values: fake but realistic emails, phone numbers, national ID formats, names. Use obviously synthetic canaries so they can be grepped.

- No canary appears in stored chunk text if the design says text is stored masked.
- No canary appears in the lexical index vocabulary, except where design says otherwise.
- No canary appears in application logs, error responses, or prompts sent to models (capture prompts if the system logs them).
- No canary appears in any `/query` response.
- Placeholder tokens in stored text are well formed and map consistently.

```bash
# Pattern: grep logs and DB dumps for canaries
docker logs <container> 2>&1 | grep -E "CANARY_[A-Z0-9]+"
```

## Versioning invariants

- At most one current version per logical document per tenant.
- Version numbers are strictly increasing and contiguous.
- Chunks, index entries, and edges of superseded versions are either removed or excluded from retrieval. Verify with a query for a fact that changed between versions: only the new value is returned.
- Re-ingesting identical content creates no new rows (idempotency): row counts per table unchanged.

```sql
-- Pattern: more than one current version
SELECT tenant, name, COUNT(*) FROM documents WHERE is_current = 1 GROUP BY tenant, name HAVING COUNT(*) > 1;
```

## Index consistency invariants

- Every chunk has exactly one vector entry, and no vector entry points at a missing chunk.
- Vector dimension is uniform across all rows.
- Every lexical-index row points at an existing chunk.
- Counts match: number of chunks = number of vectors (adjust if some chunks are intentionally unembedded).
- Superseded chunks are absent from, or flagged in, every index.

## Graph invariants

- Every edge has an endpoint entity that exists.
- Every edge relation type is within the allowed vocabulary.
- Every edge has at least one evidence record, and every evidence record points at an existing chunk whose text plausibly supports the edge (spot check).
- No self-loop unless the vocabulary allows it.
- No duplicate edge for the same (source, relation, target, tenant).
- Each entity has at least one alias or canonical name; aliases are unique per tenant or intentionally shared.
- Fields documented as always-null or always-set actually are.

```sql
-- Pattern: relation outside vocabulary
SELECT id, relation FROM edges WHERE relation NOT IN ('REL_A','REL_B','REL_C');

-- Pattern: evidence pointing nowhere
SELECT ev.id FROM edge_evidence ev LEFT JOIN chunks c ON c.id = ev.chunk_id WHERE c.id IS NULL;

-- Pattern: duplicate edges
SELECT source_id, relation, target_id, COUNT(*) FROM edges GROUP BY 1,2,3 HAVING COUNT(*) > 1;
```

## Response invariants

Check on every API response, not only on tests that target the response:

- Status code and body shape match the documented contract (including intentional stubs and error bodies).
- Error responses contain no stack traces, file paths, SQL, prompts, or sensitive data.
- Result counts do not exceed the requested limit.
- Results are deduplicated.
- Evidence text in a result actually comes from the cited chunk.
- Responses to irrelevant queries (greetings, nonsense, topics absent from the corpus) are empty or clearly low-confidence rather than filled with nearest-neighbor noise.
- Latency is within an expected bound; flag outliers for the explore phase.

## Writing a runner

Keep a single script (`check_invariants.py` or similar) that:
- connects with read-only credentials,
- runs every invariant,
- prints `PASS name` or `FAIL name (n violations)` with up to five sample rows,
- exits non-zero on any failure,
- accepts a tenant/namespace argument to scope checks to the current run, while a separate full-database mode catches cross-tenant leaks.

Store it with the test run so any finding can cite the exact invariant and its output.
