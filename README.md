# AI Skills

Portable skills for ChatGPT, Codex, and Claude Code. The repository is a skills-only Agent Plugin package, and every directory under `skills/` is also independently installable. Each skill uses the open `SKILL.md` convention: YAML metadata selects the skill, the Markdown body guides the agent, and optional `scripts/`, `assets/`, and `agents/` directories provide deterministic tooling and product metadata.

The skills use Python 3.9+ and the standard library. No package installation or API key is required.

## Available skills

| Skill | Use it when | Output |
| --- | --- | --- |
| [`flow-map`](skills/flow-map/SKILL.md) | You need to trace one Python function, method, route, or CLI entry point through calls, checks, loops, exceptions, and exits. | An interactive HTML control-flow map with source lines, test reachability, plain-language labels, and review notes. The page loads its pinned diagram libraries from jsDelivr when opened. |
| [`schema-map`](skills/schema-map/SKILL.md) | You need to explore a TiDB/MySQL schema, understand relationships, explain pasted query plans, or estimate a slow query's cost. | A tabbed workspace shown inline where supported, or saved as temporary HTML for hosts without an inline viewer. |

Both skills separate deterministic extraction and rendering from the agent's judgment. The scripts build the facts; the agent adds concise, evidence-backed explanations.

## Install for Codex

Clone the repository, then copy or symlink the skills you want into your user skill directory:

```bash
git clone https://github.com/JananiShruthi/AI_skills.git
mkdir -p ~/.codex/skills
cp -R AI_skills/skills/flow-map AI_skills/skills/schema-map ~/.codex/skills/
```

For one project only, place the skill directories under that project's `.codex/skills/` directory instead. Restart Codex after installation if the skills do not appear immediately.

Invoke a skill explicitly with a prompt such as:

```text
Use $flow-map to map src/api.py:create_order.
Use $schema-map to explain db/schema.sql.
```

The `agents/openai.yaml` files provide ChatGPT/Codex display metadata and default prompts. Automatic selection remains enabled, so Codex may also choose a skill from an ordinary request.

## Use with ChatGPT

The root [`plugin.json`](plugin.json) and `skills/` layout form a portable, skills-only Agent Plugin package. ChatGPT and Codex can consume the same package through a local, workspace, or public Plugins Directory. Repository maintainers can connect this checkout to a local marketplace for testing, then submit the package to the universal directory; ordinary users install it from the directory once published.

No MCP server or account connection is required by these skills. ChatGPT executes the packaged instructions and Python scripts against files available in its workspace.

## Install for Claude Code

```bash
mkdir -p ~/.claude/skills
cp -R AI_skills/skills/flow-map AI_skills/skills/schema-map ~/.claude/skills/
```

For project-scoped installation, copy them into `<project>/.claude/skills/`. The same `SKILL.md`, scripts, and assets work in both products; `agents/openai.yaml` is additive OpenAI metadata and does not change the Claude workflow.

## Repository layout

```text
.
├── AGENTS.md                 Shared maintainer guidance
├── CLAUDE.md                 Claude Code entry point
├── plugin.json               Portable ChatGPT/Codex package manifest
├── skills/
│   ├── flow-map/
│   │   ├── SKILL.md
│   │   ├── agents/openai.yaml
│   │   ├── assets/
│   │   └── scripts/
│   └── schema-map/
│       ├── SKILL.md
│       ├── agents/openai.yaml
│       ├── assets/
│       └── scripts/
└── tests/                    Repository-only regression tests
```

The `tests/` directory is intentionally outside the skill folders, so users install only runtime files.

## Develop and validate

Run both regression suites from the repository root:

```bash
python3 -B -m unittest discover -s tests/flow_map -v
python3 -B -m unittest discover -s tests/schema_map -v
```

Validate skill metadata with Codex's `quick_validate.py` when it is available in your Codex installation:

```bash
python3 /path/to/quick_validate.py skills/flow-map
python3 /path/to/quick_validate.py skills/schema-map
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the acceptance criteria for changes and new skills.

## License

Released under the [MIT License](LICENSE).
