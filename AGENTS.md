# Maintainer guidance

This repository contains a portable, skills-only Agent Plugin package, not an application. Each directory under `skills/` is also an independently installable skill with a required `SKILL.md` and only the scripts, assets, references, or product metadata needed at runtime.

## Portability

- Support ChatGPT, Codex, and Claude Code through the shared `SKILL.md` workflow. Put OpenAI-specific UI metadata in `agents/openai.yaml`; keep runtime instructions product-neutral.
- Keep the root `plugin.json` compatible with the portable Agent Plugins schema so ChatGPT and Codex can install the repository as one package.
- Use repository-relative or runtime-supplied paths. Generated examples, author-specific paths, credentials, and private project identifiers do not belong in the repository.
- Keep dependencies minimal and document every non-standard runtime dependency in the root README and the relevant skill.
- Keep regression tests outside installable skill directories.

## Skill changes

- Keep the YAML `name` equal to its directory name and make `description` state both capability and activation conditions.
- Put deterministic, repeated work in scripts. Reserve `SKILL.md` for decisions and judgment the agent must make.
- Keep `SKILL.md`, command help, generated schemas, templates, and `agents/openai.yaml` consistent when an interface changes.
- Add a focused regression test for each corrected parser, resolver, graph, or renderer behavior.
- Do not commit generated maps, temporary extraction output, bytecode, virtual environments, or caches.

## Verification

Run both test commands documented in `README.md` after code changes. For a new or substantially revised skill, also run Codex's `quick_validate.py` against the skill directory when that validator is available.

`flow-map` is intentionally token-efficient: extraction prints the compact source summary and the agent writes only `labels.json`. Preserve that boundary. `schema-map` is evidence-first: purpose statements, pipeline steps, and inferred relationships need direct repository evidence; unsupported claims remain explicitly unverified.
