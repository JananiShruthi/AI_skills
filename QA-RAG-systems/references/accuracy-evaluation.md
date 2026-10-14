# Retrieval Accuracy Evaluation

Retrieval quality is a first-class result of any QA pass. A benchmark made only of clean, single-document, single-chunk queries will score high and tell you almost nothing. Build the set to be hard.

## Contents
- Ground-truth design
- Making the benchmark hard
- Keyword design when sensitive data is masked
- Metrics
- Procedure
- Regression comparison
- Interpreting changes
- Reporting

## Ground-truth design

Each query record has:
- `id`
- `question`
- `expected_doc`: the document that should be the top source (or a set, for multi-document questions)
- `expected_keywords`: strings, any one of which must appear in a top-k chunk for a keyword hit (case-insensitive substring)
- `category`: so results can be broken down (see below)
- `notes`: why this query exists, what it is meant to expose

Use synthetic documents so ground truth is known exactly. Keep the generation script in version control so the set is reproducible; seed the random generator.

Do not reuse the same documents for tuning and for evaluating. If the team tunes retrieval parameters against this set, hold out a second set for checking.

## Making the benchmark hard

Include these categories, each with enough queries to see a trend:

- **Direct lookup**: the baseline. A fact stated plainly in one chunk.
- **Multi-chunk documents**: the answer sits in chunk 3 of 7. Single-chunk documents hide ranking and recall problems.
- **Near-duplicate documents**: similar structure, different values (two companies with similar product lines). Retrieval must pick the right one.
- **Conflicting facts across documents**: same field, different values; the query must disambiguate by entity or date.
- **Updated documents**: ingest version 1, then version 2 with changed facts. The query must return the new value only.
- **Multi-hop**: the answer requires linking facts across documents or through a graph path.
- **Paraphrase and vocabulary mismatch**: the query uses synonyms, abbreviations, or different phrasing than the text.
- **Alias queries**: the entity is referred to by a different surface form than in the document.
- **Numeric and unit queries**: specific figures, ranges, units.
- **Negative queries**: asking about facts that are not in the corpus. Correct behavior is no confident result. Score these separately: the metric is the fraction returning nothing or clearly low-confidence output.
- **Off-topic and degenerate queries**: greetings, single common words, nonsense strings. Same scoring as negative queries.
- **Long-tail**: rare terms, typos, unusual casing.
- **Cross-tenant distractors**: the same fact exists in another tenant. Retrieval must not return it.

Aim for at least 30% of queries to be in the harder categories. Report accuracy per category; an aggregate number hides where it fails.

## Keyword design when sensitive data is masked

If the system masks names, organizations, locations, or other identifiers before storing text, those values will not appear in stored chunks and cannot be used as expected keywords.

Safe keywords: product codes, numeric specs, years, monetary figures, technical terms, domain vocabulary. Unsafe: person, organization, and location names, and anything else the masker replaces.

Verify each candidate keyword survives masking by inspecting the stored chunk text after ingestion before adding it to the ground truth. A keyword that never appears in storage produces false misses.

Note the consequence: queries that depend on masked names test the masking and resolution path, not keyword matching. Handle those with `expected_doc` checks rather than keywords.

## Metrics

Define k (commonly 5) and state it in the report.

| Metric | Definition |
|---|---|
| Accuracy | Fraction of queries with keyword hit in top-k |
| Keyword Hit@k | 1 if any expected keyword appears in any top-k chunk |
| Doc Hit@k | 1 if any top-k chunk comes from the expected document |
| Precision@k | Relevant chunks in top-k divided by k (relevant = from the expected document) |
| Recall@k | Relevant chunks in top-k divided by total chunks in the expected document |
| F1@k | Harmonic mean of Precision@k and Recall@k |
| Top-1 accuracy | 1 if the rank-1 chunk contains a keyword |
| MRR | Mean of 1 / rank of first hit (0 if none) |
| Abstention rate | For negative and degenerate queries: fraction correctly returning nothing or low confidence |
| Latency | Wall-clock per query; report mean, p50, p95, max |

Notes:
- Precision@k is capped when the expected document has fewer than k chunks. Do not compare it across documents of different sizes without saying so.
- Recall@k can look low for long documents even when the answer chunk is found. Read it alongside Keyword Hit and MRR.
- Compute Doc Hit, Precision, and Recall by joining returned chunk ids back to their parent documents in storage, scoped to the run's tenant.

## Procedure

1. Create a fresh tenant for the run so results are not contaminated.
2. Ingest all benchmark documents. Poll until ingestion completes; fail the run if it times out, and report which documents did not finish.
3. Verify ingestion: expected chunk counts, spot check masking.
4. Run each query, recording the full response, ranked chunk ids, and latency. Run queries sequentially for clean latency numbers; run a separate concurrent pass for load behavior.
5. Compute metrics per query, then aggregate overall and per category.
6. Save raw results as JSON so reports can be rebuilt without rerunning.
7. Write the report.

Run each query at least twice if the pipeline contains LLM steps (planning, reranking). Report any query whose result changes between runs as unstable; that is a finding in itself.

## Regression comparison

Compare to the previous run's saved results, same ground truth:

- Accuracy drop of more than 5 percentage points: a retrieval regression; investigate before closing.
- Per-category drop, even if the aggregate is flat: report it.
- p95 latency increase of more than 20%: flag even if accuracy is stable.
- Queries that flipped from hit to miss (newly failing) listed separately from queries that were already missing.

## Interpreting changes

- Recall drops while precision holds: ranking or cutoff changed (reranker prompt, fusion parameters, model change).
- Precision drops while recall holds: more irrelevant material is being retrieved (embedding model change, lexical index change, expansion step too broad).
- Top-1 drops while hit@k holds: ordering got worse, correct chunk is lower in the list.
- Abstention rate drops: the system is returning filler for queries it should refuse; common after loosening thresholds.
- Accuracy drops only on updated-document queries: stale versions are still retrievable.
- Accuracy drops only on near-duplicate queries: entity discrimination weakened.
- Latency up with no accuracy change: an extra model call, a larger candidate pool, or a slower dependency.

## Reporting

Report overall metrics, a per-category table, the miss list (question, expected doc, expected keywords, what was returned instead), a latency table, and the comparison to the prior run. Include the metric definitions so readers do not have to guess. See [report-template.md](report-template.md).
