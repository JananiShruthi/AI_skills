# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

A collection of **Claude Code agent skills** for other developers to install and use in their own AI-driven development. It is not an application. Because the skills run in other people's repos, keep them portable: no hard-coded paths, no author-specific setup, and minimal dependencies. Each top-level directory is a self-contained skill (`SKILL.md` with YAML frontmatter `name`/`description`, plus supporting files) that gets copied into `~/.claude/skills/` or `<project>/.claude/skills/`. Nothing gets built and there's no package manifest. The only tests are the `flow-map` regression tests described below.

The parent directory's CLAUDE.md (`../CLAUDE.md`) describes a sibling project (GraphRAG on TiDB, `entity_resolution.py`). It is useful context for what these skills are aimed at, but none of its code lives in this repo.

- `QA-RAG-systems/`: the `qa-rag-systems` skill. It is pure Markdown, an adversarial QA workflow for RAG/LLM APIs. `SKILL.md` holds the phased workflow and rules of evidence, and `references/*.md` holds the hypothesis catalog, invariants, accuracy-eval method and report template, all linked from `SKILL.md`.
- `flow-map/`: the `flow-map` skill. It renders an interactive HTML control-flow map of one Python entry point.

## Editing the skills

- **The installed copy is separate.** `~/.claude/skills/flow-map` is a plain copy of `flow-map/`, not a symlink. Changes here don't affect the live skill until you copy them over again (`cp -r flow-map ~/.claude/skills/`).
- **`qa-rag-systems` must stay project-agnostic.** Per its README, it must not contain project identifiers (endpoints, schema names, deploy commands). Project specifics belong in a separate private skill that loads this one.
- **Its core rule is the oracle rule.** Expected results come from specs or invariants, never from the code under test. Unspecified behavior is marked `UNSPECIFIED` rather than given a verdict. Keep new content consistent with this rule.
- **Token economy is a design goal of `flow-map`** (see the latest commit). The script does all the mechanical work, and the agent only writes `labels.json` (plain-English node labels, function titles, optional edge labels, review notes). Keep work out of the agent's prompt by moving it into the script.

## flow-map architecture

Stdlib-only Python 3.9+. There are no dependencies to install.

```bash
python3 flow-map/scripts/flow_map.py entries <repo_root>                        # list candidate entry points
python3 flow-map/scripts/flow_map.py extract <repo_root> --entry path/file.py:func_or_Class.method [--depth 3] [--out DIR]
python3 flow-map/scripts/flow_map.py render <out_dir> [-o out.html]             # default: <repo_root>/docs/flows/<entry>.html
```

Regression tests live outside the skill folder so they don't ship with it: `tests/flow_map/test_flow_map.py`, run against `tests/flow_map/fixture_project/`. Each test there encodes a bug that was found and fixed.

```bash
python3 -B -m unittest discover -s tests/flow_map -v           # all
python3 -B -m unittest discover -s tests/flow_map -k finally    # one test by name
```

When fixing a mis-drawn construct, add a minimal case to the fixture and a test first. The fixture's own `tests/test_things.py` is *data* for the "Tested?" detection, not a test to run. Always pass `-B`, because a `.pyc` is tracked in git.

To try a change by hand, run `extract` then `render` against any Python project. You can point it at this repo itself, e.g. `--entry flow-map/scripts/flow_map.py:main`.

How the pipeline works:

1. **`scripts/extract_checks.py`** does the AST analysis. `Project`/`Module` index the source files, skipping venvs, build directories and the like. `Resolver` resolves calls to project functions, and `FlowExtractor` walks a function body into a nested flow tree of checks, loops, excepts, exits and calls. `analyze()` recursively follows project calls up to `--depth`, never descending into library code. It also detects routes and decorators, auth hints and pydantic validators. It attaches test reachability from `coverage.xml` or a `.coverage` sqlite file when one exists in the target root, and falls back to grepping test files for references. It can also run standalone and print JSON.
2. **`scripts/flow_map.py`** handles graph building and output. It imports `extract_checks` as `ec`. `GraphBuilder` flattens each function's flow tree into nodes and edges with ids like `n12` and kinds `input step call ext unres check loop except error exit`. `extract` writes `graph.json` to a temp directory, which by default is `$TMPDIR/flow-map/<name>-<sha1(root|entry)[:8]>`. It deletes any stale `labels.json` there, because node ids aren't stable across runs. It then prints the compact one-line-per-node summary that the agent labels from.
3. **`render`** merges `labels.json` into the graph. It applies node, edge and function labels, puts the agent's notes ahead of the auto notes, and adds the summary. It then performs string replacement of `__TITLE__` and `__FLOW_DATA__` in **`assets/template.html`**, a self-contained React Flow 11 + dagre page loaded from jsDelivr with pinned versions. `</` is escaped in the embedded JSON. Finally it warns about unlabelled nodes or untitled functions.

**Keep the interfaces in sync.** The summary format and `labels.json` schema are documented in `flow-map/SKILL.md`. Any change to the summary line format, node kinds or the labels schema must be mirrored there, and the template must still read the same graph fields.

`flow-map/scripts/__pycache__/*.pyc` is committed by accident, and there's no `.gitignore`. Don't commit regenerated `.pyc` files.
