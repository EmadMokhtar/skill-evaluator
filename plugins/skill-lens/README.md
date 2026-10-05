# skill-lens

An Agent Skill for writing, running and auditing
[skill-lens](https://github.com/EmadMokhtar/skill-evaluator) eval suites.

skill-lens turns "I think this `SKILL.md` got better" into a score, a report and an exit
code a CI pipeline can gate on. It cannot tell you which cases a skill needs, or what a
failing case means. This plugin carries that judgment.

## What it contains

| Skill | What it does |
| --- | --- |
| `writing-skill-evals` | Reads a skill's `SKILL.md` for its claims, proposes a list of cases and confirms it with you, then writes, runs and triages the eval suite. For each failing case it names the cause: the eval is wrong, the skill is wrong, or the setup is. It also audits a suite you already have. |

That one skill is the whole plugin: no commands, agents, hooks or MCP servers.

## Requirements

The skill runs the `skill-lens` command-line tool, which this plugin does not install. See
[Using it](https://emadmokhtar.github.io/skill-evaluator/writing-evals/#using-it) for the
install command.

## Install

Add the repository `EmadMokhtar/skill-evaluator` as a plugin marketplace and install the
plugin `skill-lens`. The exact command for Claude Code, GitHub Copilot, VS Code, Codex and
Cursor, and how to update, is in
[Installing it](https://emadmokhtar.github.io/skill-evaluator/writing-evals/#installing-it).

## License

MIT — see [LICENSE](LICENSE).
