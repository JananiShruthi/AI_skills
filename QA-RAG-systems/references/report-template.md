# QA Report Template

Use one regression report and one accuracy report per run. Format is up to the team (markdown, spreadsheet, or both); the content below is what matters. If the user wants spreadsheets, load the xlsx skill if available.

## Contents
- Regression report
- Test case record
- Finding record
- Accuracy report
- Run-over-run comparison
- Writing rules

## Regression report

### Summary
- Run date, build/version under test, environment, tenant used
- Scope chosen and why (full regression vs targeted)
- Totals: cases run, passed, failed, blocked
- Per-category pass/fail counts
- Newly failing (regressed) vs already-known failing
- New cases added this run (mark clearly) and cases retired, each with the reason
- Invariant results: baseline and final
- Blocked tests and what blocked them (e.g., no staging environment for fault injection)
- Top findings, ordered by severity, one line each

### Detail
One section or tab per category. Each case is a test case record.

### Unspecified behavior and open questions
Cases where no spec says what should happen. For each: the input, the observed behavior, and a concrete question for the feature owner ("What should a stopwords-only query return?"). These are neither passes nor failures until the owner decides. Also list any place where the docs and the observed behavior disagree.

### Hypotheses that held
A short list of hypotheses that were tested and did not break anything. This shows coverage and prevents re-testing the same ground.

## Test case record

| Field | Content |
|---|---|
| ID | Category prefix plus number (e.g. `CONC-002`) |
| Category | |
| Hypothesis | The specific assumption this tests |
| Motivation | Code location or observation that suggested it |
| Steps to reproduce | Exact commands (curl, SQL, script), runnable as written |
| Expected | Behavior defined by a spec, never derived from the code |
| Source of expectation | The doc, requirement, contract, or invariant that defines it; `UNSPECIFIED` if none |
| Actual | Real response and resulting state, not just "failed" |
| Invariants | Which were checked, and results |
| Severity | Per the guide in SKILL.md |
| Status | Pass, Fail, Blocked, Known gap, Unspecified |
| Notes | Server log lines captured at the time of failure, anomalies, follow-up variants |

## Finding record

Consolidate failures into findings; several test cases may share one root cause.

- **Title**: one line, specific ("Concurrent ingest of identical new document creates two current versions")
- **Severity**
- **Status**: confirmed or suspected
- **Impact**: what goes wrong for a user or operator
- **Repro**: minimal exact steps, runnable cold
- **Observed**: exact output and state
- **Expected**
- **Evidence**: log excerpts, query output, invariant violation
- **Likely cause**: only if verified in code; otherwise write "hypothesis:" and label it so
- **Suggested fix**: optional, short
- **First seen**: this run, or previous run date

## Accuracy report

- Overall metrics: accuracy, keyword hit@k, doc hit@k, precision@k, recall@k, F1@k, top-1, MRR, abstention rate, latency mean/p50/p95/max
- Per-category table of the same metrics
- Per-query results: all metrics, latency, relevant/total chunk counts
- Miss list: question, expected doc, expected keywords, what was returned
- Unstable queries (result changed between repeat runs)
- Latency table: percentile table plus queries sorted by latency
- Metric definitions
- Comparison to the previous run (below)

## Run-over-run comparison

- Metric deltas, overall and per category
- Newly failing queries (hit before, miss now), listed individually
- Newly passing queries
- Latency deltas at p50 and p95
- Verdict: regression, improvement, or flat, with the 5pp accuracy and 20% p95 thresholds applied

## Writing rules

- Report what was observed, not what was assumed. Quote actual output.
- Separate confirmed from suspected.
- Do not bury critical findings in the detail; they go in the summary.
- Do not list a known and documented gap as a new bug; list it under known gaps.
- Keep repro steps minimal: the shortest sequence that still shows the problem.
- Never paste real sensitive data into a report. Use the synthetic canaries.
- Before finishing, confirm every promised output file exists and opens.
