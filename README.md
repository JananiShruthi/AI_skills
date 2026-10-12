# qa-rag-systems

An agent skill for adversarial QA of RAG and LLM-backed APIs: ingestion, hybrid retrieval, reranking, and entity/graph extraction.

Instead of working through a fixed checklist, the skill has the agent read the code, list each component's assumptions, and write tests designed to violate them. Expected results come from documentation and requirements, never from the code under test, so tests cannot pass by construction; behavior with no governing spec is reported as an open question, not a pass. It checks system-state invariants after every test, mutates passing cases, spends time on anomalies, and measures retrieval accuracy on a benchmark that includes hard cases.

## What is inside

```
qa-rag-systems/
├── SKILL.md                         workflow and rules
└── references/
    ├── failure-hypotheses.md        catalog of ways RAG systems break
    ├── invariants.md                state checks to run after every test
    ├── accuracy-evaluation.md       benchmark design, metrics, comparison
    └── report-template.md           report structure
```

## Install

Claude Code, personal:

```bash
cp -r qa-rag-systems ~/.claude/skills/
```

Claude Code, per project:

```bash
cp -r qa-rag-systems <your-project>/.claude/skills/
```

Or, if published to a GitHub repo that follows the skills layout:

```bash
npx skills add <owner>/<repo> --skill qa-rag-systems
```

## Use

Ask your agent to QA a RAG system, for example:

- "QA the ingestion and query endpoints of this RAG service and find edge cases."
- "Build an accuracy benchmark for this retriever and run it."
- "Write a reproducible bug report for this retrieval issue."

The agent will read the code, build a hypothesis list, define invariants for your schema, execute against the running system, and report.

## Project-specific details

Keep project specifics (endpoints, schema, deployment commands, release-notes workflow) in a separate private skill or project file that tells the agent to load this one. This skill intentionally contains no project identifiers.

## License

Choose a license before publishing (MIT is common).
