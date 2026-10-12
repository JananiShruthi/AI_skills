---
name: qa-rag-systems
description: Adversarial QA for RAG and LLM-backed APIs (ingestion, hybrid retrieval, reranking, entity or graph extraction). Use when asked to test, QA, red-team, or regression-check a RAG pipeline, find edge cases, build an accuracy benchmark, or write a reproducible bug report for a retrieval system.
---

# QA for RAG Systems

Goal: find where the system breaks, not confirm it works. Passing obvious cases is not evidence of quality. Value comes from cases the developer would not think to try.

## Sources of truth

Decide what "correct" means before looking at how the code behaves. A test whose expected result is derived from the implementation passes by construction and proves nothing.

- **Expected results come from specs, never from code.** Valid sources, in rough order of authority: the requirements or feature description, user documentation, the API contract, knowledge-transfer notes, release notes, and the stated intent of the person who owns the feature. Cite the source for every expectation.
- **Code is for finding where to attack, not for deciding what is right.** Read it to locate unguarded paths, assumptions, shared state, and dependencies. Do not copy its behavior into an "Expected" column.
- **Spec and code disagree: that is a finding.** Never resolve the conflict by trusting the code.
- **No spec covers it: no verdict.** Record the observed behavior, mark the case `UNSPECIFIED`, and put it on a list of questions for the owner (e.g., "what should a stopwords-only query return?"). Do not invent an expectation that happens to match what the system did.
- **Invariants are the safest oracle.** Properties like "no cross-tenant results" or "no sensitive data stored unmasked" come from requirements, not implementation, so checking them cannot be circular.
- If no documentation exists at all, say so in the report, ask the owner for the intended behavior of the main features, and write it down as the spec before testing.

## Workflow

Follow these phases in order. Do not skip phase 2; it is what separates this from checklist testing.

Run in two passes where possible:
- **Pass A, black box.** Phases 1 (docs only), 4, and 5, working only from documentation and observed behavior, the way a human QA would. Do this before reading the code.
- **Pass B, code-informed.** Phases 2 and 3, then 4 to 6 again, using the code to design harder attacks. Expected results still come from the specs.

If a single pass is all that is feasible, do the docs step of phase 1 first and write the expected behavior down before opening any code.

### 1. Orient
- Read the specs first: user documentation, KT notes, API contract, release notes or changelog. Write down the expected behavior of each feature in your own words, with the source.
- Then read the code paths for ingestion, retrieval, model calls, and the API layer, plus the schema and config. This is for locating attack surface, per Sources of truth.
- Confirm the deployed build is the one under test before sending traffic. If it is not running or does not match, stop and report.
- Run the existing unit suite once as a floor. Note what it mocks; live testing targets what mocks cannot catch (real writes, real model output, real concurrency, real timing).
- If scope is unclear (full regression vs new feature, load testing needed, known limitations to treat as blockers), ask once, briefly, then proceed.

### 2. Hypothesize
For each component, write down the assumptions it makes about its inputs, ordering, timing, and dependencies. Then write one test per assumption designed to violate it. See [references/failure-hypotheses.md](references/failure-hypotheses.md) for a catalog to start from; treat it as a prompt, not a checklist, and add hypotheses specific to what you read in the code.

Each test must state:
- the hypothesis it tests
- the code location that motivates it
- the expected behavior and the spec that defines it (or `UNSPECIFIED`)
- what a failure would look like (including state, not just response)

Do not count tests. Do not pad with generic empty/null cases unless the code shows that path is unguarded.

### 3. Define invariants
Write queries or checks that must hold after any operation, regardless of test. See [references/invariants.md](references/invariants.md). Run them after every test and after every concurrency or fault test. A response can look correct while state is corrupted; invariants catch that.

### 4. Execute
- Test the live system, black box, with real calls.
- Record the actual response and resulting state for every case, not just pass/fail.
- On failure, immediately capture server logs before later requests overwrite them.
- Never restart or modify the deployment unless the user explicitly allows it. Fault injection needs a staging copy; if none exists, list those tests as blocked rather than skipping silently.
- Use a fresh namespace or tenant per test run so tests do not contaminate each other or real data.

### 5. Mutate
For every passing case, vary one dimension at a time: length, language, unicode and normalization, whitespace, duplicate content, sensitive-data density, ordering, timing, concurrency level. Prefer property checks ("ingest then query for a phrase from the document retrieves it") over fixed expected strings.

### 6. Explore
Reserve roughly the last 20% of effort for anomalies seen along the way: odd latency, surprising counts, unexpected 200s, slow or noisy logs. For each anomaly, write at least three variants that might make it worse. Anomalies are where the real bugs are.

### 7. Measure accuracy
Retrieval quality is a metric, not an afterthought. Build or reuse a ground-truth set and report metrics per [references/accuracy-evaluation.md](references/accuracy-evaluation.md). A benchmark that only contains easy queries is not informative; include hard cases.

### 8. Report
Use [references/report-template.md](references/report-template.md). Distinguish clearly between:
- **confirmed bug**: reproduced, with exact repro steps
- **suspected issue**: observed but not isolated
- **hypotheses that held**: tested, did not break (so coverage is visible)

Distinguish newly failing cases from already-known failures on repeat runs; that matters more than the raw pass count.

## Severity guide
- **Critical**: data corruption, cross-tenant leak, sensitive data stored or returned unmasked, security hole
- **High**: wrong retrieval result surfaced, silent data loss, permanently stuck state
- **Medium**: unhandled crash, bad error contract, resource exhaustion
- **Low**: cosmetic, minor inconsistency

## Rules of evidence
- Every expected result cites its source (spec, doc, requirement, or invariant). "The code does this" is not a source.
- Verify a root cause by reading code before stating it, after a failure is observed. Otherwise label it a hypothesis.
- Every bug needs repro steps someone else can run cold: exact input, exact command, exact observed output.
- Do not report known-and-documented gaps as new bugs; list them as known.
- Behavior with no governing spec is neither pass nor fail. List it as `UNSPECIFIED` with the observed result and a question for the owner.
