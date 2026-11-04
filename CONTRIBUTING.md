# Contributing

Contributions should keep every skill portable, focused, and independently installable.

## Change an existing skill

1. Read its `SKILL.md` and inspect the relevant script, asset, and tests.
2. Make the smallest change that fixes the observed behavior.
3. Keep user-facing instructions, CLI help, data formats, templates, and `agents/openai.yaml` synchronized.
4. Add a regression test for behavior changes and run the complete suite.

## Add a skill

A new `skills/<skill-name>/` directory must contain:

- `SKILL.md` with YAML `name` and a discriminating `description`.
- `agents/openai.yaml` with a display name, a 25–64 character short description, and a default prompt that explicitly names `$skill-name`.
- Only the scripts, references, and assets the runtime workflow actually uses.

Prefer Python 3.9+ standard-library scripts. If another dependency is essential, document installation and version expectations in both the skill and root README.

Keep detailed or conditional material in a linked `references/` file when that makes the main workflow easier to follow. Do not add per-skill READMEs, generated outputs, credentials, personal paths, or unrelated examples.

## Acceptance checks

```bash
python3 -B -m unittest discover -s tests/flow_map -v
python3 -B -m unittest discover -s tests/schema_map -v
```

When Codex's skill validator is available, also run `quick_validate.py <skill-directory>`. A contribution is ready when tests pass, metadata matches the workflow, paths are portable, and a fresh user can follow the root installation instructions.
