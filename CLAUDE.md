# AI-GNITION hackathon: watsonx Orchestrate agents

This repo is a watsonx Orchestrate ADK workspace (also opened in IBM Bob). Claude Code and Bob share the same folders.

## Layout
- `agents/` YAML agent specs
- `tools/` Python tools (`@tool`) or flows (`@flow`)
- `knowledge-bases/` KB specs and source docs
- `connections/` connection specs (never commit credentials)
- `scripts/` deploy/rollback scripts
- `.bob/skills/` Bob skills (wxo-builder, sop-builder, solution-architect, ...). Read the relevant SKILL.md before building.
- `venv/` Python 3.12 env, do not edit

## Rules
- All teams share one watsonx Orchestrate environment. EVERY artifact (agent, tool, KB, connection) must be named `<team_name>_<artifact_name>`, otherwise it can silently overwrite another team's work. Team name: nyp
- Never commit API keys, passwords or the WXO instance credentials. Use env vars or connections.
- Use git: pull before starting, commit small, push often. Teammates also edit this repo.
- Prefer the `watsonx-orchestrate-adk-docs` MCP server for ADK schemas and API details.
