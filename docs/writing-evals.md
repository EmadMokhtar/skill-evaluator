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

Adding the repository as a marketplace downloads the repository, a few megabytes, because
the catalog lives in it; the install then copies only the plugin directory. Claude Code and
Codex can limit that download to the two paths the catalog and the plugin need, which cuts it
to a fraction:

```bash
# Claude Code, from the terminal
claude plugin marketplace add EmadMokhtar/skill-evaluator \
  --sparse .claude-plugin plugins/skill-lens

# Codex
codex plugin marketplace add EmadMokhtar/skill-evaluator \
  --sparse .claude-plugin --sparse plugins/skill-lens
```

For Cursor, the same commands install it and, run again, update it — they replace the copy
rather than nesting a new one inside it:

```bash
mkdir -p ~/.cursor/plugins/local
src=$(mktemp -d)
git clone --depth 1 https://github.com/EmadMokhtar/skill-evaluator "$src"
rm -rf ~/.cursor/plugins/local/skill-lens
cp -R "$src/plugins/skill-lens" ~/.cursor/plugins/local/skill-lens
rm -rf "$src"
```

Any other client that implements Agent Plugins 1.0.0 loads `plugins/skill-lens/` the same
way: it reads `plugin.json` and discovers the skill in `skills/writing-skill-evals/`.

Then ask for it by name, or describe the task — "write evals for my order-support skill".
Claude Code lists a plugin's skills under the plugin's name, so there it is
`skill-lens:writing-skill-evals`.

!!! warning "A Copilot install reaches your Copilot evals"
    Copilot loads the plugins under `~/.copilot` in every session, including the ones
    `--runner copilot` starts. Once installed there, this skill is on offer in both arms of a
    comparison — `--baseline none` included — and in every `mode: offered` menu, beside the
    skill you are testing. Run Copilot evals from a hermetic home (one that loads nothing from
    your personal setup), as [Product runners](runners.md#product-runners) describes.
    `--runner claude-code` is unaffected: it leaves your plugins out.

### Updating it

The plugin carries no version number, on purpose. Agents install it from the tip of `main`,
not from a release, and treat a version as a cache key: with one pinned, an edit made between
releases would never reach an install that already exists, while a fresh install got it under
the same number. Without one, Claude Code keys each install on the commit, so an update always
fetches the current skill:

| Agent | Update |
| --- | --- |
| Claude Code | `claude plugin marketplace update skill-lens`, then `claude plugin update skill-lens@skill-lens` |
| GitHub Copilot CLI | `copilot plugin update skill-lens` |
| Codex | `codex plugin marketplace upgrade skill-lens` |
| Cursor | Run the commands above again |

The skill describes `skill-lens` as it is on `main`, and a merge that changes the CLI is
released straight away, so keep the CLI itself current (`uv tool upgrade skill-lens`) rather
than pinned to an old release.

### Without a plugin manager

An agent that reads only a skills directory takes the skill itself. Link it in:

```bash
git clone https://github.com/EmadMokhtar/skill-evaluator
mkdir -p ~/.claude/skills
ln -sfn "$PWD/skill-evaluator/plugins/skill-lens/skills/writing-skill-evals" \
  ~/.claude/skills/writing-skill-evals
```

The skill used to live in `skills/writing-skill-evals/`. A link made to that path dangles
after a `git pull`, and the agent drops the skill without an error. The `ln -sfn` line above
replaces it: `-f` overwrites the old link, and `-n` stops `ln` from following it.

The skill's directory also carries its own eval suite, `evals/writing-skill-evals.eval.yaml`,
because skill-lens finds a suite only beside the `SKILL.md` it tests. If you copy the skill
into a repository you run skill-lens on, leave `evals/` behind, or its cases join your gate.

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
