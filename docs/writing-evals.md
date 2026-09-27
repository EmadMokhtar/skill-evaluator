# The eval-writing skill

`skill-lens init` gives you the structure of a suite. It cannot tell you which cases
*this* skill needs, or what a red case means. That judgment ships as an Agent Skill.

## What it does

Given a skill to evaluate, it reads the `SKILL.md` for its claims, proposes a case list
and confirms it with you, scaffolds and fills in the suite, runs it, and triages the
failures — distinguishing an eval that is wrong from a skill that is wrong, and proposing
changes to your `SKILL.md` rather than making them silently. It also audits suites you
already have: missing negative controls, cases that assert nothing, rubric entries no
evidence could support.

## Installing it

The skill ships as a plugin named `skill-lens`, in
[`plugins/skill-lens/`](https://github.com/EmadMokhtar/skill-evaluator/tree/main/plugins/skill-lens).
An install copies that directory and nothing else — two manifests, the license and the
skill, about 30 KB. Each agent finds the file it reads:

| File | Read by |
| --- | --- |
| `plugins/skill-lens/plugin.json` | Clients that implement [Agent Plugins 1.0.0](https://agent-plugins.org/): GitHub Copilot (CLI and VS Code), Cursor, Codex |
| `plugins/skill-lens/.claude-plugin/plugin.json` | Claude Code, which has its own plugin format |
| `.claude-plugin/marketplace.json`, at the repository root | Claude Code, Copilot CLI, VS Code and Codex, when you add the repository as a marketplace |

Install it the way your agent installs plugins:

| Agent | Install |
| --- | --- |
| Claude Code | `/plugin marketplace add EmadMokhtar/skill-evaluator`, then `/plugin install skill-lens@skill-lens` |
| GitHub Copilot CLI | `copilot plugin marketplace add EmadMokhtar/skill-evaluator`, then `copilot plugin install skill-lens@skill-lens` |
| VS Code | Turn on `chat.plugins.enabled`, add `"EmadMokhtar/skill-evaluator"` to `chat.plugins.marketplaces`, then install `skill-lens` from **Browse Marketplace** |
| Codex | `codex plugin marketplace add EmadMokhtar/skill-evaluator`, then `codex plugin add skill-lens@skill-lens` |
| Cursor | Copy `plugins/skill-lens/` from a clone to `~/.cursor/plugins/local/skill-lens`, then run **Developer: Reload Window** |

Adding the repository as a marketplace downloads the repository, because the catalog lives
in it; the install then copies only the plugin directory. Codex can limit that download to
the two paths it needs:

```bash
codex plugin marketplace add EmadMokhtar/skill-evaluator \
  --sparse .claude-plugin --sparse plugins/skill-lens
```

For Cursor, from any working directory:

```bash
git clone --depth 1 https://github.com/EmadMokhtar/skill-evaluator
cp -R skill-evaluator/plugins/skill-lens ~/.cursor/plugins/local/skill-lens
```

Any other client that implements Agent Plugins 1.0.0 loads `plugins/skill-lens/` the same
way: it reads `plugin.json` and discovers the skill in `skills/writing-skill-evals/`.

Then ask for it by name, or describe the task — "write evals for my order-support skill".
Claude Code lists a plugin's skills under the plugin's name, so there it is
`skill-lens:writing-skill-evals`.

The plugin's version is the `skill-lens` version it was released with, so the instructions an
agent loads describe the CLI of that same version. A new `skill-lens` release is a new plugin
version.

### Without a plugin manager

An agent that reads only a skills directory takes the skill itself. Copy or symlink it in:

```bash
git clone https://github.com/EmadMokhtar/skill-evaluator
ln -s "$PWD/skill-evaluator/plugins/skill-lens/skills/writing-skill-evals" \
  ~/.claude/skills/writing-skill-evals
```

## Using it

The plugin installs the skill, not the command-line tool. The skill expects `skill-lens` on
`PATH`:

--8<-- "docs/snippets/install.md"

A project that already depends on `skill-lens` works as well. Everything the skill writes is
an ordinary eval file: nothing about the suite depends on the skill afterwards.

## Where to go next

Every field the skill writes is described in [Eval files](eval-files.md), and every term it
uses is defined in [Concepts](concepts.md). Once the suite passes, [CI integration](ci.md)
turns it into a gate on every pull request.
